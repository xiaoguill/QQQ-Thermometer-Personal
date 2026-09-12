from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.jobs.m21.config import M21Config, M21ConfigError


ROOT = Path(__file__).resolve().parents[3]


def test_config_rejects_string_booleans_and_non_integer_windows(tmp_path: Path) -> None:
    raw = json.loads((ROOT / "configs/m21/free_close.json").read_text(encoding="utf-8"))
    raw["paper_only"] = "true"
    path = tmp_path / "invalid-bool.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(M21ConfigError, match="paper_only must be true or false"):
        M21Config.from_file(path)

    raw = json.loads((ROOT / "configs/m21/free_close.json").read_text(encoding="utf-8"))
    raw["data"]["free_history_days"] = 730.5
    path = tmp_path / "invalid-integer.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(M21ConfigError, match="free_history_days must be an integer"):
        M21Config.from_file(path)
