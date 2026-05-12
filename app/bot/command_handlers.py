from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from app.bot.commands import parse_command
from app.bot.formatting import reply_in_same_thread
from app.db.session import session_scope
from app.logging_config import get_logger
from app.services.memory_service import (
    format_explicit_memory_result,
    is_explicit_memory_request,
)
from app.services.stats_image_renderer import StatsImageRenderer
from app.services.stats_renderer import StatsRenderer
from app.services.stats_report import StatsReport
from app.services.stats_service import parse_stats_args
from app.utils.telegram import display_name, message_thread_id_for, strip_notification_mentions

if TYPE_CHECKING:
    from app.auth.access_control import AccessControl
    from app.auth.yaml_store import YamlAccessStore
    from app.config import Settings
    from app.llm.runtime_config import RuntimeContextConfig
    from app.services.ai_answer_service import AiAnswerService
    from app.services.auto_delete_config import RuntimeAutoDeleteConfig
    from app.services.memory_service import MemoryService
    from app.services.stats_service import StatsService
    from app.services.tldr_service import TldrScope, TldrService
    from app.telegram_client.client import TelegramClientProtocol
    from app.telegram_client.types import TgMessage

log = get_logger(__name__)
_AUTO_DELETE_TASKS: set[asyncio.Task] = set()

HELP_TEXT = """Available commands:
/help - show this command list
/ai <question> - answer using recent thread/chat context
/tldr [12h|2d] - summarize the current thread
/tldr_all [12h|2d] - summarize recent activity across the chat
/stats [users|words|times|threads|reactions|fun] [days|12h|2d] - show chat statistics (image + collapsed details)
/memory - show compact memory for this chat
/memory_user - reply to a user's message to show stored user memory
/memory_forget <chat|all|user|fact> - admin only; forget stored memory
/memory_refresh - admin only; rebuild compact memory for this chat
/whitelist - admin only; reply to a user to start whitelisting
/confirm_whitelist <user_id> - admin only; confirm a whitelist change

Note: /stats and /help replies auto-delete after the delay configured in config/auto_delete.yaml (defaults to 5 minutes)."""


@dataclass
class CommandContext:
    message: TgMessage
    client: TelegramClientProtocol
    settings: Settings
    access_control: AccessControl
    yaml_store: YamlAccessStore
    ai_service: AiAnswerService
    tldr_service: TldrService
    stats_service: StatsService
    runtime_config: RuntimeContextConfig
    bot_username_provider: Callable[[], str | None]
    auto_delete_config: RuntimeAutoDeleteConfig | None = None
    memory_service: MemoryService | None = None


async def _reply(
    ctx: CommandContext,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    max_chars: int | None = None,
    formatting_entities: list[Any] | None = None,
) -> list[TgMessage]:
    return await reply_in_same_thread(
        ctx.client,
        ctx.message,
        text,
        max_chars or ctx.runtime_config.max_reply_chars,
        reply_to_message_id=reply_to_message_id,
        formatting_entities=formatting_entities,
    )


def _schedule_auto_delete(
    ctx: CommandContext,
    command: str,
    sent_messages: list[TgMessage],
) -> None:
    config = ctx.auto_delete_config
    if config is None:
        return
    delay = config.delay_seconds(command)
    if delay is None:
        return
    message_ids = [msg.message_id for msg in sent_messages if msg is not None]
    if not message_ids:
        return
    chat_id = ctx.message.chat.id
    client = ctx.client

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(delay)
            await client.delete_messages(chat_id, message_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "auto_delete.failed",
                command=command,
                chat_id=chat_id,
                message_ids=message_ids,
                error=str(exc),
            )

    try:
        task = asyncio.create_task(_delete_later())
        _AUTO_DELETE_TASKS.add(task)
        task.add_done_callback(_AUTO_DELETE_TASKS.discard)
    except RuntimeError as exc:
        log.warning("auto_delete.no_running_loop", error=str(exc))


def _parsed_command(ctx: CommandContext):
    return parse_command(ctx.message.text, ctx.bot_username_provider())


def _is_openrouter_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "OpenRouterError"
        and exc.__class__.__module__ == "app.llm.openrouter_client"
    )


async def handle_help_command(ctx: CommandContext) -> None:
    sent = await _reply(
        ctx,
        HELP_TEXT,
        reply_to_message_id=ctx.message.message_id,
    )
    _schedule_auto_delete(ctx, "help", sent)


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

    if is_explicit_memory_request(question):
        if ctx.memory_service is None or not ctx.memory_service.enabled:
            await _reply(
                ctx,
                "Memory is disabled right now.",
                reply_to_message_id=ctx.message.message_id,
            )
            return
        try:
            async with session_scope() as session:
                result = await ctx.memory_service.remember_text(
                    session,
                    chat_id=ctx.message.chat.id,
                    text=question,
                    source_message_id=ctx.message.message_id,
                )
            await _reply(
                ctx,
                format_explicit_memory_result(result),
                reply_to_message_id=ctx.message.message_id,
            )
        except SQLAlchemyError as exc:
            log.error("memory_explicit.db_error", error=str(exc))
            await _reply(
                ctx,
                "I could not update memory right now.",
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
            strip_notification_mentions(response.text),
            reply_to_message_id=ctx.message.message_id,
        )
    except SQLAlchemyError as exc:
        log.error("ai.db_error", error=str(exc))
        await _reply(
            ctx,
            "I could not get an AI response right now. Try again later.",
            reply_to_message_id=ctx.message.message_id,
        )
    except Exception as exc:
        if not _is_openrouter_error(exc):
            raise
        log.error("ai.failed", error=str(exc))
        await _reply(
            ctx,
            "I could not get an AI response right now. Try again later or use a smaller question.",
            reply_to_message_id=ctx.message.message_id,
        )


async def handle_tldr_command(ctx: CommandContext, scope: TldrScope) -> None:
    from app.services.tldr_service import make_tldr_request, parse_tldr_lookback

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
        await _reply(ctx, strip_notification_mentions(response.text))
    except SQLAlchemyError as exc:
        log.error(f"{log_event}.db_error", error=str(exc))
        await _reply(ctx, "I could not summarize the recent activity right now.")
    except Exception as exc:
        if not _is_openrouter_error(exc):
            raise
        log.error(f"{log_event}.failed", error=str(exc))
        await _reply(ctx, "I could not summarize the recent activity right now.")


async def handle_stats_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_use_ai_commands(user_id)
    if not decision.allowed:
        await _reply(ctx, decision.reason or "denied")
        return

    if not ctx.stats_service.enabled:
        await _reply(ctx, "Stats are disabled right now.")
        return

    parsed = _parsed_command(ctx)
    args = parsed.args if parsed else ""
    request = parse_stats_args(
        args,
        default_lookback_days=ctx.stats_service.default_lookback_days,
    )
    if isinstance(request, str):
        await _reply(
            ctx,
            request,
            reply_to_message_id=ctx.message.message_id,
            max_chars=ctx.stats_service.max_message_chars,
        )
        return

    try:
        async with session_scope() as session:
            if request.subcommand == "users":
                report = await ctx.stats_service.user_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
            elif request.subcommand == "words":
                report = await ctx.stats_service.word_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
            elif request.subcommand == "times":
                report = await ctx.stats_service.time_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
            elif request.subcommand == "threads":
                report = await ctx.stats_service.thread_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
            elif request.subcommand == "reactions":
                report = await ctx.stats_service.reaction_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                    chat_username=ctx.message.chat.username,
                )
            elif request.subcommand == "fun":
                report = await ctx.stats_service.fun_stats(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
            else:
                report = await ctx.stats_service.summary(
                    session,
                    ctx.message.chat.id,
                    request.lookback,
                )
        if not isinstance(report, StatsReport):
            report = StatsReport(
                title="Stats",
                visible_lines=list(report),
                graph_lines=[],
                detail_lines=[],
            )

        sent_messages: list[TgMessage] = []
        render_as_images = bool(getattr(ctx.stats_service, "render_as_images", False))
        if render_as_images:
            sent_messages = await _send_stats_as_image(ctx, report)
        else:
            rendered = StatsRenderer().render(
                report,
                max_chars=ctx.stats_service.max_message_chars,
            )
            sent_messages = await _reply(
                ctx,
                rendered.text,
                reply_to_message_id=ctx.message.message_id,
                max_chars=ctx.stats_service.max_message_chars,
                formatting_entities=rendered.entities,
            )
        _schedule_auto_delete(ctx, "stats", sent_messages)
    except SQLAlchemyError as exc:
        log.error("stats.db_error", error=str(exc))
        await _reply(ctx, "I could not compute stats right now.")


def _target_user_id_from_memory_args(ctx: CommandContext, args: str) -> int | None:
    if ctx.message.reply_to_from_user is not None:
        return ctx.message.reply_to_from_user.id
    token = args.split(maxsplit=1)[0] if args else ""
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


async def handle_memory_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_use_ai_commands(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return
    if ctx.memory_service is None or not ctx.memory_service.enabled:
        await _reply(ctx, "Memory is disabled right now.")
        return

    try:
        async with session_scope() as session:
            text = await ctx.memory_service.describe_chat_memory(
                session,
                chat_id=ctx.message.chat.id,
            )
        await _reply(ctx, text, reply_to_message_id=ctx.message.message_id)
    except SQLAlchemyError as exc:
        log.error("memory.db_error", error=str(exc))
        await _reply(ctx, "I could not read memory right now.")


async def handle_memory_user_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_use_ai_commands(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return
    if ctx.memory_service is None or not ctx.memory_service.enabled:
        await _reply(ctx, "Memory is disabled right now.")
        return

    parsed = _parsed_command(ctx)
    target_user_id = _target_user_id_from_memory_args(ctx, parsed.args if parsed else "")
    if target_user_id is None:
        await _reply(
            ctx,
            "Usage: reply to a user's message with /memory_user, or send /memory_user <user_id>",
            reply_to_message_id=ctx.message.message_id,
        )
        return

    try:
        async with session_scope() as session:
            text = await ctx.memory_service.describe_user_memory(
                session,
                chat_id=ctx.message.chat.id,
                user_id=target_user_id,
            )
        await _reply(ctx, text, reply_to_message_id=ctx.message.message_id)
    except SQLAlchemyError as exc:
        log.error("memory_user.db_error", error=str(exc))
        await _reply(ctx, "I could not read user memory right now.")


async def handle_memory_forget_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_manage_whitelist(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return
    if ctx.memory_service is None:
        await _reply(ctx, "Memory service is not configured.")
        return

    parsed = _parsed_command(ctx)
    args = parsed.args if parsed else ""
    parts = args.split(maxsplit=1)
    mode = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    usage = (
        "Usage: /memory_forget chat | all | user [user_id] | fact <text>. "
        "For user, you can also reply to the user's message."
    )
    if not mode:
        await _reply(ctx, usage, reply_to_message_id=ctx.message.message_id)
        return

    try:
        async with session_scope() as session:
            if mode == "thread":
                count = await ctx.memory_service.forget_thread(
                    session,
                    chat_id=ctx.message.chat.id,
                    message_thread_id=message_thread_id_for(ctx.message),
                )
            elif mode == "chat":
                count = await ctx.memory_service.forget_chat(
                    session,
                    chat_id=ctx.message.chat.id,
                )
            elif mode == "all":
                count = await ctx.memory_service.forget_all(
                    session,
                    chat_id=ctx.message.chat.id,
                )
            elif mode == "user":
                target_user_id = _target_user_id_from_memory_args(ctx, rest)
                if target_user_id is None:
                    await _reply(ctx, usage, reply_to_message_id=ctx.message.message_id)
                    return
                count = await ctx.memory_service.forget_user(
                    session,
                    chat_id=ctx.message.chat.id,
                    user_id=target_user_id,
                )
            elif mode == "fact":
                if not rest:
                    await _reply(ctx, usage, reply_to_message_id=ctx.message.message_id)
                    return
                count = await ctx.memory_service.forget_fact(
                    session,
                    chat_id=ctx.message.chat.id,
                    message_thread_id=message_thread_id_for(ctx.message),
                    fact_text=rest,
                )
            else:
                await _reply(ctx, usage, reply_to_message_id=ctx.message.message_id)
                return
        await _reply(
            ctx,
            f"Forgot {count} memory record(s).",
            reply_to_message_id=ctx.message.message_id,
        )
    except SQLAlchemyError as exc:
        log.error("memory_forget.db_error", error=str(exc))
        await _reply(ctx, "I could not forget memory right now.")


async def handle_memory_refresh_command(ctx: CommandContext) -> None:
    user_id = ctx.message.from_user.id if ctx.message.from_user else None
    decision = await ctx.access_control.can_manage_whitelist(user_id)
    if not decision.allowed:
        await _reply(
            ctx,
            decision.reason or "denied",
            reply_to_message_id=ctx.message.message_id,
        )
        return
    if ctx.memory_service is None or not ctx.memory_service.enabled:
        await _reply(ctx, "Memory is disabled right now.")
        return

    try:
        await ctx.client.send_typing(
            ctx.message.chat.id,
            message_thread_id=ctx.message.message_thread_id or None,
        )
    except Exception as exc:
        log.warning("memory_refresh.chat_action_failed", error=str(exc))

    try:
        async with session_scope() as session:
            result = await ctx.memory_service.refresh_thread(
                session,
                chat_id=ctx.message.chat.id,
                message_thread_id=message_thread_id_for(ctx.message),
                request_message_id=ctx.message.message_id,
                force=True,
            )
        if result.updated:
            text = (
                "Memory refreshed "
                f"from {result.new_message_count} message(s)"
                + (
                    f" up to message {result.latest_message_id}."
                    if result.latest_message_id is not None
                    else "."
                )
            )
        else:
            text = f"Memory was not refreshed: {result.skipped_reason or 'skipped'}."
        await _reply(ctx, text, reply_to_message_id=ctx.message.message_id)
    except SQLAlchemyError as exc:
        log.error("memory_refresh.db_error", error=str(exc))
        await _reply(ctx, "I could not refresh memory right now.")
    except Exception as exc:
        log.error("memory_refresh.failed", error=str(exc))
        await _reply(ctx, "I could not refresh memory right now.")


async def _send_stats_as_image(
    ctx: CommandContext,
    report: StatsReport,
) -> list[TgMessage]:
    sent: list[TgMessage] = []
    try:
        rendered_image = await StatsImageRenderer().render(
            report,
            max_chars=ctx.stats_service.max_message_chars,
        )
    except Exception as exc:
        log.warning("stats.image_render_failed", error=str(exc))
        rendered = StatsRenderer().render(
            report,
            max_chars=ctx.stats_service.max_message_chars,
        )
        return await _reply(
            ctx,
            rendered.text,
            reply_to_message_id=ctx.message.message_id,
            max_chars=ctx.stats_service.max_message_chars,
            formatting_entities=rendered.entities,
        )

    photo_msg = await ctx.client.send_photo(
        ctx.message.chat.id,
        rendered_image.image_bytes,
        caption=rendered_image.caption,
        reply_to_message_id=ctx.message.message_id,
        message_thread_id=ctx.message.message_thread_id or None,
        formatting_entities=rendered_image.caption_entities or None,
        file_name="stats.png",
    )
    if photo_msg is not None:
        sent.append(photo_msg)

    if rendered_image.detail_text:
        detail_messages = await reply_in_same_thread(
            ctx.client,
            ctx.message,
            rendered_image.detail_text,
            ctx.stats_service.max_message_chars,
            reply_to_message_id=None,
            formatting_entities=rendered_image.detail_entities or None,
        )
        sent.extend(detail_messages)
    return sent


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
