from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message


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
    bot: Bot,
    message: Message,
    text: str,
    max_chars: int,
    reply_to_message_id: int | None = None,
) -> list[Message]:
    chunks = split_for_telegram(text, max_chars)
    sent: list[Message] = []
    for index, chunk in enumerate(chunks):
        kwargs: dict = {"chat_id": message.chat.id, "text": chunk}
        if message.message_thread_id:
            kwargs["message_thread_id"] = message.message_thread_id
        if index == 0 and reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        sent.append(await bot.send_message(**kwargs))
    return sent
