from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import (
    fetch_messages_for_tldr,
    get_thread_titles,
    record_llm_interaction,
)
from app.llm.openrouter_client import LlmResponse, OpenRouterClient, OpenRouterError
from app.llm.prompts import TLDR_SYSTEM_PROMPT, build_tldr_user_prompt
from app.logging_config import get_logger
from app.services.thread_activity import ThreadActivity, detect_activity_periods
from app.utils.time import parse_lookback

log = get_logger(__name__)


@dataclass
class TldrRequest:
    scope: str  # "other" | "all" | "thread"
    lookback_hours: int
    scope_description: str


def parse_tldr_args(args: str, default_lookback_hours: int) -> TldrRequest:
    """Parse `/tldr [thread|all|<duration>]`.

    The user can combine duration with scope, e.g. `/tldr thread 24h`.
    """
    scope = "other"
    lookback = default_lookback_hours
    for token in (args or "").split():
        token_low = token.lower()
        if token_low == "all":
            scope = "all"
            continue
        if token_low == "thread":
            scope = "thread"
            continue
        parsed = parse_lookback(token_low)
        if parsed is not None:
            lookback = parsed
    desc_map = {
        "other": "Recent activity from other threads in this chat (current thread excluded).",
        "all": "Recent activity across all threads in this chat (including current).",
        "thread": "Recent activity from the current thread only.",
    }
    return TldrRequest(
        scope=scope,
        lookback_hours=lookback,
        scope_description=f"{desc_map[scope]} Lookback: ~{lookback}h.",
    )


def _format_activity(activities: list[ThreadActivity], max_threads: int) -> str:
    if not activities:
        return ""
    blocks: list[str] = []
    for activity in activities[:max_threads]:
        title = activity.title or f"thread {activity.message_thread_id}"
        header = f"# {title} (thread_id={activity.message_thread_id})"
        lines = [header]
        for msg in activity.messages:
            ts = msg.telegram_date.strftime("%Y-%m-%d %H:%M")
            sender = msg.sender_display_name or "anon"
            body = (msg.clean_text or msg.text or msg.caption or "").replace("\n", " ").strip()
            if not body:
                continue
            lines.append(f"[{ts}] {sender}: {body}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class TldrService:
    def __init__(
        self,
        settings: Settings,
        client: OpenRouterClient,
    ) -> None:
        self._settings = settings
        self._client = client

    async def summarize(
        self,
        session: AsyncSession,
        chat_id: int,
        message_thread_id: int,
        request: TldrRequest,
        request_message_id: int | None,
    ) -> tuple[LlmResponse | None, str | None]:
        """Returns (LlmResponse, None) on success, or (None, friendly_message) when no input.

        Raises OpenRouterError on LLM failures.
        """
        only_thread_id: int | None = None
        exclude_thread_id: int | None = None
        if request.scope == "thread":
            only_thread_id = message_thread_id
        elif request.scope == "other":
            exclude_thread_id = message_thread_id
        # scope == "all" -> neither filter

        messages = await fetch_messages_for_tldr(
            session,
            chat_id=chat_id,
            lookback_hours=request.lookback_hours,
            exclude_thread_id=exclude_thread_id,
            only_thread_id=only_thread_id,
        )
        thread_titles = await get_thread_titles(session, chat_id)
        activities = detect_activity_periods(
            messages,
            activity_gap_minutes=self._settings.tldr_activity_gap_minutes,
            max_messages_per_thread=self._settings.tldr_max_messages_per_thread,
            thread_titles=thread_titles,
        )
        context_text = _format_activity(activities, self._settings.tldr_max_threads)
        # Trim to max_context_chars budget.
        if len(context_text) > self._settings.max_context_chars:
            lines = context_text.split("\n")
            while lines and sum(len(line) + 1 for line in lines) > self._settings.max_context_chars:
                lines.pop(0)
            context_text = "\n".join(lines)

        if not context_text.strip():
            return None, "No meaningful recent activity found in other threads."

        user_prompt = build_tldr_user_prompt(request.scope_description, context_text)
        if self._settings.log_prompts:
            log.info("tldr.prompt", prompt=user_prompt)

        success = False
        error: str | None = None
        response: LlmResponse | None = None
        try:
            response = await self._client.complete(TLDR_SYSTEM_PROMPT, user_prompt)
            success = True
            return response, None
        except OpenRouterError as exc:
            error = str(exc)
            raise
        finally:
            await record_llm_interaction(
                session,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                request_message_id=request_message_id,
                command_name="tldr",
                model=self._settings.openrouter_model,
                prompt_tokens_estimate=response.prompt_tokens if response else None,
                completion_tokens_estimate=response.completion_tokens if response else None,
                latency_ms=response.latency_ms if response else None,
                success=success,
                error=error,
            )
