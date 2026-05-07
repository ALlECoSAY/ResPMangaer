from __future__ import annotations

import random
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatting import split_for_telegram
from app.config import Settings
from app.db.models import TelegramMessage
from app.db.repositories import (
    UserInput,
    count_distinct_reaction_users,
    fetch_message_by_chat_message_id,
    fetch_messages_around,
    record_llm_interaction,
    replace_user_reactions,
    upsert_user,
)
from app.llm.openrouter_client import LlmResponse, OpenRouterClient, OpenRouterError
from app.llm.reactions_config import RuntimeReactionsConfig
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import get_logger
from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgReactionUpdate

log = get_logger(__name__)


REACTION_SYSTEM_PROMPT = """\
You are a Telegram chat participant. A specific message in a group chat has
collected several user reactions, suggesting the chat finds it noteworthy
(funny, surprising, controversial, important, etc.).

Your job:
- Reply to that exact message with a single short, conversational comment.
- Match the tone and language of the surrounding chat.
- Keep it under 2 short sentences. No bullet lists, no headers, no markdown.
- Do not announce that you are a bot, do not explain reactions, do not summarize.
- Do not start with "Reply:" or any prefix.
- Stay relevant to the reacted message; treat the surrounding messages as
  background only.
- Avoid being preachy or generic. Be specific to what was actually said.
"""


REACTION_USER_PROMPT_TEMPLATE = """\
Chat context (chronological).
The line marked with >>> is the message the chat reacted to.

{context_text}

Reactions on the >>> message: {reactions_summary}

Write a single short, in-character reply to the >>> message. Output only the
reply text, nothing else."""


@dataclass(frozen=True)
class _ReactionDiff:
    added: list[str]
    new_set: list[str]


def _diff_reactions(
    old: list[str], new: list[str]
) -> _ReactionDiff:
    old_set = set(old)
    added = [e for e in new if e not in old_set]
    return _ReactionDiff(added=added, new_set=list(new))


def _format_context_message(row: TelegramMessage, marker: str = "") -> str:
    ts = row.telegram_date.strftime("%Y-%m-%d %H:%M")
    sender = row.sender_display_name or "anon"
    body = (row.clean_text or row.text or row.caption or "").strip()
    if not body:
        body = f"({row.content_type})"
    body = body.replace("\n", " ")
    prefix = f"{marker} " if marker else ""
    return f"{prefix}[{ts}] {sender}: {body}"


def _build_reactions_summary(reactions: dict[str, int]) -> str:
    if not reactions:
        return "(none)"
    return ", ".join(f"{emoji} x{count}" for emoji, count in reactions.items())


class ReactionService:
    """Persists user reactions and triggers a probabilistic LLM reply when a
    message crosses the configured distinct-user threshold.
    """

    def __init__(
        self,
        settings: Settings,
        config: RuntimeReactionsConfig,
        runtime_config: RuntimeContextConfig,
        client: OpenRouterClient,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._config = config
        self._runtime_config = runtime_config
        self._client = client
        self._rng = rng or random.Random()
        # In-memory cooldown — cleared on restart, which is fine for
        # avoiding short bursts of duplicate replies.
        self._recent_replies: dict[tuple[int, int], float] = {}

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def handle_reaction_update(
        self,
        session: AsyncSession,
        client: TelegramClientProtocol,
        event: TgReactionUpdate,
    ) -> None:
        if not self._config.enabled:
            return
        if event.user is None:
            # Anonymous channel reactions can't be attributed; skip.
            return
        if event.user.is_bot:
            # Don't count the bot's own reaction back into the threshold.
            return

        chat_id = event.chat_id
        message_id = event.message_id
        user = event.user

        old_emojis = list(event.old_emojis)
        new_emojis = list(event.new_emojis)
        if old_emojis == new_emojis:
            return

        await upsert_user(
            session,
            UserInput(
                id=user.id,
                is_bot=bool(user.is_bot),
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            ),
        )
        await replace_user_reactions(
            session,
            chat_id=chat_id,
            message_id=message_id,
            user_id=user.id,
            new_emojis=new_emojis,
        )

        diff = _diff_reactions(old_emojis, new_emojis)
        # Only newly-added qualifying emojis can cross the threshold from this
        # specific update, so use them to gate evaluation.
        added_qualifying = [
            e for e in diff.added if self._config.emoji_is_trigger(e)
        ]
        if not added_qualifying:
            return

        await session.flush()

        triggers = list(self._config.trigger_emojis)
        distinct = await count_distinct_reaction_users(
            session,
            chat_id=chat_id,
            message_id=message_id,
            only_emojis=triggers if triggers else None,
        )
        if distinct < self._config.min_distinct_users:
            return

        if self._is_in_cooldown(chat_id, message_id):
            log.debug(
                "reactions.cooldown_active",
                chat_id=chat_id,
                message_id=message_id,
            )
            return

        roll = self._rng.random()
        if roll >= self._config.reply_chance:
            log.debug(
                "reactions.dice_lost",
                chat_id=chat_id,
                message_id=message_id,
                roll=roll,
                chance=self._config.reply_chance,
            )
            return

        target = await fetch_message_by_chat_message_id(
            session,
            chat_id=chat_id,
            message_id=message_id,
        )
        if target is None:
            log.info(
                "reactions.target_not_ingested",
                chat_id=chat_id,
                message_id=message_id,
            )
            return

        before_rows, after_rows = await fetch_messages_around(
            session,
            chat_id=chat_id,
            message_thread_id=target.message_thread_id,
            target_telegram_date=target.telegram_date,
            target_message_id=target.message_id,
            before=self._config.context_before,
            after=self._config.context_after,
        )

        # Mark cooldown before the LLM call so a slow LLM response doesn't
        # let parallel reaction bursts produce duplicate replies.
        self._mark_replied(chat_id, message_id)

        reactions_summary = _build_reactions_summary(
            self._summarize_emojis(new_emojis, old_emojis)
        )
        context_text = self._build_context_text(before_rows, target, after_rows)
        user_prompt = REACTION_USER_PROMPT_TEMPLATE.format(
            context_text=context_text,
            reactions_summary=reactions_summary,
        )

        if self._settings.log_prompts:
            log.info("reactions.prompt", prompt=user_prompt)

        success = False
        error: str | None = None
        response: LlmResponse | None = None
        try:
            response = await self._client.complete(
                REACTION_SYSTEM_PROMPT, user_prompt
            )
            success = True
        except OpenRouterError as exc:
            error = str(exc)
            log.error(
                "reactions.llm_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=error,
            )
            return
        finally:
            await record_llm_interaction(
                session,
                chat_id=chat_id,
                message_thread_id=target.message_thread_id,
                request_message_id=message_id,
                command_name="reaction_reply",
                model=self._settings.openrouter_model,
                prompt_tokens_estimate=response.prompt_tokens if response else None,
                completion_tokens_estimate=(
                    response.completion_tokens if response else None
                ),
                latency_ms=response.latency_ms if response else None,
                success=success,
                error=error,
            )

        if not success or response is None:
            return

        await self._send_reply(
            client=client,
            chat_id=chat_id,
            message_thread_id=target.message_thread_id,
            target_message_id=message_id,
            text=response.text,
        )
        await self._set_bot_reaction(
            client=client,
            chat_id=chat_id,
            message_id=message_id,
        )

    def _summarize_emojis(
        self, new_emojis: list[str], old_emojis: list[str]
    ) -> dict[str, int]:
        # We only have the latest user's full set here; per-message totals
        # require a query. For the prompt we just hint with what this user
        # currently has plus what was newly added.
        seen: dict[str, int] = {}
        for e in new_emojis:
            seen[e] = seen.get(e, 0) + 1
        for e in old_emojis:
            if e not in seen:
                seen[e] = 0
        return seen

    def _build_context_text(
        self,
        before_rows: list[TelegramMessage],
        target: TelegramMessage,
        after_rows: list[TelegramMessage],
    ) -> str:
        lines: list[str] = []
        for row in before_rows:
            lines.append(_format_context_message(row))
        lines.append(_format_context_message(target, marker=">>>"))
        for row in after_rows:
            lines.append(_format_context_message(row))
        return "\n".join(lines)

    async def _send_reply(
        self,
        client: TelegramClientProtocol,
        chat_id: int,
        message_thread_id: int,
        target_message_id: int,
        text: str,
    ) -> None:
        chunks = split_for_telegram(text, self._runtime_config.max_reply_chars)
        for index, chunk in enumerate(chunks):
            kwargs: dict = {"chat_id": chat_id, "text": chunk}
            if message_thread_id:
                kwargs["message_thread_id"] = message_thread_id
            if index == 0:
                kwargs["reply_to_message_id"] = target_message_id
            try:
                await client.send_message(**kwargs)
            except Exception as exc:  # network / Telegram errors
                log.error(
                    "reactions.send_failed",
                    chat_id=chat_id,
                    message_id=target_message_id,
                    error=str(exc),
                )
                return

    async def _set_bot_reaction(
        self, client: TelegramClientProtocol, chat_id: int, message_id: int
    ) -> None:
        emoji = self._config.bot_emoji
        if not emoji:
            return
        try:
            await client.set_reaction(chat_id=chat_id, message_id=message_id, emoji=emoji)
        except Exception as exc:
            log.warning(
                "reactions.set_bot_reaction_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )

    def _is_in_cooldown(self, chat_id: int, message_id: int) -> bool:
        cooldown = self._config.cooldown_seconds
        if cooldown <= 0:
            return False
        last = self._recent_replies.get((chat_id, message_id))
        if last is None:
            return False
        return (time.monotonic() - last) < cooldown

    def _mark_replied(self, chat_id: int, message_id: int) -> None:
        self._recent_replies[(chat_id, message_id)] = time.monotonic()
        # Light GC: drop entries older than 24h or whenever the dict gets large.
        if len(self._recent_replies) > 4096:
            cutoff = time.monotonic() - 24 * 3600
            self._recent_replies = {
                key: ts
                for key, ts in self._recent_replies.items()
                if ts >= cutoff
            }
