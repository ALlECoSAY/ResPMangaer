from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.stats_report import StatsLink, StatsReport

_DETAILS_HEADER = "Details:"
_TRUNCATION_NOTICE = "Details truncated. Use a shorter lookback window."


@dataclass(frozen=True)
class RenderedStats:
    text: str
    entities: list[Any]


def bar(count: int, max_count: int, width: int = 12) -> str:
    if max_count <= 0:
        return ""
    filled = max(1, round((count / max_count) * width))
    return "█" * filled + "░" * (width - filled)


def bar_lines(rows: list[tuple[str, int]], *, width: int = 12) -> list[str]:
    if not rows:
        return []
    max_count = max(count for _label, count in rows)
    label_width = min(max(len(label) for label, _count in rows), 18)
    lines: list[str] = []
    for label, count in rows:
        compact_label = label if len(label) <= label_width else f"{label[: label_width - 1]}…"
        lines.append(f"{compact_label:<{label_width}} {bar(count, max_count, width)} {count}")
    return lines


def sparkline(values: list[int]) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    max_value = max(values)
    if max_value <= 0:
        return blocks[0] * len(values)
    return "".join(blocks[round((value / max_value) * (len(blocks) - 1))] for value in values)


class StatsRenderer:
    def render(self, report: StatsReport, *, max_chars: int) -> RenderedStats:
        detail_lines, truncated = self._fit_detail_lines(report, max_chars=max_chars)
        text, line_offsets = self._compose(report, detail_lines=detail_lines, truncated=truncated)
        entities = [*report.entities]
        entities.extend(self._link_entities(report.links, line_offsets))
        quote = line_offsets.get(("detail_quote", 0))
        if quote is not None:
            entities.append(
                _blockquote_entity(
                    offset=quote.offset,
                    length=quote.length,
                    collapsed=True,
                )
            )
        return RenderedStats(text=text, entities=entities)

    def _fit_detail_lines(
        self,
        report: StatsReport,
        *,
        max_chars: int,
    ) -> tuple[list[str], bool]:
        if max_chars <= 0 or not report.detail_lines:
            return report.detail_lines, False

        original_count = len(report.detail_lines)
        detail_lines = list(report.detail_lines)
        while detail_lines:
            text, _offsets = self._compose(report, detail_lines=detail_lines, truncated=False)
            if len(text) <= max_chars:
                return detail_lines, len(detail_lines) < original_count
            detail_lines.pop()

        text, _offsets = self._compose(
            report,
            detail_lines=[_TRUNCATION_NOTICE],
            truncated=False,
        )
        if len(text) <= max_chars:
            return [_TRUNCATION_NOTICE], True
        return [], bool(report.detail_lines)

    def _compose(
        self,
        report: StatsReport,
        *,
        detail_lines: list[str],
        truncated: bool,
    ) -> tuple[str, dict[tuple[str, int], _LineOffset]]:
        lines: list[str] = [f"📊 {report.title}"]
        offsets: dict[tuple[str, int], _LineOffset] = {}

        def append_line(section: str, line: str, line_index: int) -> None:
            offset = sum(_entity_len(existing) + 1 for existing in lines)
            offsets[(section, line_index)] = _LineOffset(offset, _entity_len(line), line)
            lines.append(line)

        if report.visible_lines:
            lines.append("")
            for index, line in enumerate(report.visible_lines):
                append_line("visible", line, index)

        if report.graph_lines:
            lines.append("")
            for index, line in enumerate(report.graph_lines):
                append_line("graph", line, index)

        final_details = list(detail_lines)
        if truncated and final_details and final_details[-1] != _TRUNCATION_NOTICE:
            final_details.append(_TRUNCATION_NOTICE)

        if final_details:
            lines.append("")
            lines.append(_DETAILS_HEADER)
            quote_start = sum(_entity_len(existing) + 1 for existing in lines)
            quote_length = 0
            for index, line in enumerate(final_details):
                quote_line = f"> {line}"
                append_line("detail", quote_line, index)
                offsets[("detail", index)] = _LineOffset(
                    offsets[("detail", index)].offset + 2,
                    _entity_len(line),
                    line,
                )
                quote_length += _entity_len(quote_line) + 1
            if quote_length:
                offsets[("detail_quote", 0)] = _LineOffset(
                    quote_start,
                    quote_length - 1,
                    "",
                )

        return "\n".join(lines), offsets

    @staticmethod
    def _link_entities(
        links: list[StatsLink],
        line_offsets: dict[tuple[str, int], _LineOffset],
    ) -> list[Any]:
        entities: list[Any] = []
        for link in links:
            line_offset = line_offsets.get((link.section, link.line_index))
            if line_offset is None:
                continue
            line_text = line_offset.text
            if link.start < 0 or link.length <= 0 or link.start + link.length > len(line_text):
                continue
            entities.append(
                _text_url_entity(
                    offset=line_offset.offset + _entity_len(line_text[: link.start]),
                    length=_entity_len(line_text[link.start : link.start + link.length]),
                    url=link.url,
                )
            )
        return entities


@dataclass(frozen=True)
class _LineOffset:
    offset: int
    length: int
    text: str


def _entity_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text_url_entity(*, offset: int, length: int, url: str) -> Any:
    from telethon.tl import types

    return types.MessageEntityTextUrl(offset=offset, length=length, url=url)


def _blockquote_entity(*, offset: int, length: int, collapsed: bool) -> Any:
    from telethon.tl import types

    try:
        return types.MessageEntityBlockquote(
            offset=offset,
            length=length,
            collapsed=collapsed,
        )
    except TypeError:
        return types.MessageEntityBlockquote(offset=offset, length=length)
