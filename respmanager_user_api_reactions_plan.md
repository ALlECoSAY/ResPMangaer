# ResPManager — User API Reactions Feature Plan

## Goal

Implement reaction tracking and probabilistic AI replies in `TELEGRAM_MODE=user`.

The feature must:

- receive reaction updates through Telegram User API / MTProto;
- register reactions in PostgreSQL;
- count distinct human users reacting to a message;
- when the configured threshold is reached, reply to the reacted message with a random chance;
- preserve the existing behavior of Bot API mode;
- avoid duplicate replies during reaction bursts and restarts.

---

## Current repository state

The migration to user API is partially implemented.

Already present:

- `pyproject.toml` includes both `aiogram` and `telethon`.
- `Settings` has `telegram_mode`, `telegram_api_id`, `telegram_api_hash`, `telegram_user_session_path`, and user-mode safety checks.
- `app/telegram_client/types.py` already defines `TgReactionUpdate`.
- `TelegramClientProtocol` already exposes `set_reaction(...)`.
- `TelethonUserClient` already implements `set_reaction(...)` with `functions.messages.SendReactionRequest`.
- `ReactionService` is already mostly framework-independent: it accepts `TelegramClientProtocol` and `TgReactionUpdate`.
- Bot API reaction updates are still supported through `reaction_update_from_aiogram(...)`.

Missing:

- user-mode runtime does not subscribe to MTProto reaction updates;
- `run_user_api()` currently warns that reactions are not supported;
- user API does not naturally provide the same per-user old/new reaction payload as Bot API, so a snapshot-based path is needed.

---

## Design decision

Do not try to force MTProto reaction updates into the exact Bot API shape.

Bot API gives per-user old/new reaction updates.

User API / MTProto may provide aggregate reaction updates and sometimes recent reactors, but not always a complete per-user diff. Therefore user mode should use a snapshot model:

1. Receive raw MTProto reaction update for a message.
2. Fetch current reaction users for that message, filtered by configured trigger emojis when possible.
3. Replace the stored DB snapshot for that message.
4. Count distinct users from the stored snapshot.
5. If the threshold is reached and the message is eligible, roll `reply_chance`.
6. On win, generate an LLM reply and reply to the reacted message.

This keeps Bot API behavior intact and gives user mode reliable counting.

---

## Wave 1 — Add snapshot DTOs

### Task 1.1 — Extend `app/telegram_client/types.py`

Add:

```python
@dataclass(frozen=True)
class TgReactionActor:
    user: TgUser
    emojis: list[str]


@dataclass(frozen=True)
class TgMessageReactionSnapshot:
    chat_id: int
    message_id: int
    actors: list[TgReactionActor]
    counts: dict[str, int]
```

Rules:

- `actors` contains only known users.
- Anonymous/channel reactions may contribute to `counts`, but not to distinct-user threshold.
- For MVP, support normal emoji reactions only.
- Custom emoji reactions may be ignored or represented as `custom:<document_id>`, but should not trigger unless explicitly supported later.

Acceptance criteria:

- `types.py` remains framework-independent.
- Bot API path continues to use `TgReactionUpdate`.
- User API path uses `TgMessageReactionSnapshot`.

---

## Wave 2 — Extend DB repository functions

### Task 2.1 — Add snapshot replacement

Add to `app/db/repositories.py`:

```python
async def replace_message_reactions_snapshot(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    rows: list[tuple[int, list[str]]],
) -> None:
    ...
```

Behavior:

- Delete all rows from `telegram_message_reactions` for `(chat_id, message_id)`.
- Insert one row per `(user_id, emoji)`.
- Use `ON CONFLICT DO NOTHING`.

Input example:

```python
[
    (123, ["🔥", "👍"]),
    (456, ["🔥"]),
]
```

Acceptance criteria:

- Existing `replace_user_reactions(...)` remains for Bot API mode.
- Snapshot replacement is idempotent.
- Existing `count_distinct_reaction_users(...)` works unchanged.

---

## Wave 3 — Add persistent reaction state

The existing service has an in-memory cooldown. That is okay for short bursts, but user-mode raw updates can repeat and restarts can re-trigger old messages. Add persistent state.

### Task 3.1 — Add model

Add table:

```sql
telegram_reaction_states
```

Fields:

```sql
id BIGSERIAL PRIMARY KEY,
chat_id BIGINT NOT NULL,
message_id BIGINT NOT NULL,
last_distinct_trigger_users INTEGER NOT NULL DEFAULT 0,
last_evaluated_at TIMESTAMPTZ,
last_reply_at TIMESTAMPTZ,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(chat_id, message_id)
```

### Task 3.2 — Add repository helpers

Add:

```python
@dataclass(frozen=True)
class ReactionState:
    chat_id: int
    message_id: int
    last_distinct_trigger_users: int
    last_evaluated_at: datetime | None
    last_reply_at: datetime | None


async def get_reaction_state(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
) -> ReactionState | None:
    ...


async def upsert_reaction_state(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
    *,
    last_distinct_trigger_users: int,
    last_evaluated_at: datetime | None = None,
    last_reply_at: datetime | None = None,
) -> None:
    ...
```

Acceptance criteria:

- A message is evaluated only when distinct trigger-user count increases or crosses the threshold.
- Restarting the app does not make it repeatedly roll on old unchanged reactions.

---

## Wave 4 — Add user-mode snapshot handling to `ReactionService`

### Task 4.1 — Add a new service method

Add:

```python
async def handle_reaction_snapshot(
    self,
    session: AsyncSession,
    client: TelegramClientProtocol,
    snapshot: TgMessageReactionSnapshot,
) -> None:
    ...
```

Algorithm:

1. Return if reactions are disabled.
2. Ignore empty snapshots.
3. Persist users from `snapshot.actors` via `upsert_user(...)`.
4. Replace message reaction snapshot in DB.
5. Count distinct trigger users:
   - use `count_distinct_reaction_users(...)`;
   - pass `trigger_emojis` if configured.
6. Load `telegram_reaction_states` for `(chat_id, message_id)`.
7. If count is below `min_distinct_users`, update state and return.
8. If count did not increase since the last state, return.
9. Check persistent cooldown:
   - if `last_reply_at` is within `cooldown_seconds`, return.
10. Roll random chance:
    - `roll = self._rng.random()`;
    - if `roll >= reply_chance`, update `last_evaluated_at` and `last_distinct_trigger_users`, then return.
11. Fetch target message from DB.
12. Fetch context around target.
13. Generate LLM reply with the existing prompt.
14. Reply to the original message through `client.send_message(..., reply_to_message_id=target_message_id)`.
15. Optionally set the account reaction through `client.set_reaction(...)`.
16. Update state with `last_reply_at`, `last_evaluated_at`, and latest count.

Acceptance criteria:

- Bot API method `handle_reaction_update(...)` remains working.
- User API uses `handle_reaction_snapshot(...)`.
- `reply_chance=0.0` never replies.
- `reply_chance=1.0` always replies once eligible.
- Random chance is tested with injected `random.Random`.

---

## Wave 5 — Implement Telethon reaction snapshot fetching

### Task 5.1 — Extend `TelegramClientProtocol`

Add:

```python
async def fetch_message_reaction_snapshot(
    self,
    chat_id: int,
    message_id: int,
    *,
    trigger_emojis: tuple[str, ...] = (),
    limit_per_emoji: int = 200,
) -> TgMessageReactionSnapshot | None:
    ...
```

For `AiogramTelegramClient`, this can raise `NotImplementedError` or return `None`, because Bot API already receives per-user updates.

For `TelethonUserClient`, implement it with MTProto reaction-list requests.

Suggested approach:

- for each configured trigger emoji, request reaction users for that emoji;
- if `trigger_emojis` is empty, first inspect aggregate reactions from the raw update, or fetch all available normal emoji reactions if possible;
- convert users to `TgUser`;
- group reactions by user;
- return `TgMessageReactionSnapshot`.

Implementation note for the agent:

Verify exact Telethon raw request names in the installed version. The expected API is around:

```python
functions.messages.GetMessageReactionsListRequest(...)
```

and reaction values around:

```python
types.ReactionEmoji(emoticon="🔥")
```

Do not guess field names blindly. Add a tiny local introspection/debug helper if needed.

Acceptance criteria:

- Normal emoji reactors are fetched in user mode.
- Unknown/custom reactions do not crash the client.
- Missing permission or unsupported request logs a warning and returns `None`.

---

## Wave 6 — Subscribe to raw reaction updates in user mode

### Task 6.1 — Replace current warning in `run_user_api()`

Current behavior:

```python
if services.reaction_service.enabled:
    log.warning("reactions.user_mode_not_supported")
```

Replace with a real raw update handler.

### Task 6.2 — Add raw event handler

In `run_user_api()`:

```python
@client.raw_client.on(events.Raw)
async def handle_raw_update(update) -> None:
    ...
```

Algorithm:

1. Return if `reaction_service.enabled` is false.
2. Detect raw reaction updates.
3. Extract:
   - peer/chat;
   - message id;
   - aggregate reaction emojis if available.
4. Convert peer to `chat_id`.
5. Enforce `TELEGRAM_ALLOWED_CHAT_IDS`.
6. Call `client.fetch_message_reaction_snapshot(...)`.
7. If snapshot is not `None`, call `reaction_service.handle_reaction_snapshot(...)`.

Expected raw update types to inspect:

- `types.UpdateMessageReactions`
- any related reaction update types exposed by the installed Telethon version.

Acceptance criteria:

- User mode no longer logs `reactions.user_mode_not_supported`.
- Reacting to an ingested message produces DB rows in `telegram_message_reactions`.
- Threshold crossing can trigger a reply.
- Updates from non-allowlisted chats are ignored.

---

## Wave 7 — Improve config

### Task 7.1 — Extend `config/reactions.yaml.example`

Recommended config:

```yaml
version: 1
reactions:
  enabled: false
  min_distinct_users: 3
  reply_chance: 0.3
  context_before: 5
  context_after: 3
  cooldown_seconds: 600
  bot_emoji: "🔥"
  trigger_emojis:
    - "🔥"
    - "👍"
    - "😂"
  user_api:
    fetch_limit_per_emoji: 200
    ignore_custom_reactions: true
```

### Task 7.2 — Extend `RuntimeReactionsConfig`

Add optional user-api settings:

```python
fetch_limit_per_emoji: int = 200
ignore_custom_reactions: bool = True
```

Acceptance criteria:

- Missing fields fall back to safe defaults.
- Existing config files continue to work.

---

## Wave 8 — Testing

### Unit tests

Add tests for:

1. `replace_message_reactions_snapshot(...)`
   - inserts users and emojis;
   - replaces stale reactions;
   - is idempotent.

2. `ReactionService.handle_reaction_snapshot(...)`
   - below threshold: no LLM call;
   - threshold reached + `reply_chance=0.0`: no reply;
   - threshold reached + `reply_chance=1.0`: exactly one reply;
   - same distinct count repeated: no second reply;
   - distinct count increased: can evaluate again;
   - cooldown blocks duplicate replies.

3. `TelethonUserClient` reaction parsing
   - emoji reaction;
   - custom reaction ignored;
   - no reactors returns empty snapshot;
   - malformed raw update does not crash.

4. `run_user_api()` integration
   - raw reaction update calls snapshot handler;
   - allowlist blocks unknown chats.

### Manual smoke test

1. Start user mode.
2. Send a message in an allowlisted group.
3. React from `min_distinct_users` different users with a trigger emoji.
4. Verify rows in `telegram_message_reactions`.
5. Set `reply_chance: 1.0`.
6. Verify the account replies to the reacted message.
7. Set `reply_chance: 0.0`.
8. Verify no reply.
9. Restart the container.
10. Add one more reaction.
11. Verify it does not spam old messages repeatedly.

---

## Wave 9 — Observability and safety

Add logs:

```text
reactions.user_raw_update_received
reactions.user_snapshot_fetched
reactions.user_snapshot_empty
reactions.user_snapshot_fetch_failed
reactions.threshold_not_met
reactions.threshold_met
reactions.dice_lost
reactions.reply_sent
reactions.persistent_cooldown_active
```

Do not log full message text unless `LOG_PROMPTS=true`.

Safety rules:

- Never process reactions outside `TELEGRAM_ALLOWED_CHAT_IDS`.
- Ignore own account reactions if the reacting user is the current account.
- Ignore bot users.
- Ignore anonymous/channel reactions for distinct-user threshold.
- Keep `reply_chance` clamped to `[0.0, 1.0]`.

---

## Agent execution order

Give the implementation agent this order:

```text
1. Add TgReactionActor and TgMessageReactionSnapshot DTOs.
2. Add DB model/migration for telegram_reaction_states.
3. Add replace_message_reactions_snapshot and reaction-state repository helpers.
4. Add ReactionService.handle_reaction_snapshot.
5. Extend TelegramClientProtocol with fetch_message_reaction_snapshot.
6. Implement TelethonUserClient.fetch_message_reaction_snapshot.
7. Add Telethon raw reaction handler in run_user_api.
8. Remove the reactions.user_mode_not_supported warning.
9. Extend reactions config with user_api fetch settings.
10. Add unit tests for repository snapshot replacement.
11. Add unit tests for reaction snapshot service behavior and random chance.
12. Add Telethon converter tests with mocked raw objects.
13. Update README and config/reactions.yaml.example.
14. Run pytest, ruff, mypy.
15. Do manual smoke test in an allowlisted Telegram group.
```

---

## Non-goals for this iteration

Do not implement these yet:

- custom emoji trigger matching;
- paid reactions;
- channel/anonymous reaction counting;
- vector search for reaction replies;
- multi-message reaction digest;
- automatic joining groups or discovering chats.

Keep the feature boring. Boring ships.
