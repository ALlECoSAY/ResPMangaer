# ResPManager — Migration Plan from Telegram Bot API to Telegram User API

## Execution Status

- [x] Waves 0-9 implemented in the codebase.
- [~] Wave 10 test coverage expanded, but full runtime validation is still limited by this sandbox environment.
- [x] Wave 11 optional aiogram removal completed after dual-mode stabilization.
- [x] `/stats` is implemented on the framework-independent command path, so it works through the Telethon user-API runtime.

## 0. Context

The current `ResPManager` project is a Python 3.12 Telegram AI assistant built around **aiogram 3** and the **Telegram Bot API**.

At the moment, the app:

- starts via `app.main`;
- creates an `aiogram.Bot` using `TELEGRAM_BOT_TOKEN`;
- runs `Dispatcher.start_polling(...)`;
- stores visible Telegram messages in PostgreSQL;
- separates forum topic context by `message_thread_id`;
- supports `/ai`, `/tldr`, `/tldr_all`, and whitelist management;
- uses OpenRouter for AI responses;
- stores access-control data in YAML config files;
- runs through Docker Compose with PostgreSQL.

The goal is to transform the project so it can run through a **Telegram user account** using the **Telegram User API / MTProto**, instead of running as a Telegram bot through the Bot API.

Recommended user API library: **Telethon**.

This migration should be done in waves so an AI coding agent can execute the work step by step without rewriting the whole system at once.

---

## 1. Target Architecture

The main architectural change is to introduce a framework-independent Telegram transport layer.

The business logic should not know whether messages came from:

- aiogram / Bot API; or
- Telethon / User API.

The following parts should remain mostly unchanged:

- OpenRouter integration;
- context building;
- `/ai` answer generation;
- `/tldr` summarization;
- PostgreSQL models and repositories;
- YAML-based access control;
- Docker/PostgreSQL infrastructure.

The following parts need to be refactored:

- Telegram event routing;
- message DTO conversion;
- message sending;
- command dispatching;
- reaction handling;
- startup/runtime configuration;
- session management for the Telegram user account.

---

## 2. Migration Waves Overview

Recommended execution order:

1. Freeze and document the current baseline.
2. Introduce Telegram DTOs and a transport protocol.
3. Add an aiogram adapter while keeping the current Bot API mode working.
4. Refactor ingestion, formatting, and helper utilities away from aiogram types.
5. Extract command business logic from aiogram handlers.
6. Add Telethon dependency and user-mode configuration.
7. Implement the Telethon user API adapter.
8. Add a session bootstrap tool.
9. Add user-mode runtime.
10. Handle whitelist flow without inline Bot API callbacks.
11. Refactor or temporarily disable reactions in user mode.
12. Update Docker, README, and operational docs.
13. Add tests and run quality checks.
14. Optionally remove aiogram after user mode is stable.

---

# Wave 0 — Freeze the Baseline

## Task 0.1 — Add a Migration Document

Create:

```text
/docs/USER_API_MIGRATION_PLAN.md
```

Put this plan into that file.

### Acceptance Criteria

```text
- The migration plan exists in the repository.
- The plan is written in English.
- The plan can be used directly as an execution checklist by an AI coding agent.
```

## Task 0.2 — Run the Current Test Suite

Run:

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy app
```

### Acceptance Criteria

```text
- The current test/quality status is recorded.
- Any pre-existing failures are documented before starting the migration.
- Migration work is not mixed with unrelated legacy bug fixes unless required.
```

---

# Wave 1 — Introduce a Telegram Transport Layer

The goal of this wave is to stop the business logic from depending directly on aiogram objects.

## Task 1.1 — Create Framework-Independent Telegram DTOs

Create:

```text
app/telegram_client/types.py
```

Suggested implementation:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TgUser:
    id: int
    is_bot: bool
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None = None


@dataclass(frozen=True)
class TgChat:
    id: int
    type: str
    title: str | None
    username: str | None
    is_forum: bool = False


@dataclass(frozen=True)
class TgMessage:
    chat: TgChat
    message_id: int
    message_thread_id: int
    from_user: TgUser | None
    date: datetime
    text: str | None
    caption: str | None
    content_type: str
    reply_to_message_id: int | None
    is_topic_message: bool = False
    topic_title: str | None = None
```

### Acceptance Criteria

```text
- types.py has no aiogram imports.
- types.py has no Telethon imports.
- TgMessage contains all data required by the current database ingestion logic.
- message_thread_id is always represented as an integer, using 0 for non-topic/general messages.
```

## Task 1.2 — Create a Telegram Client Protocol

Create:

```text
app/telegram_client/client.py
```

Suggested interface:

```python
from __future__ import annotations

from typing import Protocol

from app.telegram_client.types import TgMessage


class TelegramClientProtocol(Protocol):
    async def get_self_username(self) -> str | None:
        ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> TgMessage | None:
        ...

    async def send_typing(
        self,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        ...

    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> None:
        ...
```

### Acceptance Criteria

```text
- Business logic can depend on TelegramClientProtocol instead of aiogram.Bot.
- The protocol contains only methods actually needed by the current app.
- No Telegram framework-specific type leaks through the protocol.
```

## Task 1.3 — Add an aiogram Adapter

Create:

```text
app/telegram_client/aiogram_adapter.py
```

Responsibilities:

```text
- Convert aiogram.types.User to TgUser.
- Convert aiogram.types.Chat to TgChat.
- Convert aiogram.types.Message to TgMessage.
- Implement TelegramClientProtocol on top of aiogram.Bot.
```

### Acceptance Criteria

```text
- Current Bot API mode still works.
- All aiogram-specific conversion logic is isolated in aiogram_adapter.py.
- Other business modules should not need to know about aiogram objects.
```

---

# Wave 2 — Refactor Message Ingestion and Telegram Helpers

The current message ingestion accepts `aiogram.types.Message`. This must become framework-independent.

## Task 2.1 — Refactor Message Ingestion to Use TgMessage

Update:

```text
app/services/message_ingestion.py
```

Change the signature to:

```python
async def ingest_message(
    session: AsyncSession,
    message: TgMessage,
    settings: Settings,
    bot_username: str | None,
) -> None:
    ...
```

Remove direct aiogram imports from the module.

### Acceptance Criteria

```text
- message_ingestion.py does not import aiogram.
- Chat, thread, user, and message persistence works with TgMessage.
- Existing settings such as STORE_COMMAND_MESSAGES and STORE_BOT_MESSAGES still work.
- Command parsing still works for /ai, /tldr, /tldr_all, /whitelist, and future confirmation commands.
```

## Task 2.2 — Refactor Reply Formatting

Update:

```text
app/bot/formatting.py
```

Current behavior should stay the same, but the function should use the transport protocol.

Suggested signature:

```python
async def reply_in_same_thread(
    client: TelegramClientProtocol,
    message: TgMessage,
    text: str,
    max_chars: int,
    reply_to_message_id: int | None = None,
) -> list[TgMessage]:
    ...
```

### Acceptance Criteria

```text
- formatting.py does not import aiogram.
- Message splitting behavior remains unchanged.
- Replies still target the same chat and thread/topic.
```

## Task 2.3 — Refactor Telegram Utility Functions

Update:

```text
app/utils/telegram.py
```

Replace aiogram types with:

```text
TgUser
TgMessage
```

Functions to preserve:

```text
display_name(...)
message_thread_id_for(...)
extract_text(...)
clean_command_text(...)
```

### Acceptance Criteria

```text
- app/utils/telegram.py does not import aiogram.
- Existing command parsing and display-name behavior remains compatible.
```

---

# Wave 3 — Extract Command Business Logic

The current command handlers are defined as aiogram router handlers. The business logic should be extracted into framework-independent functions.

## Task 3.1 — Create Framework-Independent Command Handlers

Create:

```text
app/bot/command_handlers.py
```

Suggested structure:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.config import Settings
from app.llm.runtime_config import RuntimeContextConfig
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import TldrScope, TldrService
from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgMessage


@dataclass
class CommandContext:
    message: TgMessage
    client: TelegramClientProtocol
    settings: Settings
    access_control: AccessControl
    yaml_store: YamlAccessStore
    ai_service: AiAnswerService
    tldr_service: TldrService
    runtime_config: RuntimeContextConfig
    bot_username_provider: Callable[[], str | None]


async def handle_ai_command(ctx: CommandContext) -> None:
    ...


async def handle_tldr_command(ctx: CommandContext, scope: TldrScope) -> None:
    ...


async def handle_whitelist_command(ctx: CommandContext) -> None:
    ...
```

### Acceptance Criteria

```text
- command_handlers.py has no aiogram imports.
- command_handlers.py has no Telethon imports.
- aiogram handlers become thin wrappers that convert incoming events and call the shared command logic.
- /ai and /tldr behavior remains unchanged in Bot API mode.
```

## Task 3.2 — Replace Inline Callback-Dependent Whitelist Flow

The current whitelist flow uses Bot API inline buttons and callback queries. User API mode should avoid depending on Bot API inline callbacks.

Recommended new flow:

```text
Admin replies to a user's message with:
/whitelist

The app replies:
To confirm adding user <id>, send:
/confirm_whitelist <id>

Admin sends:
/confirm_whitelist <id>

The app adds the user to whitelist.yaml.
```

### Acceptance Criteria

```text
- /whitelist can identify the replied-to user.
- /confirm_whitelist <user_id> performs the actual write.
- Only admins can use both commands.
- No framework-independent command logic depends on CallbackQuery or InlineKeyboardMarkup.
```

---

# Wave 4 — Add User API Configuration and Telethon

## Task 4.1 — Add Telethon Dependency

Update:

```text
pyproject.toml
```

Add:

```toml
"telethon>=1.36.0,<2.0",
```

Do not remove aiogram yet.

### Acceptance Criteria

```text
- The project installs successfully with Telethon.
- Bot API mode still works.
- No existing imports are broken.
```

## Task 4.2 — Add Telegram Mode Settings

Update:

```text
app/config.py
```

Add settings:

```python
telegram_mode: str = "bot"  # bot | user

telegram_api_id: int | None = None
telegram_api_hash: str = ""
telegram_user_session_path: Path = Path("/app/config/telegram_user.session")
telegram_user_phone: str = ""
telegram_user_2fa_password: str = ""
allow_unsafe_all_chats: bool = False
```

Update `require_secrets()`:

```text
If TELEGRAM_MODE=bot:
- require TELEGRAM_BOT_TOKEN.

If TELEGRAM_MODE=user:
- require TELEGRAM_API_ID.
- require TELEGRAM_API_HASH.
- require a valid session file, or allow login only through the explicit bootstrap tool.
- require TELEGRAM_ALLOWED_CHAT_IDS unless ALLOW_UNSAFE_ALL_CHATS=true.
```

Update:

```text
.env.example
```

Add:

```env
TELEGRAM_MODE=bot

# Bot API mode
TELEGRAM_BOT_TOKEN=replace_me

# User API / MTProto mode
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_USER_SESSION_PATH=/app/config/telegram_user.session
TELEGRAM_USER_PHONE=
TELEGRAM_USER_2FA_PASSWORD=
ALLOW_UNSAFE_ALL_CHATS=false
```

### Acceptance Criteria

```text
- Bot mode does not require API_ID/API_HASH.
- User mode does not require TELEGRAM_BOT_TOKEN.
- User mode refuses to start without a chat allowlist unless ALLOW_UNSAFE_ALL_CHATS=true.
- Startup errors clearly explain which required settings are missing.
```

---

# Wave 5 — Implement the Telethon User API Adapter

## Task 5.1 — Create Telethon Adapter

Create:

```text
app/telegram_client/telethon_adapter.py
```

Responsibilities:

```text
- Create and manage a Telethon client session.
- Implement TelegramClientProtocol.
- Convert Telethon messages/users/chats to TgMessage/TgUser/TgChat.
- Send messages.
- Send replies.
- Preserve topic/thread behavior as much as possible.
- Send typing action if supported.
- Set message reactions if supported.
```

Suggested class:

```python
class TelethonUserClient(TelegramClientProtocol):
    ...
```

### Thread/Topic Mapping Warning

Bot API exposes topic IDs through `message_thread_id`.

Telethon/MTProto exposes forum-topic relationships differently. The adapter should normalize topic IDs carefully.

Recommended first implementation:

```text
message_thread_id = reply_to_top_id or top_msg_id or 0
```

Then verify this manually in real Telegram forum topics.

### Acceptance Criteria

```text
- telethon_adapter.py is the only module importing Telethon for runtime message conversion.
- Incoming Telethon messages are converted into TgMessage.
- Sending a message works in a normal group.
- Replying to a specific message works.
- Topic/thread ID mapping is logged in safe debug form for manual verification.
```

## Task 5.2 — Add Converter Tests

Create tests for the conversion layer.

Suggested file:

```text
tests/test_telethon_adapter_conversion.py
```

### Acceptance Criteria

```text
- Normal text messages convert correctly.
- Captions convert correctly.
- Reply target message ID converts correctly.
- Non-topic messages get message_thread_id=0.
- Topic messages preserve a stable normalized thread ID.
```

---

# Wave 6 — Add User Session Bootstrap

User API mode requires a persisted Telegram session. Do not perform interactive login inside the main service process.

## Task 6.1 — Create Session Bootstrap Script

Create:

```text
app/tools/create_telegram_session.py
```

Expected command:

```bash
docker compose run --rm bot python -m app.tools.create_telegram_session
```

The script should:

```text
- read TELEGRAM_API_ID;
- read TELEGRAM_API_HASH;
- read TELEGRAM_USER_PHONE;
- request the login code from stdin;
- request 2FA password if required;
- save the session file to TELEGRAM_USER_SESSION_PATH;
- exit successfully once the session is authorized.
```

### Acceptance Criteria

```text
- The main bot service never asks for login codes interactively.
- The session file is stored under /app/config so Docker volume persistence works.
- Restarting the container does not require re-login.
```

---

# Wave 7 — Add User API Runtime

## Task 7.1 — Split Runtime by Telegram Mode

Update:

```text
app/main.py
```

Current flow is Bot API only. Replace it with a mode switch:

```python
if settings.telegram_mode == "bot":
    await run_bot_api(...)
elif settings.telegram_mode == "user":
    await run_user_api(...)
else:
    raise ValueError(f"Unknown TELEGRAM_MODE: {settings.telegram_mode}")
```

### Acceptance Criteria

```text
- TELEGRAM_MODE=bot keeps current behavior.
- TELEGRAM_MODE=user starts the Telethon runtime.
- Shared services are initialized once and reused by either runtime.
```

## Task 7.2 — Implement Telethon NewMessage Routing

In user mode, register a Telethon `NewMessage` event handler.

Processing order:

```text
1. Convert Telethon event message to TgMessage.
2. Check TELEGRAM_ALLOWED_CHAT_IDS.
3. Ingest the message into PostgreSQL.
4. Parse command text.
5. Dispatch command if applicable.
6. Do nothing for non-command messages after ingestion.
```

Supported commands:

```text
/ai <question>
/tldr [options]
/tldr_all [options]
/whitelist
/confirm_whitelist <user_id>
```

### Acceptance Criteria

```text
- Normal visible messages are stored.
- Command messages are stored if STORE_COMMAND_MESSAGES=true.
- /ai replies in the same chat/topic when possible.
- /tldr replies in the same chat/topic when possible.
- Unauthorized users are denied before any LLM call.
- Messages from non-allowlisted chats are ignored.
```

## Task 7.3 — Implement Typing Action

Current Bot API mode sends a typing action before `/ai` responses.

In Telethon mode:

```text
- Use Telethon's typing action if available.
- If not available or if it fails, log a warning and continue.
```

### Acceptance Criteria

```text
- Failure to send typing action never breaks /ai.
- The user still receives an answer if the LLM call succeeds.
```

---

# Wave 8 — Handle Reactions

The current reaction service depends on aiogram-specific reaction update objects and Bot API reaction methods. This is the riskiest part of the migration.

Recommendation: do not block the first user API MVP on reactions.

## Task 8.1 — Add Framework-Independent Reaction DTO

Add to:

```text
app/telegram_client/types.py
```

```python
@dataclass(frozen=True)
class TgReactionUpdate:
    chat_id: int
    message_id: int
    user: TgUser | None
    old_emojis: list[str]
    new_emojis: list[str]
```

## Task 8.2 — Refactor ReactionService to Use the DTO

Update:

```text
app/services/reaction_service.py
```

New signature:

```python
async def handle_reaction_update(
    self,
    session: AsyncSession,
    client: TelegramClientProtocol,
    event: TgReactionUpdate,
) -> None:
    ...
```

### Acceptance Criteria

```text
- reaction_service.py does not import aiogram.
- Sending reaction-triggered replies uses TelegramClientProtocol.
- Bot API mode still supports reactions through the aiogram adapter.
```

## Task 8.3 — Add or Defer Telethon Reaction Events

If Telethon provides enough reliable reaction update data:

```text
- Convert reaction events to TgReactionUpdate.
- Route them into ReactionService.
```

If not:

```text
- Disable reactions in TELEGRAM_MODE=user by default.
- Log a startup warning: "Reaction handling is not supported in user mode yet."
- Keep /ai and /tldr fully functional.
```

### Acceptance Criteria

```text
- Reactions do not block user mode startup.
- User mode MVP can run with reactions disabled.
- Any unsupported behavior is clearly documented.
```

---

# Wave 9 — Docker and Operations

## Task 9.1 — Update Docker Usage

The current Compose file already mounts:

```text
./config:/app/config
```

This is good because the Telethon session file can live there.

Update README with two separate modes.

### Bot API Mode

```bash
cp .env.example .env
# set TELEGRAM_MODE=bot
# set TELEGRAM_BOT_TOKEN
docker compose up -d --build
```

### User API Mode

```bash
cp .env.example .env
# set TELEGRAM_MODE=user
# set TELEGRAM_API_ID
# set TELEGRAM_API_HASH
# set TELEGRAM_USER_PHONE
# set TELEGRAM_ALLOWED_CHAT_IDS

docker compose run --rm bot python -m app.tools.create_telegram_session
docker compose up -d --build
```

### Acceptance Criteria

```text
- README explains both modes.
- Session bootstrap instructions are clear.
- The session file persists across container restarts.
```

## Task 9.2 — Add Production Safety Checks

In user mode:

```text
- TELEGRAM_ALLOWED_CHAT_IDS should be required.
- Empty allowlist should fail startup unless ALLOW_UNSAFE_ALL_CHATS=true.
- Message text should not be logged by default.
- Prompts should only be logged when LOG_PROMPTS=true.
```

### Acceptance Criteria

```text
- User mode cannot accidentally ingest every chat visible to the user account.
- Unsafe local-development mode is explicit.
- Logs do not leak chat content by default.
```

---

# Wave 10 — Testing and Validation

## Task 10.1 — Add Unit Tests

Add or update tests for:

```text
- command parsing;
- TgMessage ingestion;
- display_name and helper functions;
- aiogram adapter conversion;
- Telethon adapter conversion;
- user-mode config validation;
- denied access before LLM calls;
- whitelist confirmation flow.
```

### Acceptance Criteria

```text
- pytest passes.
- ruff passes.
- mypy passes or only has documented legacy issues.
```

## Task 10.2 — Add Manual Smoke Test Checklist

Add this to README or `docs/USER_API_MIGRATION_PLAN.md`:

```text
- Create a Telethon session successfully.
- Start service with TELEGRAM_MODE=user.
- Send a normal message in an allowlisted group.
- Verify the message appears in telegram_messages.
- Send /ai <question> as an allowed user.
- Verify the app replies in the same chat/topic.
- Send /tldr as an allowed user.
- Verify the app replies in the same chat/topic.
- Send /ai as a non-whitelisted user.
- Verify the app denies the request before calling the LLM.
- Add a user through /whitelist + /confirm_whitelist.
- Restart the container.
- Verify no new login code is required.
```

---

# Wave 11 — Optional aiogram Removal

Do not remove aiogram immediately.

Keep both modes for at least one stabilization period, especially because Telegram topics/thread IDs can behave differently between Bot API and MTProto.

## Task 11.1 — Remove Bot API Mode Only After Stabilization

When user mode is stable, remove:

```text
- aiogram dependency;
- aiogram adapter;
- Bot API dispatcher;
- TELEGRAM_BOT_TOKEN;
- Bot command registration settings;
- callback-query whitelist flow.
```

### Acceptance Criteria

```text
- No aiogram imports remain.
- User mode is the only runtime mode.
- README no longer documents Bot API mode if it has been removed.
- Tests and linting pass.
```

---

# Recommended Backlog for an AI Coding Agent

Give the agent the tasks in this order:

```text
1. Add docs/USER_API_MIGRATION_PLAN.md.
2. Add app/telegram_client/types.py.
3. Add app/telegram_client/client.py.
4. Add app/telegram_client/aiogram_adapter.py.
5. Refactor message_ingestion.py to use TgMessage.
6. Refactor formatting.py to use TelegramClientProtocol.
7. Refactor utils/telegram.py to use TgUser/TgMessage.
8. Extract command logic into app/bot/command_handlers.py.
9. Keep aiogram handlers as thin wrappers.
10. Add Telethon dependency.
11. Add TELEGRAM_MODE and user API settings.
12. Add app/telegram_client/telethon_adapter.py.
13. Add app/tools/create_telegram_session.py.
14. Add user-mode runtime in app/main.py.
15. Add Telethon NewMessage routing.
16. Replace inline whitelist confirmation with text command confirmation.
17. Refactor or disable reactions in user mode.
18. Update .env.example, README, and Docker instructions.
19. Add tests for conversion, routing, config validation, and access control.
20. Run pytest, ruff, and mypy.
```

---

# Key Risks

## 1. Telegram Topic / Thread ID Mapping

This is the biggest migration risk.

The current database and context builder rely on stable `(chat_id, message_thread_id)` pairs. Bot API and MTProto expose topic/thread metadata differently.

Before considering user mode production-ready, manually verify that:

```text
- messages from the same forum topic get the same normalized message_thread_id;
- messages from different topics get different normalized message_thread_id values;
- non-topic messages use 0;
- replies inside topics preserve the correct topic context.
```

If this is wrong, `/ai` and `/tldr` will mix unrelated topics.

## 2. Telegram Account Safety

A user account can see more than a bot. This makes privacy mistakes more dangerous.

Mitigations:

```text
- require TELEGRAM_ALLOWED_CHAT_IDS in user mode;
- avoid logging message text by default;
- keep LOG_PROMPTS=false by default;
- do not run the user account in every chat it can access;
- use a dedicated Telegram account if possible.
```

## 3. Reactions

Reaction updates are likely to be less straightforward in user mode than normal messages.

The MVP should not depend on reaction handling.

## 4. Interactive Login in Docker

Do not ask for login codes inside the main service container logs.

Use a one-off bootstrap command to create and persist the session file.

---

# Definition of Done

The migration can be considered complete when:

```text
- TELEGRAM_MODE=user starts successfully using a persisted Telethon session.
- The app ingests messages from allowlisted chats.
- /ai works from a Telegram user account.
- /tldr works from a Telegram user account.
- Access control works exactly as before.
- Whitelist management works without Bot API callbacks.
- The app replies in the correct chat and topic/thread.
- Restarting the container does not require re-authentication.
- pytest passes.
- ruff passes.
- mypy passes or only has documented legacy issues.
- README explains setup, session creation, and production safety.
```
