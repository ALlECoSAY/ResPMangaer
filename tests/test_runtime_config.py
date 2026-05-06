from __future__ import annotations

from pathlib import Path

from app.llm.runtime_config import RuntimeContextConfig


def test_defaults_when_context_config_missing(tmp_path: Path) -> None:
    cfg = RuntimeContextConfig(path=tmp_path / "missing.yaml")
    assert cfg.bot_language == "auto"
    assert cfg.max_context_chars == 24_000
    assert cfg.max_reply_chars == 3_900
    assert cfg.ai_max_same_thread_messages == 80
    assert cfg.ai_max_cross_thread_messages == 30
    assert cfg.tldr_activity_gap_minutes == 180
    assert cfg.tldr_lookback_hours == 48
    assert cfg.tldr_max_threads == 1
    assert cfg.tldr_max_messages_per_thread == 200
    assert cfg.tldr_all_max_threads == 12
    assert cfg.tldr_all_max_messages_per_thread == 120


def test_loads_bot_behavior_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "context_limits.yaml"
    path.write_text(
        """
        version: 1
        bot:
          language: ru
          max_reply_chars: 1000
        context:
          max_chars: 12000
        ai:
          max_same_thread_messages: 200
          max_cross_thread_messages: 20
        tldr:
          activity_gap_minutes: 90
          lookback_hours: 24
          max_threads: 2
          max_messages_per_thread: 50
        tldr_all:
          max_threads: 8
          max_messages_per_thread: 70
        """,
        encoding="utf-8",
    )

    cfg = RuntimeContextConfig(path=path)

    assert cfg.bot_language == "ru"
    assert cfg.max_context_chars == 12_000
    assert cfg.max_reply_chars == 1_000
    assert cfg.ai_max_same_thread_messages == 200
    assert cfg.ai_max_cross_thread_messages == 20
    assert cfg.tldr_activity_gap_minutes == 90
    assert cfg.tldr_lookback_hours == 24
    assert cfg.tldr_max_threads == 2
    assert cfg.tldr_max_messages_per_thread == 50
    assert cfg.tldr_all_max_threads == 8
    assert cfg.tldr_all_max_messages_per_thread == 70


def test_invalid_values_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "context_limits.yaml"
    path.write_text(
        """
        bot:
          language: ""
          max_reply_chars: 0
        context:
          max_chars: nope
        ai:
          max_same_thread_messages: -1
          max_cross_thread_messages: []
        tldr:
          activity_gap_minutes: -5
          lookback_hours: null
          max_threads: 0
          max_messages_per_thread: false
        tldr_all:
          max_threads: bad
          max_messages_per_thread: -1
        """,
        encoding="utf-8",
    )

    cfg = RuntimeContextConfig(path=path)

    assert cfg.bot_language == "auto"
    assert cfg.max_context_chars == 24_000
    assert cfg.max_reply_chars == 3_900
    assert cfg.ai_max_same_thread_messages == 80
    assert cfg.ai_max_cross_thread_messages == 30
    assert cfg.tldr_activity_gap_minutes == 180
    assert cfg.tldr_lookback_hours == 48
    assert cfg.tldr_max_threads == 1
    assert cfg.tldr_max_messages_per_thread == 200
    assert cfg.tldr_all_max_threads == 12
    assert cfg.tldr_all_max_messages_per_thread == 120
