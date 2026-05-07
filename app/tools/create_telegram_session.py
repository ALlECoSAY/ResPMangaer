from __future__ import annotations

import asyncio
import getpass
import sys
from collections.abc import Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.config import get_settings


async def _prompt_text(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def _prompt_secret(prompt: str) -> str:
    return (await asyncio.to_thread(getpass.getpass, prompt)).strip()


async def _resolve_login_code(
    env_code: str,
    prompt_fn: Callable[[str], Awaitable[str]] = _prompt_text,
) -> str:
    code = env_code.strip()
    if code:
        return code
    result = await prompt_fn("Enter the Telegram login code: ")
    return str(result).strip()


async def _resolve_2fa_password(
    env_password: str,
    prompt_fn: Callable[[str], Awaitable[str]] = _prompt_secret,
) -> str:
    password = env_password.strip()
    if password:
        return password
    result = await prompt_fn("Enter Telegram 2FA password: ")
    return str(result).strip()


async def run() -> int:
    settings = get_settings()

    missing: list[str] = []
    if settings.telegram_api_id is None:
        missing.append("TELEGRAM_API_ID")
    if not settings.telegram_api_hash:
        missing.append("TELEGRAM_API_HASH")
    if not settings.telegram_user_phone:
        missing.append("TELEGRAM_USER_PHONE")
    if missing:
        print(f"Missing settings for session bootstrap: {', '.join(missing)}", file=sys.stderr)
        return 2

    settings.telegram_user_session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        str(settings.telegram_user_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(
                f"Telegram session already authorized for user id={getattr(me, 'id', None)} "
                f"username={getattr(me, 'username', None)}"
            )
            return 0

        await client.send_code_request(settings.telegram_user_phone)
        code = await _resolve_login_code(settings.telegram_login_code)
        try:
            await client.sign_in(phone=settings.telegram_user_phone, code=code)
        except SessionPasswordNeededError:
            password = await _resolve_2fa_password(
                settings.telegram_user_2fa_password
            )
            await client.sign_in(password=password)

        me = await client.get_me()
        print(
            f"Telegram session saved to {settings.telegram_user_session_path} for "
            f"user id={getattr(me, 'id', None)} username={getattr(me, 'username', None)}"
        )
        return 0
    finally:
        await client.disconnect()


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
