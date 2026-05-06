from __future__ import annotations

from dataclasses import dataclass

from app.auth.yaml_store import YamlAccessStore

DENY_AI = (
    "You are not whitelisted to use this bot. "
    "Ask an admin to add your Telegram user ID."
)
DENY_ADMIN = "Only bot admins can manage the whitelist."


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None


class AccessControl:
    def __init__(self, store: YamlAccessStore, enabled: bool) -> None:
        self._store = store
        self._enabled = enabled

    async def is_admin(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        admins = await self._store.get_admin_user_ids()
        return user_id in admins

    async def is_whitelisted(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        if await self.is_admin(user_id):
            return True
        whitelisted = await self._store.get_whitelisted_user_ids()
        return user_id in whitelisted

    async def can_use_ai_commands(self, user_id: int | None) -> AccessDecision:
        if not self._enabled:
            return AccessDecision(True)
        if user_id is None:
            return AccessDecision(False, DENY_AI)
        if await self.is_admin(user_id):
            return AccessDecision(True)
        if await self.is_whitelisted(user_id):
            return AccessDecision(True)
        return AccessDecision(False, DENY_AI)

    async def can_manage_whitelist(self, user_id: int | None) -> AccessDecision:
        if user_id is None:
            return AccessDecision(False, DENY_ADMIN)
        if not self._enabled:
            # Even with access control disabled for AI commands,
            # whitelist management remains admin-only as a safety net.
            if await self.is_admin(user_id):
                return AccessDecision(True)
            return AccessDecision(False, DENY_ADMIN)
        if await self.is_admin(user_id):
            return AccessDecision(True)
        return AccessDecision(False, DENY_ADMIN)
