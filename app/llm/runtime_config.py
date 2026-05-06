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
    ai_max_same_thread_messages: int
    ai_max_cross_thread_messages: int
    tldr_max_threads: int
    tldr_max_messages_per_thread: int
    tldr_all_max_threads: int
    tldr_all_max_messages_per_thread: int


class RuntimeContextConfig:
    """Hot-reloadable YAML config for /ai, /tldr and /tldr_all context limits.

    Re-reads the file when its mtime changes, so edits take effect without restart.
    Layout:

        version: 1
        ai:
          max_same_thread_messages: 80
          max_cross_thread_messages: 30
        tldr:
          max_threads: 1
          max_messages_per_thread: 200
        tldr_all:
          max_threads: 12
          max_messages_per_thread: 120
    """

    def __init__(
        self,
        path: Path,
        *,
        default_ai_same_thread: int,
        default_ai_cross_thread: int,
        default_tldr_max_threads: int,
        default_tldr_max_messages_per_thread: int,
        default_tldr_all_max_threads: int,
        default_tldr_all_max_messages_per_thread: int,
    ) -> None:
        self._path = path
        self._defaults = _Limits(
            ai_max_same_thread_messages=default_ai_same_thread,
            ai_max_cross_thread_messages=default_ai_cross_thread,
            tldr_max_threads=default_tldr_max_threads,
            tldr_max_messages_per_thread=default_tldr_max_messages_per_thread,
            tldr_all_max_threads=default_tldr_all_max_threads,
            tldr_all_max_messages_per_thread=default_tldr_all_max_messages_per_thread,
        )
        self._lock = threading.Lock()
        self._cached_mtime: float | None = None
        self._limits = self._defaults
        self._missing_logged = False

    @property
    def ai_max_same_thread_messages(self) -> int:
        return self._current().ai_max_same_thread_messages

    @property
    def ai_max_cross_thread_messages(self) -> int:
        return self._current().ai_max_cross_thread_messages

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

        ai_section = self._section(data, "ai")
        tldr_section = self._section(data, "tldr")
        tldr_all_section = self._section(data, "tldr_all")

        limits = _Limits(
            ai_max_same_thread_messages=self._coerce_positive_int(
                ai_section.get("max_same_thread_messages"),
                self._defaults.ai_max_same_thread_messages,
            ),
            ai_max_cross_thread_messages=self._coerce_positive_int(
                ai_section.get("max_cross_thread_messages"),
                self._defaults.ai_max_cross_thread_messages,
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
            ai_same=limits.ai_max_same_thread_messages,
            ai_cross=limits.ai_max_cross_thread_messages,
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
