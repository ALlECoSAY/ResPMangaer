from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.stats_report import StatsLink, StatsReport

_BAR_CHARS = "█░"
_BAR_RE = re.compile(rf"^(?P<label>.+?)\s+(?P<bar>[{_BAR_CHARS}]+)\s+(?P<count>\d+)\s*$")
_RANKED_RE = re.compile(rf"^\d+\.\s+(?P<label>.+?)\s+(?P<bar>[{_BAR_CHARS}]+)\s+(?P<count>\d+)\s*$")
_HOUR_BAR_RE = re.compile(rf"^(?P<hour>\d{{1,2}}):00\s+(?P<bar>[{_BAR_CHARS}]+)\s+(?P<count>\d+)\s*$")
_SPARKLINE_HOURS_RE = re.compile(r"^Hours:\s+(?P<spark>[▁-█]{24})\s*$")
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@dataclass
class _Group:
    title: str
    rows: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class _ParsedReport:
    bar_groups: list[_Group] = field(default_factory=list)
    hours: dict[int, int] | None = None
    weekdays: dict[int, int] | None = None


@dataclass(frozen=True)
class RenderedStatsImage:
    image_bytes: bytes
    caption: str
    caption_entities: list[Any]
    detail_text: str
    detail_entities: list[Any]


class StatsImageRenderer:
    """Renders a StatsReport into a PNG chart plus a textual caption.

    The image presents the report's bar charts and time series visually.
    The caption holds the visible summary lines, and a separate detail
    string keeps the existing quoted breakdown for the caller to send as
    a follow-up message.
    """

    def __init__(self, max_caption_chars: int = 1024) -> None:
        self._max_caption_chars = max_caption_chars

    async def render(
        self,
        report: StatsReport,
        *,
        max_chars: int,
    ) -> RenderedStatsImage:
        caption, caption_entities = self._caption(report)
        detail_text, detail_entities = self._detail(report, max_chars=max_chars)
        parsed = self._parse(report)
        image_bytes = await asyncio.to_thread(self._render_figure, report, parsed)
        return RenderedStatsImage(
            image_bytes=image_bytes,
            caption=caption,
            caption_entities=caption_entities,
            detail_text=detail_text,
            detail_entities=detail_entities,
        )

    def _caption(self, report: StatsReport) -> tuple[str, list[Any]]:
        lines: list[str] = [f"\U0001f4ca {report.title}"]
        if report.visible_lines:
            lines.append("")
            lines.extend(report.visible_lines)
        text = "\n".join(lines)
        entities = _shift_links_for_caption(text, lines, report.links)
        if len(text) > self._max_caption_chars:
            text = text[: self._max_caption_chars - 1] + "…"
            entities = [
                entity
                for entity in entities
                if getattr(entity, "offset", 0) + getattr(entity, "length", 0)
                <= len(text)
            ]
        return text, entities

    def _detail(
        self,
        report: StatsReport,
        *,
        max_chars: int,
    ) -> tuple[str, list[Any]]:
        if not report.detail_lines:
            return "", []
        header = "Details:"
        kept: list[str] = []
        running_len = len(header) + 1
        truncation_notice = "Details truncated. Use a shorter lookback window."
        for line in report.detail_lines:
            quoted = f"> {line}"
            if max_chars > 0 and running_len + len(quoted) + 1 > max_chars:
                break
            kept.append(line)
            running_len += len(quoted) + 1
        truncated = len(kept) < len(report.detail_lines)
        body_lines = [header]
        body_lines.extend(f"> {line}" for line in kept)
        if truncated:
            body_lines.append(f"> {truncation_notice}")
        text = "\n".join(body_lines)
        entities: list[Any] = []
        # Build the blockquote entity covering all "> ..." lines.
        quote_offset = _entity_len(header) + 1
        quote_length = max(0, _entity_len(text) - quote_offset)
        if quote_length > 0:
            entities.append(_blockquote_entity(quote_offset, quote_length))
        entities.extend(_detail_links(report, kept, body_lines))
        return text, entities

    def _parse(self, report: StatsReport) -> _ParsedReport:
        parsed = _ParsedReport()
        current_group: _Group | None = None
        hour_buckets: dict[int, int] = {}
        weekday_buckets: dict[int, int] = {}

        def commit_group() -> None:
            if current_group is not None and current_group.rows:
                parsed.bar_groups.append(current_group)

        for line in report.graph_lines:
            stripped = line.strip()
            if not stripped:
                continue
            spark_match = _SPARKLINE_HOURS_RE.match(stripped)
            if spark_match:
                hour_buckets = self._sparkline_to_hours(spark_match.group("spark"))
                continue
            hour_match = _HOUR_BAR_RE.match(stripped)
            if hour_match:
                hour_buckets[int(hour_match.group("hour"))] = int(hour_match.group("count"))
                continue
            weekday = self._weekday_count(stripped)
            if weekday is not None:
                weekday_buckets[weekday[0]] = weekday[1]
                continue
            row = self._bar_row(stripped)
            if row is not None:
                if current_group is None:
                    current_group = _Group(title=report.title)
                current_group.rows.append(row)
                continue
            commit_group()
            current_group = _Group(title=stripped.rstrip(":"))
        commit_group()

        if hour_buckets:
            parsed.hours = hour_buckets
        if weekday_buckets:
            parsed.weekdays = weekday_buckets
        return parsed

    @staticmethod
    def _bar_row(line: str) -> tuple[str, int] | None:
        ranked = _RANKED_RE.match(line)
        if ranked:
            return ranked.group("label").strip(), int(ranked.group("count"))
        plain = _BAR_RE.match(line)
        if plain:
            return plain.group("label").strip(), int(plain.group("count"))
        return None

    @staticmethod
    def _weekday_count(line: str) -> tuple[int, int] | None:
        for index, name in enumerate(_WEEKDAYS):
            if line.startswith(f"{name} "):
                match = _BAR_RE.match(line)
                if match:
                    return index, int(match.group("count"))
        return None

    @staticmethod
    def _sparkline_to_hours(spark: str) -> dict[int, int]:
        levels = [_SPARK_BLOCKS.index(ch) if ch in _SPARK_BLOCKS else 0 for ch in spark]
        return {hour: levels[hour] for hour in range(min(24, len(levels)))}

    def _render_figure(self, report: StatsReport, parsed: _ParsedReport) -> bytes:
        import matplotlib

        matplotlib.use("Agg", force=False)
        import matplotlib.pyplot as plt

        panels: list[tuple[str, Any]] = []
        for group in parsed.bar_groups:
            panels.append(("bars", group))
        if parsed.hours is not None:
            panels.append(("hours", parsed.hours))
        if parsed.weekdays is not None:
            panels.append(("weekdays", parsed.weekdays))

        if not panels:
            panels.append(("text", report.visible_lines or ["No data"]))

        rows = len(panels)
        height_per_panel = 2.6
        fig_height = max(3.0, height_per_panel * rows)
        fig, axes = plt.subplots(rows, 1, figsize=(8.5, fig_height))
        if rows == 1:
            axes = [axes]
        try:
            for ax, (kind, payload) in zip(axes, panels):
                if kind == "bars":
                    self._draw_bars(ax, payload)
                elif kind == "hours":
                    self._draw_hours(ax, payload)
                elif kind == "weekdays":
                    self._draw_weekdays(ax, payload)
                else:
                    self._draw_text_panel(ax, payload)
            fig.suptitle(report.title, fontsize=14, fontweight="bold")
            fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))

            buffer = io.BytesIO()
            fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
            return buffer.getvalue()
        finally:
            plt.close(fig)

    @staticmethod
    def _draw_bars(ax: Any, group: _Group) -> None:
        rows = list(reversed(group.rows))
        labels = [_truncate(label, 24) for label, _ in rows]
        values = [count for _, count in rows]
        ax.barh(labels, values, color="#4A90E2")
        ax.set_title(group.title, fontsize=11)
        ax.set_xlabel("count")
        ax.tick_params(axis="y", labelsize=9)
        ax.grid(axis="x", linestyle=":", alpha=0.4)
        for index, value in enumerate(values):
            ax.text(value, index, f" {value}", va="center", fontsize=8)

    @staticmethod
    def _draw_hours(ax: Any, hours: dict[int, int]) -> None:
        x = list(range(24))
        y = [hours.get(hour, 0) for hour in x]
        ax.plot(x, y, marker="o", color="#E2734A", linewidth=1.5)
        ax.fill_between(x, y, color="#E2734A", alpha=0.2)
        ax.set_title("Messages by hour", fontsize=11)
        ax.set_xlabel("hour of day")
        ax.set_xticks([0, 4, 8, 12, 16, 20, 23])
        ax.grid(linestyle=":", alpha=0.4)

    @staticmethod
    def _draw_weekdays(ax: Any, weekdays: dict[int, int]) -> None:
        x = list(range(7))
        labels = _WEEKDAYS
        values = [weekdays.get(day, 0) for day in x]
        ax.bar(labels, values, color="#7AB87A")
        ax.set_title("Messages by weekday", fontsize=11)
        ax.grid(axis="y", linestyle=":", alpha=0.4)

    @staticmethod
    def _draw_text_panel(ax: Any, lines: list[str]) -> None:
        ax.axis("off")
        text = "\n".join(lines) if lines else "No data"
        ax.text(0.0, 1.0, text, va="top", ha="left", fontsize=11, family="monospace")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _entity_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _shift_links_for_caption(
    full_text: str,
    lines: list[str],
    links: list[StatsLink],
) -> list[Any]:
    if not links:
        return []
    # Caption layout: "📊 title", "", visible_line[0], visible_line[1], ...
    # So visible line i is at lines index i + 2.
    line_offsets: dict[int, int] = {}
    running = 0
    for index, line in enumerate(lines):
        line_offsets[index] = running
        running += _entity_len(line) + 1
    entities: list[Any] = []
    for link in links:
        if link.section != "visible":
            continue
        line_index = link.line_index + 2
        if line_index >= len(lines):
            continue
        line_text = lines[line_index]
        if link.start < 0 or link.length <= 0:
            continue
        if link.start + link.length > len(line_text):
            continue
        offset = (
            line_offsets[line_index]
            + _entity_len(line_text[: link.start])
        )
        length = _entity_len(line_text[link.start : link.start + link.length])
        entities.append(_text_url_entity(offset, length, link.url))
    return entities


def _detail_links(
    report: StatsReport,
    kept_lines: list[str],
    body_lines: list[str],
) -> list[Any]:
    if not kept_lines:
        return []
    line_offsets: dict[int, int] = {}
    running = 0
    for index, body in enumerate(body_lines):
        line_offsets[index] = running
        running += _entity_len(body) + 1
    entities: list[Any] = []
    for link in report.links:
        if link.section != "detail":
            continue
        if link.line_index >= len(kept_lines):
            continue
        # body line is "> {detail_line}", with a 2-char prefix for "> ".
        body_index = link.line_index + 1  # +1 for the "Details:" header.
        body_line = body_lines[body_index]
        prefix_len = 2  # "> "
        line_text = body_line[prefix_len:]
        if link.start < 0 or link.length <= 0:
            continue
        if link.start + link.length > len(line_text):
            continue
        offset = (
            line_offsets[body_index]
            + prefix_len
            + _entity_len(line_text[: link.start])
        )
        length = _entity_len(line_text[link.start : link.start + link.length])
        entities.append(_text_url_entity(offset, length, link.url))
    return entities


def _text_url_entity(offset: int, length: int, url: str) -> Any:
    from telethon.tl import types

    return types.MessageEntityTextUrl(offset=offset, length=length, url=url)


def _blockquote_entity(offset: int, length: int) -> Any:
    from telethon.tl import types

    try:
        return types.MessageEntityBlockquote(
            offset=offset,
            length=length,
            collapsed=True,
        )
    except TypeError:
        return types.MessageEntityBlockquote(offset=offset, length=length)
