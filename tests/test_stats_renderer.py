from __future__ import annotations

from dataclasses import dataclass

from app.services.stats_renderer import StatsRenderer, bar, sparkline
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


def test_bar_and_sparkline_are_deterministic() -> None:
    assert bar(5, 10, width=4) == "██░░"
    assert sparkline([0, 1, 2]) == "▁▅█"


def test_renderer_adds_link_and_folded_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.stats_renderer._text_url_entity",
        lambda *, offset, length, url: _FakeTextUrl(offset, length, url),
    )
    monkeypatch.setattr(
        "app.services.stats_renderer._blockquote_entity",
        lambda *, offset, length, collapsed: _FakeBlockquote(offset, length, collapsed),
    )
    report = StatsReport(
        title="Reaction Stats · last 7d",
        visible_lines=["1. Message · 18 reactions"],
        graph_lines=["🔥 ████████████ 18"],
        detail_lines=["full detailed breakdown"],
        links=[
            StatsLink(
                section="visible",
                line_index=0,
                start=3,
                length=7,
                url="https://t.me/c/123/1",
            )
        ],
    )

    rendered = StatsRenderer().render(report, max_chars=3900)

    assert "Details:" in rendered.text
    assert "> full detailed breakdown" in rendered.text
    assert any(isinstance(entity, _FakeTextUrl) for entity in rendered.entities)
    quote = next(entity for entity in rendered.entities if isinstance(entity, _FakeBlockquote))
    assert quote.collapsed is True
    assert "tg://user?id" not in rendered.text


def test_renderer_truncates_details_inside_single_message(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.stats_renderer._blockquote_entity",
        lambda *, offset, length, collapsed: _FakeBlockquote(offset, length, collapsed),
    )
    report = StatsReport(
        title="Stats · last 7d",
        visible_lines=["Short"],
        graph_lines=[],
        detail_lines=["x" * 100, "y" * 100],
    )

    rendered = StatsRenderer().render(report, max_chars=120)

    assert len(rendered.text) <= 120
    assert "Details truncated" in rendered.text
