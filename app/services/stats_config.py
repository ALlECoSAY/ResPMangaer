from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _StatsLimits:
    enabled: bool
    default_lookback_days: int
    top_n_users: int
    top_n_words: int
    top_n_threads: int
    max_message_chars: int
    report_schedule: str | None


_DEFAULTS = _StatsLimits(
    enabled=True,
    default_lookback_days=7,
    top_n_users=10,
    top_n_words=20,
    top_n_threads=5,
    max_message_chars=3_900,
    report_schedule=None,
)


class RuntimeStatsConfig:
    """Hot-reloadable YAML config for the `/stats` feature.

    Layout::

        version: 1
        stats:
          enabled: true
          default_lookback_days: 7
          top_n_users: 10
          top_n_words: 20
          top_n_threads: 5
          max_message_chars: 3900
          report_schedule: null
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
    def default_lookback_days(self) -> int:
        return self._current().default_lookback_days

    @property
    def top_n_users(self) -> int:
        return self._current().top_n_users

    @property
    def top_n_words(self) -> int:
        return self._current().top_n_words

    @property
    def top_n_threads(self) -> int:
        return self._current().top_n_threads

    @property
    def max_message_chars(self) -> int:
        return self._current().max_message_chars

    @property
    def report_schedule(self) -> str | None:
        return self._current().report_schedule

    def _current(self) -> _StatsLimits:
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
                log.warning("runtime_stats_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_stats_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        section = self._section(data, "stats")
        limits = _StatsLimits(
            enabled=bool(section.get("enabled", _DEFAULTS.enabled)),
            default_lookback_days=self._coerce_positive_int(
                section.get("default_lookback_days"),
                _DEFAULTS.default_lookback_days,
            ),
            top_n_users=self._coerce_positive_int(
                section.get("top_n_users"),
                _DEFAULTS.top_n_users,
            ),
            top_n_words=self._coerce_positive_int(
                section.get("top_n_words"),
                _DEFAULTS.top_n_words,
            ),
            top_n_threads=self._coerce_positive_int(
                section.get("top_n_threads"),
                _DEFAULTS.top_n_threads,
            ),
            max_message_chars=self._coerce_positive_int(
                section.get("max_message_chars"),
                _DEFAULTS.max_message_chars,
            ),
            report_schedule=self._coerce_optional_schedule(
                section.get("report_schedule")
            ),
        )

        with self._lock:
            self._cached_mtime = mtime
            self._limits = limits
            self._missing_logged = False

        log.info(
            "runtime_stats_config.reloaded",
            path=str(self._path),
            enabled=limits.enabled,
            default_lookback_days=limits.default_lookback_days,
            top_n_users=limits.top_n_users,
            top_n_words=limits.top_n_words,
            top_n_threads=limits.top_n_threads,
            max_message_chars=limits.max_message_chars,
            report_schedule=limits.report_schedule,
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
    def _coerce_optional_schedule(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"", "none", "null", "false"}:
            return None
        if text in {"weekly", "monthly"}:
            return text
        return _DEFAULTS.report_schedule
