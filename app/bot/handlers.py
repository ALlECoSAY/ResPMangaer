from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.commands import parse_command
from app.bot.formatting import reply_in_same_thread
from app.config import Settings
from app.db.session import session_scope
from app.llm.openrouter_client import OpenRouterError
from app.logging_config import get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import TldrService, parse_tldr_args
from app.utils.telegram import message_thread_id_for

log = get_logger(__name__)


def build_router(
    settings: Settings,
    access_control: AccessControl,
    yaml_store: YamlAccessStore,
    ai_service: AiAnswerService,
    tldr_service: TldrService,
    bot_username_provider,
) -> Router:
    router = Router(name="commands")

    @router.message(Command("ai", ignore_case=True))
    async def handle_ai(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_use_ai_commands(user_id)
        if not decision.allowed:
            await reply_in_same_thread(bot, message, decision.reason or "denied", settings.max_reply_chars)
            return

        parsed = parse_command(message.text, bot_username_provider())
        question = parsed.args if parsed else ""
        if not question:
            await reply_in_same_thread(bot, message, "Usage: /ai <question>", settings.max_reply_chars)
            return

        try:
            async with session_scope() as session:
                response = await ai_service.answer(
                    session=session,
                    chat_id=message.chat.id,
                    message_thread_id=message_thread_id_for(message),
                    question=question,
                    request_message_id=message.message_id,
                )
            await reply_in_same_thread(bot, message, response.text, settings.max_reply_chars)
        except OpenRouterError as exc:
            log.error("ai.failed", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not get an AI response right now. Try again later or use a smaller question.",
                settings.max_reply_chars,
            )
        except SQLAlchemyError as exc:
            log.error("ai.db_error", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not get an AI response right now. Try again later.",
                settings.max_reply_chars,
            )

    @router.message(Command("tldr", ignore_case=True))
    async def handle_tldr(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_use_ai_commands(user_id)
        if not decision.allowed:
            await reply_in_same_thread(bot, message, decision.reason or "denied", settings.max_reply_chars)
            return

        parsed = parse_command(message.text, bot_username_provider())
        args = parsed.args if parsed else ""
        request = parse_tldr_args(args, default_lookback_hours=settings.tldr_lookback_hours)

        try:
            async with session_scope() as session:
                response, friendly = await tldr_service.summarize(
                    session=session,
                    chat_id=message.chat.id,
                    message_thread_id=message_thread_id_for(message),
                    request=request,
                    request_message_id=message.message_id,
                )
            if friendly:
                await reply_in_same_thread(bot, message, friendly, settings.max_reply_chars)
                return
            assert response is not None
            await reply_in_same_thread(bot, message, response.text, settings.max_reply_chars)
        except OpenRouterError as exc:
            log.error("tldr.failed", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not summarize the recent activity right now.",
                settings.max_reply_chars,
            )
        except SQLAlchemyError as exc:
            log.error("tldr.db_error", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not summarize the recent activity right now.",
                settings.max_reply_chars,
            )

    @router.message(Command("add_whitelist", ignore_case=True))
    async def handle_add_whitelist(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_manage_whitelist(user_id)
        if not decision.allowed:
            await reply_in_same_thread(bot, message, decision.reason or "denied", settings.max_reply_chars)
            return

        parsed = parse_command(message.text, bot_username_provider())
        args = (parsed.args if parsed else "").strip()

        target_id: int | None = None
        note: str | None = None
        if args:
            tokens = args.split(maxsplit=1)
            try:
                target_id = int(tokens[0])
            except ValueError:
                target_id = None
            if target_id is not None and len(tokens) == 2:
                note = tokens[1].strip() or None
            elif target_id is None:
                # No numeric id; treat the args as a note only when reply provides target.
                note = args
        if target_id is None and message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id

        if target_id is None:
            usage = (
                "Usage: /add_whitelist <telegram_user_id> [note]\n"
                "Or reply to a user's message with /add_whitelist"
            )
            await reply_in_same_thread(bot, message, usage, settings.max_reply_chars)
            return

        try:
            added = await yaml_store.add_whitelisted_user(
                user_id=target_id,
                note=note,
                added_by_user_id=user_id or 0,
            )
        except OSError as exc:
            log.error("whitelist.write_failed", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "Could not update the whitelist file.",
                settings.max_reply_chars,
            )
            return

        if added:
            log.info(
                "whitelist.added",
                admin_user_id=user_id,
                target_user_id=target_id,
                note=note,
            )
            await reply_in_same_thread(
                bot, message, f"Added user {target_id} to whitelist.", settings.max_reply_chars
            )
        else:
            await reply_in_same_thread(
                bot, message, f"User {target_id} is already whitelisted.", settings.max_reply_chars
            )

    return router
