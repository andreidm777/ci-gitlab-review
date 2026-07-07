## GitLab CI configuration

```yaml
stages:
  - review

ai_code_review:
  stage: review
  image: python:3.11-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_DEPTH: 0
  before_script:
    - pip install openai requests tenacity
  script:
    - python ai_review_agent.py
  allow_failure: true
```

## Required environment variables

| Variable | Description |
|---|---|
| `LLM_API_KEY` | OpenAI API key |
| `GITLAB_API_TOKEN` | GitLab personal/access token with `api` scope |
| `CI_PROJECT_ID` | Auto-provided by GitLab CI |
| `CI_MERGE_REQUEST_IID` | Auto-provided by GitLab CI |
| `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | Auto-provided by GitLab CI |
| `CI_API_V4_URL` | Auto-provided by GitLab CI |

Optional:

| Variable | Default | Description |
|---|---|---|
| `CONTEXT_MODEL` | `gpt-4o-mini` | Model used to decide which files need context |
| `REVIEW_MODEL` | `gpt-4o` | Model used for the actual code review |
| `REVIEW_LANG` | `Russian` | Language of the review response (e.g. `Russian`, `English`, `Chinese`) |

## Local testing

```bash
# Set required env vars
export LLM_API_KEY="sk-..."
export GITLAB_API_TOKEN="glpat-..."

# Run the agent
python ai_review_agent.py

# Run tests
pip install pytest
pytest test_ai_review_agent.py -v
```
