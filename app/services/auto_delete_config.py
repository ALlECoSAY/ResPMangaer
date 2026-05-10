from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _AutoDeleteSettings:
    delays: dict[str, int] = field(default_factory=dict)


_DEFAULTS = _AutoDeleteSettings(
    delays={
        "stats": 300,
        "help": 300,
    }
)


class RuntimeAutoDeleteConfig:
    """Hot-reloadable YAML config for auto-deleting bot responses.

    Layout::

        version: 1
        auto_delete:
          stats: 300    # seconds before bot's /stats responses are deleted
          help: 300
          # tldr: 600  # opt in per command, omit to keep responses
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached_mtime: float | None = None
        self._settings = _DEFAULTS
        self._missing_logged = False

    def delay_seconds(self, command: str) -> int | None:
        delays = self._current().delays
        value = delays.get(command)
        if value is None or value <= 0:
            return None
        return value

    def _current(self) -> _AutoDeleteSettings:
        self._refresh_if_changed()
        with self._lock:
            return self._settings

    def _refresh_if_changed(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            with self._lock:
                already_missing = self._cached_mtime is None and self._missing_logged
                self._cached_mtime = None
                self._settings = _DEFAULTS
                self._missing_logged = True
            if not already_missing:
                log.warning("runtime_auto_delete_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_auto_delete_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        section = self._section(data, "auto_delete")
        delays: dict[str, int] = {}
        for raw_key, raw_value in section.items():
            try:
                seconds = int(raw_value)
            except (TypeError, ValueError):
                continue
            if seconds <= 0:
                continue
            key = str(raw_key).strip().lstrip("/").lower()
            if key:
                delays[key] = seconds

        settings = _AutoDeleteSettings(delays=delays)

        with self._lock:
            self._cached_mtime = mtime
            self._settings = settings
            self._missing_logged = False

        log.info(
            "runtime_auto_delete_config.reloaded",
            path=str(self._path),
            delays=delays,
        )

    @staticmethod
    def _section(data: Any, key: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        section = data.get(key)
        return section if isinstance(section, dict) else {}
