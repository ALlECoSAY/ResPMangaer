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
    get_thread_titles,
    llm_usage_stats,
    thread_starters,
    top_reacted_messages,
)
from app.services.stats_config import RuntimeStatsConfig
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
    ) -> list[str]:
        since = self._since(lookback)
        total = await count_messages(session, chat_id, since)
        if total == 0:
            return [self._title("Chat Stats", lookback), "No messages found for this window."]

        users = await count_messages_by_user(session, chat_id, since)
        labels = await self._labels(session, [user_id for user_id, _count in users[:5]])
        words = self._word_counter(await fetch_messages_for_word_stats(session, chat_id, since))
        media = await count_media_types(session, chat_id, since)
        hours = await count_messages_by_hour(session, chat_id, since)
        weekdays = await count_messages_by_weekday(session, chat_id, since)
        reactions = await count_reactions(session, chat_id, since)
        commands = await count_commands_by_name(session, chat_id, since)
        llm_calls, llm_tokens, avg_latency = await llm_usage_stats(session, chat_id, since)

        lines = [
            self._title("Chat Stats", lookback),
            f"Messages: {total}",
            f"Active senders: {len(users)}",
        ]
        if users:
            lines.append(f"Top chatter: {labels[users[0][0]]} ({users[0][1]})")
        if words:
            word, count = words.most_common(1)[0]
            lines.append(f"Word of the window: {word} ({count})")
        if media:
            content_type, count = next(iter(media.items()))
            lines.append(f"Most common content: {content_type} ({count})")
        if hours:
            hour, count = max(hours.items(), key=lambda item: item[1])
            lines.append(f"Busiest hour: {hour:02d}:00 ({count})")
        if weekdays:
            day, count = max(weekdays.items(), key=lambda item: item[1])
            lines.append(f"Busiest day: {_WEEKDAYS[day % 7]} ({count})")
        if reactions:
            emoji, count = reactions[0]
            lines.append(f"Favorite reaction: {emoji} ({count})")
        if commands:
            command, count = next(iter(commands.items()))
            lines.append(f"Top command: /{command} ({count})")
        if llm_calls:
            lines.append(
                f"LLM usage: {llm_calls} calls, ~{llm_tokens} tokens, avg {avg_latency:.0f}ms"
            )
        return lines

    async def user_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        since = self._since(lookback)
        rows = await count_messages_by_user(session, chat_id, since)
        labels = await self._labels(session, [user_id for user_id, _count in rows])
        lines = [self._title("User Stats", lookback)]
        if not rows:
            lines.append("No messages found for this window.")
            return lines
        lines.extend(
            self._ranked_lines(
                [(labels[user_id], count) for user_id, count in rows[: self._config.top_n_users]]
            )
        )
        if len(rows) > 1:
            user_id, count = rows[-1]
            lines.append(f"Quiet corner: {labels[user_id]} ({count})")
        return lines

    async def word_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        texts = await fetch_messages_for_word_stats(session, chat_id, self._since(lookback))
        words = self._word_counter(texts)
        emojis = self._emoji_counter(texts)
        domains = self._domain_counter(texts)

        lines = [self._title("Word Stats", lookback)]
        if not words and not emojis and not domains:
            lines.append("No text found for this window.")
            return lines
        if words:
            lines.append("Top words:")
            lines.extend(self._ranked_lines(words.most_common(self._config.top_n_words)))
        if emojis:
            lines.append("")
            lines.append("Top emojis in messages:")
            lines.extend(self._ranked_lines(emojis.most_common(10)))
        if domains:
            lines.append("")
            lines.append("Top shared domains:")
            lines.extend(self._ranked_lines(domains.most_common(10)))
        return lines

    async def time_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        since = self._since(lookback)
        hours = await count_messages_by_hour(session, chat_id, since)
        weekdays = await count_messages_by_weekday(session, chat_id, since)

        lines = [self._title("Time Stats", lookback)]
        if not hours and not weekdays:
            lines.append("No messages found for this window.")
            return lines
        if hours:
            lines.append("By hour:")
            lines.extend(
                f"{hour:02d}:00 {self._bar(count, max(hours.values()))} {count}"
                for hour, count in sorted(hours.items())
            )
        if weekdays:
            lines.append("")
            lines.append("By weekday:")
            max_count = max(weekdays.values())
            lines.extend(
                f"{_WEEKDAYS[day % 7]} {self._bar(count, max_count)} {count}"
                for day, count in sorted(weekdays.items())
            )
        return lines

    async def thread_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        since = self._since(lookback)
        threads = await count_threads(session, chat_id, since)
        starters = await thread_starters(session, chat_id, since)
        titles = await get_thread_titles(session, chat_id)
        labels = await self._labels(session, [user_id for user_id, _count in starters])

        lines = [self._title("Thread Stats", lookback)]
        if not threads:
            lines.append("No thread activity found for this window.")
            return lines
        lines.append("Top threads:")
        lines.extend(
            self._ranked_lines(
                [
                    (titles.get(thread_id) or f"thread {thread_id}", count)
                    for thread_id, count in threads[: self._config.top_n_threads]
                ]
            )
        )
        if starters:
            lines.append("")
            lines.append("Thread starters:")
            lines.extend(
                self._ranked_lines(
                    [
                        (labels[user_id], count)
                        for user_id, count in starters[: self._config.top_n_users]
                    ]
                )
            )
        return lines

    async def reaction_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        since = self._since(lookback)
        reactions = await count_reactions(session, chat_id, since)
        magnets = await top_reacted_messages(
            session,
            chat_id,
            since,
            limit=self._config.top_n_threads,
        )

        lines = [self._title("Reaction Stats", lookback)]
        if not reactions and not magnets:
            lines.append("No reactions found for this window.")
            return lines
        if reactions:
            lines.append("Reaction scoreboard:")
            lines.extend(self._ranked_lines(reactions[:15]))
        if magnets:
            lines.append("")
            lines.append("Reaction magnets:")
            lines.extend(
                self._ranked_lines(
                    [(f"message {message_id}", count) for message_id, count in magnets]
                )
            )
        return lines

    async def fun_stats(
        self,
        session: AsyncSession,
        chat_id: int,
        lookback: timedelta,
    ) -> list[str]:
        since = self._since(lookback)
        users = await count_messages_by_user(session, chat_id, since)
        labels = await self._labels(session, [user_id for user_id, _count in users[:1]])
        texts = await fetch_messages_for_word_stats(session, chat_id, since)
        words = self._word_counter(texts)
        emojis = self._emoji_counter(texts)
        domains = self._domain_counter(texts)
        commands = await count_commands_by_name(session, chat_id, since)

        lines = [self._title("Fun Stats", lookback)]
        if not users and not words and not emojis and not domains and not commands:
            lines.append("No award material found for this window.")
            return lines
        if users:
            user_id, count = users[0]
            lines.append(f"Chatty McChatface: {labels[user_id]} with {count} messages")
        if words:
            word, count = words.most_common(1)[0]
            lines.append(f"Buzzword badge: {word} appeared {count} times")
        if emojis:
            emoji, count = emojis.most_common(1)[0]
            lines.append(f"Emoji monarch: {emoji} appeared {count} times")
        if domains:
            domain, count = domains.most_common(1)[0]
            lines.append(f"Link machine favorite: {domain} ({count})")
        if commands:
            command, count = next(iter(commands.items()))
            lines.append(f"Button-pusher trophy: /{command} ({count})")
        return lines

    def _since(self, lookback: timedelta) -> datetime:
        return datetime.now(UTC) - lookback

    def _title(self, title: str, lookback: timedelta) -> str:
        hours = max(1, int(lookback.total_seconds() // 3600))
        window = f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"
        return f"{title} (last {window})"

    async def _labels(self, session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
        labels = await fetch_user_display_names(session, user_ids)
        for user_id in user_ids:
            labels.setdefault(user_id, f"user {user_id}")
        return labels

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
        if max_count <= 0:
            return ""
        filled = max(1, round((count / max_count) * width))
        return "[" + "#" * filled + "." * (width - filled) + "]"
