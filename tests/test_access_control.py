from __future__ import annotations

from pathlib import Path

import pytest

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore


@pytest.fixture
def store(tmp_path: Path) -> YamlAccessStore:
    whitelist = tmp_path / "whitelist.yaml"
    admins = tmp_path / "admins.yaml"
    whitelist.write_text("version: 1\nusers:\n  - id: 100\n    note: alice\n", encoding="utf-8")
    admins.write_text("version: 1\nadmins:\n  - id: 200\n    note: owner\n", encoding="utf-8")
    return YamlAccessStore(whitelist_path=whitelist, admins_path=admins)


async def test_admin_can_use_ai(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=True)
    assert (await ac.can_use_ai_commands(200)).allowed is True


async def test_whitelisted_can_use_ai(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=True)
    assert (await ac.can_use_ai_commands(100)).allowed is True


async def test_unknown_denied(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=True)
    decision = await ac.can_use_ai_commands(999)
    assert decision.allowed is False
    assert decision.reason


async def test_none_user_denied(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=True)
    assert (await ac.can_use_ai_commands(None)).allowed is False


async def test_disabled_allows_everyone(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=False)
    assert (await ac.can_use_ai_commands(999)).allowed is True


async def test_only_admin_can_manage_whitelist(store: YamlAccessStore) -> None:
    ac = AccessControl(store, enabled=True)
    assert (await ac.can_manage_whitelist(200)).allowed is True
    assert (await ac.can_manage_whitelist(100)).allowed is False
    assert (await ac.can_manage_whitelist(None)).allowed is False


async def test_add_whitelisted_idempotent(store: YamlAccessStore) -> None:
    added = await store.add_whitelisted_user(300, "bob", added_by_user_id=200)
    assert added is True
    again = await store.add_whitelisted_user(300, "bob", added_by_user_id=200)
    assert again is False
    ids = await store.get_whitelisted_user_ids()
    assert 100 in ids and 300 in ids


async def test_add_whitelisted_persists(tmp_path: Path) -> None:
    whitelist = tmp_path / "whitelist.yaml"
    admins = tmp_path / "admins.yaml"
    whitelist.write_text("version: 1\nusers: []\n", encoding="utf-8")
    admins.write_text("version: 1\nadmins: []\n", encoding="utf-8")
    store = YamlAccessStore(whitelist, admins)
    await store.add_whitelisted_user(42, "test", added_by_user_id=1)
    text = whitelist.read_text(encoding="utf-8")
    assert "id: 42" in text
