from __future__ import annotations

from pathlib import Path

from app.services.auto_delete_config import RuntimeAutoDeleteConfig


def test_returns_none_when_yaml_missing(tmp_path: Path) -> None:
    config = RuntimeAutoDeleteConfig(path=tmp_path / "missing.yaml")
    # Falls back to defaults: stats and help configured.
    assert config.delay_seconds("stats") == 300
    assert config.delay_seconds("help") == 300
    assert config.delay_seconds("tldr") is None


def test_reads_per_command_delays_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "auto_delete.yaml"
    path.write_text(
        "version: 1\nauto_delete:\n  stats: 60\n  help: 0\n  tldr: 600\n",
        encoding="utf-8",
    )
    config = RuntimeAutoDeleteConfig(path=path)
    assert config.delay_seconds("stats") == 60
    # 0 disables — treated as None.
    assert config.delay_seconds("help") is None
    assert config.delay_seconds("tldr") == 600
    assert config.delay_seconds("ai") is None


def test_hot_reloads_on_file_change(tmp_path: Path) -> None:
    path = tmp_path / "auto_delete.yaml"
    path.write_text(
        "version: 1\nauto_delete:\n  stats: 60\n",
        encoding="utf-8",
    )
    config = RuntimeAutoDeleteConfig(path=path)
    assert config.delay_seconds("stats") == 60
    import os
    import time

    time.sleep(0.01)
    path.write_text(
        "version: 1\nauto_delete:\n  stats: 120\n",
        encoding="utf-8",
    )
    new_mtime = path.stat().st_mtime + 1
    os.utime(path, (new_mtime, new_mtime))
    assert config.delay_seconds("stats") == 120
