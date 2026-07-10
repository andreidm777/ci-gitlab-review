import os
import re
import sys
import json
import logging
import subprocess
import requests
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# --- OpenAI exception types for retry ---
# Retry only on transient network/HTTP errors, not on bad prompts or parse failures
OPENAI_RETRY_EXCEPTIONS = (
    requests.exceptions.RequestException,
)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ai-review")

# --- Settings ---
REQUIRED_ENV = ["LLM_API_KEY", "GITLAB_TOKEN", "CI_PROJECT_ID", "CI_MERGE_REQUEST_IID", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "CI_API_V4_URL"]
for env_key in REQUIRED_ENV:
    if not os.getenv(env_key):
        logger.error("Required environment variable %s is not set", env_key)
        sys.exit(1)

OPENAI_API_KEY = os.getenv("LLM_API_KEY")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_ID = os.getenv("CI_PROJECT_ID")
MR_IID = os.getenv("CI_MERGE_REQUEST_IID")
TARGET_BRANCH = os.getenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME")
GITLAB_API_URL = os.getenv("CI_API_V4_URL")

CONTEXT_MODEL = os.getenv("CONTEXT_MODEL", "gpt-4o-mini")
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "gpt-4o")
REVIEW_LANG = os.getenv("REVIEW_LANG", "Russian")  # default to Russian
MAX_DIFF_SNIPPET = 80000   # chars for context-detection step
MAX_REVIEW_DIFF = 80000   # chars for final review step (rough token guard)
LLM_TIMEOUT = 60          # seconds
GITLAB_TIMEOUT = 15       # seconds
MAX_FILE_LINES = 20000     # per-file read limit
BOT_LABEL = "🤖 **AI Code Review Agent**"

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=os.getenv("OPENAI_BASE_URL"),
)


def get_git_diff():
    """Get diff between MR diff base and current HEAD."""
    diff_base = os.getenv("CI_MERGE_REQUEST_DIFF_BASE_SHA")
    if diff_base:
        cmd = ["git", "diff", f"{diff_base}..HEAD"]
    else:
        # Validate TARGET_BRANCH to prevent shell/git injection
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9._/-]*[a-zA-Z0-9])?$", TARGET_BRANCH):
            logger.error("Invalid TARGET_BRANCH: %s", TARGET_BRANCH)
            return ""
        logger.info("CI_MERGE_REQUEST_DIFF_BASE_SHA not set, falling back to origin/%s", TARGET_BRANCH)
        logger.info("Fetching origin/%s ...", TARGET_BRANCH)
        fetch = subprocess.run(
            ["git", "fetch", "origin", TARGET_BRANCH],
            capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            logger.error("git fetch failed: %s", fetch.stderr.strip())
            return ""
        cmd = ["git", "diff", f"origin/{TARGET_BRANCH}...HEAD"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("git diff failed: %s", result.stderr.strip())
        return ""
    return result.stdout


def read_local_files(file_paths):
    """Read requested files, restricted to the git repository root."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
    ).strip()
    repo_root_abs = os.path.abspath(repo_root)
    logger.info("Repository root: %s", repo_root_abs)

    context = {}
    for path in file_paths:
        clean_path = path.strip().strip('"').strip("'")
        real_path = os.path.realpath(clean_path)
        abs_path = os.path.abspath(clean_path)
        # Guard against path traversal: ensure the resolved path is inside the repo.
        # Using startswith with trailing separator to avoid false positives (e.g. /code_other/).
        if not (real_path.startswith(repo_root_abs + os.sep) or real_path == repo_root_abs):
            logger.warning("Skipping file outside repo (realpath): %s", clean_path)
            continue
        if not (abs_path.startswith(repo_root_abs + os.sep) or abs_path == repo_root_abs):
            logger.warning("Skipping file outside repo (abspath): %s", clean_path)
            continue
        if not os.path.exists(clean_path):
            logger.warning("File not found: %s", clean_path)
            continue
        try:
            with open(clean_path, "r", encoding="utf-8") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= MAX_FILE_LINES:
                        break
                    lines.append(line)
                context[clean_path] = "".join(lines)
                logger.info("Read %s (%d lines)", clean_path, len(lines))
        except UnicodeDecodeError:
            logger.warning("Skipping binary file: %s", clean_path)
        except Exception as e:
            logger.error("Cannot read %s: %s", clean_path, e)
    return context


def _strip_markdown_json(text):
    """Remove ```json / ``` code-fence wrappers from LLM output before json.loads."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(OPENAI_RETRY_EXCEPTIONS),
)
def ask_llm_for_context(diff):
    """Ask LLM which files are needed for context."""
    if not diff.strip():
        return []

    snippet = diff[:MAX_DIFF_SNIPPET]
    # trim diff to a safe line boundary so we don't cut mid-line
    if len(snippet) < MAX_DIFF_SNIPPET:
        pass
    else:
        snippet = snippet.rsplit("\n", 1)[0]

    response = client.chat.completions.create(
        model=CONTEXT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior developer helping with code review. "
                    "Return ONLY a JSON array of file paths that would be useful "
                    "to read for additional context. If none, return []. "
                    "Do not include any text outside the JSON."
                ),
            },
            {"role": "user", "content": f"Diff to analyze:\n{snippet}"},
        ],
        response_format={"type": "json_object"},
        timeout=LLM_TIMEOUT,
    )

    try:
        raw = response.choices[0].message.content
        result = json.loads(_strip_markdown_json(raw))
        if isinstance(result, list):
            return [f for f in result if isinstance(f, str)]
        if isinstance(result, dict):
            files = result.get("files", [])
            if isinstance(files, list):
                return [f for f in files if isinstance(f, str)]
            logger.warning("LLM context 'files' key is not a list (got %s), returning []. Raw: %s",
                           type(files).__name__, raw[:200])
            return []
        logger.warning("LLM context response is not a list or dict (got %s), returning []. Raw: %s",
                       type(result).__name__, raw[:200])
        return []
    except json.JSONDecodeError as exc:
        logger.warning("LLM context response is not valid JSON: %s. Raw: %s", exc, raw[:200] if raw else "None")
        return []
    except (KeyError, IndexError) as exc:
        logger.warning("Failed to parse LLM context response: %s", exc)
        return []


def _truncate_diff_by_line(diff, max_len):
    """Truncate *diff* so it fits in *max_len* chars, cutting at a newline boundary."""
    if len(diff) <= max_len:
        return diff
    diff = diff[:max_len]
    return diff.rsplit("\n", 1)[0]


def _build_review_prompt(diff, context_str, language):
    """Build the review prompt with language constraint, truncating diff by line."""
    context_part = ""
    if context_str:
        context_part = f"For context, here are related files:\n{context_str}\n\n"

    prompt_text = f"""You are a strict and careful Code Reviewer.
Review the following diff for bugs, logical errors, security issues, and architectural concerns.

IMPORTANT: Your entire response MUST be written in {language}.

{context_part if context_part else "No additional context was needed.\n"}
Diff:
{diff}

Response format:
1. Brief summary (what this MR does).
2. List of found issues (if any), referencing specific files and lines.
3. If no issues, write "LGTM" (Looks Good To Me).
"""
    if len(prompt_text) > MAX_REVIEW_DIFF + 500:
        available = MAX_REVIEW_DIFF - len(context_part) - len("Diff:\n") - 500
        if available > 0:
            diff = _truncate_diff_by_line(diff, available)
            logger.warning("Diff truncated to fit prompt limit (%d chars)", len(diff))
            # rebuild prompt with truncated diff
            prompt_text = f"""You are a strict and careful Code Reviewer.
Review the following diff for bugs, logical errors, security issues, and architectural concerns.

IMPORTANT: Your entire response MUST be written in {language}.

{context_part if context_part else "No additional context was needed.\n"}
Diff:
{diff}

Response format:
1. Brief summary (what this MR does).
2. List of found issues (if any), referencing specific files and lines.
3. If no issues, write "LGTM" (Looks Good To Me).
"""
    return prompt_text


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(OPENAI_RETRY_EXCEPTIONS),
)
def review_code_with_context(diff, context_files_content, language=REVIEW_LANG):
    """Final code review with optional context, response in the specified language."""
    context_str = "\n\n".join(
        f"--- File: {k} ---\n{v}" for k, v in context_files_content.items()
    )
    prompt = _build_review_prompt(diff, context_str, language)

    response = client.chat.completions.create(
        model=REVIEW_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a senior software engineer performing code reviews. "
                    f"Respond in {language}. Be concise and reference specific files and lines."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        timeout=LLM_TIMEOUT,
    )

    if not response.choices:
        raise RuntimeError("LLM returned an empty response (no choices)")

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("LLM returned a choice with None content")

    return content


def get_existing_bot_comment_id():
    """Find an existing comment from this bot in the MR, return its ID or None."""
    url = f"{GITLAB_API_URL.rstrip('/')}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    params = {"order_by": "created_at", "sort": "desc", "per_page": 20}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=GITLAB_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("Failed to fetch existing notes: %s", exc)
        return None

    for note in resp.json():
        body = note.get("body", "")
        if body.startswith(BOT_LABEL):
            return note["id"]
    return None


def update_note(note_id, comment_body):
    """Update an existing GitLab note by ID."""
    url = f"{GITLAB_API_URL.rstrip('/')}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes/{note_id}"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    data = {"body": f"{BOT_LABEL}\n\n{comment_body}"}
    try:
        resp = requests.put(url, headers=headers, json=data, timeout=GITLAB_TIMEOUT)
        resp.raise_for_status()
        logger.info("Updated existing bot comment #%s", note_id)
    except requests.exceptions.RequestException as exc:
        logger.warning("Failed to update bot comment #%s: %s", note_id, exc)
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type((requests.exceptions.RequestException,)),
)
def post_mr_comment(comment_body):
    """Post a comment to the Merge Request via GitLab API (with retry)."""
    url = f"{GITLAB_API_URL.rstrip('/')}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    data = {"body": f"{BOT_LABEL}\n\n{comment_body}"}

    response = requests.post(url, headers=headers, json=data, timeout=GITLAB_TIMEOUT)
    response.raise_for_status()
    logger.info("Comment posted to MR #%s", MR_IID)


if __name__ == "__main__":
    logger.info("Starting AI review agent...")

    # 1. Get diff
    diff = get_git_diff()
    if not diff:
        logger.info("No changes to review.")
        sys.exit(0)

    # 2. Ask LLM which files need context
    logger.info("Analyzing whether additional context is needed...")
    needed_files = ask_llm_for_context(diff)

    # 3. Read files locally
    context_data = {}
    if needed_files:
        logger.info("Reading context files: %s", needed_files)
        context_data = read_local_files(needed_files)

    # 4. Final review
    logger.info("Running final code review...")
    try:
        review_result = review_code_with_context(diff, context_data)
    except Exception as exc:
        logger.error("Code review failed: %s", exc)
        sys.exit(1)

    # 5. Update existing bot comment, or post new one
    old_note = get_existing_bot_comment_id()
    if old_note:
        try:
            update_note(old_note, review_result)
        except Exception as exc:
            logger.error("Failed to update MR comment: %s", exc)
            sys.exit(1)
    else:
        try:
            post_mr_comment(review_result)
        except Exception as exc:
            logger.error("Failed to post MR comment: %s", exc)
            sys.exit(1)

    logger.info("Done!")
