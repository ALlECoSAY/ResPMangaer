from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramMessage
from app.db.repositories import (
    ChatMemoryProfile,
    ThreadMemoryProfile,
    UserMemoryProfile,
    fetch_recent_cross_thread,
    fetch_recent_same_thread,
    fetch_user_memories_for_prompt,
    get_chat_memory,
    get_thread_memory,
    get_thread_titles,
)
from app.llm.memory_config import RuntimeMemoryConfig
from app.llm.runtime_config import RuntimeContextConfig
from app.utils.telegram import safe_sender_label

DECISION_KEYWORDS = (
    "decided",
    "todo",
    "blocked",
    "ship",
    "fix",
    "deploy",
    "issue",
    "bug",
    "deadline",
)

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9_]{3,}", re.UNICODE)  # noqa: RUF001 (Cyrillic intentional)


@dataclass
class ContextMessage:
    chat_id: int
    message_thread_id: int
    sender_user_id: int | None
    sender: str
    text: str
    timestamp: datetime
    score: float = 0.0


@dataclass
class BuiltContext:
    same_thread_messages: list[ContextMessage]
    cross_thread_messages: list[ContextMessage]
    context_text: str


def _tokenize(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def _to_context_message(row: TelegramMessage) -> ContextMessage:
    body = (row.clean_text or row.text or row.caption or "").strip()
    return ContextMessage(
        chat_id=row.chat_id,
        message_thread_id=row.message_thread_id,
        sender_user_id=getattr(row, "sender_user_id", None),
        sender=safe_sender_label(row.sender_display_name),
        text=body,
        timestamp=row.telegram_date,
    )


def _format_block(messages: list[ContextMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
        text = msg.text.replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"[{ts}] {msg.sender}: {text}")
    return "\n".join(lines)


def _trim_to_budget(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    # Drop oldest lines (top of block) until we fit.
    lines = text.split("\n")
    while lines and sum(len(line) + 1 for line in lines) > budget:
        lines.pop(0)
    return "\n".join(lines)


def _trim_end_to_budget(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    if budget <= 3:
        return ""
    return text[: budget - 3].rstrip() + "..."


def _ensure_list(value: Any) -> list[Any]:
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


def _format_memory_items(title: str, values: Any, max_items: int = 6) -> list[str]:
    items = _ensure_list(values)
    if not items:
        return []
    lines = [f"{title}:"]
    for item in items[:max_items]:
        lines.append(f"- {item if isinstance(item, str) else str(item)}")
    return lines


def _format_chat_memory(memory: ChatMemoryProfile, budget: int) -> str:
    lines: list[str] = ["Chat memory:"]
    if memory.summary:
        lines.append(memory.summary)
    lines.extend(_format_memory_items("Stable facts", memory.stable_facts))
    lines.extend(_format_memory_items("Current projects", memory.current_projects))
    lines.extend(_format_memory_items("Recent decisions", memory.decisions))
    lines.extend(_format_memory_items("Open questions", memory.open_questions))
    return _trim_end_to_budget("\n".join(lines), budget)


def _format_thread_memory(memory: ThreadMemoryProfile, budget: int) -> str:
    header = "Current thread memory:"
    if memory.title:
        header += f" {memory.title}"
    lines: list[str] = [header]
    if memory.summary:
        lines.append(memory.summary)
    lines.extend(_format_memory_items("Decisions", memory.decisions))
    lines.extend(_format_memory_items("Action items", memory.action_items))
    lines.extend(_format_memory_items("Open questions", memory.open_questions))
    lines.extend(_format_memory_items("Key participants", memory.key_participants))
    return _trim_end_to_budget("\n".join(lines), budget)


def _format_user_memories(memories: list[UserMemoryProfile], budget: int) -> str:
    blocks: list[str] = []
    for memory in memories:
        lines = [f"- {memory.display_name or memory.user_id}:"]
        if memory.profile_summary:
            lines.append(f"  summary: {memory.profile_summary}")
        expertise = _ensure_list(memory.expertise)
        if expertise:
            lines.append(f"  expertise: {', '.join(str(item) for item in expertise[:5])}")
        preferences = _ensure_list(memory.stated_preferences)
        if preferences:
            lines.append(
                f"  stated preferences: {', '.join(str(item) for item in preferences[:5])}"
            )
        if memory.interaction_style:
            lines.append(f"  style: {memory.interaction_style}")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return _trim_end_to_budget("Participant memory:\n" + "\n".join(blocks), budget)


class ContextBuilder:
    def __init__(
        self,
        runtime_config: RuntimeContextConfig,
        memory_config: RuntimeMemoryConfig | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._memory_config = memory_config

    async def build_for_ai(
        self,
        session: AsyncSession,
        chat_id: int,
        message_thread_id: int,
        question: str,
    ) -> BuiltContext:
        max_same = self._runtime_config.ai_max_same_thread_messages
        max_cross = self._runtime_config.ai_max_cross_thread_messages

        same_rows = await fetch_recent_same_thread(
            session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            limit=max_same,
        )
        cross_rows = await fetch_recent_cross_thread(
            session,
            chat_id=chat_id,
            exclude_thread_id=message_thread_id,
            limit=max_cross * 4,
        )

        same_msgs = [_to_context_message(r) for r in reversed(same_rows)]
        cross_candidates = [_to_context_message(r) for r in cross_rows]
        thread_titles = await get_thread_titles(session, chat_id)
        memory_block = await self._build_memory_block(
            session,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            same_rows=list(reversed(same_rows)),
        )

        question_tokens = _tokenize(question)
        now = datetime.now(UTC)
        active_cutoff = now - timedelta(hours=24)

        active_threads = {
            m.message_thread_id
            for m in cross_candidates
            if m.timestamp >= active_cutoff
        }

        for m in cross_candidates:
            score = 0.0
            if question_tokens and (_tokenize(m.text) & question_tokens):
                score += 3.0
            if m.message_thread_id in active_threads:
                score += 2.0
            lower = m.text.lower()
            if any(word in lower for word in DECISION_KEYWORDS):
                score += 1.0
            age_hours = max((now - m.timestamp).total_seconds() / 3600.0, 0.001)
            recency = max(0.0, 1.0 - min(age_hours / 72.0, 1.0))
            score += recency
            m.score = score

        cross_candidates.sort(key=lambda m: (m.score, m.timestamp), reverse=True)
        cross_top = cross_candidates[:max_cross]
        cross_top.sort(key=lambda m: m.timestamp)

        same_block = _format_block(same_msgs)

        cross_groups: dict[int, list[ContextMessage]] = {}
        for msg in cross_top:
            cross_groups.setdefault(msg.message_thread_id, []).append(msg)

        cross_lines: list[str] = []
        for thread_id, msgs in cross_groups.items():
            title = thread_titles.get(thread_id) or ""
            header = f"# thread_id={thread_id}"
            if title:
                header += f" ({title})"
            cross_lines.append(header)
            cross_lines.append(_format_block(msgs))

        cross_block = "\n".join(line for line in cross_lines if line)

        budget = self._runtime_config.max_context_chars
        context_budget = max(0, budget - len(memory_block) - 200)
        same_budget = max(budget // 2, budget - len(cross_block) - 200)
        if memory_block:
            same_budget = max(context_budget // 2, context_budget - len(cross_block) - 200)
        same_block = _trim_to_budget(same_block, same_budget)
        remaining = max(0, context_budget - len(same_block) - 200)
        cross_block = _trim_to_budget(cross_block, remaining)

        sections: list[str] = []
        if memory_block:
            sections.append("MEMORY:\n" + memory_block)
        if same_block:
            sections.append("CURRENT THREAD CONTEXT:\n" + same_block)
        if cross_block:
            sections.append("OTHER THREAD SIGNALS:\n" + cross_block)
        context_text = "\n\n".join(sections).strip()

        return BuiltContext(
            same_thread_messages=same_msgs,
            cross_thread_messages=cross_top,
            context_text=context_text,
        )

    async def _build_memory_block(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_thread_id: int,
        same_rows: list[TelegramMessage],
    ) -> str:
        config = self._memory_config
        if config is None or not config.enabled:
            return ""

        chat_memory = await get_chat_memory(session, chat_id)
        thread_memory = await get_thread_memory(session, chat_id, message_thread_id)
        user_ids: list[int] = []
        seen_user_ids: set[int] = set()
        for row in reversed(same_rows):
            user_id = getattr(row, "sender_user_id", None)
            if user_id is None or user_id in seen_user_ids:
                continue
            seen_user_ids.add(int(user_id))
            user_ids.append(int(user_id))
            if len(user_ids) >= config.max_profiles_per_prompt:
                break
        user_memories = await fetch_user_memories_for_prompt(
            session,
            chat_id,
            user_ids,
            config.max_profiles_per_prompt,
        )

        blocks: list[str] = []
        if chat_memory is not None:
            blocks.append(
                _format_chat_memory(chat_memory, config.max_chat_memory_chars)
            )
        if thread_memory is not None:
            blocks.append(
                _format_thread_memory(thread_memory, config.max_thread_memory_chars)
            )
        user_block = _format_user_memories(user_memories, config.max_user_memory_chars)
        if user_block:
            blocks.append(user_block)
        return "\n\n".join(block for block in blocks if block).strip()
