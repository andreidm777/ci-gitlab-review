import os
import sys
import json
import logging
import subprocess
import requests
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ai-review")

# --- Settings ---
REQUIRED_ENV = ["LLM_API_KEY", "GITLAB_API_TOKEN", "CI_PROJECT_ID", "CI_MERGE_REQUEST_IID", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "CI_API_V4_URL"]
for env_key in REQUIRED_ENV:
    if not os.getenv(env_key):
        logger.error("Required environment variable %s is not set", env_key)
        sys.exit(1)

OPENAI_API_KEY = os.getenv("LLM_API_KEY")
GITLAB_TOKEN = os.getenv("GITLAB_API_TOKEN")
PROJECT_ID = os.getenv("CI_PROJECT_ID")
MR_IID = os.getenv("CI_MERGE_REQUEST_IID")
TARGET_BRANCH = os.getenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME")
GITLAB_API_URL = os.getenv("CI_API_V4_URL")

CONTEXT_MODEL = os.getenv("CONTEXT_MODEL", "gpt-4o-mini")
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "gpt-4o")
REVIEW_LANG = os.getenv("REVIEW_LANG", "Russian")  # default to Russian
MAX_DIFF_SNIPPET = 4000   # chars for context-detection step
MAX_REVIEW_DIFF = 80000   # chars for final review step (rough token guard)
LLM_TIMEOUT = 60          # seconds
GITLAB_TIMEOUT = 15       # seconds
MAX_FILE_LINES = 2000     # per-file read limit

client = OpenAI(api_key=OPENAI_API_KEY)


def get_git_diff():
    """Get diff between target branch and current HEAD using three-dot notation."""
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
    logger.info("Repository root: %s", repo_root)

    context = {}
    for path in file_paths:
        clean_path = path.strip().strip('"').strip("'")
        real_path = os.path.realpath(clean_path)
        if not real_path.startswith(repo_root):
            logger.warning("Skipping file outside repo: %s", clean_path)
            continue
        if not os.path.exists(clean_path):
            logger.warning("File not found: %s", clean_path)
            continue
        try:
            with open(clean_path, "r", encoding="utf-8") as f:
                content = "".join(f.readlines()[:MAX_FILE_LINES])
                context[clean_path] = content
                logger.info("Read %s (%d lines)", clean_path, content.count("\n") + 1)
        except Exception as e:
            logger.error("Cannot read %s: %s", clean_path, e)
    return context


def ask_llm_for_context(diff):
    """Ask LLM which files are needed for context."""
    if not diff.strip():
        return []

    snippet = diff[:MAX_DIFF_SNIPPET]
    prompt = f"""
You are a senior developer. You received a diff from a Merge Request.
Analyze the changes. If you lack context (e.g., a changed function's signature
is in another file, or you want to see how this code is used), return a JSON
array of file paths you need to read.
If no extra context is needed, return an empty array [].

Diff:
{snippet}
"""

    response = client.chat.completions.create(
        model=CONTEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        timeout=LLM_TIMEOUT,
    )

    try:
        result = json.loads(response.choices[0].message.content)
        if not isinstance(result, dict):
            logger.warning("LLM context response is not a dict, returning []")
            return []
        files = result.get("files", [])
        if not isinstance(files, list):
            logger.warning("LLM context 'files' is not a list, returning []")
            return []
        return files
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.warning("Failed to parse LLM context response: %s", exc)
        return []


def _build_review_prompt(diff, context_str, language):
    """Build the review prompt with language constraint, truncating diff if needed."""
    context_part = ""
    if context_str:
        context_part = f"For context, here are related files:\n{context_str}\n\n"

    full_diff_section = f"{context_part}Diff:\n{diff}"
    if len(full_diff_section) > MAX_REVIEW_DIFF:
        available = MAX_REVIEW_DIFF - len(context_part) - len("Diff:\n")
        if available > 0:
            diff = diff[:available]
        logger.warning("Diff truncated to fit prompt limit (%d chars)", len(diff))

    prompt = f"""You are a strict and careful Code Reviewer.
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
    return prompt


def review_code_with_context(diff, context_files_content, language=REVIEW_LANG):
    """Final code review with optional context, response in the specified language."""
    context_str = "\n\n".join(
        f"--- File: {k} ---\n{v}" for k, v in context_files_content.items()
    )
    prompt = _build_review_prompt(diff, context_str, language)

    response = client.chat.completions.create(
        model=REVIEW_MODEL,
        messages=[{"role": "user", "content": prompt}],
        timeout=LLM_TIMEOUT,
    )
    return response.choices[0].message.content


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type((requests.exceptions.RequestException,)),
)
def post_mr_comment(comment_body):
    """Post a comment to the Merge Request via GitLab API (with retry)."""
    url = f"{GITLAB_API_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    data = {"body": f"🤖 **AI Code Review Agent**\n\n{comment_body}"}

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
    review_result = review_code_with_context(diff, context_data)

    # 5. Post result
    post_mr_comment(review_result)
    logger.info("Done!")
