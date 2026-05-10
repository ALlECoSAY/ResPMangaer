from __future__ import annotations

from pathlib import Path

from app.llm.activity_config import RuntimeActivityConfig


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = RuntimeActivityConfig(path=tmp_path / "missing.yaml")
    assert cfg.enabled is False
    assert cfg.min_messages == 20
    assert cfg.window_minutes == 30
    assert cfg.max_context_messages == 40
    assert cfg.reply_chance == 0.3
    assert cfg.poll_enabled is False


def test_loads_values_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "activity.yaml"
    _write(
        path,
        """
        version: 1
        activity_responder:
          enabled: true
          min_messages: 12
          window_minutes: 15
          max_context_messages: 25
          reply_chance: 0.75
          reply_on_direct_reply_chance: 1.0
          reply_on_follow_up_chance: 0.25
          cooldown_seconds: 120
          follow_up_window_seconds: 45
          allowed_hours: [9, 10, 22]
          user_api:
            poll_enabled: true
            poll_interval_seconds: 20
            poll_window_minutes: 15
            poll_max_threads_per_tick: 7
        """,
    )
    cfg = RuntimeActivityConfig(path=path)
    assert cfg.enabled is True
    assert cfg.min_messages == 12
    assert cfg.window_minutes == 15
    assert cfg.max_context_messages == 25
    assert cfg.reply_chance == 0.75
    assert cfg.reply_on_direct_reply_chance == 1.0
    assert cfg.reply_on_follow_up_chance == 0.25
    assert cfg.cooldown_seconds == 120
    assert cfg.follow_up_window_seconds == 45
    assert cfg.allowed_hours == (9, 10, 22)
    assert cfg.hour_is_allowed(10) is True
    assert cfg.hour_is_allowed(3) is False
    assert cfg.poll_enabled is True
    assert cfg.poll_interval_seconds == 20
    assert cfg.poll_window_minutes == 15
    assert cfg.poll_max_threads_per_tick == 7


def test_clamps_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "activity.yaml"
    _write(
        path,
        """
        activity_responder:
          min_messages: -2
          window_minutes: "no"
          max_context_messages: 0
          reply_chance: 5
          reply_on_direct_reply_chance: -1
          cooldown_seconds: -10
          allowed_hours: [-1, 0, 23, 24, "bad"]
        """,
    )
    cfg = RuntimeActivityConfig(path=path)
    assert cfg.min_messages == 20
    assert cfg.window_minutes == 30
    assert cfg.max_context_messages == 40
    assert cfg.reply_chance == 1.0
    assert cfg.reply_on_direct_reply_chance == 0.0
    assert cfg.cooldown_seconds == 900
    assert cfg.allowed_hours == (0, 23)
