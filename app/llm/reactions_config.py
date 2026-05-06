from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _ReactionLimits:
    enabled: bool
    min_distinct_users: int
    reply_chance: float
    context_before: int
    context_after: int
    cooldown_seconds: int
    bot_emoji: str
    trigger_emojis: tuple[str, ...] = field(default_factory=tuple)


_DEFAULTS = _ReactionLimits(
    enabled=False,
    min_distinct_users=3,
    reply_chance=0.3,
    context_before=5,
    context_after=3,
    cooldown_seconds=600,
    bot_emoji="🔥",
    trigger_emojis=(),
)


class RuntimeReactionsConfig:
    """Hot-reloadable YAML config for the reaction-trigger feature.

    Layout::

        version: 1
        reactions:
          enabled: true
          min_distinct_users: 3
          reply_chance: 0.3
          context_before: 5
          context_after: 3
          cooldown_seconds: 600
          bot_emoji: "🔥"
          trigger_emojis: []
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached_mtime: float | None = None
        self._limits = _DEFAULTS
        self._missing_logged = False

    @property
    def enabled(self) -> bool:
        return self._current().enabled

    @property
    def min_distinct_users(self) -> int:
        return self._current().min_distinct_users

    @property
    def reply_chance(self) -> float:
        return self._current().reply_chance

    @property
    def context_before(self) -> int:
        return self._current().context_before

    @property
    def context_after(self) -> int:
        return self._current().context_after

    @property
    def cooldown_seconds(self) -> int:
        return self._current().cooldown_seconds

    @property
    def bot_emoji(self) -> str:
        return self._current().bot_emoji

    @property
    def trigger_emojis(self) -> tuple[str, ...]:
        return self._current().trigger_emojis

    def emoji_is_trigger(self, emoji: str) -> bool:
        triggers = self.trigger_emojis
        if not triggers:
            return True
        return emoji in triggers

    def _current(self) -> _ReactionLimits:
        self._refresh_if_changed()
        with self._lock:
            return self._limits

    def _refresh_if_changed(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            with self._lock:
                already_missing = self._cached_mtime is None and self._missing_logged
                self._cached_mtime = None
                self._limits = _DEFAULTS
                self._missing_logged = True
            if not already_missing:
                log.warning("runtime_reactions_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_reactions_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        section = self._section(data, "reactions")
        limits = _ReactionLimits(
            enabled=bool(section.get("enabled", _DEFAULTS.enabled)),
            min_distinct_users=self._coerce_positive_int(
                section.get("min_distinct_users"),
                _DEFAULTS.min_distinct_users,
            ),
            reply_chance=self._coerce_unit_float(
                section.get("reply_chance"),
                _DEFAULTS.reply_chance,
            ),
            context_before=self._coerce_non_negative_int(
                section.get("context_before"),
                _DEFAULTS.context_before,
            ),
            context_after=self._coerce_non_negative_int(
                section.get("context_after"),
                _DEFAULTS.context_after,
            ),
            cooldown_seconds=self._coerce_non_negative_int(
                section.get("cooldown_seconds"),
                _DEFAULTS.cooldown_seconds,
            ),
            bot_emoji=self._coerce_str(
                section.get("bot_emoji"), _DEFAULTS.bot_emoji
            ),
            trigger_emojis=self._coerce_emoji_list(section.get("trigger_emojis")),
        )

        with self._lock:
            self._cached_mtime = mtime
            self._limits = limits
            self._missing_logged = False

        log.info(
            "runtime_reactions_config.reloaded",
            path=str(self._path),
            enabled=limits.enabled,
            min_distinct_users=limits.min_distinct_users,
            reply_chance=limits.reply_chance,
            context_before=limits.context_before,
            context_after=limits.context_after,
            cooldown_seconds=limits.cooldown_seconds,
            bot_emoji=limits.bot_emoji,
            trigger_emojis=list(limits.trigger_emojis),
        )

    @staticmethod
    def _section(data: Any, key: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        section = data.get(key)
        return section if isinstance(section, dict) else {}

    @staticmethod
    def _coerce_positive_int(value: Any, default: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n > 0 else default

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n >= 0 else default

    @staticmethod
    def _coerce_unit_float(value: Any, default: float) -> float:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return default
        if n < 0.0:
            return 0.0
        if n > 1.0:
            return 1.0
        return n

    @staticmethod
    def _coerce_str(value: Any, default: str) -> str:
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _coerce_emoji_list(value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value if item)
