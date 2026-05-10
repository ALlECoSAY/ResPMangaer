from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import (
    UserDisplay,
    count_commands_by_name,
    count_media_types,
    count_messages,
    count_messages_by_hour,
    count_messages_by_user,
    count_messages_by_weekday,
    count_reactions,
    count_threads,
    fetch_messages_for_word_stats,
    fetch_user_display_names,
    fetch_user_displays,
    get_thread_titles,
    llm_usage_stats,
    thread_starters,
    top_reacted_messages,
)
from app.services.stats_config import RuntimeStatsConfig
from app.services.stats_renderer import bar, bar_lines, sparkline
from app.services.stats_report import StatsLink, StatsReport, StatsSection
from app.services.telegram_links import message_link, user_link
from app.utils.time import parse_lookback

StatsSubcommand = Literal["summary", "users", "words", "times", "threads", "reactions", "fun"]

_SUBCOMMANDS: set[str] = {
    "summary",
    "users",
    "words",
    "times",
    "threads",
    "reactions",
    "fun",
}

_WORD_RE = re.compile(r"(?u)\b[^\W\d_][\w']{2,}\b")
_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "but",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "into",
    "its",
    "just",
    "like",
    "not",
    "now",
    "our",
    "out",
    "really",
    "that",
    "the",
    "their",
    "then",
    "there",
    "they",
    "this",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
}


@dataclass(frozen=True)
class StatsRequest:
    subcommand: StatsSubcommand
    lookback: timedelta


def parse_stats_args(args: str, default_lookback_days: int) -> StatsRequest | str:
    """Parse `/stats` args.

    Accepted examples: ``users 30``, ``words 2d``, ``times 12h``. A bare number
    means days. Returns a friendly error string instead of raising.
    """
    tokens = (args or "").split()
    subcommand: StatsSubcommand = "summary"
    lookback_hours = default_lookback_days * 24

    if tokens and tokens[0].lower() in _SUBCOMMANDS:
        subcommand = tokens.pop(0).lower()  # type: ignore[assignment]
    elif tokens and parse_lookback(tokens[0].lower()) is None and not tokens[0].isdigit():
        return (
            "Usage: /stats [users|words|times|threads|reactions|fun] [days|12h|2d]"
        )

    for token in tokens:
        lowered = token.lower()
        parsed = parse_lookback(lowered)
        if parsed is not None:
            lookback_hours = parsed
            break
        if lowered.isdigit():
            days = int(lowered)
            if days <= 0:
                return "Stats lookback must be at least 1 day."
            lookback_hours = days * 24
            break
        return (
            "Usage: /stats [users|words|times|threads|reactions|fun] [days|12h|2d]"
        )

    return StatsRequest(subcommand=subcommand, lookback=timedelta(hours=lookback_hours))


class StatsService:
    def __init__(self, config: RuntimeStatsConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def default_lookback_days(self) -> int:
        return self._config.default_lookback_days

    @property
    def max_message_chars(self) -> int:
        return self._config.max_message_chars

    async def summary(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        since = self._since(lookback)
        total = await count_messages(session, chat_id, since)
        if total == 0:
            return StatsReport(
                title=self._title("Chat Stats", lookback),
                visible_lines=["No messages found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        users = await count_messages_by_user(session, chat_id, since)
        labels = await self._labels(session, [user_id for user_id, _count in users[:5]])
        words = self._word_counter(await fetch_messages_for_word_stats(session, chat_id, since))
        media = await count_media_types(session, chat_id, since)
        hours = await count_messages_by_hour(session, chat_id, since)
        weekdays = await count_messages_by_weekday(session, chat_id, since)
        reactions = await count_reactions(session, chat_id, since)
        commands = await count_commands_by_name(session, chat_id, since)
        llm_calls, llm_tokens, avg_latency = await llm_usage_stats(session, chat_id, since)

        visible_lines = [
            f"Messages: {total}",
            f"Active senders: {len(users)}",
        ]
        if users:
            visible_lines.append(f"Top chatter: {labels[users[0][0]]} ({users[0][1]})")
        if words:
            word, count = words.most_common(1)[0]
            visible_lines.append(f"Word of the window: {word} ({count})")
        if media:
            content_type, count = next(iter(media.items()))
            visible_lines.append(f"Most common content: {content_type} ({count})")
        if hours:
            hour, count = max(hours.items(), key=lambda item: item[1])
            visible_lines.append(f"Busiest hour: {hour:02d}:00 ({count})")
        if weekdays:
            day, count = max(weekdays.items(), key=lambda item: item[1])
            visible_lines.append(f"Busiest day: {_WEEKDAYS[day % 7]} ({count})")
        if reactions:
            emoji, count = reactions[0]
            visible_lines.append(f"Favorite reaction: {emoji} ({count})")

        graph_lines: list[str] = []
        if hours:
            graph_lines.append(
                "Hours: " + sparkline([hours.get(hour, 0) for hour in range(24)])
            )
        elif users:
            graph_lines.extend(
                bar_lines(
                    [(labels[user_id], count) for user_id, count in users[:3]],
                    width=8,
                )
            )

        detail_lines: list[str] = []
        if commands:
            command, count = next(iter(commands.items()))
            detail_lines.append(f"Top command: /{command} ({count})")
        if llm_calls:
            detail_lines.append(
                f"LLM usage: {llm_calls} calls, ~{llm_tokens} tokens, avg {avg_latency:.0f}ms"
            )
        return StatsReport(
            title=self._title("Chat Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
        )

    async def user_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        since = self._since(lookback)
        rows = await count_messages_by_user(session, chat_id, since)
        user_ids = [user_id for user_id, _count in rows]
        displays = await self._user_displays(session, user_ids)
        if not rows:
            return StatsReport(
                title=self._title("User Stats", lookback),
                visible_lines=["No messages found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        labels = {
            user_id: displays.get(
                user_id,
                UserDisplay(user_id=user_id, username=None, display_name=f"user {user_id}"),
            ).display_name
            for user_id in user_ids
        }
        top_rows = rows[: self._config.top_n_users]
        graph_lines = bar_lines([(labels[user_id], count) for user_id, count in top_rows])
        links = self._user_links("graph", graph_lines, top_rows, displays)

        visible_lines = [f"Active senders: {len(rows)}"]
        if top_rows:
            user_id, count = top_rows[0]
            visible_lines.append(f"Top chatter: {labels[user_id]} ({count})")
        if len(rows) > 1:
            user_id, count = rows[-1]
            visible_lines.append(f"Quiet corner: {labels[user_id]} ({count})")

        detail_lines = self._ranked_lines([(labels[user_id], count) for user_id, count in rows])
        return StatsReport(
            title=self._title("User Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
            links=links,
        )

    async def word_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        texts = await fetch_messages_for_word_stats(session, chat_id, self._since(lookback))
        words = self._word_counter(texts)
        emojis = self._emoji_counter(texts)
        domains = self._domain_counter(texts)

        if not words and not emojis and not domains:
            return StatsReport(
                title=self._title("Word Stats", lookback),
                visible_lines=["No text found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        visible_lines: list[str] = []
        graph_lines: list[str] = []
        detail_lines: list[str] = []
        if words:
            top_words = words.most_common(self._config.top_n_words)
            word, count = top_words[0]
            visible_lines.append(f"Top word: {word} ({count})")
            graph_lines.append("Top words:")
            graph_lines.extend(bar_lines(top_words))
            detail_lines.append("Words:")
            detail_lines.extend(self._ranked_lines(top_words))
        if emojis:
            top_emojis = emojis.most_common(10)
            emoji, count = top_emojis[0]
            visible_lines.append(f"Top emoji: {emoji} ({count})")
            graph_lines.append("Top emojis:")
            graph_lines.extend(bar_lines(top_emojis, width=8))
            detail_lines.append("Emojis:")
            detail_lines.extend(self._ranked_lines(top_emojis))
        if domains:
            top_domains = domains.most_common(10)
            domain, count = top_domains[0]
            visible_lines.append(f"Top domain: {domain} ({count})")
            graph_lines.append("Top shared domains:")
            graph_lines.extend(bar_lines(top_domains, width=8))
            detail_lines.append("Domains:")
            detail_lines.extend(self._ranked_lines(top_domains))
        return StatsReport(
            title=self._title("Word Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
        )

    async def time_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        since = self._since(lookback)
        hours = await count_messages_by_hour(session, chat_id, since)
        weekdays = await count_messages_by_weekday(session, chat_id, since)

        if not hours and not weekdays:
            return StatsReport(
                title=self._title("Time Stats", lookback),
                visible_lines=["No messages found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        visible_lines: list[str] = []
        graph_lines: list[str] = []
        detail_lines: list[str] = []
        if hours:
            hour, count = max(hours.items(), key=lambda item: item[1])
            visible_lines.append(f"Busiest hour: {hour:02d}:00 ({count})")
            graph_lines.append(
                "Hours: " + sparkline([hours.get(hour, 0) for hour in range(24)])
            )
            detail_lines.append("By hour:")
            max_hour = max(hours.values())
            detail_lines.extend(
                f"{hour:02d}:00 {bar(count, max_hour)} {count}"
                for hour, count in sorted(hours.items())
            )
        if weekdays:
            day, count = max(weekdays.items(), key=lambda item: item[1])
            visible_lines.append(f"Busiest day: {_WEEKDAYS[day % 7]} ({count})")
            max_count = max(weekdays.values())
            graph_lines.append("Weekdays:")
            graph_lines.extend(
                f"{_WEEKDAYS[day % 7]} {bar(count, max_count)} {count}"
                for day, count in sorted(weekdays.items())
            )
        return StatsReport(
            title=self._title("Time Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
        )

    async def thread_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        since = self._since(lookback)
        threads = await count_threads(session, chat_id, since)
        starters = await thread_starters(session, chat_id, since)
        titles = await get_thread_titles(session, chat_id)
        labels = await self._labels(session, [user_id for user_id, _count in starters])

        if not threads:
            return StatsReport(
                title=self._title("Thread Stats", lookback),
                visible_lines=["No thread activity found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        top_threads = [
            (titles.get(thread_id) or f"thread {thread_id}", count)
            for thread_id, count in threads[: self._config.top_n_threads]
        ]
        visible_lines = [f"Active threads: {len(threads)}"]
        graph_lines = ["Top threads:", *bar_lines(top_threads)]
        detail_lines = ["Top threads:", *self._ranked_lines(top_threads)]
        if starters:
            detail_lines.append("Thread starters:")
            detail_lines.extend(
                self._ranked_lines(
                    [
                        (labels[user_id], count)
                        for user_id, count in starters[: self._config.top_n_users]
                    ]
                )
            )
        return StatsReport(
            title=self._title("Thread Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
        )

    async def reaction_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
        *,
        chat_username: str | None = None,
    ) -> StatsReport:
        since = self._since(lookback)
        reactions = await count_reactions(session, chat_id, since)
        magnets = await top_reacted_messages(
            session,
            chat_id,
            since,
            limit=self._config.top_n_threads,
        )

        if not reactions and not magnets:
            return StatsReport(
                title=self._title("Reaction Stats", lookback),
                visible_lines=["No reactions found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        visible_lines: list[str] = []
        graph_lines: list[str] = []
        detail_lines: list[str] = []
        links: list[StatsLink] = []
        if reactions:
            top_reactions = reactions[:15]
            emoji, count = top_reactions[0]
            visible_lines.append(f"Favorite reaction: {emoji} ({count})")
            graph_lines.append("Reaction scoreboard:")
            graph_lines.extend(bar_lines(top_reactions))
            detail_lines.append("Reaction scoreboard:")
            detail_lines.extend(self._ranked_lines(top_reactions))
        if magnets:
            visible_lines.append("Top magnets:")
            for index, magnet in enumerate(magnets, start=1):
                label = "Message"
                preview = self._preview(magnet.preview)
                line = f"{index}. {label} · {magnet.count} reactions"
                if preview:
                    line = f"{line} · {preview}"
                line_index = len(visible_lines)
                visible_lines.append(line)
                links.append(
                    StatsLink(
                        section="visible",
                        line_index=line_index,
                        start=line.index(label),
                        length=len(label),
                        url=message_link(
                            chat_id=chat_id,
                            chat_username=chat_username,
                            message_thread_id=magnet.message_thread_id,
                            message_id=magnet.message_id,
                        ),
                    )
                )
            detail_lines.append("Reaction magnets:")
            detail_lines.extend(
                f"{index}. message {magnet.message_id} ({magnet.count})"
                for index, magnet in enumerate(magnets, start=1)
            )
        return StatsReport(
            title=self._title("Reaction Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=graph_lines,
            detail_lines=detail_lines,
            links=links,
        )

    async def fun_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> StatsReport:
        since = self._since(lookback)
        users = await count_messages_by_user(session, chat_id, since)
        labels = await self._labels(session, [user_id for user_id, _count in users[:1]])
        texts = await fetch_messages_for_word_stats(session, chat_id, since)
        words = self._word_counter(texts)
        emojis = self._emoji_counter(texts)
        domains = self._domain_counter(texts)
        commands = await count_commands_by_name(session, chat_id, since)

        if not users and not words and not emojis and not domains and not commands:
            return StatsReport(
                title=self._title("Fun Stats", lookback),
                visible_lines=["No award material found for this window."],
                graph_lines=[],
                detail_lines=[],
            )

        visible_lines: list[str] = []
        if users:
            user_id, count = users[0]
            visible_lines.append(f"Chatty McChatface: {labels[user_id]} with {count} messages")
        if words:
            word, count = words.most_common(1)[0]
            visible_lines.append(f"Buzzword badge: {word} appeared {count} times")
        if emojis:
            emoji, count = emojis.most_common(1)[0]
            visible_lines.append(f"Emoji monarch: {emoji} appeared {count} times")
        if domains:
            domain, count = domains.most_common(1)[0]
            visible_lines.append(f"Link machine favorite: {domain} ({count})")
        if commands:
            command, count = next(iter(commands.items()))
            visible_lines.append(f"Button-pusher trophy: /{command} ({count})")
        return StatsReport(
            title=self._title("Fun Stats", lookback),
            visible_lines=visible_lines,
            graph_lines=[],
            detail_lines=[],
        )

    def _since(self, lookback: timedelta) -> datetime:
        return datetime.now(UTC) - lookback

    def _title(self, title: str, lookback: timedelta) -> str:
        hours = max(1, int(lookback.total_seconds() // 3600))
        window = f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"
        return f"{title} · last {window}"

    async def _labels(self, session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
        labels = await fetch_user_display_names(session, user_ids)
        for user_id in user_ids:
            labels.setdefault(user_id, f"user {user_id}")
        return labels

    async def _user_displays(
        self,
        session: AsyncSession | None,
        user_ids: list[int],
    ) -> dict[int, UserDisplay]:
        if not user_ids:
            return {}
        if session is None:
            labels = await fetch_user_display_names(session, user_ids)  # type: ignore[arg-type]
            return {
                user_id: UserDisplay(
                    user_id=user_id,
                    username=label[1:] if label.startswith("@") else None,
                    display_name=label,
                )
                for user_id, label in labels.items()
            }
        displays = await fetch_user_displays(session, user_ids)
        for user_id in user_ids:
            displays.setdefault(
                user_id,
                UserDisplay(user_id=user_id, username=None, display_name=f"user {user_id}"),
            )
        return displays

    def _user_links(
        self,
        section: StatsSection,
        lines: list[str],
        rows: list[tuple[int, int]],
        displays: dict[int, UserDisplay],
    ) -> list[StatsLink]:
        links: list[StatsLink] = []
        for line_index, (user_id, _count) in enumerate(rows):
            display = displays.get(user_id)
            if display is None:
                continue
            url = user_link(display.username)
            if url is None:
                continue
            start = lines[line_index].find(display.display_name)
            if start < 0:
                continue
            links.append(
                StatsLink(
                    section=section,
                    line_index=line_index,
                    start=start,
                    length=len(display.display_name),
                    url=url,
                )
            )
        return links

    @staticmethod
    def _preview(value: str | None, *, max_chars: int = 48) -> str | None:
        if not value:
            return None
        one_line = " ".join(value.split())
        if len(one_line) <= max_chars:
            return one_line
        return f"{one_line[: max_chars - 1]}…"

    def _word_counter(self, texts: list[str]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for text in texts:
            stripped = _URL_RE.sub(" ", text)
            for match in _WORD_RE.finditer(stripped.lower()):
                word = match.group(0).strip("'")
                if len(word) < 3 or word in _STOP_WORDS:
                    continue
                counter[word] += 1
        return counter

    def _emoji_counter(self, texts: list[str]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for text in texts:
            for char in text:
                category = unicodedata.category(char)
                if category == "So" or (category == "Sk" and ord(char) > 127):
                    counter[char] += 1
        return counter

    def _domain_counter(self, texts: list[str]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for text in texts:
            for match in _URL_RE.finditer(text):
                host = urlparse(match.group(0)).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    counter[host] += 1
        return counter

    def _ranked_lines(self, rows: list[tuple[str, int]]) -> list[str]:
        if not rows:
            return []
        max_count = max(count for _label, count in rows)
        lines: list[str] = []
        for index, (label, count) in enumerate(rows, start=1):
            lines.append(f"{index}. {label} {self._bar(count, max_count)} {count}")
        return lines

    @staticmethod
    def _bar(count: int, max_count: int, width: int = 12) -> str:
        return bar(count, max_count, width)
