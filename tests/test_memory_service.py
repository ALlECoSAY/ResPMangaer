from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.config import Settings
from app.services.memory_service import (
    ExplicitMemoryResult,
    MemoryService,
    extract_explicit_memory_text,
    format_explicit_memory_result,
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


def test_explicit_memory_text_extracts_body() -> None:
    assert extract_explicit_memory_text("запомни Phoenix2005 зовут Алиса") == (
        "Phoenix2005 зовут Алиса"
    )
    assert extract_explicit_memory_text("обычный вопрос") is None


def test_explicit_memory_confirmation_mentions_sanitized_labels() -> None:
    result = ExplicitMemoryResult(
        updated=True,
        saved_text="Qaw3ri зовут Давид",
        removed_unsafe_labels=True,
    )

    assert "Оскорбительные ярлыки" in format_explicit_memory_result(result)


def test_should_not_refresh_before_message_or_time_threshold() -> None:
    service = MemoryService(
        settings=Settings(_env_file=None),
        config=_FakeMemoryConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
    )
    fresh = datetime.now(UTC) - timedelta(minutes=5)

    assert service._should_refresh(fresh, [_message(11, "one"), _message(12, "two")]) is False


def test_should_refresh_on_message_count_keyword_or_stale_memory() -> None:
    service = MemoryService(
        settings=Settings(_env_file=None),
        config=_FakeMemoryConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
    )
    fresh = datetime.now(UTC) - timedelta(minutes=5)
    stale = datetime.now(UTC) - timedelta(hours=2)

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
