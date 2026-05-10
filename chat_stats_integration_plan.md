# Integration and Development Plan for Chat Statistics in ResPManager

## Implementation progress

- [x] Added repository aggregation helpers for message, command, reaction, thread, media, word-source, user-label, and LLM usage stats.
- [x] Added hot-reloadable `config/stats.yaml` support and `config/stats.yaml.example`.
- [x] Added `StatsService` with `/stats` summary, users, words, times, threads, reactions, and fun report methods.
- [x] Wired `/stats` into command parsing, command handling, access control, and the Telethon user-API runtime.
- [x] Added unit coverage for stats argument parsing, service formatting, and command reply splitting.
- [x] Updated README and User API migration notes.
- [ ] Periodic scheduled stats reports are deferred; `report_schedule` is reserved in config but no scheduler is running yet.

## 1 – Overview and goals

The goal is to extend ResPManager with a statistics subsystem that analyses stored messages and reactions to produce insightful and humorous summaries.  Because messages are already persisted in PostgreSQL with rich metadata【861253106093984†L2-L4】【46031338995147†L98-L133】, most statistics can be computed via SQL queries without altering the schema.  The plan below follows the project’s existing conventions (async SQLAlchemy, separation between transport and business logic, configuration via YAML, and extensive testing) and is compatible with both Bot API and User API modes.

### Objectives

1. **Create a `StatsService`** responsible for computing statistics and formatting results.
2. **Add repository helper functions** to perform aggregations efficiently.
3. **Expose a new `/stats` command** (with sub‑commands) in the command handlers.
4. **Optionally schedule periodic summary reports** using an internal scheduler.
5. **Write tests and update documentation** to cover the new functionality.
6. **Keep the architecture modular and configurable** so that additional stats can be added later without large refactoring.

## 2 – Add database query helpers

No new tables are required; existing tables already capture users, messages, reactions and LLM interactions【46031338995147†L98-L133】.  However, complex statistics should be computed server‑side to minimise memory usage.

### 2.1 – Extend `app/db/repositories.py`

Add asynchronous functions that accept an `AsyncSession` and return summarised results:

- `async def count_messages_by_user(session: AsyncSession, chat_id: int, since: datetime | None) -> list[tuple[int, int]]`: return `(user_id, message_count)` ordered by count descending.
- `async def count_messages_by_hour(session: AsyncSession, chat_id: int, since: datetime | None) -> dict[int, int]`: group messages by `extract(hour from telegram_date)`.
- `async def count_messages_by_weekday(session: AsyncSession, chat_id: int, since: datetime | None) -> dict[int, int]`.
- `async def count_commands_by_name(session: AsyncSession, chat_id: int, since: datetime | None) -> dict[str, int]` using `TelegramMessage.is_command` and `command_name`.
- `async def count_reactions(session: AsyncSession, chat_id: int, since: datetime | None) -> list[tuple[str, int]]`: aggregate `emoji` from `telegram_message_reactions`.
- `async def top_reacted_messages(session: AsyncSession, chat_id: int, since: datetime | None, limit: int) -> list[tuple[int, int]]`: return `(message_id, reaction_count)`.
- `async def fetch_messages_for_word_stats(session: AsyncSession, chat_id: int, since: datetime | None) -> list[str]`: fetch `clean_text`/`text` fields for lexical analysis.
- `async def count_media_types(session: AsyncSession, chat_id: int, since: datetime | None) -> dict[str, int]`: group by `content_type`.
- `async def count_threads(session: AsyncSession, chat_id: int, since: datetime | None) -> list[tuple[int, int]]`: return `(message_thread_id, count)`.
- `async def thread_starters(session: AsyncSession, chat_id: int, since: datetime | None) -> list[tuple[int, int]]`: count how many initial messages (no `reply_to_message_id`) each user posted per thread.
- `async def llm_usage_stats(session: AsyncSession, chat_id: int, since: datetime | None) -> tuple[int, int, float]`: compute total LLM calls, total tokens and average latency from `llm_interactions`.

Each helper should accept a `since` timestamp (nullable) so look‑back windows can be applied easily.  Where appropriate, use SQL `group_by`, `func.count` and indexes defined on `telegram_date`【46031338995147†L90-L97】 for performance.

## 3 – Implement `StatsService`

Create a new module `app/services/stats_service.py` with a class `StatsService`.  The service should:

- Accept configuration (e.g., default lookback period, maximum number of items) via `Settings` or a new `RuntimeStatsConfig` dataclass loaded from `config/stats.yaml`.
- Accept a reference to `TelegramClientProtocol` for sending messages and `OpenRouterClient` if optional sentiment or LLM‑based summaries are implemented.
- Provide high‑level methods such as:
  - `async def summary(session, chat_id, lookback_hours) -> list[str]`: build a general summary combining multiple categories.
  - `async def user_stats(...)`, `word_stats(...)`, `time_stats(...)`, `thread_stats(...)`, `reaction_stats(...)`, `fun_stats(...)`: each returns a list of formatted lines ready to send.
  - Use helper functions for lexical analysis (e.g., `collections.Counter` on words after removing stop words; optional use of `regex` to find URLs or emojis).  For sentiment and language detection, wrap optional imports in try/except and skip gracefully if the libraries are unavailable.
- Include private helper methods for formatting ASCII tables and charts.  Keep messages under the maximum length defined in `reply_in_same_thread()` by splitting into chunks.

## 4 – Add `/stats` command and update command handler

Modify `app/bot/command_handlers.py` to recognise a new `/stats` command with optional sub‑commands and look‑back arguments.  Suggested changes:

1. Extend the `CommandContext` dataclass with a `stats_service: StatsService` field (injected at runtime in `app/main.py`).
2. Add a new handler:

   ```python
   async def handle_stats_command(ctx: CommandContext, args: str) -> None:
       # parse args: subcommand and days
       subcommand, days = parse_stats_args(args)
       lookback = timedelta(days=days) if days else default
       async with async_session() as session:
           if subcommand == "users":
               lines = await ctx.stats_service.user_stats(session, ctx.message.chat.id, lookback)
           elif subcommand == "words":
               ...
           else:
               lines = await ctx.stats_service.summary(session, ctx.message.chat.id, lookback)
       await reply_in_same_thread(ctx.client, ctx.message, "\n".join(lines), max_chars=settings.stats_max_chars)
   ```

3. Register `/stats` in both Bot and User API routers.  Because the bot’s commands are strings in the database, no new table is needed.

4. Update `app/utils/telegram.py` if necessary to parse `/stats` arguments.  Follow existing patterns in `parse_tldr_lookback()`【232986476689640†L37-L46】.

## 5 – Configuration and scheduling

1. **Add `config/stats.yaml.example`** with fields such as:

   ```yaml
   stats:
     enabled: true
     default_lookback_days: 7
     top_n_users: 10
     top_n_words: 20
     top_n_threads: 5
     report_schedule: "weekly"    # or "monthly" or null
     max_message_chars: 4096       # safe limit for Telegram
   ```

2. Add a `RuntimeStatsConfig` dataclass in `app/config.py` similar to existing runtime configs (e.g., `RuntimeContextConfig` used for `/tldr`).  Merge defaults from YAML and environment variables.

3. **Periodic reports**:  if `report_schedule` is set, schedule a coroutine inside `run_bot_api()` and `run_user_api()` that sleeps until the next scheduled time, then calls `StatsService.summary()` and posts it.  Python’s `asyncio.create_task()` and `asyncio.sleep()` can suffice; avoid adding heavy cron dependencies.  Use `settings.telegram_allowed_chat_ids` to know which chats should receive reports.

## 6 – Testing

Add unit tests in `tests/test_stats_service.py` and `tests/test_stats_command.py`:

- Use an in‑memory SQLite database via SQLAlchemy to insert sample data (users, messages, reactions, llm interactions).
- Verify that each StatsService method returns expected counts and formatting.
- Test parsing of `/stats` arguments and error handling for unknown sub‑commands.
- Mock `TelegramClientProtocol` in command tests to verify that long messages are split correctly and sent to the right thread.
- Add integration tests to ensure the stats command respects access control and does not run in non‑allow‑listed chats.

Run existing quality checks (`pytest`, `ruff`, `mypy`) and ensure no regressions.

## 7 – Documentation and user guidance

1. Update `README.md`:
   - Document the `/stats` command and list available sub‑commands.
   - Describe how to enable or disable stats in `config/stats.yaml`.
   - Provide examples of the generated reports and explain limitations (e.g., sentiment analysis requires optional dependencies).

2. Provide a brief summary of the new features in `PROGRESS.md` (if used) and update `docs/USER_API_MIGRATION_PLAN.md` to note that the stats feature is compatible with both modes.

## 8 – Optional enhancements

- **Caching**: for very active chats, some statistics (like word counts) may be expensive.  Consider memoising results per chat/look‑back window with a TTL cache (e.g., `async_lru`) to avoid recomputing the same queries repeatedly.
- **Real‑time dashboards**: optionally expose an HTTP endpoint (FastAPI) that returns JSON stats for integration with dashboards.  This can reuse `StatsService`.
- **Interactive charts**: in the future, generate PNG charts (via matplotlib) and upload them to the chat.  Use the existing `imagegen` integration for decorations.

By following the steps above, ResPManager can evolve from an AI assistant into a **chat archivist**, producing playful insights that engage users and help moderators understand their community.
