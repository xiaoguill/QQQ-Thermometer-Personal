from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

from src.jobs.m21.config import M21Config
from src.jobs.m21.runner import latest_completed_session, requested_start_date, run_close
from src.storage.normalization import TradingCalendar


ROOT = Path(__file__).resolve().parents[3]


def test_latest_completed_session_never_uses_pre_close_daily_bar() -> None:
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")
    from datetime import datetime, timezone

    before_close = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 8, 10, 20, 30, tzinfo=timezone.utc)
    assert latest_completed_session(config, now=before_close) == "2026-08-07"
    assert latest_completed_session(config, now=after_close) == "2026-08-10"


def test_free_close_window_covers_strategy_warmup_before_replay() -> None:
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")
    start = requested_start_date(config, "2026-09-11")
    calendar = TradingCalendar(
        extra_closed_dates=("2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09")
    )
    context_sessions = calendar.sessions(start, "2024-12-31")
    assert len(context_sessions) >= 150


def _write_fixture_files(
    tmp_path: Path,
    *,
    dates: tuple[str, ...] = ("2026-08-07", "2026-08-10"),
    include_vix3m: bool = False,
) -> tuple[Path, Path, Path]:
    symbols = ("QQQ", "QLD", "VXX", "SVXY", "BIL", "TLT", "IAU", "XLU", "VOO", "SPY")
    prices = tmp_path / "prices.csv"
    with prices.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", *symbols])
        writer.writeheader()
        for index, session in enumerate(dates):
            writer.writerow({"date": session, **{symbol: str(100 + index) for symbol in symbols}})
    vix = tmp_path / "vix.csv"
    with vix.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "VIX", "VIX3M"])
        writer.writeheader()
        for index, session in enumerate(dates):
            writer.writerow(
                {
                    "date": session,
                    "VIX": str(20 + index),
                    "VIX3M": str(25 + index) if include_vix3m else "",
                }
            )
    vxx = tmp_path / "vxx.csv"
    with vxx.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "close"])
        writer.writeheader()
        for index, session in enumerate(dates):
            writer.writerow({"date": session, "close": str(30 + index)})
    return prices, vix, vxx


def test_missing_vix3m_fails_closed_before_replay(tmp_path: Path) -> None:
    prices, vix, vxx = _write_fixture_files(tmp_path)
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")
    config = replace(
        config,
        provider="local_csv",
        history_floor_date="2026-08-07",
        local_prices_csv=str(prices),
        local_vix_csv=str(vix),
        local_vxx_csv=str(vxx),
    )
    output = tmp_path / "run"
    decision = run_close(config, output, as_of_date="2026-08-10")
    assert decision["status"] == "DATA_ERROR"
    assert decision["decision_eligible"] is False
    assert decision["target_weights"] == {}
    assert decision["manual_action"] == "数据不完整，本次不调仓。"
    assert decision["failure_code"] == "MISSING_FREE_SERIES"
    evidence = (output / "availability_evidence.json").read_text(encoding="utf-8")
    assert "VIX3M" in evidence
    assert "no VIX/SVXY/BIL substitute" in evidence


def test_internal_session_gap_fails_closed_before_replay(tmp_path: Path) -> None:
    prices, vix, vxx = _write_fixture_files(
        tmp_path,
        dates=("2026-08-06", "2026-08-10"),
        include_vix3m=True,
    )
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")
    config = replace(
        config,
        provider="local_csv",
        history_floor_date="2026-08-06",
        local_prices_csv=str(prices),
        local_vix_csv=str(vix),
        local_vxx_csv=str(vxx),
    )
    output = tmp_path / "run"
    decision = run_close(config, output, as_of_date="2026-08-10")

    assert decision["status"] == "DATA_ERROR"
    assert decision["failure_code"] == "MISSING_SESSION"
    assert decision["failure_class"] == "data_quality"
    assert decision["manual_action"] == "数据不完整，本次不调仓。"
    evidence = json.loads(
        (output / "availability_evidence.json").read_text(encoding="utf-8")
    )
    qqq = next(
        item for item in evidence["session_completeness"] if item["symbol"] == "QQQ"
    )
    assert qqq["missing_session_count"] == 1
    assert qqq["complete"] is False
