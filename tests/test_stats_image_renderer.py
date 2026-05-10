from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services import stats_image_renderer as image_module
from app.services.stats_image_renderer import StatsImageRenderer
from app.services.stats_report import StatsLink, StatsReport


@dataclass(frozen=True)
class _FakeTextUrl:
    offset: int
    length: int
    url: str


@dataclass(frozen=True)
class _FakeBlockquote:
    offset: int
    length: int
    collapsed: bool


@pytest.fixture(autouse=True)
def _stub_telethon(monkeypatch):
    monkeypatch.setattr(
        image_module,
        "_text_url_entity",
        lambda offset, length, url: _FakeTextUrl(offset, length, url),
    )
    monkeypatch.setattr(
        image_module,
        "_blockquote_entity",
        lambda offset, length: _FakeBlockquote(offset, length, True),
    )
    monkeypatch.setattr(
        StatsImageRenderer,
        "_render_figure",
        lambda self, report, parsed: b"PNGBYTES",
    )


async def test_render_returns_image_caption_and_detail() -> None:
    report = StatsReport(
        title="User Stats · last 7d",
        visible_lines=["Active senders: 3", "Top chatter: alice (12)"],
        graph_lines=[
            "alice          ████████████ 12",
            "bob            ██████░░░░░░ 6",
            "carol          ███░░░░░░░░░ 3",
        ],
        detail_lines=["1. alice ████████████ 12", "2. bob ██████░░░░░░ 6"],
    )

    rendered = await StatsImageRenderer().render(report, max_chars=3900)

    assert rendered.image_bytes == b"PNGBYTES"
    assert "User Stats · last 7d" in rendered.caption
    assert "Top chatter: alice (12)" in rendered.caption
    assert rendered.detail_text.startswith("Details:")
    assert "> 1. alice" in rendered.detail_text
    assert any(isinstance(e, _FakeBlockquote) and e.collapsed for e in rendered.detail_entities)


async def test_render_preserves_visible_link_in_caption() -> None:
    report = StatsReport(
        title="Reaction Stats · last 7d",
        visible_lines=["Top magnets:", "1. Message · 18 reactions"],
        graph_lines=["🔥 ████ 18"],
        detail_lines=[],
        links=[
            StatsLink(
                section="visible",
                line_index=1,
                start=3,
                length=7,
                url="https://t.me/c/123/45",
            )
        ],
    )

    rendered = await StatsImageRenderer().render(report, max_chars=3900)

    text_urls = [e for e in rendered.caption_entities if isinstance(e, _FakeTextUrl)]
    assert text_urls, "expected a text-url entity for the magnet link"
    assert text_urls[0].url == "https://t.me/c/123/45"
    assert "Message" in rendered.caption


async def test_render_detail_truncated_when_too_long() -> None:
    long_lines = [f"line-{i} " + "x" * 50 for i in range(20)]
    report = StatsReport(
        title="Stats · last 7d",
        visible_lines=["Short"],
        graph_lines=[],
        detail_lines=long_lines,
    )

    rendered = await StatsImageRenderer().render(report, max_chars=200)

    assert "Details truncated" in rendered.detail_text
    assert len(rendered.detail_text) <= 200 + 80  # allow notice slightly over limit
