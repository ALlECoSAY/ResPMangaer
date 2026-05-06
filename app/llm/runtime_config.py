from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _Limits:
    bot_language: str
    max_context_chars: int
    max_reply_chars: int
    ai_max_same_thread_messages: int
    ai_max_cross_thread_messages: int
    tldr_activity_gap_minutes: int
    tldr_lookback_hours: int
    tldr_max_threads: int
    tldr_max_messages_per_thread: int
    tldr_all_max_threads: int
    tldr_all_max_messages_per_thread: int


_DEFAULTS = _Limits(
    bot_language="auto",
    max_context_chars=24_000,
    max_reply_chars=3_900,
    ai_max_same_thread_messages=80,
    ai_max_cross_thread_messages=30,
    tldr_activity_gap_minutes=180,
    tldr_lookback_hours=48,
    tldr_max_threads=1,
    tldr_max_messages_per_thread=200,
    tldr_all_max_threads=12,
    tldr_all_max_messages_per_thread=120,
)


class RuntimeContextConfig:
    """Hot-reloadable YAML config for bot runtime behavior.

    Re-reads the file when its mtime changes, so edits take effect without restart.
    Layout:

        version: 1
        bot:
          language: auto
          max_reply_chars: 3900
        context:
          max_chars: 24000
        ai:
          max_same_thread_messages: 80
          max_cross_thread_messages: 30
        tldr:
          activity_gap_minutes: 180
          lookback_hours: 48
          max_threads: 1
          max_messages_per_thread: 200
        tldr_all:
          max_threads: 12
          max_messages_per_thread: 120
    """

    def __init__(
        self,
        path: Path,
    ) -> None:
        self._path = path
        self._defaults = _DEFAULTS
        self._lock = threading.Lock()
        self._cached_mtime: float | None = None
        self._limits = self._defaults
        self._missing_logged = False

    @property
    def bot_language(self) -> str:
        return self._current().bot_language

    @property
    def max_context_chars(self) -> int:
        return self._current().max_context_chars

    @property
    def max_reply_chars(self) -> int:
        return self._current().max_reply_chars

    @property
    def ai_max_same_thread_messages(self) -> int:
        return self._current().ai_max_same_thread_messages

    @property
    def ai_max_cross_thread_messages(self) -> int:
        return self._current().ai_max_cross_thread_messages

    @property
    def tldr_activity_gap_minutes(self) -> int:
        return self._current().tldr_activity_gap_minutes

    @property
    def tldr_lookback_hours(self) -> int:
        return self._current().tldr_lookback_hours

    @property
    def tldr_max_threads(self) -> int:
        return self._current().tldr_max_threads

    @property
    def tldr_max_messages_per_thread(self) -> int:
        return self._current().tldr_max_messages_per_thread

    @property
    def tldr_all_max_threads(self) -> int:
        return self._current().tldr_all_max_threads

    @property
    def tldr_all_max_messages_per_thread(self) -> int:
        return self._current().tldr_all_max_messages_per_thread

    def _current(self) -> _Limits:
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
                self._limits = self._defaults
                self._missing_logged = True
            if not already_missing:
                log.warning("runtime_context_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_context_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        bot_section = self._section(data, "bot")
        context_section = self._section(data, "context")
        ai_section = self._section(data, "ai")
        tldr_section = self._section(data, "tldr")
        tldr_all_section = self._section(data, "tldr_all")

        limits = _Limits(
            bot_language=self._coerce_non_empty_str(
                bot_section.get("language"),
                self._defaults.bot_language,
            ),
            max_context_chars=self._coerce_positive_int(
                self._first_present(
                    context_section.get("max_chars"),
                    ai_section.get("max_context_chars"),
                ),
                self._defaults.max_context_chars,
            ),
            max_reply_chars=self._coerce_positive_int(
                bot_section.get("max_reply_chars"),
                self._defaults.max_reply_chars,
            ),
            ai_max_same_thread_messages=self._coerce_positive_int(
                ai_section.get("max_same_thread_messages"),
                self._defaults.ai_max_same_thread_messages,
            ),
            ai_max_cross_thread_messages=self._coerce_positive_int(
                ai_section.get("max_cross_thread_messages"),
                self._defaults.ai_max_cross_thread_messages,
            ),
            tldr_activity_gap_minutes=self._coerce_positive_int(
                tldr_section.get("activity_gap_minutes"),
                self._defaults.tldr_activity_gap_minutes,
            ),
            tldr_lookback_hours=self._coerce_positive_int(
                tldr_section.get("lookback_hours"),
                self._defaults.tldr_lookback_hours,
            ),
            tldr_max_threads=self._coerce_positive_int(
                tldr_section.get("max_threads"),
                self._defaults.tldr_max_threads,
            ),
            tldr_max_messages_per_thread=self._coerce_positive_int(
                tldr_section.get("max_messages_per_thread"),
                self._defaults.tldr_max_messages_per_thread,
            ),
            tldr_all_max_threads=self._coerce_positive_int(
                tldr_all_section.get("max_threads"),
                self._defaults.tldr_all_max_threads,
            ),
            tldr_all_max_messages_per_thread=self._coerce_positive_int(
                tldr_all_section.get("max_messages_per_thread"),
                self._defaults.tldr_all_max_messages_per_thread,
            ),
        )

        with self._lock:
            self._cached_mtime = mtime
            self._limits = limits
            self._missing_logged = False

        log.info(
            "runtime_context_config.reloaded",
            path=str(self._path),
            bot_language=limits.bot_language,
            max_context_chars=limits.max_context_chars,
            max_reply_chars=limits.max_reply_chars,
            ai_same=limits.ai_max_same_thread_messages,
            ai_cross=limits.ai_max_cross_thread_messages,
            tldr_gap_minutes=limits.tldr_activity_gap_minutes,
            tldr_lookback_hours=limits.tldr_lookback_hours,
            tldr_threads=limits.tldr_max_threads,
            tldr_msgs_per_thread=limits.tldr_max_messages_per_thread,
            tldr_all_threads=limits.tldr_all_max_threads,
            tldr_all_msgs_per_thread=limits.tldr_all_max_messages_per_thread,
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
    def _coerce_non_empty_str(value: Any, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _first_present(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None
