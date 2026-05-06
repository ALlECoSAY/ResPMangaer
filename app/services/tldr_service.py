from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import (
    fetch_messages_for_tldr,
    get_thread_titles,
    record_llm_interaction,
)
from app.llm.openrouter_client import LlmResponse, OpenRouterClient, OpenRouterError
from app.llm.prompts import TLDR_SYSTEM_PROMPT, build_tldr_user_prompt
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import get_logger
from app.services.thread_activity import ThreadActivity, detect_activity_periods
from app.utils.time import parse_lookback

log = get_logger(__name__)

TldrScope = Literal["thread", "all"]

_SCOPE_DESCRIPTIONS: dict[TldrScope, str] = {
    "thread": "Recent activity from the current thread only.",
    "all": "Recent activity across all threads in this chat.",
}


@dataclass
class TldrRequest:
    scope: TldrScope
    lookback_hours: int
    scope_description: str


def parse_tldr_lookback(args: str, default_lookback_hours: int) -> int:
    """Parse a lookback duration token (e.g. `24h`, `2d`) from `/tldr` args.

    Unknown tokens are ignored; falls back to ``default_lookback_hours``.
    """
    for token in (args or "").split():
        parsed = parse_lookback(token.lower())
        if parsed is not None:
            return parsed
    return default_lookback_hours


def make_tldr_request(scope: TldrScope, lookback_hours: int) -> TldrRequest:
    desc = _SCOPE_DESCRIPTIONS[scope]
    return TldrRequest(
        scope=scope,
        lookback_hours=lookback_hours,
        scope_description=f"{desc} Lookback: ~{lookback_hours}h.",
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
        runtime_config: RuntimeContextConfig,
    ) -> None:
        self._settings = settings
        self._client = client
        self._runtime_config = runtime_config

    def _limits_for(self, scope: TldrScope) -> tuple[int, int]:
        if scope == "thread":
            return (
                self._runtime_config.tldr_max_threads,
                self._runtime_config.tldr_max_messages_per_thread,
            )
        return (
            self._runtime_config.tldr_all_max_threads,
            self._runtime_config.tldr_all_max_messages_per_thread,
        )

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
        if request.scope == "thread":
            only_thread_id = message_thread_id

        max_threads, max_messages_per_thread = self._limits_for(request.scope)

        messages = await fetch_messages_for_tldr(
            session,
            chat_id=chat_id,
            lookback_hours=request.lookback_hours,
            exclude_thread_id=None,
            only_thread_id=only_thread_id,
        )
        thread_titles = await get_thread_titles(session, chat_id)
        activities = detect_activity_periods(
            messages,
            activity_gap_minutes=self._runtime_config.tldr_activity_gap_minutes,
            max_messages_per_thread=max_messages_per_thread,
            thread_titles=thread_titles,
        )
        context_text = _format_activity(activities, max_threads)
        # Trim to max_context_chars budget.
        max_context_chars = self._runtime_config.max_context_chars
        if len(context_text) > max_context_chars:
            lines = context_text.split("\n")
            while lines and sum(len(line) + 1 for line in lines) > max_context_chars:
                lines.pop(0)
            context_text = "\n".join(lines)

        if not context_text.strip():
            return None, "No meaningful recent activity found."

        user_prompt = build_tldr_user_prompt(request.scope_description, context_text)
        if self._settings.log_prompts:
            log.info("tldr.prompt", scope=request.scope, prompt=user_prompt)

        success = False
        error: str | None = None
        response: LlmResponse | None = None
        command_name = "tldr_all" if request.scope == "all" else "tldr"
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
                command_name=command_name,
                model=self._settings.openrouter_model,
                prompt_tokens_estimate=response.prompt_tokens if response else None,
                completion_tokens_estimate=response.completion_tokens if response else None,
                latency_ms=response.latency_ms if response else None,
                success=success,
                error=error,
            )
