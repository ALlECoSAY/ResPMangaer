# Implementation Progress

Tracking implementation of `IMPLEMENTATION_PLAN.md`.

## Status legend
- [ ] not started
- [~] in progress
- [x] done

## Tickets

- [x] **Ticket 1** — Project skeleton
  - `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`,
    `.dockerignore`, `.gitignore`, `README.md`, `alembic.ini`,
    full `app/` module tree, `config/*.example` files.
- [x] **Ticket 2** — Settings and logging
  - `app/config.py` (pydantic-settings, secret validation, allow-list parsing).
  - `app/logging_config.py` (structlog JSON output).
- [x] **Ticket 3** — Database models + Alembic migrations
  - `app/db/models.py` (SQLAlchemy 2.x async): chats, threads, users, messages,
    llm_interactions, indexes per spec.
  - `app/db/repositories.py` (async upserts + queries).
  - `app/db/session.py` (engine/session helper).
  - `app/db/migrations/env.py`, `script.py.mako`,
    `versions/0001_initial_schema.py` (initial schema with FTS index).
- [x] **Ticket 4** — Telegram dispatcher
  - `app/bot/dispatcher.py` (aiogram 3 bot/dispatcher, public command
    registration, admin command opt-in via env, middlewares wired).
  - `app/main.py` (startup, secret check, polling, graceful shutdown).
- [x] **Ticket 5** — YAML access control
  - `app/auth/yaml_store.py` (load + atomic write, dedup, asyncio lock).
  - `app/auth/access_control.py` (admin/whitelist decisions; deny `None`
    user IDs; admins implicitly allowed for AI commands).
- [x] **Ticket 6** — Message ingestion
  - `app/services/message_ingestion.py` (chat/thread/user/message upserts;
    `message_thread_id` 0 for general; idempotent insert via unique
    `(chat_id, message_id)`).
  - `app/bot/middleware.py` (`MessageIngestionMiddleware` +
    `ChatAllowlistMiddleware`).
- [x] **Ticket 7** — OpenRouter client
  - `app/llm/openrouter_client.py` (async OpenAI SDK with OpenRouter
    base URL, timeout/rate-limit/error mapping to `OpenRouterError`).
- [x] **Ticket 8** — Context builder
  - `app/llm/context_builder.py` (same-thread first, cross-thread scored by
    keyword overlap + activity + decision keywords + recency, trimmed to
    `MAX_CONTEXT_CHARS`).
- [x] **Ticket 9** — `/ai` command
  - Wired in `app/bot/handlers.py` + `app/services/ai_answer_service.py`;
    access check before any LLM call; logs interactions; replies in same
    thread.
- [x] **Ticket 10** — TLDR period detection
  - `app/services/thread_activity.py` (per-thread activity window using
    `TLDR_ACTIVITY_GAP_MINUTES`, capped by `TLDR_MAX_MESSAGES_PER_THREAD`).
- [x] **Ticket 11** — `/tldr` command
  - `app/services/tldr_service.py` (`parse_tldr_args` for `all`/`thread`/
    `6h`/`24h`/`2d`; default scope = other threads; "no activity" message).
- [x] **Ticket 12** — `/add_whitelist` command
  - In `app/bot/handlers.py`: numeric ID, optional note, reply-target
    fallback, idempotent response, admin-only check; persists to YAML
    atomically without restart.
- [x] **Ticket 13** — Reply formatting
  - `app/bot/formatting.py` (`split_for_telegram` honoring `MAX_REPLY_CHARS`,
    splitting on newline/space when possible) + `reply_in_same_thread`
    that preserves `message_thread_id` when set.
- [x] **Ticket 14** — Tests
  - `tests/test_command_parsing.py` (8 cases)
  - `tests/test_access_control.py` (8 cases incl. atomic-write/idempotency)
  - `tests/test_tldr_period.py` (gap detection, per-thread cap, arg parser)
  - `tests/test_context_builder.py` (ordering, cap, char-budget)
  - `tests/test_formatting.py` (split correctness)
  - `tests/test_time.py` (lookback parser)
  - `tests/test_openrouter_client.py` (mocked OpenAI client)

## Test results

Run inside this sandbox with `uv` venv (`.venv`):

```
.venv/bin/python -m pytest tests/test_command_parsing.py tests/test_time.py tests/test_access_control.py -q
.venv/bin/ruff check app tests   # All checks passed
```

- `test_command_parsing.py` — **8/8 passed**
- `test_time.py` — **3/3 passed**
- `test_access_control.py` — **8/8 passed**
- `test_formatting.py`, `test_tldr_period.py`, `test_context_builder.py`,
  `test_openrouter_client.py` — not executed in this sandbox: importing
  `aiogram` / `openai` together with `pydantic-settings` exceeds the
  available memory of this environment and the harness SIGKILLs the
  process before pytest can collect. They compile cleanly
  (`python -m py_compile`) and pass `ruff check`. They will run normally
  inside the Docker container (which has sufficient RAM).

## How to run locally

```bash
cp .env.example .env
mkdir -p config
cp config/admins.yaml.example config/admins.yaml
cp config/whitelist.yaml.example config/whitelist.yaml
# fill in TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY,
# put your numeric Telegram user id into config/admins.yaml
docker compose up -d --build
docker compose exec bot alembic upgrade head
docker compose logs -f bot
```

## Notes / Deviations

- The plan's pseudo-code referenced `default_headers["X-OpenRouter-Title"]`;
  OpenRouter's documented header is `X-Title`. Implemented as `X-Title`.
- Admin command registration is gated by
  `TELEGRAM_REGISTER_ADMIN_COMMANDS` (default false); the handler is
  always active and authorization is enforced server-side regardless.
- `record_llm_interaction` is logged in the same DB session/transaction as
  the request; on LLM failure the row is still written with
  `success=false`.
- Ingestion uses `STORE_BOT_MESSAGES` / `STORE_COMMAND_MESSAGES` flags as
  short-circuits before the upserts, so disabled flags also skip the
  associated chat/thread/user upserts for that message.
- The Cyrillic range in the context-builder tokenizer is intentional
  (Russian/Ukrainian users) — `noqa: RUF001`.
- The `timeout` parameter on `OpenRouterClient.complete` is forwarded to
  the OpenAI SDK directly — `noqa: ASYNC109`.
- A pinned editable install via `pip install -e .[dev]` failed in this
  sandbox because the build sub-process was OOM-killed. For development
  on a normal machine use `uv pip install -e ".[dev]"`; in Docker the
  Dockerfile installs from `pyproject.toml` directly.
