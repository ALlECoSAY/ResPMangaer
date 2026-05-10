from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StatsSection = Literal["visible", "graph", "detail"]


@dataclass(frozen=True)
class StatsLink:
    section: StatsSection
    line_index: int
    start: int
    length: int
    url: str


@dataclass(frozen=True)
class StatsReport:
    title: str
    visible_lines: list[str]
    graph_lines: list[str]
    detail_lines: list[str]
    links: list[StatsLink] = field(default_factory=list)
    entities: list[Any] = field(default_factory=list)
