from __future__ import annotations

from typing import Any, Protocol

from app.telegram_client.types import TgMessage, TgMessageReactionSnapshot


class TelegramClientProtocol(Protocol):
    async def get_self_username(self) -> str | None:
        ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        formatting_entities: list[Any] | None = None,
    ) -> TgMessage | None:
        ...

    async def send_photo(
        self,
        chat_id: int,
        image_bytes: bytes,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        formatting_entities: list[Any] | None = None,
        file_name: str = "stats.png",
    ) -> TgMessage | None:
        ...

    async def delete_messages(
        self,
        chat_id: int,
        message_ids: list[int],
    ) -> None:
        ...

    async def send_typing(
        self,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        ...

    async def set_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> None:
        ...

    async def fetch_message_reaction_snapshot(
        self,
        chat_id: int,
        message_id: int,
        *,
        trigger_emojis: tuple[str, ...] = (),
        limit_per_emoji: int = 200,
    ) -> TgMessageReactionSnapshot | None:
        ...
