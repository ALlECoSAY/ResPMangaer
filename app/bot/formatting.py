from __future__ import annotations

from typing import Any

from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgMessage


def split_for_telegram(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return [text]
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        slice_end = max_chars
        # Prefer splitting at the last newline within the limit.
        newline_idx = remaining.rfind("\n", 0, max_chars)
        if newline_idx >= max_chars // 2:
            slice_end = newline_idx
        else:
            space_idx = remaining.rfind(" ", 0, max_chars)
            if space_idx >= max_chars // 2:
                slice_end = space_idx
        chunk = remaining[:slice_end].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[slice_end:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def reply_in_same_thread(
    client: TelegramClientProtocol,
    message: TgMessage,
    text: str,
    max_chars: int,
    reply_to_message_id: int | None = None,
    formatting_entities: list[Any] | None = None,
) -> list[TgMessage]:
    chunks = split_for_telegram(text, max_chars)
    if formatting_entities and len(chunks) > 1:
        formatting_entities = None
    sent: list[TgMessage] = []
    for index, chunk in enumerate(chunks):
        sent_message = await client.send_message(
            message.chat.id,
            chunk,
            reply_to_message_id=reply_to_message_id if index == 0 else None,
            message_thread_id=message.message_thread_id or None,
            formatting_entities=formatting_entities if index == 0 else None,
        )
        if sent_message is not None:
            sent.append(sent_message)
    return sent
