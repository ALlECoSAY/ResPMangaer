from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageReactionUpdated,
)
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
from app.services.reaction_service import ReactionService
from app.services.tldr_service import (
    TldrScope,
    TldrService,
    make_tldr_request,
    parse_tldr_lookback,
)
from app.utils.telegram import display_name, message_thread_id_for

WL_CB_ADD = "wl:add:"
WL_CB_CANCEL = "wl:cancel"


def _whitelist_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Да", callback_data=f"{WL_CB_ADD}{target_id}"),
                InlineKeyboardButton(text="🔴 Нет", callback_data=WL_CB_CANCEL),
            ]
        ]
    )

log = get_logger(__name__)


def build_router(
    settings: Settings,
    access_control: AccessControl,
    yaml_store: YamlAccessStore,
    ai_service: AiAnswerService,
    tldr_service: TldrService,
    reaction_service: ReactionService,
    bot_username_provider,
) -> Router:
    router = Router(name="commands")

    @router.message_reaction()
    async def handle_message_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
        if settings.allowed_chat_ids and event.chat.id not in settings.allowed_chat_ids:
            return
        try:
            async with session_scope() as session:
                await reaction_service.handle_reaction_update(
                    session=session,
                    bot=bot,
                    event=event,
                )
        except SQLAlchemyError as exc:
            log.error("reactions.db_error", error=str(exc))
        except Exception as exc:  # don't let reaction handling crash polling
            log.error("reactions.unexpected", error=str(exc))

    @router.message(Command("ai", ignore_case=True))
    async def handle_ai(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_use_ai_commands(user_id)
        if not decision.allowed:
            await reply_in_same_thread(
                bot,
                message,
                decision.reason or "denied",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
            return

        parsed = parse_command(message.text, bot_username_provider())
        question = parsed.args if parsed else ""
        if not question:
            await reply_in_same_thread(
                bot,
                message,
                "Usage: /ai <question>",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
            return

        chat_action_kwargs: dict = {"chat_id": message.chat.id, "action": "typing"}
        if message.message_thread_id:
            chat_action_kwargs["message_thread_id"] = message.message_thread_id
        try:
            await bot.send_chat_action(**chat_action_kwargs)
        except Exception as exc:
            log.warning("ai.chat_action_failed", error=str(exc))

        try:
            async with session_scope() as session:
                response = await ai_service.answer(
                    session=session,
                    chat_id=message.chat.id,
                    message_thread_id=message_thread_id_for(message),
                    question=question,
                    request_message_id=message.message_id,
                )
            await reply_in_same_thread(
                bot,
                message,
                response.text,
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
        except OpenRouterError as exc:
            log.error("ai.failed", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not get an AI response right now. Try again later or use a smaller question.",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
        except SQLAlchemyError as exc:
            log.error("ai.db_error", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not get an AI response right now. Try again later.",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )

    async def _run_tldr(scope: TldrScope, message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_use_ai_commands(user_id)
        if not decision.allowed:
            await reply_in_same_thread(bot, message, decision.reason or "denied", settings.max_reply_chars)
            return

        parsed = parse_command(message.text, bot_username_provider())
        args = parsed.args if parsed else ""
        lookback = parse_tldr_lookback(args, default_lookback_hours=settings.tldr_lookback_hours)
        request = make_tldr_request(scope=scope, lookback_hours=lookback)

        log_event = f"{scope}_tldr"
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
            log.error(f"{log_event}.failed", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not summarize the recent activity right now.",
                settings.max_reply_chars,
            )
        except SQLAlchemyError as exc:
            log.error(f"{log_event}.db_error", error=str(exc))
            await reply_in_same_thread(
                bot,
                message,
                "I could not summarize the recent activity right now.",
                settings.max_reply_chars,
            )

    @router.message(Command("tldr", ignore_case=True))
    async def handle_tldr(message: Message, bot: Bot) -> None:
        await _run_tldr("thread", message, bot)

    @router.message(Command("tldr_all", ignore_case=True))
    async def handle_tldr_all(message: Message, bot: Bot) -> None:
        await _run_tldr("all", message, bot)

    @router.message(Command("whitelist", ignore_case=True))
    async def handle_whitelist(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id if message.from_user else None
        decision = await access_control.can_manage_whitelist(user_id)
        if not decision.allowed:
            await reply_in_same_thread(
                bot,
                message,
                decision.reason or "denied",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
            return

        target_user = (
            message.reply_to_message.from_user
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        )
        if target_user is None:
            await reply_in_same_thread(
                bot,
                message,
                "Reply to a user's message with /whitelist to add them.",
                settings.max_reply_chars,
                reply_to_message_id=message.message_id,
            )
            return

        prompt = f"Точно? Добавить {display_name(target_user)} (id={target_user.id}) в whitelist?"
        send_kwargs: dict = {
            "chat_id": message.chat.id,
            "text": prompt,
            "reply_markup": _whitelist_keyboard(target_user.id),
            "reply_to_message_id": message.message_id,
        }
        if message.message_thread_id:
            send_kwargs["message_thread_id"] = message.message_thread_id
        await bot.send_message(**send_kwargs)

    @router.callback_query(F.data.startswith("wl:"))
    async def handle_whitelist_callback(query: CallbackQuery) -> None:
        clicker_id = query.from_user.id if query.from_user else None
        if not await access_control.is_admin(clicker_id):
            await query.answer("Только админ может подтвердить.", show_alert=True)
            return

        data = query.data or ""
        message = query.message

        async def _edit(text: str) -> None:
            if message is None:
                return
            try:
                await message.edit_text(text)
            except TelegramBadRequest as exc:
                log.warning("whitelist.edit_failed", error=str(exc))

        if data == WL_CB_CANCEL:
            await _edit("Отменено.")
            await query.answer("Отменено.")
            return

        if data.startswith(WL_CB_ADD):
            try:
                target_id = int(data[len(WL_CB_ADD):])
            except ValueError:
                await query.answer("Bad payload.", show_alert=True)
                return
            try:
                added = await yaml_store.add_whitelisted_user(
                    user_id=target_id,
                    note=None,
                    added_by_user_id=clicker_id or 0,
                )
            except OSError as exc:
                log.error("whitelist.write_failed", error=str(exc))
                await query.answer("Не удалось записать whitelist.", show_alert=True)
                return
            if added:
                log.info(
                    "whitelist.added",
                    admin_user_id=clicker_id,
                    target_user_id=target_id,
                )
                await _edit(f"✅ Пользователь {target_id} добавлен в whitelist.")
            else:
                await _edit(f"Пользователь {target_id} уже в whitelist.")
            await query.answer()
            return

        await query.answer("Неизвестное действие.", show_alert=True)

    return router
