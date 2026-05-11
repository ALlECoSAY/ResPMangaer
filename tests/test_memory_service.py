from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.config import Settings
from app.db.repositories import ThreadMemoryProfile
from app.services.memory_service import (
    MemoryService,
    merge_json_list,
    parse_memory_json,
    should_apply_user_update,
)


class _FakeMemoryConfig:
    enabled = True
    update_min_new_messages = 3
    update_min_interval_minutes = 60
    trigger_keywords = ("важно", "deadline")


def _message(message_id: int, body: str):
    return SimpleNamespace(
        message_id=message_id,
        clean_text=body,
        text=body,
        caption=None,
    )


def _thread_memory(updated_at: datetime) -> ThreadMemoryProfile:
    return ThreadMemoryProfile(
        chat_id=1,
        message_thread_id=5,
        title=None,
        summary=None,
        decisions=[],
        action_items=[],
        open_questions=[],
        key_participants=[],
        source_until_message_id=10,
        source_until_date=None,
        updated_at=updated_at,
    )


def test_merge_json_list_deduplicates_and_keeps_latest_budget() -> None:
    merged = merge_json_list(
        ["old", {"x": 1}, "duplicate"],
        [{"x": 1}, "new", "duplicate", "last"],
        max_items=4,
    )

    assert merged == [{"x": 1}, "duplicate", "new", "last"]


def test_parse_memory_json_accepts_fenced_json() -> None:
    payload = parse_memory_json('```json\n{"thread_summary": "ok"}\n```')

    assert payload == {"thread_summary": "ok"}


def test_should_not_refresh_before_message_or_time_threshold() -> None:
    service = MemoryService(
        settings=Settings(_env_file=None),
        config=_FakeMemoryConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
    )
    fresh = _thread_memory(datetime.now(UTC) - timedelta(minutes=5))

    assert service._should_refresh(fresh, [_message(11, "one"), _message(12, "two")]) is False


def test_should_refresh_on_message_count_keyword_or_stale_memory() -> None:
    service = MemoryService(
        settings=Settings(_env_file=None),
        config=_FakeMemoryConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
    )
    fresh = _thread_memory(datetime.now(UTC) - timedelta(minutes=5))
    stale = _thread_memory(datetime.now(UTC) - timedelta(hours=2))

    assert service._should_refresh(fresh, [_message(1, "a"), _message(2, "b"), _message(3, "c")])
    assert service._should_refresh(fresh, [_message(4, "это важно")])
    assert service._should_refresh(stale, [_message(5, "small update")])


def test_user_profile_update_requires_evidence_or_explicit_preference() -> None:
    assert not should_apply_user_update(
        {"evidence_message_ids": [1], "expertise": ["python"]},
        min_evidence_messages=3,
    )
    assert should_apply_user_update(
        {"evidence_message_ids": [1], "stated_preferences": ["prefers short replies"]},
        min_evidence_messages=3,
    )
    assert should_apply_user_update(
        {"evidence_message_ids": [1, 2, 3]},
        min_evidence_messages=3,
    )
