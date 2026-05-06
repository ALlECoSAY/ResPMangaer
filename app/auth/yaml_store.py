from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.logging_config import get_logger

log = get_logger(__name__)


class YamlAccessStore:
    def __init__(self, whitelist_path: Path, admins_path: Path) -> None:
        self.whitelist_path = whitelist_path
        self.admins_path = admins_path
        self._lock = asyncio.Lock()

    async def get_whitelisted_user_ids(self) -> set[int]:
        data = await asyncio.to_thread(self._read_yaml, self.whitelist_path)
        return self._extract_ids(data, "users")

    async def get_admin_user_ids(self) -> set[int]:
        data = await asyncio.to_thread(self._read_yaml, self.admins_path)
        return self._extract_ids(data, "admins")

    async def add_whitelisted_user(
        self,
        user_id: int,
        note: str | None,
        added_by_user_id: int,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._add_whitelisted_user_sync, user_id, note, added_by_user_id
            )

    def _add_whitelisted_user_sync(
        self, user_id: int, note: str | None, added_by_user_id: int
    ) -> bool:
        data = self._read_yaml(self.whitelist_path) or {}
        if not isinstance(data, dict):
            data = {}
        users = data.get("users")
        if not isinstance(users, list):
            users = []
        existing_ids: set[int] = set()
        normalized: list[dict[str, Any]] = []
        for entry in users:
            if not isinstance(entry, dict):
                continue
            try:
                existing_id = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if existing_id in existing_ids:
                continue
            existing_ids.add(existing_id)
            entry["id"] = existing_id
            normalized.append(entry)
        if user_id in existing_ids:
            return False
        new_entry: dict[str, Any] = {
            "id": user_id,
            "note": note,
            "added_by": str(added_by_user_id),
            "added_at": datetime.now(UTC).isoformat(),
        }
        normalized.append(new_entry)
        data["version"] = data.get("version", 1)
        data["users"] = normalized
        self._atomic_write_yaml(self.whitelist_path, data)
        return True

    @staticmethod
    def _read_yaml(path: Path) -> Any:
        if not path.exists():
            log.warning("yaml_store.missing", path=str(path))
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            log.error("yaml_store.parse_error", path=str(path), error=str(exc))
            return None

    @staticmethod
    def _atomic_write_yaml(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
            os.replace(temp_name, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise

    @staticmethod
    def _extract_ids(data: Any, key: str) -> set[int]:
        if not isinstance(data, dict):
            return set()
        items = data.get(key)
        if not isinstance(items, list):
            return set()
        ids: set[int] = set()
        for entry in items:
            if not isinstance(entry, dict):
                continue
            value = entry.get("id")
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return ids
