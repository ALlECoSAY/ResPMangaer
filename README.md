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
cp config/activity.yaml.example config/activity.yaml
cp config/stats.yaml.example config/stats.yaml
cp config/auto_delete.yaml.example config/auto_delete.yaml
cp config/prompts.yaml.example config/prompts.yaml
cp config/identity.yaml.example config/identity.yaml
```

Bot behavior such as reply size, context budget, `/ai` message caps, and `/tldr`
lookback/gap limits lives in `config/context_limits.yaml`. Keep `.env` for
secrets, connection strings, feature flags, and YAML file paths. Chat statistics
settings live in `config/stats.yaml`. Random active-chat replies live in
`config/activity.yaml`. Per-command auto-deletion of bot responses lives in
`config/auto_delete.yaml`.

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

The app container runs `alembic upgrade head` before startup, so new database
tables are applied automatically when the service is recreated.

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
cp config/activity.yaml.example config/activity.yaml
cp config/stats.yaml.example config/stats.yaml
cp config/auto_delete.yaml.example config/auto_delete.yaml

# Set API credentials, phone, allowlisted chats, and OpenRouter key.
docker compose build
docker compose run --rm telegram-auth
docker compose up -d
```

## Commands

- `/ai <question>` answers using current thread context plus relevant cross-thread context.
- `/tldr [options]` summarizes recent activity in the same thread or across the chat.
- `/stats [subcommand] [days|12h|2d]` reports chat statistics for a recent window.
- `/help` lists available commands.
- `/whitelist` is admin-only and must be used as a reply to the target user's message.
- `/confirm_whitelist <user_id>` is admin-only and completes the whitelist write.

### Stats Commands

Stats use the messages, commands, reactions, threads, and LLM interaction rows
already stored in PostgreSQL. They do not call the LLM.

- `/stats` shows a compact summary across users, words, media, time, reactions, commands, and LLM usage.
- `/stats users [days]` ranks top chatters and the quiet corner.
- `/stats words [days]` shows common words, message emojis, and shared domains.
- `/stats times [days]` prints hourly and weekday activity bars.
- `/stats threads [days]` ranks active topics and thread starters.
- `/stats reactions [days]` shows reaction emoji counts and reaction magnets.
- `/stats fun [days]` gives playful awards such as Chatty McChatface and Buzzword badge.

Edit `config/stats.yaml` (hot-reloads on file change):

```yaml
stats:
  enabled: true
  default_lookback_days: 7
  top_n_users: 10
  top_n_words: 20
  top_n_threads: 5
  max_message_chars: 3900
  report_schedule: null
  render_as_images: true
```

When `render_as_images` is `true`, `/stats` replies as a PNG chart with a short
text caption (the visible summary lines). The detailed breakdown is sent as a
follow-up text message inside a collapsed Telegram blockquote. Setting
`render_as_images` to `false` falls back to the original ASCII bar chart
rendering — useful if matplotlib is unavailable or undesirable in the runtime.

The `report_schedule` field is reserved for automatic weekly/monthly reports;
manual `/stats` commands are available in both user-API and allowlisted chat
deployments.

### Auto-deleting bot responses

The bot can delete its own responses for chosen commands after a configurable
delay so that helper output (such as `/stats` charts and `/help` listings) does
not clutter the chat. Edit `config/auto_delete.yaml` (hot-reloads on file
change):

```yaml
auto_delete:
  stats: 300   # delete /stats responses (image + detail message) after 5 minutes
  help: 300    # delete /help response after 5 minutes
  # tldr: 1800 # opt in by adding a key here; omit a key to disable for that command
```

Delays are in seconds. Setting a value to `0` disables auto-deletion for that
command. Commands not listed in the file are never auto-deleted. The default
configuration deletes `/stats` and `/help` after 5 minutes and leaves `/tldr`
output in place.

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

## Activity Responder

The bot can occasionally join lively conversations without a command. A
background poller counts recent non-command, non-bot messages per chat/thread;
when a thread crosses the configured activity threshold, the bot rolls a dice,
selects a recent message, and replies with a short LLM-generated comment.

It also tracks the last activity reply in `telegram_activity_reply_states`. If
someone replies directly to that bot message, the bot can answer with a
separate probability. If someone continues in the same thread shortly after the
bot reply without using Telegram's reply UI, the bot can treat that as a
follow-up and decide whether to answer.

Edit `config/activity.yaml` (hot-reloads on file change):

```yaml
activity_responder:
  enabled: true
  min_messages: 20              # messages needed in the activity window
  window_minutes: 30            # threshold window for spontaneous replies
  max_context_messages: 40      # recent messages sent to the LLM
  reply_chance: 0.3             # random reply probability once eligible
  reply_on_direct_reply_chance: 1.0
  reply_on_follow_up_chance: 0.5
  cooldown_seconds: 900         # per chat/thread spontaneous cooldown
  follow_up_window_seconds: 300 # non-reply follow-up detection window
  allowed_hours: []             # empty = allow all hours
  user_api:
    poll_enabled: true
    poll_interval_seconds: 60
    poll_window_minutes: 30
    poll_max_threads_per_tick: 20
```

Set `enabled: false` to disable the feature. Set `reply_chance: 0.0` to observe
activity without spontaneous replies. `allowed_hours` uses the runtime/server
hour and only gates spontaneous replies; direct replies to the bot can still be
handled through their own chance setting.

## Long-Term Memory

The bot keeps compact memory per chat, shared across all forum topics in that
chat. A background poller checks stored non-command, non-bot messages and
refreshes memory when a chat has enough new messages, a configured keyword is
seen, a popular reaction threshold is reached, or existing memory becomes stale.

Edit `config/memory.yaml` (hot-reloads on file change):

```yaml
memory:
  enabled: true
  user_profiles_enabled: true
  update_min_new_messages: 30
  update_min_interval_minutes: 360
  trigger_keywords: [decided, todo, deadline, bug, important, решили, дедлайн, баг, важно]
  user_api:
    poll_enabled: true
    poll_interval_seconds: 300
    poll_max_chats_per_tick: 5
```

Use `/memory_refresh` to force a rebuild for the current chat and `/memory` to
inspect what is stored.

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
