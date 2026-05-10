from __future__ import annotations

from datetime import timedelta

from app.services import stats_service as stats_module
from app.services.stats_service import StatsService, parse_stats_args


class _FakeStatsConfig:
    enabled = True
    default_lookback_days = 7
    top_n_users = 3
    top_n_words = 5
    top_n_threads = 2
    max_message_chars = 3900


def test_parse_stats_args_defaults_to_summary():
    request = parse_stats_args("", default_lookback_days=7)
    assert not isinstance(request, str)
    assert request.subcommand == "summary"
    assert request.lookback == timedelta(days=7)


def test_parse_stats_args_subcommand_and_plain_days():
    request = parse_stats_args("users 30", default_lookback_days=7)
    assert not isinstance(request, str)
    assert request.subcommand == "users"
    assert request.lookback == timedelta(days=30)


def test_parse_stats_args_duration_token():
    request = parse_stats_args("words 12h", default_lookback_days=7)
    assert not isinstance(request, str)
    assert request.subcommand == "words"
    assert request.lookback == timedelta(hours=12)


def test_parse_stats_args_unknown_subcommand():
    error = parse_stats_args("bananas", default_lookback_days=7)
    assert isinstance(error, str)
    assert "Usage" in error


async def test_summary_formats_highlights(monkeypatch):
    async def _count_messages(session, chat_id, since):
        return 5

    async def _count_messages_by_user(session, chat_id, since):
        return [(100, 3), (200, 2)]

    async def _fetch_user_display_names(session, user_ids):
        return {100: "@alice", 200: "@bob"}

    async def _fetch_messages_for_word_stats(session, chat_id, since):
        return ["hello project hello", "see https://example.com"]

    async def _count_media_types(session, chat_id, since):
        return {"text": 4, "photo": 1}

    async def _count_messages_by_hour(session, chat_id, since):
        return {9: 2, 20: 3}

    async def _count_messages_by_weekday(session, chat_id, since):
        return {1: 2, 5: 3}

    async def _count_reactions(session, chat_id, since):
        return [("+1", 4)]

    async def _count_commands_by_name(session, chat_id, since):
        return {"ai": 2}

    async def _llm_usage_stats(session, chat_id, since):
        return 2, 123, 45.0

    monkeypatch.setattr(stats_module, "count_messages", _count_messages)
    monkeypatch.setattr(stats_module, "count_messages_by_user", _count_messages_by_user)
    monkeypatch.setattr(stats_module, "fetch_user_display_names", _fetch_user_display_names)
    monkeypatch.setattr(
        stats_module,
        "fetch_messages_for_word_stats",
        _fetch_messages_for_word_stats,
    )
    monkeypatch.setattr(stats_module, "count_media_types", _count_media_types)
    monkeypatch.setattr(stats_module, "count_messages_by_hour", _count_messages_by_hour)
    monkeypatch.setattr(
        stats_module,
        "count_messages_by_weekday",
        _count_messages_by_weekday,
    )
    monkeypatch.setattr(stats_module, "count_reactions", _count_reactions)
    monkeypatch.setattr(stats_module, "count_commands_by_name", _count_commands_by_name)
    monkeypatch.setattr(stats_module, "llm_usage_stats", _llm_usage_stats)

    report = await StatsService(_FakeStatsConfig()).summary(
        session=None,
        chat_id=1,
        lookback=timedelta(days=7),
    )

    text = "\n".join([*report.visible_lines, *report.graph_lines, *report.detail_lines])
    assert "Messages: 5" in text
    assert "Top chatter: @alice (3)" in text
    assert "Word of the window: hello (2)" in text
    assert "Top command: /ai (2)" in text


async def test_word_stats_counts_words_emojis_and_domains(monkeypatch):
    async def _fetch_messages_for_word_stats(session, chat_id, since):
        return [
            "Launch launch launch https://example.com",
            "Ship it! https://example.com/path",
        ]

    monkeypatch.setattr(
        stats_module,
        "fetch_messages_for_word_stats",
        _fetch_messages_for_word_stats,
    )

    report = await StatsService(_FakeStatsConfig()).word_stats(
        session=None,
        chat_id=1,
        lookback=timedelta(days=1),
    )

    text = "\n".join([*report.visible_lines, *report.graph_lines, *report.detail_lines])
    assert "launch" in text
    assert "example.com" in text


async def test_user_stats_includes_graph_and_username_link(monkeypatch):
    async def _count_messages_by_user(session, chat_id, since):
        return [(100, 12), (200, 6), (300, 3)]

    async def _fetch_user_display_names(session, user_ids):
        return {100: "@alice", 200: "Bob", 300: "Carol"}

    monkeypatch.setattr(stats_module, "count_messages_by_user", _count_messages_by_user)
    monkeypatch.setattr(stats_module, "fetch_user_display_names", _fetch_user_display_names)

    report = await StatsService(_FakeStatsConfig()).user_stats(
        session=None,
        chat_id=1,
        lookback=timedelta(days=1),
    )

    assert any("█" in line for line in report.graph_lines)
    assert report.links[0].url == "https://t.me/alice"
    assert all("tg://user?id" not in link.url for link in report.links)


async def test_time_stats_includes_sparkline(monkeypatch):
    async def _count_messages_by_hour(session, chat_id, since):
        return {0: 1, 1: 2, 2: 4}

    async def _count_messages_by_weekday(session, chat_id, since):
        return {1: 4, 2: 8}

    monkeypatch.setattr(stats_module, "count_messages_by_hour", _count_messages_by_hour)
    monkeypatch.setattr(stats_module, "count_messages_by_weekday", _count_messages_by_weekday)

    report = await StatsService(_FakeStatsConfig()).time_stats(
        session=None,
        chat_id=1,
        lookback=timedelta(days=1),
    )

    assert any(line.startswith("Hours: ") for line in report.graph_lines)
    assert any("Mon" in line and "█" in line for line in report.graph_lines)


async def test_reaction_stats_links_magnets(monkeypatch):
    from app.db.repositories import ReactedMessageStat

    async def _count_reactions(session, chat_id, since):
        return [("🔥", 5), ("👍", 2)]

    async def _top_reacted_messages(session, chat_id, since, limit):
        return [
            ReactedMessageStat(
                message_id=50,
                message_thread_id=12,
                count=5,
                preview="a spicy message",
            )
        ]

    monkeypatch.setattr(stats_module, "count_reactions", _count_reactions)
    monkeypatch.setattr(stats_module, "top_reacted_messages", _top_reacted_messages)

    report = await StatsService(_FakeStatsConfig()).reaction_stats(
        session=None,
        chat_id=-1001234567890,
        lookback=timedelta(days=1),
    )

    assert "a spicy message" in "\n".join(report.visible_lines)
    assert report.links[0].url == "https://t.me/c/1234567890/12/50"
