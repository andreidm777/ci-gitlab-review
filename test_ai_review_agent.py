import json
import os
import sys
import subprocess
from unittest.mock import MagicMock, patch, mock_open

# ---------------------------------------------------------------------------
# Helpers to make the module importable without real env vars
# ---------------------------------------------------------------------------
_FAKE_ENV = {
    "LLM_API_KEY": "fake-key",
    "GITLAB_API_TOKEN": "fake-token",
    "CI_PROJECT_ID": "42",
    "CI_MERGE_REQUEST_IID": "7",
    "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
    "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
}


def _bootstrap_module():
    """Load ai_review_agent in an isolated way so tests can patch freely."""
    # Set env before importing
    for k, v in _FAKE_ENV.items():
        os.environ.setdefault(k, v)
    # Import the module fresh each time (avoid stale cache)
    import importlib
    import ai_review_agent as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnvValidation:
    def test_all_required_vars_present(self):
        mod = _bootstrap_module()
        assert mod.OPENAI_API_KEY == "fake-key"

    @patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False)
    def test_missing_var_exits(self, monkeypatch):
        # Remove one required var
        for k in list(_FAKE_ENV.keys()):
            os.environ.pop(k, None)
        os.environ["LLM_API_KEY"] = ""
        for k, v in _FAKE_ENV.items():
            os.environ.setdefault(k, v)
        os.environ["LLM_API_KEY"] = ""

        # We re-set to simulate missing
        for k in _FAKE_ENV:
            os.environ[k] = _FAKE_ENV[k]
        os.environ["LLM_API_KEY"] = ""

        import importlib
        import ai_review_agent as mod
        with patch.object(sys, "exit") as mock_exit:
            importlib.reload(mod)
            mock_exit.assert_called_once_with(1)


class TestGetGitDiff:
    def test_successful_diff(self):
        mod = _bootstrap_module()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "diff --git a/foo.py b/foo.py\n+print('hi')"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            diff = mod.get_git_diff()
            assert "diff --git" in diff
            # Should call git fetch first, then git diff
            assert mock_run.call_count == 2

    def test_fetch_failure_returns_empty(self):
        mod = _bootstrap_module()
        err_result = MagicMock()
        err_result.returncode = 1
        err_result.stderr = "fatal: couldn't find remote ref"

        with patch.object(subprocess, "run", return_value=err_result) as mock_run:
            diff = mod.get_git_diff()
            assert diff == ""
            assert mock_run.call_count == 1  # only fetch, no diff

    def test_diff_failure_returns_empty(self):
        mod = _bootstrap_module()
        fetch_ok = MagicMock(returncode=0, stdout="", stderr="")
        diff_err = MagicMock(returncode=1, stdout="", stderr="error")

        with patch.object(subprocess, "run", side_effect=[fetch_ok, diff_err]) as mock_run:
            diff = mod.get_git_diff()
            assert diff == ""
            assert mock_run.call_count == 2


class TestReadLocalFiles:
    def test_read_valid_files(self):
        mod = _bootstrap_module()
        sample_content = "line1\nline2\nline3\n"
        file_paths = ["/repo/src/foo.py", "/repo/src/bar.py"]

        def mock_open_real(path, *args, **kwargs):
            return mock_open(read_data=sample_content).return_value

        with patch("builtins.open", mock_open_real), \
             patch.object(os.path, "realpath", side_effect=lambda p: p), \
             patch.object(os.path, "exists", return_value=True), \
             patch.object(subprocess, "check_output", return_value="/repo\n"):
            ctx = mod.read_local_files(file_paths)
            assert len(ctx) == 2
            assert "/repo/src/foo.py" in ctx
            assert "/repo/src/bar.py" in ctx

    def test_rejects_file_outside_repo(self):
        mod = _bootstrap_module()
        with patch("builtins.open", mock_open()), \
             patch.object(os.path, "realpath", return_value="/etc/passwd"), \
             patch.object(subprocess, "check_output", return_value="/repo\n"):
            ctx = mod.read_local_files(["/etc/passwd"])
            assert ctx == {}

    def test_truncates_long_files(self):
        mod = _bootstrap_module()
        long_content = "\n".join(f"line{i}" for i in range(3000))
        with patch("builtins.open", mock_open(read_data=long_content)), \
             patch.object(os.path, "realpath", return_value="/repo/src/foo.py"), \
             patch.object(os.path, "exists", return_value=True), \
             patch.object(subprocess, "check_output", return_value="/repo\n"):
            ctx = mod.read_local_files(["/repo/src/foo.py"])
            assert ctx
            line_count = ctx["/repo/src/foo.py"].count("\n") + 1
            assert line_count <= mod.MAX_FILE_LINES + 1  # readlines[:N] can yield N+1 lines


class TestAskLlmForContext:
    def test_empty_diff(self):
        mod = _bootstrap_module()
        assert mod.ask_llm_for_context("") == []
        assert mod.ask_llm_for_context("   \n  ") == []

    def test_valid_json_response(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps({"files": ["src/a.py", "src/b.py"]})

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        files = mod.ask_llm_for_context("diff here")
        assert files == ["src/a.py", "src/b.py"]

    def test_invalid_json_returns_empty(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not json at all"

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        files = mod.ask_llm_for_context("diff here")
        assert files == []

    def test_non_dict_response_returns_empty(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(["plain", "array"])

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        files = mod.ask_llm_for_context("diff here")
        assert files == []

    def test_passes_timeout_and_model(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps({"files": []})

        call_args = {}
        def capture_create(**kwargs):
            call_args.update(kwargs)
            return mock_resp

        mod.client.chat.completions.create = MagicMock(side_effect=capture_create)
        mod.ask_llm_for_context("some diff")

        assert call_args.get("model") == mod.CONTEXT_MODEL
        assert call_args.get("timeout") == mod.LLM_TIMEOUT


class TestReviewCodeWithContext:
    def test_basic_review(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "LGTM"

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        result = mod.review_code_with_context("diff content", {})
        assert result == "LGTM"

    def test_with_context(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Found issue in foo.py"

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        context = {"/repo/foo.py": "def foo(): pass"}
        result = mod.review_code_with_context("diff", context)
        assert "Found issue" in result

    def test_language_passed_to_prompt(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "LGTM"

        captured_content = {}
        def capture(**kwargs):
            captured_content["content"] = kwargs["messages"][0]["content"]
            return mock_resp

        mod.client.chat.completions.create = MagicMock(side_effect=capture)
        mod.review_code_with_context("diff", {}, language="German")
        assert "German" in captured_content["content"]

    def test_default_language_is_russian(self):
        mod = _bootstrap_module()
        assert mod.REVIEW_LANG == "Russian"

    def test_truncates_huge_diff(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "LGTM"

        mod.client.chat.completions.create = MagicMock(return_value=mock_resp)
        huge_diff = "x\n" * 100000  # way too long
        result = mod.review_code_with_context(huge_diff, {})
        # Should not raise, should truncate internally
        assert result == "LGTM"


class TestPostMrComment:
    def test_success(self):
        mod = _bootstrap_module()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.raise_for_status = MagicMock()

        mod.requests.post = MagicMock(return_value=mock_resp)
        mod.post_mr_comment("Some comment")
        mock_resp.raise_for_status.assert_called_once()

    def test_retry_on_failure(self):
        import requests.exceptions as exc
        mod = _bootstrap_module()
        call_count = [0]

        def fail_then_succeed(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] <= 2:
                resp.raise_for_status = MagicMock(side_effect=exc.RequestException("fail"))
            else:
                resp.raise_for_status = MagicMock()
            return resp

        mod.requests.post = MagicMock(side_effect=fail_then_succeed)
        # tenacity: 1st call + 2 retries = 3 total attempts (only retries on RequestException)
        mod.post_mr_comment("retry test")
        assert call_count[0] == 3
