# Telegram AI Thread Bot

Production-ready Python Telegram assistant for multi-topic groups. It stores
visible messages in PostgreSQL and serves `/ai` plus `/tldr` through a Telegram
user account using Telethon.

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

## Run It

```bash
# in .env
# TELEGRAM_API_ID=...
# TELEGRAM_API_HASH=...
# TELEGRAM_USER_PHONE=...
# TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890

docker compose run --rm telegram-auth
docker compose up -d --build
docker compose logs -f bot
```

The service refuses to start with an empty allowlist unless
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

# Set API credentials, phone, allowlisted chats, and OpenRouter key.
docker compose build
docker compose run --rm telegram-auth
docker compose up -d
```

## Commands

- `/ai <question>` answers using current thread context plus relevant cross-thread context.
- `/tldr [options]` summarizes recent activity in the same thread or across the chat.
- `/whitelist` is admin-only and must be used as a reply to the target user's message.
- `/confirm_whitelist <user_id>` is admin-only and completes the whitelist write.

## Reactions Feature

The bot can probabilistically reply to messages that the chat finds noteworthy.
When several distinct users react to the same message, the bot rolls a dice and
may post a short LLM-generated reply.

### How it works

1. Telegram delivers an `UpdateMessageReactions` raw update to the user-API
   client whenever a reaction count changes on a message in an allowlisted
   chat.
2. The client fetches the current reactor list via
   `messages.GetMessageReactionsListRequest`. With trigger emojis configured,
   it queries each emoji separately and merges per-user emoji sets; with no
   filters, a single unfiltered request returns all reactors.
3. The reaction snapshot replaces the stored rows in
   `telegram_message_reactions` for that message — the user-API path always
   stores a full snapshot rather than a per-user diff.
4. The service counts distinct human users (bots, anonymous and channel
   reactions are excluded) restricted to `trigger_emojis` if configured.
5. State is persisted in `telegram_reaction_states` so:
   - the same count seen twice does not re-roll the dice;
   - a message that already received a reply respects `cooldown_seconds`
     across container restarts.
6. If the count crossed the threshold and persistent cooldown is clear, the
   bot rolls `reply_chance`. On a win it pulls `context_before`/`context_after`
   neighbouring messages, calls the LLM, replies to the reacted message, and
   optionally puts its own `bot_emoji` reaction.

### Configuration

Edit `config/reactions.yaml` (hot-reloads on file change):

```yaml
reactions:
  enabled: true
  min_distinct_users: 3       # threshold of distinct human reactors
  reply_chance: 0.3           # probability in [0, 1] to actually reply
  context_before: 5           # neighbouring messages before the target
  context_after: 3            # neighbouring messages after the target
  cooldown_seconds: 600       # per-message cooldown for replies
  bot_emoji: "🔥"             # emoji the bot adds with its reply ("" disables)
  trigger_emojis:             # only count these emojis (empty = any)
    - "🔥"
    - "👍"
    - "😂"
  user_api:
    fetch_limit_per_emoji: 200   # MTProto reactor fetch limit per emoji
    ignore_custom_reactions: true
```

Setting `enabled: false` disables both the snapshot fetch and the reply path.
Setting `reply_chance: 0.0` keeps reaction rows in the DB but never replies;
`reply_chance: 1.0` always replies once eligible.

### Safety and scope

- Reactions are only processed for chats in `TELEGRAM_ALLOWED_CHAT_IDS`.
- Bot users and anonymous/channel reactions never count toward the threshold.
- Custom emoji reactions and paid reactions are ignored for the MVP.
- The bot never replies twice to the same message within `cooldown_seconds`.
- Restarts do not re-trigger replies on previously evaluated messages.

### Observability

Notable structured log events:

- `reactions.user_raw_update_received` — raw reaction update accepted.
- `reactions.user_snapshot_fetched` / `reactions.user_snapshot_empty` /
  `reactions.user_snapshot_fetch_failed` — snapshot fetch outcomes.
- `reactions.threshold_not_met` / `reactions.threshold_met` — gate decisions.
- `reactions.dice_lost` — threshold crossed but the dice roll failed.
- `reactions.persistent_cooldown_active` — a recent reply is still cooling
  down for this message.
- `reactions.reply_sent` — the bot replied to the message.

Prompts are only logged when `LOG_PROMPTS=true`.

## Manual Smoke Checklist

- Create a Telethon session successfully.
- Start the service with the Telethon runtime.
- Send a normal message in an allowlisted group and verify it is ingested.
- Send `/ai <question>` as an allowed user and verify the reply stays in the same chat/topic.
- Send `/tldr` as an allowed user and verify the reply stays in the same chat/topic.
- Send `/ai` as a non-whitelisted user and verify access is denied before any LLM call.
- Add a user through `/whitelist` plus `/confirm_whitelist <user_id>`.
- Restart the container and verify no new login code is required.
- Set `reactions.enabled: true` with `reply_chance: 1.0` and have
  `min_distinct_users` different users react to a message; verify the bot
  replies once, that rows appear in `telegram_message_reactions`, and that
  additional reactions to the same message do not trigger another reply
  before `cooldown_seconds` elapses.
