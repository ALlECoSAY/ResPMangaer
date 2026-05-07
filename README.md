# Telegram AI Thread Bot

Production-ready Python Telegram assistant for multi-topic groups. It stores
visible messages in PostgreSQL and serves `/ai` plus `/tldr` through either the
Telegram Bot API (`aiogram`) or the Telegram User API (`Telethon`).

See [docs/USER_API_MIGRATION_PLAN.md](docs/USER_API_MIGRATION_PLAN.md) for the
migration checklist and `PROGRESS.md` for current implementation status.

## Setup

```bash
cp .env.example .env
mkdir -p config
cp config/admins.yaml.example config/admins.yaml
cp config/whitelist.yaml.example config/whitelist.yaml
cp config/context_limits.yaml.example config/context_limits.yaml
cp config/reactions.yaml.example config/reactions.yaml
```

Bot behavior such as reply size, context budget, `/ai` message caps, and `/tldr`
lookback/gap limits lives in `config/context_limits.yaml`. Keep `.env` for
secrets, connection strings, feature flags, and YAML file paths.

## Bot API Mode

```bash
# in .env
# TELEGRAM_MODE=bot
# TELEGRAM_BOT_TOKEN=...
docker compose up -d --build
docker compose logs -f bot
```

## User API Mode

```bash
# in .env
# TELEGRAM_MODE=user
# TELEGRAM_API_ID=...
# TELEGRAM_API_HASH=...
# TELEGRAM_USER_PHONE=...
# TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890

docker compose run --rm telegram-auth
docker compose up -d --build
docker compose logs -f bot
```

User mode refuses to start with an empty allowlist unless
`ALLOW_UNSAFE_ALL_CHATS=true`.

The login flow stores the Telethon session at `TELEGRAM_USER_SESSION_PATH`
inside `./config`, so it survives container rebuilds and restarts.

If you want a fully non-interactive one-off login, you can also prefill:

```bash
TELEGRAM_LOGIN_CODE=12345 docker compose run --rm telegram-auth
```

If Telegram asks for 2FA and `TELEGRAM_USER_2FA_PASSWORD` is set in `.env`, the
bootstrap command will use it automatically; otherwise it will prompt for it.

Recommended deployment flow:

```bash
cp .env.example .env
mkdir -p config
cp config/admins.yaml.example config/admins.yaml
cp config/whitelist.yaml.example config/whitelist.yaml
cp config/context_limits.yaml.example config/context_limits.yaml
cp config/reactions.yaml.example config/reactions.yaml

# Set TELEGRAM_MODE=user, API credentials, phone, allowlisted chats, and OpenRouter key.
docker compose build
docker compose run --rm telegram-auth
docker compose up -d
```

## Commands

- `/ai <question>` answers using current thread context plus relevant cross-thread context.
- `/tldr [options]` summarizes recent activity in the same thread or across the chat.
- `/whitelist` is admin-only and must be used as a reply to the target user's message.
- `/confirm_whitelist <user_id>` is admin-only and completes the whitelist write.

## Manual Smoke Checklist

- Create a Telethon session successfully.
- Start the service with `TELEGRAM_MODE=user`.
- Send a normal message in an allowlisted group and verify it is ingested.
- Send `/ai <question>` as an allowed user and verify the reply stays in the same chat/topic.
- Send `/tldr` as an allowed user and verify the reply stays in the same chat/topic.
- Send `/ai` as a non-whitelisted user and verify access is denied before any LLM call.
- Add a user through `/whitelist` plus `/confirm_whitelist <user_id>`.
- Restart the container and verify no new login code is required.
