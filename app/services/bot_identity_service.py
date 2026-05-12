from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import (
    BotIdentityProfile,
    get_bot_identity,
    get_chat_memory,
    upsert_bot_identity,
)
from app.llm.prompt_config import RuntimePromptConfig
from app.logging_config import get_logger
from app.services.identity_config import RuntimeIdentityConfig

if TYPE_CHECKING:
    from app.llm.openrouter_client import OpenRouterClient

log = get_logger(__name__)


_UNSAFE_INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore\s+previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+previous\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"@[A-Za-z0-9_]{2,}", re.IGNORECASE),
    re.compile(r"\bsystem\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class PersonalityUpdateOutcome:
    applied: bool
    reason: str
    new_version: int | None = None
    awaiting_approval: bool = False


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _is_unsafe_prompt(text: str) -> bool:
    return any(p.search(text) for p in _UNSAFE_INSTRUCTION_PATTERNS)


def _parse_personality_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        stripped = stripped[start : end + 1]
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"personality_update: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("personality_update: expected JSON object")
    return payload


class BotIdentityService:
    def __init__(
        self,
        *,
        prompt_config: RuntimePromptConfig,
        identity_config: RuntimeIdentityConfig,
        client: OpenRouterClient | None = None,
    ) -> None:
        self._prompt_config = prompt_config
        self._identity_config = identity_config
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._identity_config.enabled

    async def get_identity(
        self,
        session: AsyncSession,
        chat_id: int,
    ) -> BotIdentityProfile | None:
        return await get_bot_identity(session, chat_id)

    async def get_personality_prompt(
        self,
        session: AsyncSession,
        chat_id: int,
    ) -> str:
        identity = await get_bot_identity(session, chat_id)
        if (
            identity is not None
            and identity.personality_prompt
            and identity.personality_prompt.strip()
        ):
            return identity.personality_prompt
        return self._prompt_config.base_personality_prompt

    async def describe_identity(
        self,
        session: AsyncSession,
        chat_id: int,
    ) -> str:
        identity = await get_bot_identity(session, chat_id)
        if identity is None:
            return (
                "No persistent identity stored for this chat yet.\n"
                "Using default YAML personality."
            )
        lines: list[str] = []
        if identity.display_name:
            lines.append(f"Display name: {identity.display_name}")
        else:
            lines.append("Display name: (not set; using account profile name)")
        lines.append(f"Personality version: {identity.personality_version}")
        if identity.personality_updated_at:
            lines.append(
                f"Personality updated: {identity.personality_updated_at.isoformat()}"
            )
        if identity.last_self_update_at:
            lines.append(
                f"Last self-update: {identity.last_self_update_at.isoformat()}"
            )
        if identity.self_update_reason:
            lines.append(f"Last reason: {identity.self_update_reason}")
        if identity.avatar_updated_at:
            lines.append(f"Avatar updated: {identity.avatar_updated_at.isoformat()}")
        pending = identity.pending_proposal
        if pending:
            lines.append("Pending proposal exists (admin must approve).")
        return "\n".join(lines).strip()

    async def describe_personality(
        self,
        session: AsyncSession,
        chat_id: int,
    ) -> str:
        prompt = await self.get_personality_prompt(session, chat_id)
        return prompt or "(empty)"

    async def set_personality(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        new_prompt: str,
        reason: str | None,
        is_self_update: bool = False,
    ) -> PersonalityUpdateOutcome:
        cleaned = (new_prompt or "").strip()
        limits = self._identity_config.personality
        if not cleaned:
            return PersonalityUpdateOutcome(applied=False, reason="empty_prompt")
        if len(cleaned) > limits.max_prompt_chars:
            return PersonalityUpdateOutcome(applied=False, reason="prompt_too_long")
        if _is_unsafe_prompt(cleaned):
            return PersonalityUpdateOutcome(applied=False, reason="unsafe_prompt")

        existing = await get_bot_identity(session, chat_id)
        now = datetime.now(UTC)
        version = (existing.personality_version if existing else 0) + 1
        await upsert_bot_identity(
            session,
            chat_id=chat_id,
            display_name=existing.display_name if existing else None,
            avatar_file_id=existing.avatar_file_id if existing else None,
            avatar_prompt=existing.avatar_prompt if existing else None,
            avatar_updated_at=existing.avatar_updated_at if existing else None,
            personality_prompt=cleaned,
            personality_version=version,
            personality_updated_at=now,
            last_self_update_at=(now if is_self_update else (existing.last_self_update_at if existing else None)),
            self_update_reason=reason if is_self_update else (existing.self_update_reason if existing else None),
            pending_proposal=None,
            metadata_json=existing.metadata_json if existing else None,
        )
        log.info(
            "bot_identity.personality_updated",
            chat_id=chat_id,
            version=version,
            is_self_update=is_self_update,
            reason=reason,
        )
        return PersonalityUpdateOutcome(applied=True, reason="ok", new_version=version)

    async def set_display_name(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        display_name: str,
    ) -> PersonalityUpdateOutcome:
        limits = self._identity_config.display_name
        cleaned = (display_name or "").strip()
        if not cleaned:
            return PersonalityUpdateOutcome(applied=False, reason="empty_name")
        if len(cleaned) > limits.max_length:
            return PersonalityUpdateOutcome(applied=False, reason="name_too_long")

        existing = await get_bot_identity(session, chat_id)
        await upsert_bot_identity(
            session,
            chat_id=chat_id,
            display_name=cleaned,
            avatar_file_id=existing.avatar_file_id if existing else None,
            avatar_prompt=existing.avatar_prompt if existing else None,
            avatar_updated_at=existing.avatar_updated_at if existing else None,
            personality_prompt=existing.personality_prompt if existing else None,
            personality_version=existing.personality_version if existing else 1,
            personality_updated_at=existing.personality_updated_at if existing else None,
            last_self_update_at=existing.last_self_update_at if existing else None,
            self_update_reason=existing.self_update_reason if existing else None,
            pending_proposal=existing.pending_proposal if existing else None,
            metadata_json=existing.metadata_json if existing else None,
        )
        log.info(
            "bot_identity.display_name_updated",
            chat_id=chat_id,
            display_name=cleaned,
        )
        return PersonalityUpdateOutcome(applied=True, reason="ok")

    async def store_pending_proposal(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        proposal: dict[str, Any],
    ) -> None:
        existing = await get_bot_identity(session, chat_id)
        await upsert_bot_identity(
            session,
            chat_id=chat_id,
            display_name=existing.display_name if existing else None,
            avatar_file_id=existing.avatar_file_id if existing else None,
            avatar_prompt=existing.avatar_prompt if existing else None,
            avatar_updated_at=existing.avatar_updated_at if existing else None,
            personality_prompt=existing.personality_prompt if existing else None,
            personality_version=existing.personality_version if existing else 1,
            personality_updated_at=existing.personality_updated_at if existing else None,
            last_self_update_at=existing.last_self_update_at if existing else None,
            self_update_reason=existing.self_update_reason if existing else None,
            pending_proposal=proposal,
            metadata_json=existing.metadata_json if existing else None,
        )

    async def apply_pending_proposal(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
    ) -> PersonalityUpdateOutcome:
        existing = await get_bot_identity(session, chat_id)
        if existing is None or not existing.pending_proposal:
            return PersonalityUpdateOutcome(applied=False, reason="no_pending_proposal")
        proposal = existing.pending_proposal
        if not isinstance(proposal, dict):
            return PersonalityUpdateOutcome(applied=False, reason="bad_proposal")
        new_prompt = str(proposal.get("new_personality") or "").strip()
        reason = str(proposal.get("reason") or "") or None
        if not new_prompt:
            return PersonalityUpdateOutcome(applied=False, reason="empty_proposal")
        return await self.set_personality(
            session,
            chat_id=chat_id,
            new_prompt=new_prompt,
            reason=reason,
            is_self_update=True,
        )

    async def discard_pending_proposal(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
    ) -> bool:
        existing = await get_bot_identity(session, chat_id)
        if existing is None or not existing.pending_proposal:
            return False
        await upsert_bot_identity(
            session,
            chat_id=chat_id,
            display_name=existing.display_name,
            avatar_file_id=existing.avatar_file_id,
            avatar_prompt=existing.avatar_prompt,
            avatar_updated_at=existing.avatar_updated_at,
            personality_prompt=existing.personality_prompt,
            personality_version=existing.personality_version,
            personality_updated_at=existing.personality_updated_at,
            last_self_update_at=existing.last_self_update_at,
            self_update_reason=existing.self_update_reason,
            pending_proposal=None,
            metadata_json=existing.metadata_json,
        )
        return True

    async def propose_personality_update(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        recent_messages_text: str,
    ) -> PersonalityUpdateOutcome:
        """Ask the LLM whether the personality should change. Gated by config and safety."""
        limits = self._identity_config.personality
        if not limits.self_update_enabled:
            return PersonalityUpdateOutcome(applied=False, reason="self_update_disabled")
        if self._client is None:
            return PersonalityUpdateOutcome(applied=False, reason="no_llm_client")

        existing = await get_bot_identity(session, chat_id)
        now = datetime.now(UTC)
        if existing is not None and existing.personality_updated_at is not None:
            age = now - _aware(existing.personality_updated_at)
            if age < timedelta(days=limits.min_days_between_updates):
                return PersonalityUpdateOutcome(applied=False, reason="cooldown")

        chat_memory = await get_chat_memory(session, chat_id)
        chat_memory_text = (
            chat_memory.summary if chat_memory and chat_memory.summary else "(none)"
        )
        current_personality = await self.get_personality_prompt(session, chat_id)

        system_prompt = self._prompt_config.render_system("personality_update")
        user_prompt = self._prompt_config.render_user(
            "personality_update",
            current_personality=current_personality,
            chat_memory=chat_memory_text,
            messages=recent_messages_text or "(no messages)",
        )

        try:
            response = await self._client.complete(
                system_prompt,
                user_prompt,
                temperature=0.1,
                timeout=60.0,
                model=limits.model,
            )
        except Exception as exc:
            log.error("bot_identity.llm_failed", chat_id=chat_id, error=str(exc))
            return PersonalityUpdateOutcome(applied=False, reason="llm_failed")

        try:
            payload = _parse_personality_json(response.text)
        except ValueError as exc:
            log.error("bot_identity.parse_failed", chat_id=chat_id, error=str(exc))
            return PersonalityUpdateOutcome(applied=False, reason="parse_failed")

        should_update = bool(payload.get("should_update"))
        confidence = float(payload.get("confidence") or 0.0)
        new_prompt = str(payload.get("new_personality") or "").strip()
        reason = str(payload.get("reason") or "") or None

        if not should_update:
            return PersonalityUpdateOutcome(applied=False, reason="model_declined")
        if confidence < limits.min_confidence:
            return PersonalityUpdateOutcome(applied=False, reason="low_confidence")
        if not new_prompt:
            return PersonalityUpdateOutcome(applied=False, reason="empty_proposal")
        if len(new_prompt) > limits.max_prompt_chars:
            return PersonalityUpdateOutcome(applied=False, reason="prompt_too_long")
        if _is_unsafe_prompt(new_prompt):
            return PersonalityUpdateOutcome(applied=False, reason="unsafe_prompt")

        if limits.require_admin_approval:
            await self.store_pending_proposal(
                session,
                chat_id=chat_id,
                proposal={
                    "new_personality": new_prompt,
                    "reason": reason or "",
                    "confidence": confidence,
                    "proposed_at": now.isoformat(),
                },
            )
            return PersonalityUpdateOutcome(
                applied=False,
                reason="awaiting_approval",
                awaiting_approval=True,
            )

        return await self.set_personality(
            session,
            chat_id=chat_id,
            new_prompt=new_prompt,
            reason=reason,
            is_self_update=True,
        )
