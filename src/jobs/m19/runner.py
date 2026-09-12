"""M19 provider-to-decision runner.

The runner has two source modes:

* ``massive`` fetches five explicitly required daily series using the existing
  read-only Massive client boundary.  Failed or unavailable VXX/VIX inputs
  stop the run; no silent substitution is allowed.
* ``local_csv`` is a free/offline mode for replaying a previously captured
  dataset.  It is useful for historical validation, but it is not a live
  fallback for a failed Massive request.

Both modes write temporary, bounded CSV inputs and call the already validated
v12.2 causal replay.  M19 contains no indicator, regime, target-weight, or
strategy threshold of its own.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.jobs.m18.massive_history import M18MassiveHistoryError, MassiveDailyHistoryAdapter
from src.jobs.m18.v12_2_walk_forward import ReplayConfig, load_config as load_replay_config, run_walk_forward, write_artifacts
from src.realtime.config import RealtimeConfig
from src.realtime.massive_client import MassiveClient, MassiveClientError, MissingApiKeyError
from src.storage.normalization import TradingCalendar

from .config import (
    EXPECTED_REPLAY_VERSION,
    EXPECTED_STRATEGY_VERSION,
    M19Config,
    M19ConfigError,
    M19_RUNTIME_VERSION,
    REQUIRED_INTERNAL_SYMBOLS,
)


class M19ProviderError(RuntimeError):
    """A safe, user-facing provider or data-boundary failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DataInputs:
    prices_csv: Path
    vix_csv: Path
    vxx_csv: Path
    source: str
    source_manifest: tuple[Mapping[str, Any], ...]
    requested_start: str
    requested_end: str


_EXTRA_CLOSED_DATES = ("2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09")
_PREFERRED_WEIGHT_ORDER = ("QQQ", "QLD", "VXX", "BIL", "TLT", "IAU", "XLU", "SVXY")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise M19ProviderError("INVALID_DATE", f"{field_name} is not a calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise M19ProviderError("INVALID_DATE", f"{field_name} is not a calendar date") from exc
    if parsed.isoformat() != value:
        raise M19ProviderError("INVALID_DATE", f"{field_name} is not a calendar date")
    return value


def _parse_number(value: Any, field_name: str) -> float:
    if value in (None, "") or isinstance(value, bool):
        raise M19ProviderError("INVALID_BAR", f"{field_name} is missing")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise M19ProviderError("INVALID_BAR", f"{field_name} is not numeric") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise M19ProviderError("INVALID_BAR", f"{field_name} is not positive and finite")
    return number


def _number_text(value: Any) -> str:
    return format(float(value), ".12g")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise M19ProviderError("FILE_NOT_FOUND", f"data file is missing: {path.name}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise M19ProviderError("INVALID_CSV", f"data file has no header: {path.name}")
            rows = [dict(row) for row in reader]
            return [str(field) for field in reader.fieldnames], rows
    except M19ProviderError:
        raise
    except (OSError, csv.Error) as exc:
        raise M19ProviderError("INVALID_CSV", f"unable to read data file: {path.name}") from exc


def _manifest_for_file(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]], *, role: str, end_date: str) -> dict[str, Any]:
    dates = sorted(str(row.get("date", "")) for row in rows if row.get("date"))
    return {
        "role": role,
        "source": "local_csv",
        "path": str(path),
        "sha256": _sha256_file(path),
        "columns": list(fields),
        "rows_visible_through_end_date": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "requested_end_date": end_date,
        "price_field": "source-declared field; no forward fill or substitution",
    }


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _normalize_local(config: M19Config, end_date: str, workdir: Path) -> DataInputs:
    assert config.local_prices_csv and config.local_vix_csv and config.local_vxx_csv
    prices_path = config.resolve(config.local_prices_csv)
    vix_path = config.resolve(config.local_vix_csv)
    vxx_path = config.resolve(config.local_vxx_csv)
    price_fields, price_rows_raw = _read_csv(prices_path)
    vix_fields, vix_rows_raw = _read_csv(vix_path)
    vxx_fields, vxx_rows_raw = _read_csv(vxx_path)
    if "date" not in price_fields or "QQQ" not in price_fields or "BIL" not in price_fields:
        raise M19ProviderError("INVALID_CSV", "local price CSV must contain date, QQQ and BIL")
    if not {"date", "VIX", "VIX3M"}.issubset(set(vix_fields)):
        raise M19ProviderError("INVALID_CSV", "local VIX CSV must contain date,VIX,VIX3M")
    vxx_close_field = "close" if "close" in vxx_fields else "adj_close" if "adj_close" in vxx_fields else None
    if "date" not in vxx_fields or vxx_close_field is None:
        raise M19ProviderError("INVALID_CSV", "local VXX CSV must contain date and close or adj_close")

    def visible(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for row in rows:
            session = _parse_date(row.get("date"), "CSV.date")
            if session <= end_date:
                result.append({str(key): str(value) if value is not None else "" for key, value in row.items()})
        return sorted(result, key=lambda row: str(row["date"]))

    price_rows = visible(price_rows_raw)
    vix_rows = visible(vix_rows_raw)
    vxx_rows = visible(vxx_rows_raw)
    if not price_rows or not vix_rows or not vxx_rows:
        raise M19ProviderError("EMPTY_DATA", "one or more local CSV files have no visible rows")

    normalized_prices: list[dict[str, Any]] = []
    for row in price_rows:
        qqq = row.get("QQQ", "")
        bil = row.get("BIL", "")
        if qqq or bil:
            if qqq:
                _parse_number(qqq, f"QQQ.{row['date']}")
            if bil:
                _parse_number(bil, f"BIL.{row['date']}")
        normalized_prices.append({"date": row["date"], "QQQ": qqq, "BIL": bil})
    normalized_vix: list[dict[str, Any]] = []
    for row in vix_rows:
        for name in ("VIX", "VIX3M"):
            if row.get(name, ""):
                _parse_number(row[name], f"{name}.{row['date']}")
        normalized_vix.append({"date": row["date"], "VIX": row.get("VIX", ""), "VIX3M": row.get("VIX3M", "")})
    normalized_vxx: list[dict[str, Any]] = []
    for row in vxx_rows:
        close = row.get(vxx_close_field, "")
        _parse_number(close, f"VXX.close.{row['date']}")
        normalized_vxx.append({"date": row["date"], "symbol": "VXX", "close": close})

    prices_out = workdir / "prices_adj_close.csv"
    vix_out = workdir / "vix_indices.csv"
    vxx_out = workdir / "vxx.csv"
    _write_csv(prices_out, ("date", "QQQ", "BIL"), normalized_prices)
    _write_csv(vix_out, ("date", "VIX", "VIX3M"), normalized_vix)
    _write_csv(vxx_out, ("date", "symbol", "close"), normalized_vxx)
    manifest = (
        _manifest_for_file(prices_path, price_fields, price_rows, role="prices_adj_close", end_date=end_date),
        _manifest_for_file(vix_path, vix_fields, vix_rows, role="vix_indices", end_date=end_date),
        _manifest_for_file(vxx_path, vxx_fields, vxx_rows, role="vxx_execution_close", end_date=end_date),
    )
    return DataInputs(prices_out, vix_out, vxx_out, "local_csv", manifest, config.history_start_date, end_date)


def _massive_model_symbol(request: Mapping[str, Any]) -> str:
    symbols = request.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != 1:
        raise M19ProviderError("INVALID_PROVIDER_RESPONSE", "Massive returned an unexpected symbol request")
    return str(symbols[0]).strip().upper()


def _normalize_massive(config: M19Config, end_date: str, workdir: Path) -> DataInputs:
    realtime_path = config.resolve(config.massive_config_path)
    try:
        realtime_config = RealtimeConfig.from_file(realtime_path)
        declared = {item.symbol for item in realtime_config.symbols}
        required = set(config.required_provider_symbols)
        if not required.issubset(declared):
            missing = sorted(required - declared)
            raise M19ProviderError("CONFIG_MISSING_SYMBOL", f"Massive config is missing required symbols: {missing}")
        client = MassiveClient.from_env(realtime_config)
        adapter = MassiveDailyHistoryAdapter(realtime_config, client)
        snapshots = adapter.fetch(
            start_date=config.history_start_date,
            end_date=end_date,
            symbols=config.required_provider_symbols,
        )
    except MissingApiKeyError as exc:
        raise M19ProviderError("MISSING_API_KEY", "Massive API key is not configured in the Action environment") from exc
    except M19ProviderError:
        raise
    except (M19ConfigError, MassiveClientError, M18MassiveHistoryError, OSError, ValueError) as exc:
        text = str(exc).upper()
        code = "NOT_ENTITLED" if "NOT_ENTITLED" in text else "RATE_LIMITED" if "RATE_LIMITED" in text else "PROVIDER_ERROR"
        raise M19ProviderError(code, "Massive daily history could not be fetched") from exc

    failures = [snapshot for snapshot in snapshots if snapshot.status != "success"]
    if failures:
        codes = sorted({str(snapshot.error_code or "PROVIDER_ERROR") for snapshot in failures})
        raise M19ProviderError(codes[0] if len(codes) == 1 else "PROVIDER_ERROR", f"Massive required data unavailable for {len(failures)} required series")

    bars_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    manifest: list[Mapping[str, Any]] = []
    for snapshot in snapshots:
        symbol = _massive_model_symbol(snapshot.request)
        payload = snapshot.payload
        bars = payload.get("bars") if isinstance(payload, Mapping) else None
        if not isinstance(bars, list) or not bars:
            raise M19ProviderError("EMPTY_PAYLOAD", f"Massive returned no daily bars for {symbol}")
        bars_by_symbol[symbol] = [item for item in bars if isinstance(item, Mapping) and str(item.get("date", "")) <= end_date]
        manifest.append(snapshot.manifest_entry())
    missing_internal = sorted(set(REQUIRED_INTERNAL_SYMBOLS) - set(bars_by_symbol))
    if missing_internal:
        raise M19ProviderError("MISSING_SERIES", f"Massive response did not contain required series: {missing_internal}")

    price_dates = sorted({str(item.get("date")) for name in ("QQQ", "BIL") for item in bars_by_symbol[name]})
    prices_rows = []
    qqq_by_date = {str(item["date"]): item for item in bars_by_symbol["QQQ"]}
    bil_by_date = {str(item["date"]): item for item in bars_by_symbol["BIL"]}
    for session in price_dates:
        prices_rows.append({
            "date": session,
            "QQQ": _number_text(_parse_number(qqq_by_date[session]["close"], f"QQQ.{session}")) if session in qqq_by_date else "",
            "BIL": _number_text(_parse_number(bil_by_date[session]["close"], f"BIL.{session}")) if session in bil_by_date else "",
        })
    vix_dates = sorted({str(item.get("date")) for name in ("VIX", "VIX3M") for item in bars_by_symbol[name]})
    vix_by_date = {name: {str(item["date"]): item for item in bars_by_symbol[name]} for name in ("VIX", "VIX3M")}
    vix_rows = []
    for session in vix_dates:
        vix_rows.append({
            "date": session,
            "VIX": _number_text(_parse_number(vix_by_date["VIX"][session]["close"], f"VIX.{session}")) if session in vix_by_date["VIX"] else "",
            "VIX3M": _number_text(_parse_number(vix_by_date["VIX3M"][session]["close"], f"VIX3M.{session}")) if session in vix_by_date["VIX3M"] else "",
        })
    vxx_rows = []
    for item in sorted(bars_by_symbol["VXX"], key=lambda value: str(value.get("date"))):
        session = str(item.get("date"))
        vxx_rows.append({"date": session, "symbol": "VXX", "close": _number_text(_parse_number(item.get("close"), f"VXX.close.{session}"))})
    if not prices_rows or not vix_rows or not vxx_rows:
        raise M19ProviderError("EMPTY_PAYLOAD", "Massive returned an empty required data set")
    prices_out = workdir / "prices_adj_close.csv"
    vix_out = workdir / "vix_indices.csv"
    vxx_out = workdir / "vxx.csv"
    _write_csv(prices_out, ("date", "QQQ", "BIL"), prices_rows)
    _write_csv(vix_out, ("date", "VIX", "VIX3M"), vix_rows)
    _write_csv(vxx_out, ("date", "symbol", "close"), vxx_rows)
    return DataInputs(prices_out, vix_out, vxx_out, "massive", tuple(manifest), config.history_start_date, end_date)


def _calendar() -> TradingCalendar:
    return TradingCalendar(extra_closed_dates=_EXTRA_CLOSED_DATES)


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def latest_completed_session(config: M19Config, checkpoint: str, *, now: datetime | None = None, as_of_date: str | None = None) -> str:
    if checkpoint not in {"open", "midday", "close", "email"}:
        raise M19ConfigError(f"unsupported checkpoint: {checkpoint}")
    if as_of_date:
        return _parse_date(as_of_date, "as_of_date")
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config.market_timezone))
    today = current.date()
    calendar = _calendar()
    sessions = calendar.sessions(config.history_start_date, today.isoformat())
    if not sessions:
        raise M19ProviderError("NO_SESSION", "no NYSE session is available for the configured range")
    today_is_session = calendar.is_trading_day(today)
    after_close = current.hour * 60 + current.minute >= _minutes(config.close_confirmation_time)
    include_today = today_is_session and after_close and checkpoint in {"close", "email"}
    if include_today:
        return today.isoformat()
    prior = [session for session in sessions if session < today.isoformat()]
    if prior:
        return prior[-1]
    if today_is_session and not include_today:
        raise M19ProviderError("INSUFFICIENT_CONTEXT", "a prior completed NYSE session is required before the first run")
    return sessions[-1]


def _safe_metric(metrics: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("cagr", "total_return", "max_drawdown", "sharpe_zero_rf", "daily_win_rate", "correlation_to_QQQ", "final_equity")
    return {name: metrics.get(name) for name in keep}


def _weights_changed(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool | None:
    if previous is None:
        return None
    names = set(previous) | set(current)
    return any(abs(float(previous.get(name, 0.0)) - float(current.get(name, 0.0))) > 1e-12 for name in names)


def _ordered_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    names = list(_PREFERRED_WEIGHT_ORDER) + sorted(set(weights) - set(_PREFERRED_WEIGHT_ORDER))
    return {name: float(weights[name]) for name in names if name in weights and float(weights[name]) > 1e-12}


def _run_decision(config: M19Config, checkpoint: str, end_date: str, output_dir: Path) -> dict[str, Any]:
    inputs_dir = output_dir / "inputs"
    replay_dir = output_dir / "replay"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    if config.provider == "massive":
        inputs = _normalize_massive(config, end_date, inputs_dir)
    else:
        inputs = _normalize_local(config, end_date, inputs_dir)
    provider_manifest = list(inputs.source_manifest)
    _write_json(output_dir / "provider_manifest.json", {
        "schema": "qqq-m19-provider-manifest/v1",
        "runtime_version": M19_RUNTIME_VERSION,
        "provider": inputs.source,
        "requested_start_date": inputs.requested_start,
        "requested_end_date": inputs.requested_end,
        "required_series": list(REQUIRED_INTERNAL_SYMBOLS),
        "snapshots_or_files": provider_manifest,
        "manifest_hash": _sha256_bytes(_canonical_json(provider_manifest).encode("utf-8")),
    })
    base_config = load_replay_config(config.resolve(config.replay_config_path))
    raw = base_config.as_dict()
    raw.update({
        "start_date": config.replay_start_date,
        "end_date": end_date,
        "prices_adj_close_csv": str(inputs.prices_csv),
        "vix_indices_csv": str(inputs.vix_csv),
        "vxx_ohlcv_csv": str(inputs.vxx_csv),
        "initial_capital": config.initial_capital,
        "cost_bps": list(config.cost_bps),
        "require_vxx_for_returns": config.require_vxx,
        "output_root": str(replay_dir),
    })
    replay_config = ReplayConfig.from_mapping(raw)
    replay = run_walk_forward(replay_config)
    write_artifacts(replay, replay_dir)
    signal = dict(replay.signals[-1])
    previous = dict(replay.signals[-2]) if len(replay.signals) >= 2 else None
    quality_ok = signal.get("data_quality") == "OK" and signal.get("normalization_quality") == "OK" and bool(signal.get("indicator_ready"))
    is_decision_checkpoint = checkpoint in {"close", "email"}
    data_status = "READY" if quality_ok else "NEEDS_REVIEW"
    target_weights = _ordered_weights(signal.get("target_weights", {}))
    metrics = {f"strategy_{cost:g}bps": _safe_metric(replay.summary[f"strategy_{cost:g}bps"]) for cost in config.cost_bps}
    metrics["benchmark_QQQ"] = _safe_metric(replay.summary["benchmark_QQQ"])
    decision = {
        "schema": "qqq-m19-decision/v1",
        "runtime_version": M19_RUNTIME_VERSION,
        "replay_version": EXPECTED_REPLAY_VERSION,
        "strategy_version": EXPECTED_STRATEGY_VERSION,
        "checkpoint": checkpoint,
        "status": data_status,
        "decision_eligible": bool(is_decision_checkpoint and quality_ok),
        "paper_only": True,
        "execution_allowed": False,
        "signal_date": signal.get("signal_date"),
        "execution_date": signal.get("execution_date"),
        "as_of": signal.get("as_of"),
        "state": signal.get("state"),
        "previous_state": signal.get("previous_state"),
        "temperature": signal.get("temperature"),
        "trend": signal.get("trend"),
        "data_quality": signal.get("data_quality"),
        "normalization_quality": signal.get("normalization_quality"),
        "indicator_ready": signal.get("indicator_ready"),
        "observation_status": "INTRADAY_PROVISIONAL" if not is_decision_checkpoint else signal.get("observation_status"),
        "formal_publication_status": signal.get("formal_publication_status"),
        "strategy_formal_execution_eligible": bool(signal.get("execution_eligible")),
        "target_weights": target_weights if quality_ok else {},
        "target_changed_from_previous_signal": _weights_changed(previous.get("target_weights") if previous else None, target_weights),
        "reason_codes": list(signal.get("reason_codes", [])),
        "indicator_values": signal.get("indicator_values", {}),
        "data_window": {
            "provider_requested_start": inputs.requested_start,
            "provider_requested_end": inputs.requested_end,
            "signal_date": signal.get("signal_date"),
            "execution_date": signal.get("execution_date"),
            "uses_data_through_signal_date": True,
            "execution_delay_trading_days": 1,
        },
        "source": {
            "provider": inputs.source,
            "data_version": replay.dataset.data_version,
            "provider_manifest_hash": _sha256_bytes(_canonical_json(provider_manifest).encode("utf-8")),
        },
        "metrics": metrics,
        "checks": {
            "no_future_bar_in_prefix": bool(replay.checks.get("no_future_bar_in_prefix")),
            "execution_date_is_next_session": bool(replay.checks.get("execution_date_is_next_session")),
            "target_weight_sum": bool(replay.checks.get("target_weight_sum")),
            "missing_vxx_policy": "fail_closed",
            "intraday_does_not_publish_target": not is_decision_checkpoint,
        },
        "manual_action": (
            "盘中只观察，不改变纸上目标。"
            if not is_decision_checkpoint
            else "下一交易日只可按纸上目标人工复核；程序没有下单权限。"
        ),
    }
    _write_json(output_dir / "decision.json", decision)
    _write_json(output_dir / "run_metadata.json", {
        "schema": "qqq-m19-run-metadata/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "checkpoint": checkpoint,
        "config": config.public_dict(),
        "github_sha": os.environ.get("GITHUB_SHA"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_workflow": os.environ.get("GITHUB_WORKFLOW"),
    })
    return decision


def _failure_decision(config: M19Config, checkpoint: str, end_date: str | None, error: M19ProviderError | Exception) -> dict[str, Any]:
    if isinstance(error, M19ProviderError):
        code, message = error.code, error.message
    else:
        code, message = "RUNNER_ERROR", "M19 could not produce a safe decision; manual review is required"
    return {
        "schema": "qqq-m19-decision/v1",
        "runtime_version": M19_RUNTIME_VERSION,
        "replay_version": EXPECTED_REPLAY_VERSION,
        "strategy_version": EXPECTED_STRATEGY_VERSION,
        "checkpoint": checkpoint,
        "status": "DATA_ERROR",
        "decision_eligible": False,
        "paper_only": True,
        "execution_allowed": False,
        "signal_date": None,
        "execution_date": None,
        "as_of": None,
        "state": None,
        "temperature": None,
        "target_weights": {},
        "data_quality": "FAILED",
        "failure_code": code,
        "failure_message": message,
        "data_window": {"provider_requested_start": config.history_start_date, "provider_requested_end": end_date},
        "checks": {"missing_vxx_policy": "fail_closed", "safe_failure": True},
        "manual_action": "数据不完整或权限不足，暂不调仓；请先修复数据源或权限。",
    }


def run_checkpoint(config: M19Config, checkpoint: str, output_dir: Path, *, now: datetime | None = None, as_of_date: str | None = None) -> dict[str, Any]:
    """Run one bounded checkpoint and always write a decision JSON."""

    output_dir.mkdir(parents=True, exist_ok=True)
    end_date: str | None = None
    try:
        end_date = latest_completed_session(config, checkpoint, now=now, as_of_date=as_of_date)
        decision = _run_decision(config, checkpoint, end_date, output_dir)
    except (M19ProviderError, M19ConfigError, ValueError, OSError) as exc:
        decision = _failure_decision(config, checkpoint, end_date, exc)
        _write_json(output_dir / "decision.json", decision)
        _write_json(output_dir / "run_metadata.json", {
            "schema": "qqq-m19-run-metadata/v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checkpoint": checkpoint,
            "config": config.public_dict(),
            "github_sha": os.environ.get("GITHUB_SHA"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        })
    except Exception as exc:  # noqa: BLE001 - the scheduled boundary must always fail closed with a decision file
        decision = _failure_decision(config, checkpoint, end_date, exc)
        _write_json(output_dir / "decision.json", decision)
        _write_json(output_dir / "run_metadata.json", {
            "schema": "qqq-m19-run-metadata/v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "checkpoint": checkpoint,
            "config": config.public_dict(),
            "github_sha": os.environ.get("GITHUB_SHA"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        })
    return decision


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one QQQ M19 read-only checkpoint.")
    parser.add_argument("--config", type=Path, default=Path("configs/m19/readonly.json"))
    parser.add_argument("--checkpoint", choices=("open", "midday", "close", "email"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", choices=("massive", "local_csv"), help="Optional offline test override; scheduled runs use the config value.")
    parser.add_argument("--local-prices-csv", type=str, help="Optional offline test override for the prices CSV.")
    parser.add_argument("--local-vix-csv", type=str, help="Optional offline test override for the VIX CSV.")
    parser.add_argument("--local-vxx-csv", type=str, help="Optional offline test override for the VXX CSV.")
    parser.add_argument("--as-of-date", type=str, help="Deterministic test date; never required by scheduled runs.")
    args = parser.parse_args(argv)
    try:
        config = M19Config.from_file(args.config)
        if args.provider:
            config = replace(config, provider=args.provider)
        if args.local_prices_csv or args.local_vix_csv or args.local_vxx_csv:
            config = replace(
                config,
                local_prices_csv=args.local_prices_csv or config.local_prices_csv,
                local_vix_csv=args.local_vix_csv or config.local_vix_csv,
                local_vxx_csv=args.local_vxx_csv or config.local_vxx_csv,
            )
        config.validate()
        decision = run_checkpoint(config, args.checkpoint, args.output_dir, as_of_date=args.as_of_date)
    except M19ConfigError as exc:
        print(f"M19 configuration failed: {exc}")
        return 2
    print(f"M19 checkpoint={args.checkpoint} status={decision.get('status')} signal_date={decision.get('signal_date') or '-'} output={args.output_dir}")
    if decision.get("status") == "DATA_ERROR":
        print(f"M19 fail-closed code={decision.get('failure_code', 'UNKNOWN')}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DataInputs", "M19ProviderError", "latest_completed_session", "run_checkpoint"]
