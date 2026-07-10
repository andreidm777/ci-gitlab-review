# AI Code Review Agent

> **Languages:** [English](#getting-started-en) | [Русский](#%D0%B1%D1%8B%D1%81%D1%82%D1%80%D1%8B%D0%B9-%D1%81%D1%82%D0%B0%D1%80%D1%82-%D1%80%D1%83)

GitLab CI integration for automated Merge Request reviews using LLM. The agent analyzes diffs, loads context files when needed, and posts review comments in the specified language.

GitLab CI интеграция для автоматического ревью Merge Request с помощью LLM. Агент анализирует diff, при необходимости загружает контекстные файлы и оставляет комментарий в MR на заданном языке.

---

## Getting Started (EN)

### 1. Copy the file to your project

```bash
cp /path/to/ai_review_agent.py your-project/
cp /path/to/.gitignore your-project/   # optional
```

### 2. Add GitLab CI configuration

Add to your `.gitlab-ci.yml` (or a separate pipeline file):

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

### 3. Configure GitLab Environment Variables

Go to **Settings → CI/CD → Variables** for your project:

| Variable | Type | Description |
|---|---|---|
| `LLM_API_KEY` | Variable (masked, protected) | OpenAI API key (`sk-...`) |
| `GITLAB_TOKEN` | Variable (masked, protected) | GitLab Personal Access Token with `api` scope (`glpat-...`) |

**Auto-provided by GitLab CI** (no action needed):
- `CI_PROJECT_ID` — Project ID
- `CI_MERGE_REQUEST_IID` — MR IID (only in `merge_request_event`)
- `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` — Target branch
- `CI_API_V4_URL` — GitLab API URL

**Optional** (for fine-tuning):

| Variable | Default | Description |
|---|---|---|
| `CONTEXT_MODEL` | `gpt-4o-mini` | Model for determining which files need context |
| `REVIEW_MODEL` | `gpt-4o` | Model for the final code review |
| `REVIEW_LANG` | `Russian` | Response language (e.g. `Russian`, `English`, `Chinese`) |
| `OPENAI_BASE_URL` | — | Custom OpenAI-compatible endpoint (proxy, Ollama, LiteLLM) |

### 4. Commit and push

```bash
git add ai_review_agent.py .gitlab-ci.yml
git commit -m "feat: add AI code review agent"
git push
```

The next MR will automatically trigger the review and post a comment from 🤖 AI Code Review Agent.

---

## Required Environment Variables (EN)

| Variable | Description |
|---|---|
| `LLM_API_KEY` | OpenAI API key (`sk-...`) |
| `GITLAB_TOKEN` | GitLab Personal Access Token with `api` scope (`glpat-...`) |
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
| `OPENAI_BASE_URL` | — | Custom OpenAI-compatible endpoint (proxy, Ollama, LiteLLM, etc.) |

---

## How It Works (EN)

1. **Get diff** — `git diff` between target branch and MR HEAD
2. **Context analysis** — LLM (`CONTEXT_MODEL`) determines which files to read for a full review
3. **Read files** — Local file reading from the repository (with path traversal protection)
4. **Final review** — LLM (`REVIEW_MODEL`) generates the review in the specified language
5. **Post to MR** — If the bot's comment exists, it's updated; otherwise, a new one is created

---

## Local Testing (EN)

```bash
# Set required env vars
export LLM_API_KEY="sk-..."
export GITLAB_TOKEN="glpat-..."
export CI_PROJECT_ID="123"
export CI_MERGE_REQUEST_IID="42"
export CI_MERGE_REQUEST_TARGET_BRANCH_NAME="main"
export CI_API_V4_URL="https://gitlab.example.com/api/v4"

# Run the agent
python ai_review_agent.py

# Run tests
pip install pytest
pytest test_ai_review_agent.py -v
```

---

## Security (EN)

- File reading is restricted to the repository root (path traversal protection)
- Secrets (`LLM_API_KEY`, `GITLAB_TOKEN`) are stored in GitLab CI/CD Variables (masked + protected)
- `allow_failure: true` — review failure does not block the pipeline

---

<div align="right">
<a href="#ai-code-review-agent">↑ Back to top</a> &nbsp;|&nbsp; <a href="#%D0%B1%D1%8B%D1%81%D1%82%D1%80%D1%8B%D0%B9-%D1%81%D1%82%D0%B0%D1%80%D1%82-%D1%80%D1%83">Русская версия ↓</a>
</div>

---

## Быстрый старт (РУ)

### 1. Скопируйте файл в проект

```bash
cp /path/to/ai_review_agent.py your-project/
cp /path/to/.gitignore your-project/   # опционально
```

### 2. Добавьте GitLab CI конфигурацию

Вставьте в `.gitlab-ci.yml` (или в отдельный файл пайплайна):

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

### 3. Настройте переменные окружения в GitLab

Перейдите в **Settings → CI/CD → Variables** для вашего проекта:

| Переменная | Тип | Описание |
|---|---|---|
| `LLM_API_KEY` | Variable (masked, protected) | Ключ OpenAI API (`sk-...`) |
| `GITLAB_TOKEN` | Variable (masked, protected) | GitLab Personal Access Token с scope `api` (`glpat-...`) |

**Не обязательные** (CI предоставляет автоматически):
- `CI_PROJECT_ID` — ID проекта (авто)
- `CI_MERGE_REQUEST_IID` — IID MR (авто, только в `merge_request_event`)
- `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` — целевая ветка (авто)
- `CI_API_V4_URL` — URL GitLab API (авто)

**Опциональные** (для тонкой настройки):

| Переменная | Значение по умолчанию | Описание |
|---|---|---|
| `CONTEXT_MODEL` | `gpt-4o-mini` | Модель для определения нужных файлов контекста |
| `REVIEW_MODEL` | `gpt-4o` | Модель для финального ревью |
| `REVIEW_LANG` | `Russian` | Язык ответа (например `Russian`, `English`, `Chinese`) |
| `OPENAI_BASE_URL` | — | Прокси/компьютерный провайдер (например, LiteLLM, Ollama) |

### 4. Коммит и push

```bash
git add ai_review_agent.py .gitlab-ci.yml
git commit -m "feat: add AI code review agent"
git push
```

При следующем MR пайплайн автоматически запустит ревью и оставит комментарий от 🤖 AI Code Review Agent.

---

## Обязательные переменные окружения (РУ)

| Переменная | Описание |
|---|---|
| `LLM_API_KEY` | Ключ OpenAI API (`sk-...`) |
| `GITLAB_TOKEN` | GitLab Personal Access Token с scope `api` (`glpat-...`) |
| `CI_PROJECT_ID` | Автоматически предоставляется GitLab CI |
| `CI_MERGE_REQUEST_IID` | Автоматически предоставляется GitLab CI |
| `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | Автоматически предоставляется GitLab CI |
| `CI_API_V4_URL` | Автоматически предоставляется GitLab CI |

Опциональные:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `CONTEXT_MODEL` | `gpt-4o-mini` | Модель для определения нужных файлов контекста |
| `REVIEW_MODEL` | `gpt-4o` | Модель для финального ревью |
| `REVIEW_LANG` | `Russian` | Язык ответа (например `Russian`, `English`, `Chinese`) |
| `OPENAI_BASE_URL` | — | Свой OpenAI-совместимый endpoint (прокси, Ollama, LiteLLM) |

---

## Как это работает (РУ)

1. **Получение diff** — `git diff` между целевой веткой и HEAD MR
2. **Анализ контекста** — LLM (`CONTEXT_MODEL`) определяет, какие файлы нужно прочитать для полноценного ревью
3. **Чтение файлов** — локальное чтение файлов из репозитория (с защитой от path traversal)
4. **Финальное ревью** — LLM (`REVIEW_MODEL`) формирует ревью на заданном языке
5. **Комментарий в MR** — если комментарий бота уже существует, он обновляется; иначе создаётся новый

---

## Локальное тестирование (РУ)

```bash
# Установить обязательные переменные
export LLM_API_KEY="sk-..."
export GITLAB_TOKEN="glpat-..."
export CI_PROJECT_ID="123"
export CI_MERGE_REQUEST_IID="42"
export CI_MERGE_REQUEST_TARGET_BRANCH_NAME="main"
export CI_API_V4_URL="https://gitlab.example.com/api/v4"

# Запустить агент
python ai_review_agent.py

# Запустить тесты
pip install pytest
pytest test_ai_review_agent.py -v
```

---

## Безопасность (РУ)

- Чтение файлов ограничено корнем репозитория (path traversal protection)
- Секреты (`LLM_API_KEY`, `GITLAB_TOKEN`) хранятся в GitLab CI/CD Variables (masked + protected)
- `allow_failure: true` — падение ревью не блокирует пайплайн
