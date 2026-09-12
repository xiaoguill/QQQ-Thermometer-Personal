from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.jobs.m19.config import M19Config
from src.jobs.m19.runner import latest_completed_session, run_checkpoint


ROOT = Path(__file__).resolve().parents[3]


def test_latest_completed_session_does_not_use_open_session() -> None:
    config = M19Config.from_file(ROOT / "configs/m19/readonly.json")
    # 2026-08-10 was a Monday.  09:00 New York is before the close, so the
    # latest usable daily bar is Friday 2026-08-07.
    now = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
    assert latest_completed_session(config, "open", now=now) == "2026-08-07"
    assert latest_completed_session(config, "close", now=now) == "2026-08-07"
    assert latest_completed_session(config, "close", as_of_date="2026-08-10") == "2026-08-10"


def test_missing_local_vxx_is_fail_closed(tmp_path: Path) -> None:
    config = M19Config.from_file(ROOT / "configs/m19/readonly.json")
    config = replace(config, provider="local_csv", local_vxx_csv=str(tmp_path / "missing-vxx.csv"))
    config.validate()
    decision = run_checkpoint(config, "close", tmp_path / "run", as_of_date="2026-08-10")
    assert decision["status"] == "DATA_ERROR"
    assert decision["decision_eligible"] is False
    assert decision["failure_code"] == "FILE_NOT_FOUND"
    assert decision["target_weights"] == {}
