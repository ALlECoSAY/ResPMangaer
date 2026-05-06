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
# edit .env and the YAML files (add your own Telegram user ID to admins)
docker compose up -d --build
docker compose logs -f bot
```

## Commands

- `/ai <question>` — answer using current thread context plus relevant cross-thread context.
- `/tldr [options]` — summarize recent activity from other threads.
- `/whitelist` — admin-only. Reply to a user's message with `/whitelist`; the bot
  asks "Точно?" and the admin confirms with the 🟢 Да / 🔴 Нет buttons.
