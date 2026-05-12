from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import get_bot_identity, upsert_bot_identity
from app.logging_config import get_logger
from app.services.identity_config import RuntimeIdentityConfig
from app.services.image_generation_client import (
    ImageGenerationClient,
    ImageGenerationError,
)

if TYPE_CHECKING:
    from app.services.bot_identity_service import BotIdentityService
    from app.telegram_client.client import TelegramClientProtocol

log = get_logger(__name__)


@dataclass(frozen=True)
class AvatarRefreshOutcome:
    applied: bool
    reason: str
    avatar_prompt: str | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _monthly_count(metadata: list | dict | None, now: datetime) -> int:
    if not isinstance(metadata, dict):
        return 0
    counts = metadata.get("avatar_monthly_counts")
    if not isinstance(counts, dict):
        return 0
    key = f"{now.year:04d}-{now.month:02d}"
    try:
        return int(counts.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _bumped_metadata(
    metadata: list | dict | None,
    now: datetime,
) -> dict:
    base = metadata if isinstance(metadata, dict) else {}
    counts = dict(base.get("avatar_monthly_counts") or {})
    key = f"{now.year:04d}-{now.month:02d}"
    try:
        existing = int(counts.get(key) or 0)
    except (TypeError, ValueError):
        existing = 0
    counts[key] = existing + 1
    new = dict(base)
    new["avatar_monthly_counts"] = counts
    return new


class AvatarService:
    def __init__(
        self,
        *,
        identity_config: RuntimeIdentityConfig,
        identity_service: BotIdentityService,
        image_client: ImageGenerationClient,
    ) -> None:
        self._identity_config = identity_config
        self._identity_service = identity_service
        self._image_client = image_client

    async def refresh_avatar(
        self,
        session: AsyncSession,
        *,
        client: TelegramClientProtocol,
        chat_id: int,
        admin_instruction: str | None = None,
    ) -> AvatarRefreshOutcome:
        limits = self._identity_config.avatar
        if not limits.enabled:
            return AvatarRefreshOutcome(applied=False, reason="avatar_disabled")
        if not self._image_client.configured:
            return AvatarRefreshOutcome(
                applied=False, reason="image_generation_unconfigured"
            )

        existing = await get_bot_identity(session, chat_id)
        now = datetime.now(UTC)
        if existing is not None and existing.avatar_updated_at is not None:
            age = now - _aware(existing.avatar_updated_at)
            if age < timedelta(days=limits.min_days_between_updates):
                return AvatarRefreshOutcome(applied=False, reason="cooldown")

        if limits.max_generations_per_month > 0:
            current = _monthly_count(
                existing.metadata_json if existing else None,
                now,
            )
            if current >= limits.max_generations_per_month:
                return AvatarRefreshOutcome(
                    applied=False, reason="monthly_limit_reached"
                )

        personality = await self._identity_service.get_personality_prompt(
            session, chat_id
        )
        prompt_parts = [
            "A friendly chat-bot avatar based on the following persona:",
            personality.strip()[:600],
        ]
        if admin_instruction:
            prompt_parts.append(f"Admin instruction: {admin_instruction.strip()[:300]}")
        prompt_parts.append(
            "Style: clean, simple, recognizable as a profile picture at small sizes."
        )
        prompt = "\n".join(prompt_parts)

        try:
            image_bytes = await self._image_client.generate_avatar(
                prompt,
                model=limits.image_model,
            )
        except ImageGenerationError as exc:
            log.error("bot_identity.avatar_generate_failed", error=str(exc))
            return AvatarRefreshOutcome(applied=False, reason="generation_failed")

        try:
            await client.update_profile_photo(image_bytes)
        except Exception as exc:
            log.error("bot_identity.avatar_upload_failed", error=str(exc))
            return AvatarRefreshOutcome(applied=False, reason="upload_failed")

        await upsert_bot_identity(
            session,
            chat_id=chat_id,
            display_name=existing.display_name if existing else None,
            avatar_file_id=existing.avatar_file_id if existing else None,
            avatar_prompt=prompt,
            avatar_updated_at=now,
            personality_prompt=existing.personality_prompt if existing else None,
            personality_version=existing.personality_version if existing else 1,
            personality_updated_at=existing.personality_updated_at if existing else None,
            last_self_update_at=existing.last_self_update_at if existing else None,
            self_update_reason=existing.self_update_reason if existing else None,
            pending_proposal=existing.pending_proposal if existing else None,
            metadata_json=_bumped_metadata(
                existing.metadata_json if existing else None,
                now,
            ),
        )
        log.info("bot_identity.avatar_updated", chat_id=chat_id)
        return AvatarRefreshOutcome(applied=True, reason="ok", avatar_prompt=prompt)
