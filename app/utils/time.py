from __future__ import annotations

import re
from datetime import UTC, datetime

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hd])\s*$", re.IGNORECASE)


def parse_lookback(value: str) -> int | None:
    """Parse a string like ``6h``, ``24h``, ``2d`` into hours.

    Returns None if the value does not match.
    """
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "h":
        return amount
    if unit == "d":
        return amount * 24
    return None


def utcnow() -> datetime:
    return datetime.now(UTC)
