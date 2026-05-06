from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from app.db.models import TelegramMessage


@dataclass
class ThreadActivity:
    chat_id: int
    message_thread_id: int
    title: str | None
    messages: list[TelegramMessage] = field(default_factory=list)


def detect_activity_periods(
    messages: list[TelegramMessage],
    activity_gap_minutes: int,
    max_messages_per_thread: int,
    thread_titles: dict[int, str | None] | None = None,
) -> list[ThreadActivity]:
    """Return one ThreadActivity per thread with the most-recent contiguous run.

    Walks backward from the newest message and stops when the gap between
    adjacent messages exceeds ``activity_gap_minutes``.
    """
    if not messages:
        return []
    titles = thread_titles or {}
    by_thread: dict[int, list[TelegramMessage]] = defaultdict(list)
    for msg in messages:
        by_thread[msg.message_thread_id].append(msg)

    gap = timedelta(minutes=activity_gap_minutes)
    out: list[ThreadActivity] = []
    for thread_id, msgs in by_thread.items():
        msgs.sort(key=lambda m: m.telegram_date)
        # Walk newest -> oldest, keep a contiguous block.
        block: list[TelegramMessage] = []
        prev = None
        for m in reversed(msgs):
            if prev is None:
                block.append(m)
                prev = m
                continue
            if (prev.telegram_date - m.telegram_date) > gap:
                break
            block.append(m)
            prev = m
        if not block:
            continue
        block.reverse()  # back to chronological
        if len(block) > max_messages_per_thread:
            block = block[-max_messages_per_thread:]
        out.append(
            ThreadActivity(
                chat_id=block[0].chat_id,
                message_thread_id=thread_id,
                title=titles.get(thread_id),
                messages=block,
            )
        )
    out.sort(
        key=lambda a: max(m.telegram_date for m in a.messages),
        reverse=True,
    )
    return out
