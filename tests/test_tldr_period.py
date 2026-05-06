from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.thread_activity import detect_activity_periods
from app.services.tldr_service import make_tldr_request, parse_tldr_lookback


def _msg(thread_id: int, when: datetime, body: str = "x") -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=1,
        message_thread_id=thread_id,
        telegram_date=when,
        clean_text=body,
        text=body,
        caption=None,
        sender_display_name="alice",
    )


def test_detect_period_stops_at_gap():
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    msgs = [
        _msg(1, base - timedelta(hours=10), "old1"),
        _msg(1, base - timedelta(hours=9, minutes=50), "old2"),
        # large gap
        _msg(1, base - timedelta(minutes=30), "recent1"),
        _msg(1, base - timedelta(minutes=10), "recent2"),
    ]
    activities = detect_activity_periods(msgs, activity_gap_minutes=180, max_messages_per_thread=100)
    assert len(activities) == 1
    bodies = [m.clean_text for m in activities[0].messages]
    assert "recent1" in bodies and "recent2" in bodies
    assert "old1" not in bodies and "old2" not in bodies


def test_detect_caps_per_thread():
    base = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    msgs = [_msg(1, base - timedelta(minutes=i), f"m{i}") for i in range(50)]
    activities = detect_activity_periods(msgs, activity_gap_minutes=180, max_messages_per_thread=10)
    assert len(activities[0].messages) == 10


def test_parse_tldr_lookback_default():
    assert parse_tldr_lookback("", default_lookback_hours=48) == 48


def test_parse_tldr_lookback_with_duration():
    assert parse_tldr_lookback("6h", default_lookback_hours=48) == 6


def test_parse_tldr_lookback_ignores_unknown_tokens():
    assert parse_tldr_lookback("all 6h", default_lookback_hours=48) == 6


def test_parse_tldr_lookback_2d():
    assert parse_tldr_lookback("2d", default_lookback_hours=12) == 48  # 2d == 48h


def test_make_tldr_request_thread_scope():
    req = make_tldr_request(scope="thread", lookback_hours=24)
    assert req.scope == "thread" and req.lookback_hours == 24
    assert "current thread" in req.scope_description.lower()


def test_make_tldr_request_all_scope():
    req = make_tldr_request(scope="all", lookback_hours=12)
    assert req.scope == "all" and req.lookback_hours == 12
    assert "all threads" in req.scope_description.lower()
