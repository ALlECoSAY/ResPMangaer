from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import TelegramMessage
from app.db.repositories import (
    ChatMemoryProfile,
    ThreadMemoryProfile,
    UserMemoryProfile,
    delete_all_memory_for_chat,
    delete_chat_memory,
    delete_thread_memory,
    delete_user_memory,
    fetch_messages_for_memory_update,
    get_chat_memory,
    get_thread_memory,
    get_user_memory,
    record_llm_interaction,
    upsert_chat_memory,
    upsert_thread_memory,
    upsert_user_memory,
)
from app.llm.memory_config import RuntimeMemoryConfig
from app.llm.prompts import MEMORY_SYSTEM_PROMPT, build_memory_user_prompt
from app.logging_config import get_logger
from app.utils.telegram import safe_sender_label

if TYPE_CHECKING:
    from app.llm.openrouter_client import OpenRouterClient

log = get_logger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class MemoryUpdateError(Exception):
    """Raised when memory refresh receives unusable LLM output."""


@dataclass(frozen=True)
class MemoryRefreshResult:
    updated: bool
    new_message_count: int = 0
    latest_message_id: int | None = None
    skipped_reason: str | None = None


def trim_text(text: Any, max_chars: int) -> str:
    value = str(text or "").strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    text = str(value).strip()
    return [text] if text else []


def merge_json_list(
    existing: Any,
    incoming: Any,
    *,
    max_items: int = 80,
) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*ensure_list(existing), *ensure_list(incoming)]:
        if item in ("", None):
            continue
        key = json.dumps(item, sort_keys=True, ensure_ascii=False).lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    if max_items > 0 and len(merged) > max_items:
        return merged[-max_items:]
    return merged


def parse_memory_json(text: str) -> dict[str, Any]:
    stripped = _JSON_FENCE_RE.sub("", text.strip()).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        stripped = stripped[start : end + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MemoryUpdateError("Memory model returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise MemoryUpdateError("Memory model returned a non-object JSON value")
    return data


def should_apply_user_update(
    update: dict[str, Any],
    *,
    min_evidence_messages: int,
) -> bool:
    evidence = _coerce_int_list(update.get("evidence_message_ids"))
    if len(evidence) >= min_evidence_messages:
        return True
    preferences = ensure_list(update.get("stated_preferences"))
    return bool(preferences and evidence)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value: Any) -> list[int]:
    ids: list[int] = []
    for item in ensure_list(value):
        parsed = _coerce_int(item)
        if parsed is not None:
            ids.append(parsed)
    return ids


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _json_contains(item: Any, needle: str) -> bool:
    text = json.dumps(item, ensure_ascii=False, sort_keys=True).lower()
    return needle in text


def _remove_fact_from_json_list(value: Any, fact_text: str) -> list[Any]:
    needle = fact_text.strip().lower()
    if not needle:
        return ensure_list(value)
    return [item for item in ensure_list(value) if not _json_contains(item, needle)]


def _remove_fact_from_text(value: str | None, fact_text: str) -> str | None:
    if not value:
        return value
    needle = fact_text.strip().lower()
    if not needle:
        return value
    lines = value.splitlines()
    filtered = [line for line in lines if needle not in line.lower()]
    if len(filtered) != len(lines):
        return "\n".join(filtered).strip()
    return value.replace(fact_text, "").strip()


def _format_json_list(title: str, values: Any, max_items: int = 10) -> list[str]:
    items = ensure_list(values)
    if not items:
        return []
    lines = [f"{title}:"]
    for item in items[:max_items]:
        if isinstance(item, str):
            lines.append(f"- {item}")
        else:
            lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    return lines


def _message_body(message: TelegramMessage) -> str:
    return (message.clean_text or message.text or message.caption or "").strip()


def _format_messages_for_prompt(messages: list[TelegramMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        body = _message_body(message).replace("\n", " ")
        if not body:
            continue
        body = trim_text(body, 1200)
        sender = safe_sender_label(message.sender_display_name)
        sender_user_id = message.sender_user_id if message.sender_user_id is not None else "unknown"
        ts = message.telegram_date.strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"id={message.message_id} time={ts} "
            f"sender_id={sender_user_id} sender={sender}: {body}"
        )
    return "\n".join(lines)


def _format_chat_memory_for_prompt(memory: ChatMemoryProfile | None) -> str:
    if memory is None:
        return ""
    payload = {
        "summary": memory.summary or "",
        "stable_facts": ensure_list(memory.stable_facts),
        "current_projects": ensure_list(memory.current_projects),
        "decisions": ensure_list(memory.decisions),
        "open_questions": ensure_list(memory.open_questions),
    }
    return json.dumps(payload, ensure_ascii=False)


def _format_thread_memory_for_prompt(memory: ThreadMemoryProfile | None) -> str:
    if memory is None:
        return ""
    payload = {
        "title": memory.title or "",
        "summary": memory.summary or "",
        "decisions": ensure_list(memory.decisions),
        "action_items": ensure_list(memory.action_items),
        "open_questions": ensure_list(memory.open_questions),
        "key_participants": ensure_list(memory.key_participants),
    }
    return json.dumps(payload, ensure_ascii=False)


class MemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        config: RuntimeMemoryConfig,
        client: OpenRouterClient,
    ) -> None:
        self._settings = settings
        self._config = config
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def refresh_thread(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
        request_message_id: int | None = None,
        force: bool = False,
        skip_threshold: bool = False,
    ) -> MemoryRefreshResult:
        if not self._config.enabled:
            return MemoryRefreshResult(updated=False, skipped_reason="disabled")

        thread_memory = await get_thread_memory(session, chat_id, message_thread_id)
        after_message_id = None if force else (
            thread_memory.source_until_message_id if thread_memory else None
        )
        messages = await fetch_messages_for_memory_update(
            session,
            chat_id,
            message_thread_id,
            after_message_id=after_message_id,
            limit=self._config.max_messages_per_update,
            latest=force,
        )
        if not messages:
            return MemoryRefreshResult(
                updated=False,
                skipped_reason="no_new_messages",
            )
        if (
            not force
            and not skip_threshold
            and not self._should_refresh(thread_memory, messages)
        ):
            return MemoryRefreshResult(
                updated=False,
                new_message_count=len(messages),
                latest_message_id=int(messages[-1].message_id),
                skipped_reason="below_threshold",
            )

        chat_memory = await get_chat_memory(session, chat_id)
        prompt = build_memory_user_prompt(
            chat_memory=_format_chat_memory_for_prompt(chat_memory),
            thread_memory=_format_thread_memory_for_prompt(thread_memory),
            messages=_format_messages_for_prompt(messages),
            max_chat_chars=self._config.max_chat_memory_chars,
            max_thread_chars=self._config.max_thread_memory_chars,
            max_user_chars=self._config.max_user_memory_chars,
        )
        if self._settings.log_prompts:
            log.info("memory.prompt", chat_id=chat_id, thread_id=message_thread_id, prompt=prompt)

        response = None
        success = False
        error: str | None = None
        try:
            response = await self._client.complete(
                MEMORY_SYSTEM_PROMPT,
                prompt,
                temperature=0.1,
                timeout=90.0,
                model=self._config.summarize_model,
            )
            payload = parse_memory_json(response.text)
            await self._apply_payload(
                session,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                chat_memory=chat_memory,
                thread_memory=thread_memory,
                messages=messages,
                payload=payload,
            )
            success = True
            return MemoryRefreshResult(
                updated=True,
                new_message_count=len(messages),
                latest_message_id=int(messages[-1].message_id),
            )
        except MemoryUpdateError as exc:
            error = str(exc)
            raise
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            await record_llm_interaction(
                session,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                request_message_id=request_message_id,
                command_name="memory_refresh",
                model=response.model if response else self._config.summarize_model,
                prompt_tokens_estimate=response.prompt_tokens if response else None,
                completion_tokens_estimate=(
                    response.completion_tokens if response else None
                ),
                latency_ms=response.latency_ms if response else None,
                success=success,
                error=error,
            )

    def _should_refresh(
        self,
        thread_memory: ThreadMemoryProfile | None,
        messages: list[TelegramMessage],
    ) -> bool:
        if len(messages) >= self._config.update_min_new_messages:
            return True
        joined = "\n".join(_message_body(message).lower() for message in messages)
        if any(keyword.lower() in joined for keyword in self._config.trigger_keywords):
            return True
        if thread_memory is None or thread_memory.updated_at is None:
            return False
        stale_after = timedelta(minutes=self._config.update_min_interval_minutes)
        age = datetime.now(UTC) - thread_memory.updated_at
        return age >= stale_after

    async def _apply_payload(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
        chat_memory: ChatMemoryProfile | None,
        thread_memory: ThreadMemoryProfile | None,
        messages: list[TelegramMessage],
        payload: dict[str, Any],
    ) -> None:
        latest = messages[-1]
        latest_message_id = int(latest.message_id)
        latest_date = latest.telegram_date

        await upsert_chat_memory(
            session,
            chat_id=chat_id,
            summary=trim_text(
                payload.get("chat_summary")
                or (chat_memory.summary if chat_memory else ""),
                self._config.max_chat_memory_chars,
            ),
            stable_facts=merge_json_list(
                chat_memory.stable_facts if chat_memory else [],
                payload.get("new_stable_facts"),
                max_items=80,
            ),
            current_projects=merge_json_list(
                chat_memory.current_projects if chat_memory else [],
                payload.get("new_current_projects"),
                max_items=80,
            ),
            decisions=merge_json_list(
                chat_memory.decisions if chat_memory else [],
                payload.get("new_decisions"),
                max_items=100,
            ),
            open_questions=merge_json_list(
                chat_memory.open_questions if chat_memory else [],
                payload.get("new_open_questions"),
                max_items=100,
            ),
            source_until_message_id=latest_message_id,
            source_until_date=latest_date,
        )

        thread_title = payload.get("thread_title")
        if thread_title is not None:
            thread_title = trim_text(thread_title, 160) or None
        await upsert_thread_memory(
            session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            title=thread_title or (thread_memory.title if thread_memory else None),
            summary=trim_text(
                payload.get("thread_summary")
                or (thread_memory.summary if thread_memory else ""),
                self._config.max_thread_memory_chars,
            ),
            decisions=merge_json_list(
                thread_memory.decisions if thread_memory else [],
                payload.get("new_decisions"),
                max_items=80,
            ),
            action_items=merge_json_list(
                thread_memory.action_items if thread_memory else [],
                payload.get("new_action_items"),
                max_items=80,
            ),
            open_questions=merge_json_list(
                thread_memory.open_questions if thread_memory else [],
                payload.get("new_open_questions"),
                max_items=80,
            ),
            key_participants=merge_json_list(
                thread_memory.key_participants if thread_memory else [],
                payload.get("key_participants"),
                max_items=50,
            ),
            source_until_message_id=latest_message_id,
            source_until_date=latest_date,
        )

        if self._config.user_profiles_enabled:
            await self._apply_user_updates(
                session,
                chat_id=chat_id,
                latest_message_id=latest_message_id,
                updates=ensure_list(payload.get("user_profile_updates")),
            )

    async def _apply_user_updates(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        latest_message_id: int,
        updates: list[Any],
    ) -> None:
        for raw_update in updates:
            if not isinstance(raw_update, dict):
                continue
            user_id = _coerce_int(raw_update.get("user_id"))
            if user_id is None:
                continue
            if not should_apply_user_update(
                raw_update,
                min_evidence_messages=(
                    self._config.user_profile_min_evidence_messages
                ),
            ):
                continue
            existing = await get_user_memory(session, chat_id, user_id)
            await self._upsert_merged_user_profile(
                session,
                chat_id=chat_id,
                user_id=user_id,
                existing=existing,
                update=raw_update,
                latest_message_id=latest_message_id,
            )

    async def _upsert_merged_user_profile(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        user_id: int,
        existing: UserMemoryProfile | None,
        update: dict[str, Any],
        latest_message_id: int,
    ) -> None:
        existing_confidence = existing.confidence if existing and existing.confidence else 0.0
        update_confidence = _coerce_float(update.get("confidence"), existing_confidence)
        profile_summary = trim_text(
            update.get("profile_summary")
            or (existing.profile_summary if existing else ""),
            self._config.max_user_memory_chars,
        )
        await upsert_user_memory(
            session,
            chat_id=chat_id,
            user_id=user_id,
            display_name=trim_text(
                update.get("display_name")
                or (existing.display_name if existing else ""),
                160,
            )
            or None,
            aliases=merge_json_list(
                existing.aliases if existing else [],
                update.get("aliases"),
                max_items=30,
            ),
            profile_summary=profile_summary,
            expertise=merge_json_list(
                existing.expertise if existing else [],
                update.get("expertise"),
                max_items=50,
            ),
            stated_preferences=merge_json_list(
                existing.stated_preferences if existing else [],
                update.get("stated_preferences"),
                max_items=50,
            ),
            interaction_style=trim_text(
                update.get("interaction_style")
                or (existing.interaction_style if existing else ""),
                240,
            )
            or None,
            evidence_message_ids=merge_json_list(
                existing.evidence_message_ids if existing else [],
                _coerce_int_list(update.get("evidence_message_ids")),
                max_items=80,
            ),
            confidence=max(existing_confidence, update_confidence),
            source_until_message_id=max(
                latest_message_id,
                existing.source_until_message_id if existing and existing.source_until_message_id else 0,
            ),
        )

    async def describe_thread_memory(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
    ) -> str:
        chat = await get_chat_memory(session, chat_id)
        thread = await get_thread_memory(session, chat_id, message_thread_id)
        if chat is None and thread is None:
            return "No memory stored for this chat/thread yet."

        lines: list[str] = []
        if chat is not None:
            lines.append("Chat memory")
            if chat.summary:
                lines.append(chat.summary)
            lines.extend(_format_json_list("Current projects", chat.current_projects))
            lines.extend(_format_json_list("Recent decisions", chat.decisions))
            lines.extend(_format_json_list("Open questions", chat.open_questions))
        if thread is not None:
            if lines:
                lines.append("")
            header = "Thread memory"
            if thread.title:
                header += f": {thread.title}"
            lines.append(header)
            if thread.summary:
                lines.append(thread.summary)
            lines.extend(_format_json_list("Decisions", thread.decisions))
            lines.extend(_format_json_list("Action items", thread.action_items))
            lines.extend(_format_json_list("Open questions", thread.open_questions))
            lines.extend(_format_json_list("Key participants", thread.key_participants))
        return "\n".join(line for line in lines if line is not None).strip()

    async def describe_user_memory(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        user_id: int,
    ) -> str:
        profile = await get_user_memory(session, chat_id, user_id)
        if profile is None:
            return "No memory stored for this user in this chat yet."

        lines = [f"User memory: {profile.display_name or profile.user_id}"]
        if profile.profile_summary:
            lines.append(profile.profile_summary)
        lines.extend(_format_json_list("Aliases", profile.aliases))
        lines.extend(_format_json_list("Expertise", profile.expertise))
        lines.extend(_format_json_list("Stated preferences", profile.stated_preferences))
        if profile.interaction_style:
            lines.append(f"Interaction style: {profile.interaction_style}")
        if profile.confidence is not None:
            lines.append(f"Confidence: {profile.confidence:.2f}")
        return "\n".join(lines).strip()

    async def forget_thread(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
    ) -> int:
        return await delete_thread_memory(session, chat_id, message_thread_id)

    async def forget_chat(self, session: AsyncSession, *, chat_id: int) -> int:
        return await delete_chat_memory(session, chat_id)

    async def forget_user(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        user_id: int,
    ) -> int:
        return await delete_user_memory(session, chat_id, user_id)

    async def forget_all(self, session: AsyncSession, *, chat_id: int) -> int:
        return await delete_all_memory_for_chat(session, chat_id)

    async def forget_fact(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
        fact_text: str,
    ) -> int:
        chat = await get_chat_memory(session, chat_id)
        thread = await get_thread_memory(session, chat_id, message_thread_id)
        changed = 0
        if chat is not None:
            new_summary = _remove_fact_from_text(chat.summary, fact_text)
            new_stable = _remove_fact_from_json_list(chat.stable_facts, fact_text)
            new_projects = _remove_fact_from_json_list(chat.current_projects, fact_text)
            new_decisions = _remove_fact_from_json_list(chat.decisions, fact_text)
            new_questions = _remove_fact_from_json_list(chat.open_questions, fact_text)
            if (
                new_summary != chat.summary
                or new_stable != ensure_list(chat.stable_facts)
                or new_projects != ensure_list(chat.current_projects)
                or new_decisions != ensure_list(chat.decisions)
                or new_questions != ensure_list(chat.open_questions)
            ):
                await upsert_chat_memory(
                    session,
                    chat_id=chat_id,
                    summary=new_summary,
                    stable_facts=new_stable,
                    current_projects=new_projects,
                    decisions=new_decisions,
                    open_questions=new_questions,
                    source_until_message_id=chat.source_until_message_id,
                    source_until_date=chat.source_until_date,
                )
                changed += 1
        if thread is not None:
            new_summary = _remove_fact_from_text(thread.summary, fact_text)
            new_decisions = _remove_fact_from_json_list(thread.decisions, fact_text)
            new_actions = _remove_fact_from_json_list(thread.action_items, fact_text)
            new_questions = _remove_fact_from_json_list(thread.open_questions, fact_text)
            new_participants = _remove_fact_from_json_list(
                thread.key_participants,
                fact_text,
            )
            if (
                new_summary != thread.summary
                or new_decisions != ensure_list(thread.decisions)
                or new_actions != ensure_list(thread.action_items)
                or new_questions != ensure_list(thread.open_questions)
                or new_participants != ensure_list(thread.key_participants)
            ):
                await upsert_thread_memory(
                    session,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    title=thread.title,
                    summary=new_summary,
                    decisions=new_decisions,
                    action_items=new_actions,
                    open_questions=new_questions,
                    key_participants=new_participants,
                    source_until_message_id=thread.source_until_message_id,
                    source_until_date=thread.source_until_date,
                )
                changed += 1
        return changed
