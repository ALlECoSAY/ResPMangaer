from __future__ import annotations

from app.utils.time import parse_lookback


def test_hours():
    assert parse_lookback("6h") == 6
    assert parse_lookback("24h") == 24


def test_days():
    assert parse_lookback("2d") == 48


def test_invalid():
    assert parse_lookback("") is None
    assert parse_lookback("nope") is None
    assert parse_lookback("10m") is None
