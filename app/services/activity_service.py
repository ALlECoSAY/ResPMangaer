from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatting import split_for_telegram
from app.config import Settings
from app.db.models import TelegramMessage
from app.db.repositories import (
    ActivityReplyState,
    fetch_last_messages,
    fetch_recent_message_count,
    get_activity_reply_state,
    record_llm_interaction,
    upsert_activity_reply_state,
)
from app.llm.activity_config import RuntimeActivityConfig
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import get_logger
from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgMessage
from app.utils.telegram import safe_sender_label, strip_notification_mentions

log = get_logger(__name__)


class _LlmResponse(Protocol):
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


class _LlmClient(Protocol):
    async def complete(
        self, system_prompt: str, user_prompt: str
    ) -> _LlmResponse:
        ...


ACTIVITY_SYSTEM_PROMPT = """\
You are a regular participant in a Telegram group chat. The chat has been
lively recently, and you are chiming in naturally.

Your job:
- Reply to the marked message with a single short conversational comment.
- Match the tone and language of the surrounding chat.
- Keep it under 2 short sentences. No bullet lists, no headers, no markdown.
- Do not announce that you are a bot and do not explain why you are replying.
- Stay relevant to the messages shown. Be specific, not generic.
- Do not start with "Reply:" or any prefix.
- Never write @username mentions. Refer to people by their plain display name
  (no leading "@") so the bot never triggers Telegram notifications.
"""


ACTIVITY_USER_PROMPT_TEMPLATE = """\
Recent chat context (chronological).
The line marked with >>> is the message you should reply to.

{context_text}

Write a single short, in-character reply to the >>> message. Output only the
reply text, nothing else."""


FOLLOW_UP_SYSTEM_PROMPT = """\
You are continuing a Telegram group chat conversation after someone addressed
your previous message.

Your job:
- Answer the latest marked user message naturally and briefly.
- Keep it under 2 short sentences. No bullet lists, no headers, no markdown.
- Do not announce that you are a bot.
- Stay grounded in the recent chat context.
- Do not start with "Reply:" or any prefix.
- Never write @username mentions. Refer to people by their plain display name
  (no leading "@") so the bot never triggers Telegram notifications.
"""


def _message_body(row: TelegramMessage) -> str:
    return (row.clean_text or row.text or row.caption or "").strip()


def _format_activity_context_message(
    row: TelegramMessage, marker: str = ""
) -> str:
    ts = row.telegram_date.strftime("%Y-%m-%d %H:%M")
    sender = safe_sender_label(row.sender_display_name)
    body = _message_body(row)
    if not body:
        body = f"({row.content_type})"
    body = body.replace("\n", " ")
    prefix = f"{marker} " if marker else ""
    return f"{prefix}[{ts}] {sender}: {body}"


class ActivityService:
    """Triggers probabilistic LLM replies during active chat bursts."""

    def __init__(
        self,
        settings: Settings,
        config: RuntimeActivityConfig,
        runtime_config: RuntimeContextConfig,
        client: _LlmClient,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._config = config
        self._runtime_config = runtime_config
        self._client = client
        self._rng = rng or random.Random()
        self._recent_replies: dict[tuple[int, int], float] = {}

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def maybe_trigger_random_reply(
        self,
        session: AsyncSession,
        client: TelegramClientProtocol,
        *,
        chat_id: int,
        message_thread_id: int,
        observed_count: int | None = None,
    ) -> None:
        if not self._config.enabled:
            return

        now = datetime.now(UTC)
        if not self._config.hour_is_allowed(now.hour):
            log.debug("activity.hour_not_allowed", chat_id=chat_id, hour=now.hour)
            return

        state = await get_activity_reply_state(
            session, chat_id=chat_id, message_thread_id=message_thread_id
        )
        if self._is_in_cooldown(chat_id, message_thread_id, state, now):
            log.debug(
                "activity.cooldown_active",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )
            return

        since = now - timedelta(minutes=self._config.window_minutes)
        message_count = observed_count
        if message_count is None:
            message_count = await fetch_recent_message_count(
                session,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                since=since,
            )
        if message_count < self._config.min_messages:
            log.debug(
                "activity.threshold_not_met",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                count=message_count,
                threshold=self._config.min_messages,
            )
            return

        log.info(
            "activity.threshold_met",
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            count=message_count,
        )

        roll = self._rng.random()
        if roll >= self._config.reply_chance:
            log.info(
                "activity.dice_lost",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                roll=roll,
                chance=self._config.reply_chance,
            )
            return

        rows = await fetch_last_messages(
            session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            limit=self._config.max_context_messages,
            since=since,
        )
        target = self._select_target(rows)
        if target is None:
            log.info(
                "activity.no_target",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )
            return

        self._mark_replied(chat_id, message_thread_id)
        await self._reply_to_row(
            session=session,
            client=client,
            rows=rows,
            target=target,
            command_name="activity_reply",
            system_prompt=ACTIVITY_SYSTEM_PROMPT,
        )

    async def handle_incoming_message(
        self,
        session: AsyncSession,
        client: TelegramClientProtocol,
        message: TgMessage,
    ) -> bool:
        """Handle direct replies and near follow-ups to activity replies.

        Returns True when a reply was sent or attempted.
        """
        if not self._config.enabled:
            return False
        if message.from_user is not None and message.from_user.is_bot:
            return False

        chat_id = message.chat.id
        message_thread_id = int(message.message_thread_id or 0)
        state = await get_activity_reply_state(
            session, chat_id=chat_id, message_thread_id=message_thread_id
        )
        if state is None or state.last_bot_message_id is None:
            return False

        direct = message.reply_to_message_id == state.last_bot_message_id
        if direct:
            chance = self._config.reply_on_direct_reply_chance
            reason = "direct_reply"
            command_name = "activity_direct_reply"
        else:
            if not self._is_follow_up(message, state):
                return False
            chance = self._config.reply_on_follow_up_chance
            reason = "follow_up"
            command_name = "activity_follow_up_reply"

        roll = self._rng.random()
        if roll >= chance:
            log.info(
                "activity.follow_up_dice_lost",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                message_id=message.message_id,
                reason=reason,
                roll=roll,
                chance=chance,
            )
            return False

        rows = await fetch_last_messages(
            session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            limit=self._config.max_context_messages,
        )
        target = self._find_row(rows, message.message_id)
        if target is None:
            log.info(
                "activity.follow_up_target_not_ingested",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                message_id=message.message_id,
            )
            return False

        self._mark_replied(chat_id, message_thread_id)
        await self._reply_to_row(
            session=session,
            client=client,
            rows=rows,
            target=target,
            command_name=command_name,
            system_prompt=FOLLOW_UP_SYSTEM_PROMPT,
        )
        return True

    def _is_follow_up(self, message: TgMessage, state: ActivityReplyState) -> bool:
        if message.reply_to_message_id is not None:
            return False
        if state.last_reply_at is None or state.last_bot_message_id is None:
            return False
        last_reply_at = self._aware(state.last_reply_at)
        message_date = self._aware(message.date)
        age = (message_date - last_reply_at).total_seconds()
        if age < 0 or age > self._config.follow_up_window_seconds:
            return False
        return message.message_id > state.last_bot_message_id

    async def _reply_to_row(
        self,
        *,
        session: AsyncSession,
        client: TelegramClientProtocol,
        rows: list[TelegramMessage],
        target: TelegramMessage,
        command_name: str,
        system_prompt: str,
    ) -> None:
        context_text = self._build_context_text(rows, target)
        user_prompt = ACTIVITY_USER_PROMPT_TEMPLATE.format(context_text=context_text)

        if self._settings.log_prompts:
            log.info("activity.prompt", prompt=user_prompt)

        success = False
        error: str | None = None
        response: _LlmResponse | None = None
        try:
            response = await self._client.complete(system_prompt, user_prompt)
            success = True
        except Exception as exc:
            error = str(exc)
            log.error(
                "activity.llm_failed",
                chat_id=target.chat_id,
                message_thread_id=target.message_thread_id,
                message_id=target.message_id,
                error=error,
            )
            return
        finally:
            await record_llm_interaction(
                session,
                chat_id=target.chat_id,
                message_thread_id=target.message_thread_id,
                request_message_id=target.message_id,
                command_name=command_name,
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

        sent = await self._send_reply(
            client=client,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            target_message_id=target.message_id,
            text=response.text,
        )
        if sent is None:
            return

        now = datetime.now(UTC)
        await upsert_activity_reply_state(
            session,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            last_reply_at=now,
            last_bot_message_id=sent.message_id,
            last_target_message_id=target.message_id,
        )
        log.info(
            "activity.reply_sent",
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            target_message_id=target.message_id,
            bot_message_id=sent.message_id,
        )

    def _build_context_text(
        self, rows: list[TelegramMessage], target: TelegramMessage
    ) -> str:
        lines: list[str] = []
        for row in rows:
            marker = ">>>" if row.message_id == target.message_id else ""
            lines.append(_format_activity_context_message(row, marker=marker))
        return "\n".join(lines)

    def _select_target(self, rows: list[TelegramMessage]) -> TelegramMessage | None:
        candidates = [row for row in rows if not row.is_bot_message and _message_body(row)]
        if not candidates:
            return rows[-1] if rows else None

        tail = candidates[-min(12, len(candidates)) :]
        question_rows = [row for row in tail if "?" in _message_body(row)]
        if question_rows:
            return question_rows[-1]

        substantive = [row for row in tail if len(_message_body(row)) >= 35]
        if substantive:
            return max(substantive, key=lambda row: len(_message_body(row)))

        return tail[-1]

    @staticmethod
    def _find_row(
        rows: list[TelegramMessage], message_id: int
    ) -> TelegramMessage | None:
        for row in rows:
            if row.message_id == message_id:
                return row
        return None

    async def _send_reply(
        self,
        client: TelegramClientProtocol,
        chat_id: int,
        message_thread_id: int,
        target_message_id: int,
        text: str,
    ) -> TgMessage | None:
        text = strip_notification_mentions(text)
        chunks = split_for_telegram(text, self._runtime_config.max_reply_chars)
        first_sent: TgMessage | None = None
        for index, chunk in enumerate(chunks):
            kwargs: dict = {"chat_id": chat_id, "text": chunk}
            if message_thread_id:
                kwargs["message_thread_id"] = message_thread_id
            if index == 0:
                kwargs["reply_to_message_id"] = target_message_id
            try:
                sent = await client.send_message(**kwargs)
            except Exception as exc:
                log.error(
                    "activity.send_failed",
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    message_id=target_message_id,
                    error=str(exc),
                )
                return first_sent
            if index == 0:
                first_sent = sent
        return first_sent

    def _is_in_cooldown(
        self,
        chat_id: int,
        message_thread_id: int,
        state: ActivityReplyState | None,
        now: datetime,
    ) -> bool:
        cooldown = self._config.cooldown_seconds
        if cooldown <= 0:
            return False
        key = (chat_id, message_thread_id)
        last_reply = self._recent_replies.get(key)
        if last_reply is not None and time.time() - last_reply < cooldown:
            return True
        if state is None or state.last_reply_at is None:
            return False
        return (now - self._aware(state.last_reply_at)).total_seconds() < cooldown

    def _mark_replied(self, chat_id: int, message_thread_id: int) -> None:
        self._recent_replies[(chat_id, message_thread_id)] = time.time()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
