from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


_DEFAULT_TRIGGER_KEYWORDS = (
    "decided",
    "todo",
    "deadline",
    "blocked",
    "fix",
    "deploy",
    "bug",
    "important",
    "решили",
    "todo",
    "дедлайн",
    "blocked",
    "фикс",
    "деплой",
    "баг",  # noqa: RUF001 (Cyrillic keyword intentional)
    "важно",
)


@dataclass(frozen=True)
class _MemoryLimits:
    enabled: bool
    user_profiles_enabled: bool
    max_chat_memory_chars: int
    max_thread_memory_chars: int
    max_user_memory_chars: int
    update_min_new_messages: int
    update_min_interval_minutes: int
    max_profiles_per_prompt: int
    summarize_model: str
    max_messages_per_update: int
    user_profile_min_evidence_messages: int
    trigger_keywords: tuple[str, ...] = field(default_factory=tuple)
    update_reaction_min_count: int = 5
    poll_enabled: bool = False
    poll_interval_seconds: int = 300
    poll_max_threads_per_tick: int = 5


_DEFAULTS = _MemoryLimits(
    enabled=False,
    user_profiles_enabled=True,
    max_chat_memory_chars=1800,
    max_thread_memory_chars=1800,
    max_user_memory_chars=1200,
    update_min_new_messages=80,
    update_min_interval_minutes=360,
    max_profiles_per_prompt=6,
    summarize_model="openai/gpt-4.1-mini",
    max_messages_per_update=180,
    user_profile_min_evidence_messages=3,
    trigger_keywords=_DEFAULT_TRIGGER_KEYWORDS,
    update_reaction_min_count=5,
    poll_enabled=False,
    poll_interval_seconds=300,
    poll_max_threads_per_tick=5,
)


class RuntimeMemoryConfig:
    """Hot-reloadable YAML config for compact long-term memory."""

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
    def user_profiles_enabled(self) -> bool:
        return self._current().user_profiles_enabled

    @property
    def max_chat_memory_chars(self) -> int:
        return self._current().max_chat_memory_chars

    @property
    def max_thread_memory_chars(self) -> int:
        return self._current().max_thread_memory_chars

    @property
    def max_user_memory_chars(self) -> int:
        return self._current().max_user_memory_chars

    @property
    def update_min_new_messages(self) -> int:
        return self._current().update_min_new_messages

    @property
    def update_min_interval_minutes(self) -> int:
        return self._current().update_min_interval_minutes

    @property
    def max_profiles_per_prompt(self) -> int:
        return self._current().max_profiles_per_prompt

    @property
    def summarize_model(self) -> str:
        return self._current().summarize_model

    @property
    def max_messages_per_update(self) -> int:
        return self._current().max_messages_per_update

    @property
    def user_profile_min_evidence_messages(self) -> int:
        return self._current().user_profile_min_evidence_messages

    @property
    def trigger_keywords(self) -> tuple[str, ...]:
        return self._current().trigger_keywords

    @property
    def update_reaction_min_count(self) -> int:
        return self._current().update_reaction_min_count

    @property
    def poll_enabled(self) -> bool:
        return self._current().poll_enabled

    @property
    def poll_interval_seconds(self) -> int:
        return self._current().poll_interval_seconds

    @property
    def poll_max_threads_per_tick(self) -> int:
        return self._current().poll_max_threads_per_tick

    def _current(self) -> _MemoryLimits:
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
                log.warning("runtime_memory_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_memory_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        section = self._section(data, "memory")
        user_api_section = self._section(section, "user_api")
        limits = _MemoryLimits(
            enabled=bool(section.get("enabled", _DEFAULTS.enabled)),
            user_profiles_enabled=bool(
                section.get(
                    "user_profiles_enabled",
                    _DEFAULTS.user_profiles_enabled,
                )
            ),
            max_chat_memory_chars=self._coerce_positive_int(
                section.get("max_chat_memory_chars"),
                _DEFAULTS.max_chat_memory_chars,
            ),
            max_thread_memory_chars=self._coerce_positive_int(
                section.get("max_thread_memory_chars"),
                _DEFAULTS.max_thread_memory_chars,
            ),
            max_user_memory_chars=self._coerce_positive_int(
                section.get("max_user_memory_chars"),
                _DEFAULTS.max_user_memory_chars,
            ),
            update_min_new_messages=self._coerce_positive_int(
                section.get("update_min_new_messages"),
                _DEFAULTS.update_min_new_messages,
            ),
            update_min_interval_minutes=self._coerce_positive_int(
                section.get("update_min_interval_minutes"),
                _DEFAULTS.update_min_interval_minutes,
            ),
            max_profiles_per_prompt=self._coerce_positive_int(
                section.get("max_profiles_per_prompt"),
                _DEFAULTS.max_profiles_per_prompt,
            ),
            summarize_model=self._coerce_str(
                section.get("summarize_model"),
                _DEFAULTS.summarize_model,
            ),
            max_messages_per_update=self._coerce_positive_int(
                section.get("max_messages_per_update"),
                _DEFAULTS.max_messages_per_update,
            ),
            user_profile_min_evidence_messages=self._coerce_positive_int(
                section.get("user_profile_min_evidence_messages"),
                _DEFAULTS.user_profile_min_evidence_messages,
            ),
            trigger_keywords=self._coerce_str_list(
                section.get("trigger_keywords"),
                _DEFAULTS.trigger_keywords,
            ),
            update_reaction_min_count=self._coerce_non_negative_int(
                section.get("update_reaction_min_count"),
                _DEFAULTS.update_reaction_min_count,
            ),
            poll_enabled=bool(
                user_api_section.get("poll_enabled", _DEFAULTS.poll_enabled)
            ),
            poll_interval_seconds=self._coerce_positive_int(
                user_api_section.get("poll_interval_seconds"),
                _DEFAULTS.poll_interval_seconds,
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
            "runtime_memory_config.reloaded",
            path=str(self._path),
            enabled=limits.enabled,
            user_profiles_enabled=limits.user_profiles_enabled,
            max_chat_memory_chars=limits.max_chat_memory_chars,
            max_thread_memory_chars=limits.max_thread_memory_chars,
            max_user_memory_chars=limits.max_user_memory_chars,
            update_min_new_messages=limits.update_min_new_messages,
            update_min_interval_minutes=limits.update_min_interval_minutes,
            max_profiles_per_prompt=limits.max_profiles_per_prompt,
            summarize_model=limits.summarize_model,
            max_messages_per_update=limits.max_messages_per_update,
            user_profile_min_evidence_messages=limits.user_profile_min_evidence_messages,
            trigger_keywords=list(limits.trigger_keywords),
            update_reaction_min_count=limits.update_reaction_min_count,
            poll_enabled=limits.poll_enabled,
            poll_interval_seconds=limits.poll_interval_seconds,
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
    def _coerce_str(value: Any, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _coerce_str_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(value, list):
            return default
        normalized = tuple(str(item).strip() for item in value if str(item).strip())
        return normalized or default
