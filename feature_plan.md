# Analysis of the **ResPManager** repository and plan for the "random active‑chat replies" feature

## 1 Understanding the current codebase

**Purpose:** *ResPManager* is a production‑ready Telegram AI assistant.  It is written in Python and uses [Telethon](https://docs.telethon.dev/) to run a user‑account client that listens for messages in allow‑listed chats, stores them in PostgreSQL, calls large language models (LLMs) via OpenRouter, and replies through commands (`/ai`, `/tldr`, `/stats`, etc.).  The bot also has an existing **Reactions** feature that monitors emoji reactions to messages.  When several distinct users react to a message with certain emojis, the bot may reply with a short LLM‑generated comment 【704762143286879†L111-L160】.  Configurable parameters (min distinct users, reply probability, context windows, cooldowns, trigger emojis and polling settings) live in `config/reactions.yaml`【385848833926141†L0-L24】.  

### 1.1 Major components

| Component | Description | Relevant observations |
|---|---|---|
| `app/main.py` | Entry point for the **user API**.  Starts a Telethon client, ingests every new message into the DB, parses commands and dispatches handlers, starts the `ReactionPoller` and calls `ReactionService` on raw reaction updates【979001785294313†L120-L235】. | This file shows how new services can be wired into the runtime (see section 2.4). |
| `app/services/message_ingestion.py` | Stores incoming messages into the DB.  It inserts chat/thread/user rows, stores the message with metadata (`reply_to_message_id`, `message_thread_id` etc.) and logs the ingestion【628050918688308†L22-L112】. | The new feature can reuse this ingestion to track activity windows. |
| `app/services/reaction_service.py` | Persists reaction snapshots, counts distinct users, rolls a dice and calls the LLM for a short reply.  It builds prompts that include context around the target message and a summary of reactions; it then sends the reply via Telethon.  Cooldowns prevent multiple replies to the same message【10393012218268†L94-L114】【10393012218268†L200-L213】. | This service encapsulates reply probability, context building and the LLM call.  A similar design will be useful for the activity‑based feature. |
| `app/services/reaction_poller.py` | Periodically queries recently ingested messages and fetches reaction snapshots via Telethon to detect reaction changes【869415281329614†L81-L107】. | The activity‑based feature will need a poller that periodically inspects recent message counts rather than reaction snapshots. |
| `config/reactions.yaml.example` | Defines `enabled`, thresholds, reply probability, context size, cooldowns and poller settings【385848833926141†L0-L24】. | This YAML structure can be mirrored for the new feature. |

## 2 Summary of the requested feature

The user described a "random active‑chat replies" feature.  Their requirements can be summarized as:

1. **Trigger on chat activity:** When there is an active burst of messages (e.g., 20 messages in the last 30 minutes), the bot should occasionally jump into the conversation.  It should *not* trigger when the chat is quiet or at night when everyone is asleep.
2. **Contextual reply:** The bot should fetch a limited number of recent messages from the activity window (so as not to feed thousands of messages to the LLM) and produce a short, conversational reply in the style of the chat.  The reply should be attached to one of the recent messages that appears interesting or was active near the end of the window.
3. **Respond to replies:** If someone replies directly to the bot’s message, the bot should answer back with 100 % probability (configurable).  If someone writes after the bot’s message without using the reply UI but clearly continues the conversation, the bot should detect that and decide, with a configurable probability, whether to respond.
4. **Configuration:** The feature must be configurable.  Parameters such as minimum messages in the activity window, the size of the window, maximum context length, reply probabilities, and cooldowns should be adjustable via a YAML file.

## 3 High‑level design for the new feature

The existing **Reactions** feature provides a robust pattern: a configuration class loads YAML, a service class encapsulates logic for when to call the LLM and how to build prompts, and a poller periodically checks data and triggers the service.  A very similar architecture can be used for the new feature:

1. **Configuration (`config/activity.yaml`)** – defines parameters for the activity‑based responder.
2. **Runtime config class** – monitors the YAML file and hot‑reloads updated values.
3. **ActivityService** – decides whether to send a spontaneous reply based on recent message volume, builds a context prompt from recent messages, calls the LLM, and sends the reply.
4. **ActivityPoller** – periodically queries the message repository for recent activity; when the configured threshold is exceeded, it instructs the ActivityService to generate a reply.
5. **Reply‑follow‑ups detection** – integrated into the `on_new_message` handler.  If a new message is a reply to the bot’s previous activity‑based reply or obviously a follow‑up (same thread and close in time), the service will decide whether to respond again.

The following subsections detail the plan for each component.

### 3.1 Configuration file and runtime loader

Create `config/activity.yaml` (with `.example` and documentation).  A suggested schema:

```yaml
version: 1
activity_responder:
  enabled: true
  # minimum number of messages in the window to consider activity
  min_messages: 20
  # sliding window duration in minutes
  window_minutes: 30
  # maximum number of recent messages to include in the LLM context
  max_context_messages: 40
  # probability to generate a random reply when activity threshold is met
  reply_chance: 0.3
  # probability to reply when someone replies directly to the bot
  reply_on_direct_reply_chance: 1.0
  # probability to reply when someone writes after the bot (no explicit reply)
  reply_on_follow_up_chance: 0.5
  # seconds before a subsequent activity burst can trigger another random reply in the same chat/thread
  cooldown_seconds: 900
  # list of hours (0–23) during which spontaneous replies are allowed; empty list = allow always
  allowed_hours:
    - 8
    - 9
    # ... etc.
  user_api:
    poll_enabled: true
    poll_interval_seconds: 60
    poll_window_minutes: 30
    # maximum chats/threads to evaluate per tick
    poll_max_threads_per_tick: 20
```

A runtime loader similar to `RuntimeReactionsConfig` should watch this YAML file for changes and provide typed access.  The loader can be implemented in `app/llm/activity_config.py`.  Use `watchdog` or the existing approach in `RuntimeReactionsConfig` for hot‑reloading (for consistency).

### 3.2 ActivityService

Implement `ActivityService` in `app/services/activity_service.py`.  Its responsibilities include:

1. **Tracking state:** Keep in memory a mapping `(chat_id, thread_id) → last_reply_time` to enforce `cooldown_seconds`.  Optionally store the message IDs of the bot’s last replies to detect follow‑ups.  Use a similar pattern to the in‑memory cooldown in `ReactionService`【10393012218268†L112-L114】.
2. **Evaluate activity:** Provide a method `maybe_trigger_random_reply(session, client, chat_id, thread_id)` that queries the DB for the count of messages in the last `window_minutes`.  If `count ≥ min_messages`, evaluate the dice roll: `random.random() < reply_chance`.  Also ensure the current hour is in `allowed_hours` (if defined) and that no `cooldown_seconds` is active.
3. **Select a target message:** Fetch the last `max_context_messages` messages (e.g., from `fetch_messages_around` or new repository function `fetch_last_messages`) in chronological order.  Choose a message to reply to; possible heuristics:
   * Prefer messages with text rather than media.
   * Prefer longer messages or those containing a question mark.
   * If no obvious candidate, choose a random message among the last `max_context_messages` or simply reply to the latest message.
   * Mark the selected `(chat_id, message_id)` as the bot’s last auto‑reply target for follow‑up detection.
4. **Build the LLM prompt:** Use a new system prompt, for example:

   > "You are a regular participant in a Telegram group chat.  The chat has been lively recently.  Your job is to chime in with a short, conversational comment that fits the tone of the recent discussion.  Keep it under two sentences, do not introduce yourself as a bot, and stay relevant to the messages shown."

   Build a `user_prompt` similar to `REACTION_USER_PROMPT_TEMPLATE` but without the reactions summary.  List the context lines (each with timestamp, sender and cleaned content) and then ask the model to reply to the chosen line.  Use the `_format_context_message` helper from `reaction_service.py` for formatting【10393012218268†L77-L86】.
5. **Call the LLM:** Use `OpenRouterClient.complete` with the new prompts.  Log the prompt if `LOG_PROMPTS` is enabled and record the interaction via `record_llm_interaction` (command name `activity_reply`).
6. **Send the reply:** Use `TelegramClient.send_message` with `reply_to_message_id` set to the target’s message ID, just like `_send_reply` in `ReactionService`【10393012218268†L593-L607】.  If the reply text exceeds Telegram’s length limits, split it using `split_for_telegram` from `app/bot/formatting.py`.
7. **Follow‑up handling:** Provide methods `on_direct_reply(session, client, event)` and `on_follow_up(session, client, event)` to respond when a user replies to the bot’s last message.  These should:
   * Check if the new message’s `reply_to_message_id` matches the last auto‑reply message ID; if so, roll a dice using `reply_on_direct_reply_chance` and call the LLM again with a context consisting of the bot’s previous reply and the user’s message.
   * If the new message has no `reply_to_message_id` but appears in the same thread and arrives within a short window (e.g., 5 minutes after the bot’s reply), treat it as a follow‑up and roll `reply_on_follow_up_chance`.
   * After each reply, update the last reply time and message ID to enforce cooldown.

### 3.3 ActivityPoller

Create `ActivityPoller` in `app/services/activity_poller.py`.  It should mirror `ReactionPoller` but work on message counts instead of reaction snapshots:

1. In its `start` method, spawn a background task that periodically calls `_tick` if `poll_enabled` is true.
2. In `_tick`, use `datetime.now(UTC)` to compute a `since` timestamp (`now − poll_window_minutes`), then query a new repository function (e.g., `fetch_active_threads`) to return candidate `(chat_id, message_thread_id, message_count)` tuples where `message_count ≥ activity_config.min_messages`.  Limit the number of candidates to `poll_max_threads_per_tick`.
3. For each candidate, call `ActivityService.maybe_trigger_random_reply`.
4. Use `asyncio.wait_for(self._stop.wait(), timeout=poll_interval_seconds)` to sleep between ticks, similar to `ReactionPoller`【869415281329614†L76-L79】.

### 3.4 Database queries and repository helpers

Add functions to `app/db/repositories.py` to support the activity responder:

* `fetch_recent_message_count(session, chat_id, thread_id, since)` → returns count of messages in a given thread after a timestamp.
* `fetch_last_messages(session, chat_id, thread_id, limit)` → returns the most recent `limit` messages (excluding the bot’s own messages if desired).
* Optionally, insert a table or flag to mark messages sent by the activity responder (e.g., `message.is_activity_reply` boolean) so that follow‑ups can be detected without in‑memory state persisting across restarts.

### 3.5 Integrating into `main.py`

Modify `build_services()` to load the activity configuration and construct `ActivityService` and `ActivityPoller`, passing them the Telethon client and `OpenRouterClient`.  Extend the `AppServices` dataclass accordingly.  Start and stop the `ActivityPoller` alongside `ReactionPoller`【979001785294313†L233-L239】.

Extend the `handle_new_message` event handler:

1. After ingesting the message, call `activity_service.on_direct_reply` if `message.reply_to_message_id` refers to a message that the bot sent via the activity responder and the feature is enabled.
2. If not a direct reply but in the same thread as the bot’s last reply and within a short follow‑up window, call `activity_service.on_follow_up`.

### 3.6 Observability and safety

* Add logging events (`activity.threshold_met`, `activity.dice_lost`, `activity.reply_sent`, `activity.cooldown_active`, etc.) similar to those defined for reactions【10393012218268†L233-L244】 to aid monitoring.
* Respect `LOG_PROMPTS` by logging the built prompts only when enabled.  Keep replies under two sentences and avoid profanity.
* Ensure the service honours `TELEGRAM_ALLOWED_CHAT_IDS` (already enforced in `main.py`【979001785294313†L123-L125】) and does not respond in chats outside the allowlist.
* Provide an `enabled` flag so that the feature can be toggled off.

### 3.7 Documentation and examples

Update the `README.md` to describe the new feature and its configuration parameters.  Explain how the random replies are triggered and emphasise that it only activates during high activity and within allowed hours.  Provide a migration guide similar to `docs/USER_API_MIGRATION_PLAN.md` if necessary.

## 4 Implementation plan (sequence of tasks)

1. **Create configuration:** Add `config/activity.yaml.example` and document fields.  Add the file to `.env.example` if any new paths are needed.
2. **Runtime loader:** Implement `RuntimeActivityConfig` that watches `activity.yaml` and exposes typed properties.
3. **Database changes:** Add an optional column `is_activity_reply` in `telegram_messages` to mark messages sent by the activity responder.  Write Alembic migration scripts to add this column and update models.
4. **Repository functions:** Add helper functions (`fetch_recent_message_count`, `fetch_last_messages`) to `app/db/repositories.py`.
5. **Service implementation:** Create `ActivityService` with methods described in §3.2.  Reuse context‑building helpers from `ReactionService` for formatting.  Write a dedicated prompt for the activity replies.
6. **Poller implementation:** Create `ActivityPoller` with periodic scanning logic.  Ensure proper cancellation and error handling similar to `ReactionPoller`【869415281329614†L46-L73】.
7. **Integration:** Update `app/main.py` to load the activity configuration, build the service and poller, and wire them into the Telethon event handlers.  Ensure `ActivityPoller.start` is called on startup and `stop` on shutdown.
8. **Testing:** Write unit tests for the new configuration loader, service heuristics (activity threshold, message selection, cooldowns) and poller.  Use mock Telethon clients and DB sessions.  Perform manual smoke tests: send bursts of messages, verify that the bot sometimes replies at random times, check that it doesn’t respond when the chat is quiet or outside `allowed_hours`, and that replies follow up correctly.
9. **Documentation:** Update `README.md` with usage instructions and the configuration example.  Document new environment variables if required.
10. **Future improvements:** Consider adding machine‑learning‑based selection of interesting messages (e.g., using sentiment or question detection) instead of simple heuristics.  Add per‑user or per‑chat rate limits.

## 5 Conclusion

The existing *ResPManager* project already includes a sophisticated reactions‑based responder.  By reusing its architectural patterns—configuration‑driven behaviour, services encapsulating decision logic, pollers for async tasks, and careful observability—we can integrate an activity‑based random reply system without disrupting the current structure.  The new `ActivityService` and `ActivityPoller` will monitor message volume, decide when to interject with a witty comment and handle follow‑up conversations.  Configuration via `activity.yaml` will give administrators full control over thresholds, probabilities, context length and allowed hours, ensuring the feature remains respectful of chat dynamics and user preferences.  
