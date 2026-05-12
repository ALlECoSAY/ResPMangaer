from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class _PersonalityLimits:
    self_update_enabled: bool
    min_days_between_updates: int
    min_new_messages_between_updates: int
    require_admin_approval: bool
    max_prompt_chars: int
    model: str
    min_confidence: float


@dataclass(frozen=True)
class _DisplayNameLimits:
    self_update_enabled: bool
    require_admin_approval: bool
    min_days_between_updates: int
    max_length: int


@dataclass(frozen=True)
class _AvatarLimits:
    enabled: bool
    self_update_enabled: bool
    require_admin_approval: bool
    min_days_between_updates: int
    image_model: str
    max_generations_per_month: int


@dataclass(frozen=True)
class _IdentityData:
    enabled: bool
    personality: _PersonalityLimits
    display_name: _DisplayNameLimits
    avatar: _AvatarLimits


_DEFAULTS = _IdentityData(
    enabled=True,
    personality=_PersonalityLimits(
        self_update_enabled=False,
        min_days_between_updates=14,
        min_new_messages_between_updates=500,
        require_admin_approval=True,
        max_prompt_chars=1800,
        model="openai/gpt-4.1-mini",
        min_confidence=0.75,
    ),
    display_name=_DisplayNameLimits(
        self_update_enabled=False,
        require_admin_approval=True,
        min_days_between_updates=30,
        max_length=32,
    ),
    avatar=_AvatarLimits(
        enabled=False,
        self_update_enabled=False,
        require_admin_approval=True,
        min_days_between_updates=90,
        image_model="gpt-image-1",
        max_generations_per_month=1,
    ),
)


class RuntimeIdentityConfig:
    """Hot-reloadable YAML config for bot identity (personality, name, avatar)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached_mtime: float | None = None
        self._data = _DEFAULTS
        self._missing_logged = False

    @property
    def enabled(self) -> bool:
        return self._current().enabled

    @property
    def personality(self) -> _PersonalityLimits:
        return self._current().personality

    @property
    def display_name(self) -> _DisplayNameLimits:
        return self._current().display_name

    @property
    def avatar(self) -> _AvatarLimits:
        return self._current().avatar

    def _current(self) -> _IdentityData:
        self._refresh_if_changed()
        with self._lock:
            return self._data

    def _refresh_if_changed(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            with self._lock:
                already_missing = self._cached_mtime is None and self._missing_logged
                self._cached_mtime = None
                self._data = _DEFAULTS
                self._missing_logged = True
            if not already_missing:
                log.warning("runtime_identity_config.missing", path=str(self._path))
            return

        with self._lock:
            if self._cached_mtime == mtime:
                return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error(
                "runtime_identity_config.parse_error",
                path=str(self._path),
                error=str(exc),
            )
            return

        try:
            data = self._build(raw)
        except Exception as exc:
            log.error(
                "runtime_identity_config.build_failed",
                path=str(self._path),
                error=str(exc),
            )
            return

        with self._lock:
            self._cached_mtime = mtime
            self._data = data
            self._missing_logged = False

        log.info(
            "runtime_identity_config.reloaded",
            path=str(self._path),
            enabled=data.enabled,
            avatar_enabled=data.avatar.enabled,
        )

    def _build(self, raw: Any) -> _IdentityData:
        identity = self._section(raw, "identity")
        personality_section = self._section(identity, "personality")
        display_section = self._section(identity, "display_name")
        avatar_section = self._section(identity, "avatar")

        personality = _PersonalityLimits(
            self_update_enabled=bool(
                personality_section.get(
                    "self_update_enabled",
                    _DEFAULTS.personality.self_update_enabled,
                )
            ),
            min_days_between_updates=self._coerce_non_negative_int(
                personality_section.get("min_days_between_updates"),
                _DEFAULTS.personality.min_days_between_updates,
            ),
            min_new_messages_between_updates=self._coerce_non_negative_int(
                personality_section.get("min_new_messages_between_updates"),
                _DEFAULTS.personality.min_new_messages_between_updates,
            ),
            require_admin_approval=bool(
                personality_section.get(
                    "require_admin_approval",
                    _DEFAULTS.personality.require_admin_approval,
                )
            ),
            max_prompt_chars=self._coerce_positive_int(
                personality_section.get("max_prompt_chars"),
                _DEFAULTS.personality.max_prompt_chars,
            ),
            model=self._coerce_str(
                personality_section.get("model"),
                _DEFAULTS.personality.model,
            ),
            min_confidence=self._coerce_float(
                personality_section.get("min_confidence"),
                _DEFAULTS.personality.min_confidence,
            ),
        )
        display = _DisplayNameLimits(
            self_update_enabled=bool(
                display_section.get(
                    "self_update_enabled",
                    _DEFAULTS.display_name.self_update_enabled,
                )
            ),
            require_admin_approval=bool(
                display_section.get(
                    "require_admin_approval",
                    _DEFAULTS.display_name.require_admin_approval,
                )
            ),
            min_days_between_updates=self._coerce_non_negative_int(
                display_section.get("min_days_between_updates"),
                _DEFAULTS.display_name.min_days_between_updates,
            ),
            max_length=self._coerce_positive_int(
                display_section.get("max_length"),
                _DEFAULTS.display_name.max_length,
            ),
        )
        avatar = _AvatarLimits(
            enabled=bool(
                avatar_section.get("enabled", _DEFAULTS.avatar.enabled)
            ),
            self_update_enabled=bool(
                avatar_section.get(
                    "self_update_enabled",
                    _DEFAULTS.avatar.self_update_enabled,
                )
            ),
            require_admin_approval=bool(
                avatar_section.get(
                    "require_admin_approval",
                    _DEFAULTS.avatar.require_admin_approval,
                )
            ),
            min_days_between_updates=self._coerce_non_negative_int(
                avatar_section.get("min_days_between_updates"),
                _DEFAULTS.avatar.min_days_between_updates,
            ),
            image_model=self._coerce_str(
                avatar_section.get("image_model"),
                _DEFAULTS.avatar.image_model,
            ),
            max_generations_per_month=self._coerce_non_negative_int(
                avatar_section.get("max_generations_per_month"),
                _DEFAULTS.avatar.max_generations_per_month,
            ),
        )

        return _IdentityData(
            enabled=bool(identity.get("enabled", _DEFAULTS.enabled)),
            personality=personality,
            display_name=display,
            avatar=avatar,
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
    def _coerce_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, parsed))
