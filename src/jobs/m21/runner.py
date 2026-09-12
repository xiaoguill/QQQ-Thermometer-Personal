"""M21 daily close runner: free stock closes + official Cboe volatility data.

The runner is intentionally an adapter around the existing v12.2 causal
replay.  It does not implement a second strategy.  It collects a complete,
date-bounded input set, records source-by-source evidence, and only then
invokes the existing M19/v12.2 replay boundary through temporary normalized
files.  Any missing or stale required series produces an empty target and the
literal user-facing action: ``数据不完整，本次不调仓。``
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.jobs.m18.v12_2_walk_forward import load_config as load_replay_config
from src.jobs.m19.config import M19Config
from src.jobs.m19.runner import run_checkpoint as run_m19_checkpoint
from src.storage.normalization import TradingCalendar

from .config import (
    DECISION_SYMBOLS,
    EXPECTED_REPLAY_VERSION,
    EXPECTED_STRATEGY_VERSION,
    INDEX_SYMBOLS,
    M21Config,
    M21ConfigError,
    M21_DECISION_SCHEMA,
    M21_RUNTIME_VERSION,
    STOCK_SYMBOLS,
)
from .sources import (
    CboeOfficialSource,
    M21SourceError,
    MassiveFreeStocksSource,
    SeriesResult,
    classify_vxx_issue,
    write_price_csv,
    write_vix_csv,
    write_vxx_csv,
)


class M21RunnerError(RuntimeError):
    """A safe runner failure that can be represented in Evidence."""

    def __init__(self, code: str, failure_class: str, message: str) -> None:
        self.code = code
        self.failure_class = failure_class
        self.message = message
        super().__init__(f"{code}: {message}")


_EXTRA_CLOSED_DATES = ("2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_iso(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise M21RunnerError("INVALID_DATE", "data_quality", f"{field_name} is not YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise M21RunnerError("INVALID_DATE", "data_quality", f"{field_name} is not YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise M21RunnerError("INVALID_DATE", "data_quality", f"{field_name} is not YYYY-MM-DD")
    return value


def _positive(value: Any, field_name: str) -> float:
    if value in (None, "") or isinstance(value, bool):
        raise M21RunnerError("INVALID_BAR", "data_quality", f"{field_name} is missing")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M21RunnerError("INVALID_BAR", "data_quality", f"{field_name} is not numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise M21RunnerError("INVALID_BAR", "data_quality", f"{field_name} is not positive and finite")
    return number


def _calendar() -> TradingCalendar:
    return TradingCalendar(extra_closed_dates=_EXTRA_CLOSED_DATES)


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def latest_completed_session(config: M21Config, *, now: datetime | None = None, as_of_date: str | None = None) -> str:
    """Return a completed NYSE session; never use a partial daily bar."""

    if as_of_date:
        return _parse_iso(as_of_date, "as_of_date")
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config.market_timezone))
    today = current.date()
    calendar = _calendar()
    sessions = calendar.sessions(config.history_floor_date, today.isoformat())
    if not sessions:
        raise M21RunnerError("NO_SESSION", "calendar", "no NYSE session is available")
    today_is_session = calendar.is_trading_day(today)
    after_close = current.hour * 60 + current.minute >= _minutes(config.close_confirmation_time)
    if today_is_session and after_close:
        return today.isoformat()
    prior = [session for session in sessions if session < today.isoformat()]
    if prior:
        return prior[-1]
    raise M21RunnerError("INSUFFICIENT_CONTEXT", "data_quality", "a prior completed session is required")


def requested_start_date(config: M21Config, end_date: str) -> str:
    """Use a bounded rolling window compatible with a free EOD plan."""

    end = date.fromisoformat(end_date)
    rolling = end - timedelta(days=config.free_history_days - 1)
    floor = date.fromisoformat(config.history_floor_date)
    return max(floor, rolling).isoformat()


def _failed_result(symbol: str, provider: str, start_date: str, end_date: str, *, code: str, failure_class: str, message: str, price_basis: str = "adjusted_ohlcv") -> SeriesResult:
    return SeriesResult(
        symbol=symbol,
        provider=provider,
        status="failed",
        rows=(),
        requested_start=start_date,
        requested_end=end_date,
        failure_code=code,
        failure_class=failure_class,
        failure_message=message,
        price_basis=price_basis,
    )


def _read_local_series(path: Path, *, symbol: str, start_date: str, end_date: str, value_field: str, provider: str, price_basis: str) -> SeriesResult:
    if not path.exists():
        return _failed_result(symbol, provider, start_date, end_date, code="FILE_NOT_FOUND", failure_class="data_quality", message=f"local file is missing: {path.name}", price_basis=price_basis)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            if "date" not in fields or value_field not in fields:
                return _failed_result(symbol, provider, start_date, end_date, code="INVALID_CSV", failure_class="interface", message=f"local file lacks date/{value_field}", price_basis=price_basis)
            values: dict[str, float] = {}
            for row in reader:
                session = _parse_iso(row.get("date"), f"{symbol}.date")
                if session < start_date:
                    continue
                if session > end_date:
                    continue
                if session in values:
                    return _failed_result(symbol, provider, start_date, end_date, code="DUPLICATE_BAR", failure_class="data_quality", message=f"duplicate local {symbol} date", price_basis=price_basis)
                values[session] = _positive(row.get(value_field), f"{symbol}.{session}")
    except M21RunnerError as exc:
        return _failed_result(symbol, provider, start_date, end_date, code=exc.code, failure_class=exc.failure_class, message=exc.message, price_basis=price_basis)
    except (OSError, csv.Error):
        return _failed_result(symbol, provider, start_date, end_date, code="INVALID_CSV", failure_class="interface", message=f"local file cannot be read: {path.name}", price_basis=price_basis)
    if not values:
        return _failed_result(symbol, provider, start_date, end_date, code="EMPTY_PAYLOAD", failure_class="data_quality", message=f"local file has no visible {symbol} rows", price_basis=price_basis)
    rows = tuple({"date": session, "symbol": symbol, "close": value} for session, value in sorted(values.items()))
    return SeriesResult(
        symbol=symbol,
        provider=provider,
        status="success" if end_date in values else "failed",
        rows=rows if end_date in values else (),
        requested_start=start_date,
        requested_end=end_date,
        first_date=min(values),
        last_date=max(values),
        has_end_date=end_date in values,
        failure_code=None if end_date in values else "STALE_OR_MISSING",
        failure_class=None if end_date in values else "data_quality",
        failure_message=None if end_date in values else f"local {symbol} has no value on requested close date",
        request={"provider": provider, "path": str(path), "value_field": value_field},
        raw_sha256=_file_hash(path),
        raw_row_count=len(values),
        price_basis=price_basis,
    )


def _read_local_vix(path: Path, *, start_date: str, end_date: str) -> tuple[SeriesResult, SeriesResult]:
    if not path.exists():
        failure = _failed_result("VIX", "local_csv", start_date, end_date, code="FILE_NOT_FOUND", failure_class="data_quality", message=f"local file is missing: {path.name}", price_basis="official_index_close")
        return failure, replace(failure, symbol="VIX3M")
    values: dict[str, dict[str, float]] = {symbol: {} for symbol in INDEX_SYMBOLS}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"date", "VIX", "VIX3M"}.issubset(set(reader.fieldnames or ())):
                failure = _failed_result("VIX", "local_csv", start_date, end_date, code="INVALID_CSV", failure_class="interface", message="local VIX file lacks date,VIX,VIX3M", price_basis="official_index_close")
                return failure, replace(failure, symbol="VIX3M")
            for row in reader:
                session = _parse_iso(row.get("date"), "VIX.date")
                if session < start_date:
                    continue
                if session > end_date:
                    continue
                for symbol in INDEX_SYMBOLS:
                    if row.get(symbol) in (None, ""):
                        continue
                    if session in values[symbol]:
                        failure = _failed_result(symbol, "local_csv", start_date, end_date, code="DUPLICATE_BAR", failure_class="data_quality", message=f"duplicate local {symbol} date", price_basis="official_index_close")
                        return failure, replace(failure, symbol="VIX" if symbol == "VIX3M" else "VIX3M")
                    values[symbol][session] = _positive(row.get(symbol), f"{symbol}.{session}")
    except M21RunnerError as exc:
        failure = _failed_result("VIX", "local_csv", start_date, end_date, code=exc.code, failure_class=exc.failure_class, message=exc.message, price_basis="official_index_close")
        return failure, replace(failure, symbol="VIX3M")
    except (OSError, csv.Error):
        failure = _failed_result("VIX", "local_csv", start_date, end_date, code="INVALID_CSV", failure_class="interface", message=f"local file cannot be read: {path.name}", price_basis="official_index_close")
        return failure, replace(failure, symbol="VIX3M")
    results: list[SeriesResult] = []
    for symbol in INDEX_SYMBOLS:
        source_values = values[symbol]
        rows = tuple({"date": session, "symbol": symbol, "close": value} for session, value in sorted(source_values.items()))
        results.append(SeriesResult(
            symbol=symbol,
            provider="local_csv",
            status="success" if end_date in source_values else "failed",
            rows=rows if end_date in source_values else (),
            requested_start=start_date,
            requested_end=end_date,
            first_date=min(source_values) if source_values else None,
            last_date=max(source_values) if source_values else None,
            has_end_date=end_date in source_values,
            failure_code=None if end_date in source_values else "MISSING_FREE_SERIES",
            failure_class=None if end_date in source_values else "data_quality",
            failure_message=None if end_date in source_values else f"local {symbol} has no value on requested close date",
            request={"provider": "local_csv", "path": str(path), "value_field": symbol},
            raw_sha256=_file_hash(path),
            raw_row_count=len(source_values),
            price_basis="official_index_close",
        ))
    return results[0], results[1]


def _local_inputs(config: M21Config, *, start_date: str, end_date: str, workdir: Path) -> tuple[list[SeriesResult], list[SeriesResult]]:
    if not config.local_prices_csv or not config.local_vix_csv or not config.local_vxx_csv:
        raise M21RunnerError("CONFIG_ERROR", "configuration", "local_csv requires prices, VIX and VXX paths")
    prices_path = config.resolve(config.local_prices_csv)
    stock_results = [_read_local_series(prices_path, symbol=symbol, start_date=start_date, end_date=end_date, value_field=symbol, provider="local_csv", price_basis="adjusted_ohlcv") for symbol in STOCK_SYMBOLS if symbol != "VXX"]
    vxx_result = _read_local_series(config.resolve(config.local_vxx_csv), symbol="VXX", start_date=start_date, end_date=end_date, value_field=config.local_vxx_value_field, provider="local_csv", price_basis="adjusted_ohlcv")
    stock_results.append(vxx_result)
    vix_results = list(_read_local_vix(config.resolve(config.local_vix_csv), start_date=start_date, end_date=end_date))
    return stock_results, vix_results


def _free_inputs(config: M21Config, *, start_date: str, end_date: str) -> tuple[list[SeriesResult], list[SeriesResult]]:
    try:
        stocks_source = MassiveFreeStocksSource.from_config(config)
    except M21SourceError as exc:
        stock_results = [_failed_result(symbol, "massive-free-stocks", start_date, end_date, code=exc.code, failure_class=exc.failure_class, message=exc.message) for symbol in STOCK_SYMBOLS]
    else:
        stock_results = list(stocks_source.fetch_all(start_date=start_date, end_date=end_date))
    cboe_source = CboeOfficialSource(config)
    index_results = [cboe_source.fetch_index(symbol, start_date=start_date, end_date=end_date) for symbol in INDEX_SYMBOLS]
    return stock_results, index_results


def _expected_sessions(start_date: str, end_date: str) -> tuple[str, ...]:
    """Return the sessions that every required daily series must cover."""

    return _calendar().sessions(start_date, end_date)


def _session_completeness(result: SeriesResult, expected: set[str]) -> dict[str, Any]:
    returned = {str(row.get("date")) for row in result.rows if row.get("date")}
    missing = sorted(expected - returned)
    unexpected = sorted(returned - expected)
    return {
        "symbol": result.symbol,
        "expected_session_count": len(expected),
        "returned_session_count": len(returned),
        "missing_session_count": len(missing),
        "missing_sessions_sample": missing[:20],
        "unexpected_session_count": len(unexpected),
        "unexpected_sessions_sample": unexpected[:20],
        # Some official Cboe history files publish index observations on dates
        # that the NYSE equity calendar marks closed.  Extra observations are
        # retained in Evidence and ignored by the replay calendar; only a
        # missing expected NYSE session can make a daily series incomplete.
        "complete": not missing,
    }


def _source_complete(
    stock_results: Sequence[SeriesResult],
    index_results: Sequence[SeriesResult],
    *,
    start_date: str,
    end_date: str,
) -> bool:
    expected = set(STOCK_SYMBOLS) | set(INDEX_SYMBOLS)
    actual = {result.symbol for result in (*stock_results, *index_results)}
    expected_sessions = set(_expected_sessions(start_date, end_date))
    return actual == expected and all(
        result.status == "success"
        and result.has_end_date
        and _session_completeness(result, expected_sessions)["complete"]
        for result in (*stock_results, *index_results)
    )


def _result_failure(result: SeriesResult) -> dict[str, Any]:
    return {
        "symbol": result.symbol,
        "code": result.failure_code or "UNKNOWN",
        "class": result.failure_class or "unknown",
        "message": result.failure_message or "series unavailable",
    }


def _primary_failure(
    stock_results: Sequence[SeriesResult],
    index_results: Sequence[SeriesResult],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    expected_sessions = set(_expected_sessions(start_date, end_date))
    failures: list[SeriesResult] = []
    for result in (*stock_results, *index_results):
        if result.status != "success" or not result.has_end_date:
            failures.append(result)
            continue
        if not _session_completeness(result, expected_sessions)["complete"]:
            return {
                "code": "MISSING_SESSION",
                "class": "data_quality",
                "message": f"{result.symbol} does not cover every expected NYSE session",
            }
    priority = {"MISSING_API_KEY": 0, "NOT_ENTITLED": 1, "CONFIG_MISSING_SYMBOL": 2, "RESPONSE_SYMBOL_MISMATCH": 3, "NOT_FOUND": 4, "MISSING_FREE_SERIES": 5, "STALE_OR_MISSING": 6, "RATE_LIMITED": 7}
    if not failures:
        return {"code": "UNKNOWN", "class": "unknown", "message": "data source did not complete"}
    chosen = sorted(failures, key=lambda item: (priority.get(item.failure_code or "", 99), item.symbol))[0]
    return _result_failure(chosen)


def _availability_evidence(
    config: M21Config,
    *,
    start_date: str,
    end_date: str,
    stock_results: Sequence[SeriesResult],
    index_results: Sequence[SeriesResult],
) -> dict[str, Any]:
    vxx = next((item for item in stock_results if item.symbol == "VXX"), None)
    expected_sessions = set(_expected_sessions(start_date, end_date))
    vxx_diagnostic = classify_vxx_issue(
        declared_in_config="VXX" in config.required_stock_symbols,
        status=vxx.status if vxx else "failed",
        error_code=vxx.failure_code if vxx else "CONFIG_MISSING_SYMBOL",
        response_symbol=None,
    )
    return {
        "schema": "qqq-m21-availability-evidence/v1",
        "runtime_version": M21_RUNTIME_VERSION,
        "provider_mode": config.provider,
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "stock_provider": "massive-free-stocks" if config.provider == "free_close" else "local_csv",
        "index_provider": "cboe-official-cdn" if config.provider == "free_close" else "local_csv",
        "stock_series": [item.manifest_entry() for item in stock_results],
        "index_series": [item.manifest_entry() for item in index_results],
        "session_completeness": [
            _session_completeness(item, expected_sessions)
            for item in (*stock_results, *index_results)
        ],
        "vxx_diagnostic": {
            **vxx_diagnostic,
            "declared_in_stock_contract": "VXX" in config.required_stock_symbols,
            "tested_provider": "massive-free-stocks" if config.provider == "free_close" else "local_csv",
            "interpretation": {
                "permission": "provider explicitly denied access; no substitution is allowed",
                "interface_or_symbol": "provider endpoint or ticker response needs inspection",
                "symbol_contract": "the request/configuration or returned ticker did not identify VXX",
                "credentials": "provider test was not authorized because the API key was missing",
            },
        },
        "all_required_series_complete": _source_complete(
            stock_results,
            index_results,
            start_date=start_date,
            end_date=end_date,
        ),
        "decision_policy": "incomplete, stale, or failed series produces no target and no rebalance",
        "vix3m_policy": "missing VIX3M remains missing; no VIX/SVXY/BIL substitute",
    }


def _failure_decision(config: M21Config, *, end_date: str | None, failure: Mapping[str, Any], availability: Mapping[str, Any] | None = None) -> dict[str, Any]:
    requested_start = availability.get("requested_start_date") if isinstance(availability, Mapping) else None
    if not isinstance(requested_start, str) or not requested_start:
        requested_start = config.history_floor_date
    return {
        "schema": M21_DECISION_SCHEMA,
        "runtime_version": M21_RUNTIME_VERSION,
        "replay_version": EXPECTED_REPLAY_VERSION,
        "strategy_version": EXPECTED_STRATEGY_VERSION,
        "checkpoint": "close",
        "status": "DATA_ERROR",
        "decision_eligible": False,
        "paper_only": True,
        "execution_allowed": False,
        "signal_date": None,
        "execution_date": None,
        "as_of": None,
        "state": None,
        "temperature": None,
        "trend": None,
        "target_weights": {},
        "data_quality": "FAILED",
        "normalization_quality": "FAILED",
        "indicator_ready": False,
        "failure_code": failure.get("code", "RUNNER_ERROR"),
        "failure_class": failure.get("class", "unknown"),
        "failure_message": failure.get("message", "data source failed"),
        "source": {"provider": config.provider},
        "data_window": {"provider_requested_start": requested_start, "provider_requested_end": end_date},
        "availability": dict(availability or {}),
        "checks": {"safe_failure": True, "target_weight_sum": False, "missing_vxx_policy": "fail_closed"},
        "manual_action": "数据不完整，本次不调仓。",
    }


def _replay_with_prepared_files(config: M21Config, *, end_date: str, requested_start: str, inputs_dir: Path, replay_dir: Path) -> dict[str, Any]:
    """Call the existing M19/v12.2 replay without changing its source."""

    base = M19Config.from_file(config.project_root / "configs" / "m19" / "readonly.json")
    effective_replay_start = max(config.replay_start_date, requested_start)
    replay_config = replace(
        base,
        provider="local_csv",
        local_prices_csv=str(inputs_dir / "prices_adj_close.csv"),
        local_vix_csv=str(inputs_dir / "vix_indices.csv"),
        local_vxx_csv=str(inputs_dir / "vxx.csv"),
        history_start_date=requested_start,
        replay_start_date=effective_replay_start,
        replay_config_path=config.replay_config_path,
        initial_capital=config.initial_capital,
        cost_bps=config.cost_bps,
    )
    replay_config.validate()
    decision = run_m19_checkpoint(replay_config, "close", replay_dir, as_of_date=end_date)
    if not isinstance(decision, Mapping):
        raise M21RunnerError("REPLAY_ERROR", "strategy_boundary", "v12.2 replay did not return a decision")
    return dict(decision)


def _decorate_replay_decision(
    config: M21Config,
    decision: Mapping[str, Any],
    *,
    end_date: str,
    requested_start: str,
    availability: Mapping[str, Any],
    stock_results: Sequence[SeriesResult],
    index_results: Sequence[SeriesResult],
) -> dict[str, Any]:
    value = dict(decision)
    value.update({
        "schema": M21_DECISION_SCHEMA,
        "runtime_version": M21_RUNTIME_VERSION,
        "replay_version": EXPECTED_REPLAY_VERSION,
        "strategy_version": EXPECTED_STRATEGY_VERSION,
        "checkpoint": "close",
        "paper_only": True,
        "execution_allowed": False,
        "availability": dict(availability),
        "source": {
            "provider": "massive-free-stocks+cboe-official" if config.provider == "free_close" else "local_csv",
            "stock_provider": "massive-free-stocks" if config.provider == "free_close" else "local_csv",
            "index_provider": "cboe-official-cdn" if config.provider == "free_close" else "local_csv",
            "requested_start_date": requested_start,
            "requested_end_date": end_date,
            "source_manifest_hash": _canonical_hash({"stocks": [item.manifest_entry() for item in stock_results], "indices": [item.manifest_entry() for item in index_results]}),
        },
        "data_window": {
            "provider_requested_start": requested_start,
            "provider_requested_end": end_date,
            "signal_date": value.get("signal_date"),
            "execution_date": value.get("execution_date"),
            "replay_start_date": max(config.replay_start_date, requested_start),
            "uses_data_through_signal_date": True,
            "execution_delay_trading_days": 1,
        },
        "checks": {
            **dict(value.get("checks") if isinstance(value.get("checks"), Mapping) else {}),
            "all_required_series_complete": True,
            "vix_source_is_official_cboe": config.provider == "free_close",
            "vix3m_missing_is_not_substituted": True,
            "missing_vxx_policy": "fail_closed",
            "close_only_daily_source": True,
            "read_only_paper_boundary": True,
        },
    })
    if value.get("status") != "READY" or not value.get("decision_eligible"):
        value["target_weights"] = {}
        value["decision_eligible"] = False
        value["manual_action"] = "数据不完整，本次不调仓。"
    return value


def run_close(config: M21Config, output_dir: Path, *, now: datetime | None = None, as_of_date: str | None = None) -> dict[str, Any]:
    """Run one bounded close observation and always leave inspectable evidence."""

    output_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = output_dir / "inputs"
    replay_dir = output_dir / "replay"
    end_date: str | None = None
    availability: dict[str, Any] | None = None
    try:
        end_date = latest_completed_session(config, now=now, as_of_date=as_of_date)
        start_date = config.history_floor_date if config.provider == "local_csv" else requested_start_date(config, end_date)
        if config.provider == "free_close":
            stock_results, index_results = _free_inputs(config, start_date=start_date, end_date=end_date)
        else:
            stock_results, index_results = _local_inputs(config, start_date=start_date, end_date=end_date, workdir=inputs_dir)
        availability = _availability_evidence(config, start_date=start_date, end_date=end_date, stock_results=stock_results, index_results=index_results)
        _write_json(output_dir / "availability_evidence.json", availability)
        _write_json(output_dir / "provider_manifest.json", {
            "schema": "qqq-m21-provider-manifest/v1",
            "runtime_version": M21_RUNTIME_VERSION,
            "provider_mode": config.provider,
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "stock_series": [item.manifest_entry() for item in stock_results],
            "index_series": [item.manifest_entry() for item in index_results],
            "manifest_hash": _canonical_hash({"stocks": [item.manifest_entry() for item in stock_results], "indices": [item.manifest_entry() for item in index_results]}),
        })
        if not _source_complete(
            stock_results,
            index_results,
            start_date=start_date,
            end_date=end_date,
        ):
            failure = _primary_failure(
                stock_results,
                index_results,
                start_date=start_date,
                end_date=end_date,
            )
            decision = _failure_decision(config, end_date=end_date, failure=failure, availability=availability)
        else:
            inputs_dir.mkdir(parents=True, exist_ok=True)
            write_price_csv(inputs_dir / "prices_adj_close.csv", stock_results)
            write_vxx_csv(inputs_dir / "vxx.csv", next(item for item in stock_results if item.symbol == "VXX"))
            write_vix_csv(inputs_dir / "vix_indices.csv", index_results)
            replay_decision = _replay_with_prepared_files(config, end_date=end_date, requested_start=start_date, inputs_dir=inputs_dir, replay_dir=replay_dir)
            decision = _decorate_replay_decision(config, replay_decision, end_date=end_date, requested_start=start_date, availability=availability, stock_results=stock_results, index_results=index_results)
    except (M21RunnerError, M21ConfigError, OSError, ValueError) as exc:
        if isinstance(exc, (M21RunnerError, M21ConfigError)):
            failure = {"code": getattr(exc, "code", "CONFIG_ERROR"), "class": getattr(exc, "failure_class", "configuration"), "message": getattr(exc, "message", str(exc))}
        else:
            failure = {"code": "RUNNER_ERROR", "class": "runner", "message": "M21 could not produce a safe decision; manual review is required"}
        decision = _failure_decision(config, end_date=end_date, failure=failure, availability=availability)
        if availability is None:
            _write_json(output_dir / "availability_evidence.json", {
                "schema": "qqq-m21-availability-evidence/v1",
                "runtime_version": M21_RUNTIME_VERSION,
                "provider_mode": config.provider,
                "requested_end_date": end_date,
                "all_required_series_complete": False,
                "runner_failure": failure,
                "vix3m_policy": "missing VIX3M remains missing; no VIX/SVXY/BIL substitute",
            })
    except Exception:
        decision = _failure_decision(config, end_date=end_date, failure={"code": "RUNNER_ERROR", "class": "runner", "message": "M21 could not produce a safe decision; manual review is required"}, availability=availability)
    _write_json(output_dir / "decision.json", decision)
    _write_json(output_dir / "run_metadata.json", {
        "schema": "qqq-m21-run-metadata/v1",
        "runtime_version": M21_RUNTIME_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "checkpoint": "close",
        "config": config.public_dict(),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_workflow": os.environ.get("GITHUB_WORKFLOW"),
    })
    return decision


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one M21 free-close QQQ read-only decision.")
    parser.add_argument("--config", type=Path, default=Path("configs/m21/free_close.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", choices=("free_close", "local_csv"), help="Offline test override; scheduled runs use the config provider.")
    parser.add_argument("--as-of-date", help="Deterministic test date; never required by the scheduled workflow.")
    args = parser.parse_args(argv)
    try:
        config = M21Config.from_file(args.config)
        if args.provider:
            config = replace(config, provider=args.provider)
        config.validate()
        decision = run_close(config, args.output_dir, as_of_date=args.as_of_date)
    except M21ConfigError as exc:
        print(f"M21 configuration failed: {exc}")
        return 2
    print(f"M21 close status={decision.get('status')} signal_date={decision.get('signal_date') or '-'} output={args.output_dir}")
    if decision.get("status") != "READY" or not decision.get("decision_eligible"):
        print(f"M21 fail-closed code={decision.get('failure_code', 'NEEDS_REVIEW')} action=数据不完整，本次不调仓")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["latest_completed_session", "requested_start_date", "run_close"]
