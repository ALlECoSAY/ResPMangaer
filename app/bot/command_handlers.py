from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from app.auth.access_control import AccessControl
from app.auth.yaml_store import YamlAccessStore
from app.bot.commands import parse_command
from app.bot.formatting import reply_in_same_thread
from app.config import Settings
from app.db.session import session_scope
from app.llm.openrouter_client import OpenRouterError
from app.llm.runtime_config import RuntimeContextConfig
from app.logging_config import get_logger
from app.services.ai_answer_service import AiAnswerService
from app.services.tldr_service import (
    TldrScope,
    TldrService,
    make_tldr_request,
    parse_tldr_lookback,
)
from app.telegram_client.client import TelegramClientProtocol
from app.telegram_client.types import TgMessage
from app.utils.telegram import display_name, message_thread_id_for

log = get_logger(__name__)


@dataclass
class CommandContext:
    message: TgMessage
    client: TelegramClientProtocol
    settings: Settings
    access_control: AccessControl
    yaml_store: YamlAccessStore
    ai_service: AiAnswerService
    tldr_service: TldrService
    runtime_config: RuntimeContextConfig
    bot_username_provider: Callable[[], str | None]


async def _reply(
    ctx: CommandContext,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    await reply_in_same_thread(
        ctx.client,
        ctx.message,
        text,
        ctx.runtime_config.max_reply_chars,
        reply_to_message_id=reply_to_message_id,
    )


def _parsed_command(ctx: CommandContext):
    return parse_command(ctx.message.text, ctx.bot_username_provider())


async def handle_ai_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_use_ai_commands(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    parsed = _parsed_command(ctx)
    question = parsed.args if parsed else ""
    if not question:
        await _reply(
            ctx,
            "Usage: /ai <question>",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    try:
        await ctx.client.send_typing(
            ctx.message.chat.id,
            message_thread_id=ctx.message.message_thread_id or None,
        )
    except Exception as exc:
        log.warning("ai.chat_action_failed", error=str(exc))

    try:
        async with session_scope() as session:
            response = await ctx.ai_service.answer(
                session=session,
                chat_id=ctx.message.chat.id,
                message_thread_id=message_thread_id_for(ctx.message),
                question=question,
                request_message_id=ctx.message.message_id,
            )
        await _reply(
            ctx,
            response.text,
            reply_to_message_id=ctx.message.message_id,
        )
    except OpenRouterError as exc:
        log.error("ai.failed", error=str(exc))
        await _reply(
            ctx,
            "I could not get an AI response right now. Try again later or use a smaller question.",
            reply_to_message_id=ctx.message.message_id,
        )
    except SQLAlchemyError as exc:
        log.error("ai.db_error", error=str(exc))
        await _reply(
            ctx,
            "I could not get an AI response right now. Try again later.",
            reply_to_message_id=ctx.message.message_id,
        )


async def handle_tldr_command(ctx: CommandContext, scope: TldrScope) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_use_ai_commands(user_id)
    if not decision.allowed:
        await _reply(ctx, decision.reason or "denied")
        return

    parsed = _parsed_command(ctx)
    args = parsed.args if parsed else ""
    lookback = parse_tldr_lookback(
        args,
        default_lookback_hours=ctx.runtime_config.tldr_lookback_hours,
    )
    request = make_tldr_request(scope=scope, lookback_hours=lookback)
    log_event = f"{scope}_tldr"

    try:
        async with session_scope() as session:
            response, friendly = await ctx.tldr_service.summarize(
                session=session,
                chat_id=ctx.message.chat.id,
                message_thread_id=message_thread_id_for(ctx.message),
                request=request,
                request_message_id=ctx.message.message_id,
            )
        if friendly:
            await _reply(ctx, friendly)
            return
        assert response is not None
        await _reply(ctx, response.text)
    except OpenRouterError as exc:
        log.error(f"{log_event}.failed", error=str(exc))
        await _reply(ctx, "I could not summarize the recent activity right now.")
    except SQLAlchemyError as exc:
        log.error(f"{log_event}.db_error", error=str(exc))
        await _reply(ctx, "I could not summarize the recent activity right now.")


async def handle_whitelist_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_manage_whitelist(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    target_user = ctx.message.reply_to_from_user
    if target_user is None:
        await _reply(
            ctx,
            "Reply to a user's message with /whitelist to add them.",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    prompt = (
        f"To confirm adding user {display_name(target_user)} (id={target_user.id}), send:\n"
        f"/confirm_whitelist {target_user.id}"
    )
    await _reply(
        ctx,
        prompt,
        reply_to_message_id=ctx.message.message_id,
    )


async def handle_confirm_whitelist_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_manage_whitelist(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    parsed = _parsed_command(ctx)
    target_token = (parsed.args.split(maxsplit=1)[0] if parsed and parsed.args else "").strip()
    if not target_token:
        await _reply(
            ctx,
            "Usage: /confirm_whitelist <user_id>",
            reply_to_message_id=ctx.message.message_id,
        )
        return
    try:
        target_id = int(target_token)
    except ValueError:
        await _reply(
            ctx,
            "Usage: /confirm_whitelist <user_id>",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    try:
        added = await ctx.yaml_store.add_whitelisted_user(
            user_id=target_id,
            note=None,
            added_by_user_id=user_id or 0,
        )
    except OSError as exc:
        log.error("whitelist.write_failed", error=str(exc))
        await _reply(
            ctx,
            "Could not update whitelist.yaml right now.",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    if added:
        log.info(
            "whitelist.added",
            admin_user_id=user_id,
            target_user_id=target_id,
        )
        await _reply(
            ctx,
            f"User {target_id} added to whitelist.",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    await _reply(
        ctx,
        f"User {target_id} is already in whitelist.",
        reply_to_message_id=ctx.message.message_id,
    )
