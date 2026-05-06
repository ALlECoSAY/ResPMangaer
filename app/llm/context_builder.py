from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramMessage
from app.db.repositories import (
    fetch_recent_cross_thread,
    fetch_recent_same_thread,
    get_thread_titles,
)
from app.llm.runtime_config import RuntimeContextConfig

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
        sender=row.sender_display_name or "anon",
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


class ContextBuilder:
    def __init__(
        self,
        runtime_config: RuntimeContextConfig,
    ) -> None:
        self._runtime_config = runtime_config

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
        same_budget = max(budget // 2, budget - len(cross_block) - 200)
        same_block = _trim_to_budget(same_block, same_budget)
        remaining = max(0, budget - len(same_block) - 200)
        cross_block = _trim_to_budget(cross_block, remaining)

        sections: list[str] = []
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
