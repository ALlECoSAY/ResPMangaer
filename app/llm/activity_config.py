from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _ActivityLimits:
    enabled: bool
    min_messages: int
    window_minutes: int
    max_context_messages: int
    reply_chance: float
    reply_on_direct_reply_chance: float
    reply_on_follow_up_chance: float
    cooldown_seconds: int
    follow_up_window_seconds: int
    allowed_hours: tuple[int, ...] = field(default_factory=tuple)
    poll_enabled: bool = False
    poll_interval_seconds: int = 60
    poll_window_minutes: int = 30
    poll_max_threads_per_tick: int = 20


_DEFAULTS = _ActivityLimits(
    enabled=False,
    min_messages=20,
    window_minutes=30,
    max_context_messages=40,
    reply_chance=0.3,
    reply_on_direct_reply_chance=1.0,
    reply_on_follow_up_chance=0.5,
    cooldown_seconds=900,
    follow_up_window_seconds=300,
    allowed_hours=(),
    poll_enabled=False,
    poll_interval_seconds=60,
    poll_window_minutes=30,
    poll_max_threads_per_tick=20,
)


class RuntimeActivityConfig:
    """Hot-reloadable YAML config for activity-based random replies."""

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
    def min_messages(self) -> int:
        return self._current().min_messages

    @property
    def window_minutes(self) -> int:
        return self._current().window_minutes

    @property
    def max_context_messages(self) -> int:
        return self._current().max_context_messages

    @property
    def reply_chance(self) -> float:
        return self._current().reply_chance

    @property
    def reply_on_direct_reply_chance(self) -> float:
        return self._current().reply_on_direct_reply_chance

    @property
    def reply_on_follow_up_chance(self) -> float:
        return self._current().reply_on_follow_up_chance

    @property
    def cooldown_seconds(self) -> int:
        return self._current().cooldown_seconds

    @property
    def follow_up_window_seconds(self) -> int:
        return self._current().follow_up_window_seconds

    @property
    def allowed_hours(self) -> tuple[int, ...]:
        return self._current().allowed_hours

    @property
    def poll_enabled(self) -> bool:
        return self._current().poll_enabled

    @property
    def poll_interval_seconds(self) -> int:
        return self._current().poll_interval_seconds

    @property
    def poll_window_minutes(self) -> int:
        return self._current().poll_window_minutes

    @property
    def poll_max_threads_per_tick(self) -> int:
        return self._current().poll_max_threads_per_tick

    def hour_is_allowed(self, hour: int) -> bool:
        hours = self.allowed_hours
        return not hours or hour in hours

    def _current(self) -> _ActivityLimits:
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
                log.warning("runtime_activity_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_activity_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        section = self._section(data, "activity_responder")
        user_api_section = self._section(section, "user_api")
        limits = _ActivityLimits(
            enabled=bool(section.get("enabled", _DEFAULTS.enabled)),
            min_messages=self._coerce_positive_int(
                section.get("min_messages"), _DEFAULTS.min_messages
            ),
            window_minutes=self._coerce_positive_int(
                section.get("window_minutes"), _DEFAULTS.window_minutes
            ),
            max_context_messages=self._coerce_positive_int(
                section.get("max_context_messages"),
                _DEFAULTS.max_context_messages,
            ),
            reply_chance=self._coerce_unit_float(
                section.get("reply_chance"), _DEFAULTS.reply_chance
            ),
            reply_on_direct_reply_chance=self._coerce_unit_float(
                section.get("reply_on_direct_reply_chance"),
                _DEFAULTS.reply_on_direct_reply_chance,
            ),
            reply_on_follow_up_chance=self._coerce_unit_float(
                section.get("reply_on_follow_up_chance"),
                _DEFAULTS.reply_on_follow_up_chance,
            ),
            cooldown_seconds=self._coerce_non_negative_int(
                section.get("cooldown_seconds"), _DEFAULTS.cooldown_seconds
            ),
            follow_up_window_seconds=self._coerce_non_negative_int(
                section.get("follow_up_window_seconds"),
                _DEFAULTS.follow_up_window_seconds,
            ),
            allowed_hours=self._coerce_hours(section.get("allowed_hours")),
            poll_enabled=bool(
                user_api_section.get("poll_enabled", _DEFAULTS.poll_enabled)
            ),
            poll_interval_seconds=self._coerce_positive_int(
                user_api_section.get("poll_interval_seconds"),
                _DEFAULTS.poll_interval_seconds,
            ),
            poll_window_minutes=self._coerce_positive_int(
                user_api_section.get("poll_window_minutes"),
                _DEFAULTS.poll_window_minutes,
            ),
            poll_max_threads_per_tick=self._coerce_positive_int(
                user_api_section.get("poll_max_threads_per_tick"),
                _DEFAULTS.poll_max_threads_per_tick,
            ),
        )

        with self._lock:
            self._cached_mtime = mtime
            self._limits = limits
            self._missing_logged = False

        log.info(
            "runtime_activity_config.reloaded",
            path=str(self._path),
            enabled=limits.enabled,
            min_messages=limits.min_messages,
            window_minutes=limits.window_minutes,
            max_context_messages=limits.max_context_messages,
            reply_chance=limits.reply_chance,
            reply_on_direct_reply_chance=limits.reply_on_direct_reply_chance,
            reply_on_follow_up_chance=limits.reply_on_follow_up_chance,
            cooldown_seconds=limits.cooldown_seconds,
            follow_up_window_seconds=limits.follow_up_window_seconds,
            allowed_hours=list(limits.allowed_hours),
            poll_enabled=limits.poll_enabled,
            poll_interval_seconds=limits.poll_interval_seconds,
            poll_window_minutes=limits.poll_window_minutes,
            poll_max_threads_per_tick=limits.poll_max_threads_per_tick,
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
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default

    @staticmethod
    def _coerce_unit_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _coerce_hours(value: Any) -> tuple[int, ...]:
        if not isinstance(value, list):
            return ()
        hours: list[int] = []
        for item in value:
            try:
                hour = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= hour <= 23 and hour not in hours:
                hours.append(hour)
        return tuple(hours)
