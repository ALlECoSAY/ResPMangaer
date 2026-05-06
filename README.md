# Telegram AI Thread Bot

Production-ready Python Telegram bot for multi-topic groups. It registers every
visible message, then offers `/ai` (thread-aware Q&A) and `/tldr` (summary of
recent activity in other threads) backed by OpenRouter.

See `IMPLEMENTATION_PLAN.md` for full design and `PROGRESS.md` for status.

## Quickstart

```bash
cp .env.example .env
mkdir -p config
cp config/admins.yaml.example config/admins.yaml
cp config/whitelist.yaml.example config/whitelist.yaml
cp config/context_limits.yaml.example config/context_limits.yaml
cp config/reactions.yaml.example config/reactions.yaml
# edit .env for secrets/paths; edit YAML files for access control and bot behavior
docker compose up -d --build
docker compose logs -f bot
```

Bot behavior such as reply size, context budget, `/ai` message caps, and `/tldr`
lookback/gap limits lives in `config/context_limits.yaml`. Keep `.env` for
secrets, connection strings, feature flags, and YAML file paths.

## Commands

- `/ai <question>` — answer using current thread context plus relevant cross-thread context.
- `/tldr [options]` — summarize recent activity from other threads.
- `/whitelist` — admin-only. Reply to a user's message with `/whitelist`; the bot
  asks "Точно?" and the admin confirms with the 🟢 Да / 🔴 Нет buttons.
