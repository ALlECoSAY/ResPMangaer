from __future__ import annotations

from pathlib import Path

from app.llm.reactions_config import RuntimeReactionsConfig


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = RuntimeReactionsConfig(path=tmp_path / "missing.yaml")
    assert cfg.enabled is False
    assert cfg.min_distinct_users == 3
    assert cfg.reply_chance == 0.3
    assert cfg.context_before == 5
    assert cfg.context_after == 3


def test_loads_values_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        version: 1
        reactions:
          enabled: true
          min_distinct_users: 5
          reply_chance: 0.75
          context_before: 4
          context_after: 6
          cooldown_seconds: 120
          bot_emoji: "👀"
          trigger_emojis: ["🔥", "🤔"]
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    assert cfg.enabled is True
    assert cfg.min_distinct_users == 5
    assert cfg.reply_chance == 0.75
    assert cfg.context_before == 4
    assert cfg.context_after == 6
    assert cfg.cooldown_seconds == 120
    assert cfg.bot_emoji == "👀"
    assert cfg.trigger_emojis == ("🔥", "🤔")
    assert cfg.emoji_is_trigger("🔥") is True
    assert cfg.emoji_is_trigger("👍") is False


def test_empty_trigger_emojis_means_any(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        reactions:
          enabled: true
          trigger_emojis: []
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    assert cfg.trigger_emojis == ()
    assert cfg.emoji_is_trigger("anything") is True


def test_clamps_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        reactions:
          min_distinct_users: -2
          reply_chance: 5.0
          context_before: -1
          context_after: "garbage"
          cooldown_seconds: -10
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    # Falls back to defaults / clamps to legal range
    assert cfg.min_distinct_users == 3  # default
    assert cfg.reply_chance == 1.0  # clamped
    assert cfg.context_before == 5  # default (negative -> default)
    assert cfg.context_after == 3  # default (non-int -> default)
    assert cfg.cooldown_seconds == 600  # default


def test_user_api_section_loaded(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        reactions:
          enabled: true
          user_api:
            fetch_limit_per_emoji: 50
            ignore_custom_reactions: false
            poll_enabled: true
            poll_interval_seconds: 15
            poll_window_minutes: 30
            poll_max_messages_per_tick: 25
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    assert cfg.fetch_limit_per_emoji == 50
    assert cfg.ignore_custom_reactions is False
    assert cfg.poll_enabled is True
    assert cfg.poll_interval_seconds == 15
    assert cfg.poll_window_minutes == 30
    assert cfg.poll_max_messages_per_tick == 25


def test_user_api_section_defaults(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        reactions:
          enabled: true
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    assert cfg.fetch_limit_per_emoji == 200
    assert cfg.ignore_custom_reactions is True
    assert cfg.poll_enabled is False
    assert cfg.poll_interval_seconds == 30
    assert cfg.poll_window_minutes == 60
    assert cfg.poll_max_messages_per_tick == 50


def test_hot_reload_on_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    _write(
        path,
        """
        reactions:
          min_distinct_users: 2
        """,
    )
    cfg = RuntimeReactionsConfig(path=path)
    assert cfg.min_distinct_users == 2

    # Bump mtime to a strictly newer value to force reload.
    new_mtime = path.stat().st_mtime + 5
    _write(
        path,
        """
        reactions:
          min_distinct_users: 7
        """,
    )
    import os

    os.utime(path, (new_mtime, new_mtime))
    assert cfg.min_distinct_users == 7
