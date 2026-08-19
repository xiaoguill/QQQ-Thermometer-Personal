"""Causal local-data Walk-Forward replay for the QQQ thermometer.

This module is deliberately an audit runner, not a new strategy engine.
``v12.2-causal-walk-forward`` is the replay/version label.  The actual
strategy rules, thresholds and candidate weights are read from the frozen
``v10_preserve_shock_recovery`` contract through the existing M02--M07
services.  No indicator, regime or target-weight rule is copied here.

The runner has three important properties:

* every signal-date request is rebuilt from a prefix ending on that date;
* M18's existing M02--M07 chain is called for each requested signal date;
* return simulation starts on the next execution session and never silently
  substitutes VIX for VXX or fills a missing tradable price.

The default output location is outside the Git worktree.  This keeps a
historical evidence bundle separate from source, frozen contracts and the
existing research outputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.storage.indicators import IndicatorRun, IndicatorSnapshot, calculate_indicator_snapshots
from src.storage.market_data import MarketDataRequest, RawSnapshot
from src.storage.normalization import NormalizedBar, NormalizationResult, TradingCalendar, normalize_snapshots
from src.thermometer.contracts import load_contract
from src.thermometer.explanation import CLOSE_CONFIRMED, ExplanationInput, ExplanationModel, build_explanation
from src.thermometer.regime import RegimeConfig, RegimeInput, RegimeState, RegimeSnapshot, evaluate_regime
from src.thermometer.target_weights import TargetWeightSnapshot, build_target_weights


REPLAY_VERSION = "v12.2-causal-walk-forward/v1"
STRATEGY_VERSION = "v10_preserve_shock_recovery"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "m18" / "v12_2_walk_forward.json"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SIGNAL_INDICATORS = (
    "qqq_return_5d",
    "qqq_return_10d",
    "qqq_return_20d",
    "qqq_ema10",
    "qqq_sma150",
    "qqq_momentum126",
    "qqq_rv20",
    "vix",
    "vix3m",
    "vix_term_ratio",
)
_EXECUTION_ASSETS = ("QQQ", "BIL", "VXX")
# Full-market closures represented in the local historical files but not in
# the small dependency-free NYSE calendar used by M03.  These are calendar
# facts, not strategy parameters.  Filtering them prevents an absent closure
# from becoming a synthetic missing trading session.
_KNOWN_EXTRA_CLOSED_DATES = ("2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09")


class WalkForwardError(ValueError):
    """Raised when the causal replay input or audit boundary is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WalkForwardError(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: Any, field_name: str) -> float:
    if value is None or isinstance(value, bool):
        raise WalkForwardError(f"{field_name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise WalkForwardError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise WalkForwardError(f"{field_name} must be finite")
    return result


def _iso_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise WalkForwardError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise WalkForwardError(f"{field_name} must be an ISO date") from exc


def _as_of(signal_date: str) -> str:
    # 23:59:59 UTC still maps to the same NYSE local date.  The replay treats
    # the daily close as available only after that date's close.
    return f"{signal_date}T23:59:59Z"


def _json_text(value: Any) -> str:
    return _canonical_json(value)


@dataclass(frozen=True)
class ReplayConfig:
    """Validated, non-secret inputs for one replay run."""

    start_date: str
    end_date: str | None
    prices_adj_close_csv: str
    vix_indices_csv: str
    vxx_ohlcv_csv: str | None
    initial_capital: float
    cost_bps: tuple[float, ...]
    require_vxx_for_returns: bool
    calendar_timezone: str
    exchange: str
    output_root: str | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ReplayConfig":
        _require(isinstance(raw, Mapping), "walk-forward config must be an object")
        costs = raw.get("cost_bps", [5.0, 10.0, 25.0])
        _require(isinstance(costs, Sequence) and not isinstance(costs, (str, bytes)) and costs, "cost_bps must be a non-empty list")
        normalized_costs = tuple(_number(value, "cost_bps") for value in costs)
        _require(all(value >= 0.0 for value in normalized_costs), "cost_bps cannot be negative")
        return cls(
            start_date=_iso_date(raw.get("start_date", "2025-01-01"), "start_date"),
            end_date=None if raw.get("end_date") in (None, "") else _iso_date(raw["end_date"], "end_date"),
            prices_adj_close_csv=str(raw.get("prices_adj_close_csv", "research/qqq_drawdown_strategy/output/prices_adj_close.csv")),
            vix_indices_csv=str(raw.get("vix_indices_csv", "research/qqq_drawdown_strategy/output/vix_indices.csv")),
            vxx_ohlcv_csv=None if raw.get("vxx_ohlcv_csv") in (None, "") else str(raw["vxx_ohlcv_csv"]),
            initial_capital=_number(raw.get("initial_capital", 1_000_000.0), "initial_capital"),
            cost_bps=normalized_costs,
            require_vxx_for_returns=bool(raw.get("require_vxx_for_returns", True)),
            calendar_timezone=str(raw.get("calendar_timezone", "America/New_York")),
            exchange=str(raw.get("exchange", "NYSE")),
            output_root=None if raw.get("output_root") in (None, "") else str(raw["output_root"]),
        )

    def __post_init__(self) -> None:
        _require(self.initial_capital > 0.0, "initial_capital must be positive")
        _require(self.calendar_timezone.strip() == "America/New_York", "the current M03 calendar must remain America/New_York")
        _require(self.exchange.strip().upper() == "NYSE", "the current M03 calendar must remain NYSE")
        _require(date.fromisoformat(self.start_date) <= date.fromisoformat(self.end_date) if self.end_date else True, "start_date must not be after end_date")

    def as_dict(self) -> dict[str, Any]:
        return {
            "replay_version": REPLAY_VERSION,
            "strategy_version": STRATEGY_VERSION,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "prices_adj_close_csv": self.prices_adj_close_csv,
            "vix_indices_csv": self.vix_indices_csv,
            "vxx_ohlcv_csv": self.vxx_ohlcv_csv,
            "initial_capital": self.initial_capital,
            "cost_bps": list(self.cost_bps),
            "require_vxx_for_returns": self.require_vxx_for_returns,
            "calendar_timezone": self.calendar_timezone,
            "exchange": self.exchange,
            "output_root": self.output_root,
            "signal_timing": {
                "signal_cutoff": "close",
                "signal_uses_data_through": "signal_date",
                "execution_delay_trading_days": 1,
                "execution_price_basis": "next_session_close_to_close_return_proxy",
            },
        }


@dataclass(frozen=True)
class CausalMarketDataset:
    """Parsed local files with explicit per-symbol date maps."""

    prices: Mapping[str, Mapping[str, float]]
    vix: Mapping[str, float]
    vix3m: Mapping[str, float]
    vxx: Mapping[str, float]
    source_manifest: tuple[Mapping[str, Any], ...]
    data_version: str
    signal_context_start: str
    signal_end: str

    def signal_dates(self, calendar: TradingCalendar) -> tuple[str, ...]:
        qqq = self.prices.get("QQQ", {})
        common = set(qqq) & set(self.vix) & set(self.vix3m)
        return tuple(
            session
            for session in calendar.sessions(self.signal_context_start, self.signal_end)
            if session in common
        )

    def performance_dates(self, calendar: TradingCalendar, *, start_date: str) -> tuple[str, ...]:
        common = set(self.prices.get("QQQ", {})) & set(self.prices.get("BIL", {})) & set(self.vxx)
        return tuple(
            session
            for session in calendar.sessions(start_date, self.signal_end)
            if session in common
        )


@dataclass(frozen=True)
class ReplayRun:
    config: ReplayConfig
    dataset: CausalMarketDataset
    signals: tuple[Mapping[str, Any], ...]
    bootstrap_signal: Mapping[str, Any]
    equity_by_cost: Mapping[float, tuple[Mapping[str, Any], ...]]
    transactions: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    checks: Mapping[str, Any]


@dataclass(frozen=True)
class CausalChainContext:
    """Efficient historical state context built from the chronological stream.

    M04 indicators are causal by construction: each snapshot only consumes
    bars at or before its signal date.  M05 is replayed once in chronological
    order with the same state-machine function, but only the small prior tail
    needed for the RV comparison is passed to each call.  This avoids the
    quadratic validation cost of the general-purpose ``replay_regimes``
    façade while preserving its transition semantics.
    """

    registry: Any
    regime_config: RegimeConfig
    full_indicator_run: IndicatorRun
    full_inputs: tuple[RegimeInput, ...]
    indicators_by_date: Mapping[str, IndicatorSnapshot]
    qqq_bars_by_date: Mapping[str, NormalizedBar]
    previous_states: Mapping[str, RegimeState | None]
    full_regimes: Mapping[str, RegimeSnapshot]


@dataclass(frozen=True)
class CausalSignalParts:
    indicator: IndicatorSnapshot
    qqq_bar: NormalizedBar
    regime: RegimeSnapshot
    explanation: ExplanationModel
    target: TargetWeightSnapshot
    normalization_quality: str


def _resolve_path(value: str | None) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(value)
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _replay_calendar(config: ReplayConfig) -> TradingCalendar:
    return TradingCalendar(
        exchange=config.exchange,
        timezone=config.calendar_timezone,
        extra_closed_dates=_KNOWN_EXTRA_CLOSED_DATES,
    )


def _read_prices_csv(path: Path) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    _require(path.exists(), f"price file does not exist: {path}")
    series: dict[str, dict[str, float]] = {}
    columns: list[str] = []
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None and "date" in reader.fieldnames, "price CSV must contain date")
        columns = [str(value) for value in reader.fieldnames if value and value != "date"]
        for row in reader:
            row_count += 1
            session = _iso_date(row.get("date"), "price.date")
            for symbol in columns:
                raw = row.get(symbol)
                if raw in (None, ""):
                    continue
                bucket = series.setdefault(symbol.strip().upper(), {})
                _require(session not in bucket, f"duplicate price date: {symbol} {session}")
                price = _number(raw, f"price.{symbol}.{session}")
                _require(price > 0.0, f"price.{symbol}.{session} must be positive")
                bucket[session] = price
    _require("QQQ" in series and series["QQQ"], "price CSV must contain QQQ")
    meta = {
        "role": "prices_adj_close",
        "path": str(path),
        "sha256": _file_hash(path),
        "columns": columns,
        "rows": row_count,
        "first_date": min(min(values) for values in series.values()),
        "last_date": max(max(values) for values in series.values()),
        "price_field": "adjusted close",
        "ohlc_available": False,
        "ohlc_replay_adapter": "open_high_low_repeated_from_close_for_M02_compatibility",
    }
    return series, meta


def _read_vix_csv(path: Path) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    _require(path.exists(), f"VIX file does not exist: {path}")
    vix: dict[str, float] = {}
    vix3m: dict[str, float] = {}
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None and {"date", "VIX", "VIX3M"}.issubset(set(reader.fieldnames)), "VIX CSV must contain date,VIX,VIX3M")
        for row in reader:
            row_count += 1
            session = _iso_date(row.get("date"), "vix.date")
            if row.get("VIX") not in (None, ""):
                _require(session not in vix, f"duplicate VIX date: {session}")
                vix[session] = _number(row["VIX"], f"VIX.{session}")
            if row.get("VIX3M") not in (None, ""):
                _require(session not in vix3m, f"duplicate VIX3M date: {session}")
                vix3m[session] = _number(row["VIX3M"], f"VIX3M.{session}")
    _require(vix and vix3m, "VIX CSV must contain non-empty VIX and VIX3M series")
    meta = {
        "role": "vix_indices",
        "path": str(path),
        "sha256": _file_hash(path),
        "columns": ["date", "VIX", "VIX3M"],
        "rows": row_count,
        "first_date": min(min(vix), min(vix3m)),
        "last_date": max(max(vix), max(vix3m)),
        "price_field": "CBOE index close",
        "ohlc_available": False,
    }
    return vix, vix3m, meta


def _read_vxx_csv(path: Path | None) -> tuple[dict[str, float], Mapping[str, Any] | None]:
    if path is None:
        return {}, None
    _require(path.exists(), f"VXX file does not exist: {path}")
    values: dict[str, float] = {}
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None and {"date", "close"}.issubset(set(reader.fieldnames)), "VXX CSV must contain date and close")
        for row in reader:
            row_count += 1
            session = _iso_date(row.get("date"), "VXX.date")
            if row.get("symbol") not in (None, ""):
                _require(str(row["symbol"]).strip().upper() == "VXX", "VXX input contains another symbol")
            _require(session not in values, f"duplicate VXX date: {session}")
            close = _number(row.get("close"), f"VXX.close.{session}")
            _require(close > 0.0, f"VXX.close.{session} must be positive")
            values[session] = close
    _require(values, "VXX CSV is empty")
    return values, {
        "role": "vxx_execution_close",
        "path": str(path),
        "sha256": _file_hash(path),
        "columns": list(reader.fieldnames or ()),
        "rows": row_count,
        "first_date": min(values),
        "last_date": max(values),
        "price_field": "local cached VXX split-adjusted close proxy",
        "ohlc_available": True,
        "total_return_adjusted": False,
    }


def load_dataset(config: ReplayConfig) -> CausalMarketDataset:
    prices_path = _resolve_path(config.prices_adj_close_csv)
    vix_path = _resolve_path(config.vix_indices_csv)
    vxx_path = _resolve_path(config.vxx_ohlcv_csv)
    _require(prices_path is not None and vix_path is not None, "price and VIX paths are required")
    prices, prices_meta = _read_prices_csv(prices_path)
    vix, vix3m, vix_meta = _read_vix_csv(vix_path)
    vxx, vxx_meta = _read_vxx_csv(vxx_path)
    if config.require_vxx_for_returns:
        _require(vxx, "VXX execution data is required; refusing to substitute VIX or BIL")
    _require("BIL" in prices and prices["BIL"], "price CSV must contain BIL for the frozen candidate policy")

    calendar = _replay_calendar(config)
    qqq_dates = set(prices["QQQ"])
    common = qqq_dates & set(vix) & set(vix3m)
    _require(common, "QQQ, VIX and VIX3M have no common dates")
    context_start = min(common)
    natural_end = min(max(qqq_dates), max(vix), max(vix3m))
    signal_end = min(natural_end, config.end_date) if config.end_date else natural_end
    _require(signal_end >= config.start_date, "requested start_date is after available signal data")
    _require(calendar.sessions(context_start, signal_end), "no NYSE sessions in the requested signal range")

    source_manifest = [prices_meta, vix_meta]
    if vxx_meta is not None:
        source_manifest.append(vxx_meta)
    data_version = "local-causal-" + _sha256_json(
        {
            "sources": [{"role": item["role"], "sha256": item["sha256"]} for item in source_manifest],
            "signal_end": signal_end,
            "calendar": calendar.calendar_id,
        }
    )[:16]
    return CausalMarketDataset(
        prices=prices,
        vix=vix,
        vix3m=vix3m,
        vxx=vxx,
        source_manifest=tuple(source_manifest),
        data_version=data_version,
        signal_context_start=context_start,
        signal_end=signal_end,
    )


def _bars_from_series(
    symbol: str,
    series: Mapping[str, float],
    *,
    start_date: str,
    end_date: str,
    calendar: TradingCalendar,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in sorted(day for day in series if start_date <= day <= end_date and calendar.is_trading_day(day)):
        close = float(series[session])
        rows.append(
            {
                "symbol": symbol,
                "date": session,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
            }
        )
    return rows


def build_prefix_snapshots(
    dataset: CausalMarketDataset,
    *,
    signal_date: str,
    calendar: TradingCalendar,
) -> tuple[RawSnapshot, ...]:
    """Build exactly the M02 prefix visible at one signal date."""

    signal_date = _iso_date(signal_date, "signal_date")
    _require(dataset.signal_context_start <= signal_date <= dataset.signal_end, "signal_date is outside the dataset context")
    as_of = _as_of(signal_date)
    stock_request = MarketDataRequest(
        source="local-adjusted-close-csv",
        symbols=("QQQ",),
        start_date=dataset.signal_context_start,
        end_date=signal_date,
        price_basis="adjusted_ohlcv",
        timezone=calendar.timezone,
        exchange=calendar.exchange,
        provider_params={"dataset": "prices_adj_close.csv", "ohlc_adapter": "close_repeated"},
    )
    index_request = MarketDataRequest(
        source="local-cboe-index-csv",
        symbols=("VIX", "VIX3M"),
        start_date=dataset.signal_context_start,
        end_date=signal_date,
        price_basis="index_level",
        timezone=calendar.timezone,
        exchange=calendar.exchange,
        provider_params={"dataset": "vix_indices.csv", "ohlc_adapter": "close_repeated"},
    )
    stock_rows = _bars_from_series("QQQ", dataset.prices["QQQ"], start_date=dataset.signal_context_start, end_date=signal_date, calendar=calendar)
    index_rows = _bars_from_series("VIX", dataset.vix, start_date=dataset.signal_context_start, end_date=signal_date, calendar=calendar)
    index_rows.extend(_bars_from_series("VIX3M", dataset.vix3m, start_date=dataset.signal_context_start, end_date=signal_date, calendar=calendar))
    _require(stock_rows and index_rows, f"no prefix bars available for {signal_date}")
    snapshots = (
        RawSnapshot.capture(
            source=stock_request.source,
            request=stock_request,
            retrieved_at=as_of,
            payload={"bars": stock_rows},
            price_basis=stock_request.price_basis,
            timezone_name=stock_request.timezone,
        ),
        RawSnapshot.capture(
            source=index_request.source,
            request=index_request,
            retrieved_at=as_of,
            payload={"bars": sorted(index_rows, key=lambda item: (item["date"], item["symbol"]))},
            price_basis=index_request.price_basis,
            timezone_name=index_request.timezone,
        ),
    )
    for snapshot in snapshots:
        request = dict(snapshot.request)
        _require(request["end_date"] == signal_date, "prefix request end_date is not the signal date")
        payload = snapshot.payload
        _require(all(str(row["date"]) <= signal_date for row in payload["bars"]), "prefix payload contains a future bar")
    return snapshots


def _next_session(calendar: TradingCalendar, signal_date: str) -> str:
    start = date.fromisoformat(signal_date)
    sessions = calendar.sessions(signal_date, (start + timedelta(days=14)).isoformat())
    _require(len(sessions) >= 2 and sessions[0] == signal_date, f"no next NYSE session after {signal_date}")
    return sessions[1]


def _quality_max(values: Iterable[str]) -> str:
    priority = {"OK": 0, "PARTIAL": 1, "STALE": 2, "FAILED": 3, "NEEDS_REVIEW": 4}
    present = tuple(values)
    _require(all(value in priority for value in present), "unknown normalization quality")
    return max(present, key=lambda value: priority[value]) if present else "FAILED"


def _normalize_groups(
    snapshots: Sequence[RawSnapshot],
    *,
    as_of: str,
    calendar: TradingCalendar,
) -> tuple[NormalizationResult, ...]:
    """Run M03 separately for ETF and index request semantics."""

    groups: dict[str, list[RawSnapshot]] = {}
    for snapshot in snapshots:
        key = _canonical_json(dict(snapshot.request))
        groups.setdefault(key, []).append(snapshot)
    _require(groups, "no raw snapshot groups supplied")
    return tuple(
        normalize_snapshots(group, as_of=as_of, calendar=calendar)
        for key, group in sorted(groups.items())
    )


def _build_chain_context(dataset: CausalMarketDataset, *, calendar: TradingCalendar) -> CausalChainContext:
    """Build the historical causal state context once in chronological order."""

    registry = load_contract()
    regime_config = RegimeConfig.from_registry(registry)
    full_snapshots = build_prefix_snapshots(dataset, signal_date=dataset.signal_end, calendar=calendar)
    normalization_results = _normalize_groups(full_snapshots, as_of=_as_of(dataset.signal_end), calendar=calendar)
    full_indicator_run = calculate_indicator_snapshots(normalization_results, calendar=calendar)
    _require(full_indicator_run.quality == "OK", f"full causal input quality is {full_indicator_run.quality}; refusing to backfill earlier dates")
    qqq_bars = {
        bar.bar_date: bar
        for result in normalization_results
        for bar in result.bars
        if bar.symbol == "QQQ"
    }
    full_inputs = tuple(
        RegimeInput(snapshot, qqq_bars[snapshot.signal_date])
        for snapshot in full_indicator_run.snapshots
        if snapshot.signal_date in qqq_bars
    )
    _require(len(full_inputs) == len(full_indicator_run.snapshots), "full causal chain lost a QQQ bar")
    previous_states: dict[str, RegimeState | None] = {}
    full_regimes: dict[str, RegimeSnapshot] = {}
    state: RegimeState | None = None
    tail_length = regime_config.recovery_rv_comparison_days
    for index, current in enumerate(full_inputs):
        previous_states[current.indicators.signal_date] = state
        prior_inputs = full_inputs[max(0, index - tail_length) : index]
        snapshot = evaluate_regime(
            current,
            config=regime_config,
            calendar=calendar,
            previous_state=state,
            prior_inputs=prior_inputs,
        )
        full_regimes[current.indicators.signal_date] = snapshot
        state = RegimeState(snapshot.state, snapshot.elapsed_state_sessions, snapshot.medium_gate_streak)
    _require(len(full_regimes) == len(full_inputs), "full causal state replay is incomplete")
    return CausalChainContext(
        registry,
        regime_config,
        full_indicator_run,
        full_inputs,
        {item.signal_date: item for item in full_indicator_run.snapshots},
        qqq_bars,
        previous_states,
        full_regimes,
    )


def _prefix_signal_parts(
    dataset: CausalMarketDataset,
    context: CausalChainContext,
    *,
    signal_date: str,
    calendar: TradingCalendar,
) -> tuple[CausalSignalParts, tuple[RawSnapshot, ...], Mapping[str, Any]]:
    """Calculate one exact-as-of M03--M07 result from a date prefix."""

    snapshots = build_prefix_snapshots(dataset, signal_date=signal_date, calendar=calendar)
    # M04's implementation is prefix-stable: the full chronological run
    # computes each value from observations at or before its own date.  We
    # still build the exact raw prefix above for every output row and bind the
    # selected snapshot/bar provenance to that prefix.  Selected prefix
    # recalculations are performed by ``_prefix_indicator_crosscheck``.
    indicator = context.indicators_by_date.get(signal_date)
    full_qqq_bar = context.qqq_bars_by_date.get(signal_date)
    _require(indicator is not None and full_qqq_bar is not None, f"causal M04/M03 context did not reach {signal_date}")
    prefix_qqq_snapshot = next(snapshot for snapshot in snapshots if "QQQ" in tuple(dict(snapshot.request)["symbols"]))
    indicator = replace(indicator, as_of=_as_of(signal_date))
    qqq_bar = replace(
        full_qqq_bar,
        snapshot_ids=(prefix_qqq_snapshot.snapshot_id,),
        retrieved_at_by_source=((prefix_qqq_snapshot.source, _as_of(signal_date)),),
    )
    current = RegimeInput(indicator, qqq_bar)
    prior_full_inputs = [item for item in context.full_inputs if item.indicators.signal_date < signal_date]
    tail_length = context.regime_config.recovery_rv_comparison_days
    prior_inputs = tuple(
        RegimeInput(
            replace(item.indicators, as_of=_as_of(item.indicators.signal_date)),
            replace(
                item.qqq_bar,
                retrieved_at_by_source=((item.qqq_bar.source, _as_of(item.qqq_bar.bar_date)),),
            ),
        )
        for item in prior_full_inputs[-tail_length:]
    )
    regime = evaluate_regime(
        current,
        config=context.regime_config,
        calendar=calendar,
        previous_state=context.previous_states.get(signal_date),
        prior_inputs=prior_inputs,
    )
    full_regime = context.full_regimes.get(signal_date)
    _require(full_regime is not None, f"full causal state is missing {signal_date}")
    _require(regime.state == full_regime.state, f"prefix/full state mismatch on {signal_date}: {regime.state} != {full_regime.state}")
    explanation = build_explanation(ExplanationInput(regime, indicator, CLOSE_CONFIRMED))
    target = build_target_weights(regime, registry=context.registry, calendar=calendar)
    parts = CausalSignalParts(indicator, qqq_bar, regime, explanation, target, context.full_indicator_run.quality)
    raw_manifest = [snapshot.manifest_entry() for snapshot in snapshots]
    max_visible_date = max(str(row["date"]) for snapshot in snapshots for row in (snapshot.payload or {}).get("bars", []))
    audit = {
        "signal_date": signal_date,
        "request_end_dates": [dict(snapshot.request)["end_date"] for snapshot in snapshots],
        "max_visible_bar_date": max_visible_date,
        "indicator_input_dates": {key: list(value) for key, value in indicator.input_bar_dates.items()},
        "future_input_dates": [day for values in indicator.input_bar_dates.values() for day in values if day > signal_date],
        "weights_sum": sum(float(value) for value in target.target_weights.values()),
        "execution_date_is_next_session": regime.execution_date == _next_session(calendar, signal_date),
        "state_matches_full_causal_stream": regime.state == full_regime.state,
        "normalization_quality": parts.normalization_quality,
    }
    _require(max_visible_date <= signal_date, f"future bar entered signal {signal_date}")
    _require(not audit["future_input_dates"], f"indicator future input detected on {signal_date}")
    _require(audit["execution_date_is_next_session"], f"execution lag mismatch on {signal_date}")
    _require(audit["state_matches_full_causal_stream"], f"state mismatch on {signal_date}")
    _require(abs(float(audit["weights_sum"]) - 1.0) <= 1e-8, f"target weights do not sum to 1 on {signal_date}")
    return parts, snapshots, {**audit, "raw_manifest": raw_manifest}


def _run_signal(
    dataset: CausalMarketDataset,
    context: CausalChainContext,
    *,
    signal_date: str,
    calendar: TradingCalendar,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    parts, snapshots, audit = _prefix_signal_parts(dataset, context, signal_date=signal_date, calendar=calendar)
    indicator = parts.indicator
    regime = parts.regime
    explanation = parts.explanation
    target = parts.target
    raw_manifest = list(audit["raw_manifest"])
    max_visible_date = str(audit["max_visible_bar_date"])
    record = {
        "replay_version": REPLAY_VERSION,
        "strategy_version": target.strategy_version,
        "strategy_config_hash": target.strategy_config_hash,
        "signal_date": signal_date,
        "execution_date": regime.execution_date,
        "as_of": indicator.as_of,
        "run_id": f"v12.2-causal|{signal_date}",
        "data_version": dataset.data_version,
        "data_quality": indicator.quality,
        "normalization_quality": parts.normalization_quality,
        "indicator_ready": indicator.ready,
        "state": regime.state,
        "previous_state": regime.previous_state,
        "transition": regime.transition,
        "regime_confirmed": regime.confirmed,
        "formal_publication_status": target.weight_status,
        "candidate_only": target.candidate_only,
        "execution_eligible": target.execution_eligible,
        "temperature": explanation.temperature,
        "trend": explanation.trend,
        "signal_agreement": explanation.signal_agreement,
        "reason_codes": list(dict.fromkeys((*regime.reason_codes, *explanation.reason_codes, *target.change_reason_codes))),
        "regime_evidence": [item.as_dict() for item in regime.evidence],
        "indicator_values": {name: indicator.values.get(name) for name in _SIGNAL_INDICATORS},
        "target_weights": dict(target.target_weights),
        "raw_snapshot_ids": [snapshot.snapshot_id for snapshot in snapshots],
        "raw_manifest": raw_manifest,
        "max_visible_bar_date": max_visible_date,
        "prefix_hash": _sha256_json({"manifest": raw_manifest, "indicator": indicator.as_dict()}),
    }
    return record, audit


def _prefix_indicator_crosscheck(
    dataset: CausalMarketDataset,
    context: CausalChainContext,
    *,
    signal_dates: Sequence[str],
    calendar: TradingCalendar,
) -> tuple[str, ...]:
    """Recalculate selected exact prefixes and compare them with M04's stream.

    The full M04 run is the efficient chronological implementation.  This
    independent spot-check prevents a future row from changing an earlier
    snapshot and records the dates in the evidence bundle.  The daily raw
    prefix itself is still built for every signal row by ``_run_signal``.
    """

    checked: list[str] = []
    for signal_date in signal_dates:
        snapshots = build_prefix_snapshots(dataset, signal_date=signal_date, calendar=calendar)
        results = _normalize_groups(snapshots, as_of=_as_of(signal_date), calendar=calendar)
        prefix_run = calculate_indicator_snapshots(results, calendar=calendar)
        _require(prefix_run.quality == "OK", f"prefix indicator quality is {prefix_run.quality} on {signal_date}")
        _require(prefix_run.snapshots and prefix_run.snapshots[-1].signal_date == signal_date, f"prefix crosscheck did not reach {signal_date}")
        observed = prefix_run.snapshots[-1]
        expected = context.indicators_by_date[signal_date]
        for name in _SIGNAL_INDICATORS:
            left = observed.values.get(name)
            right = expected.values.get(name)
            if left is None or right is None:
                _require(left is right, f"prefix indicator availability mismatch for {name} on {signal_date}")
            else:
                _require(abs(float(left) - float(right)) <= 1e-12, f"prefix indicator mismatch for {name} on {signal_date}")
        _require(observed.ready == expected.ready and observed.quality == expected.quality, f"prefix indicator status mismatch on {signal_date}")
        checked.append(signal_date)
    return tuple(checked)


def _previous_session(calendar: TradingCalendar, session: str, context_start: str) -> str:
    sessions = calendar.sessions(context_start, session)
    _require(len(sessions) >= 2 and sessions[-1] == session, f"no previous session before {session}")
    return sessions[-2]


def _safe_return(left: float, right: float, field_name: str) -> float:
    _require(left > 0.0 and right > 0.0, f"{field_name} prices must be positive")
    return right / left - 1.0


def _drawdown(equity: Sequence[float]) -> tuple[float, int]:
    peak = 0.0
    max_drawdown = 0.0
    current_duration = 0
    max_duration = 0
    for value in equity:
        peak = max(peak, value)
        drawdown = value / peak - 1.0 if peak else 0.0
        if drawdown < 0.0:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown, max_duration


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = _mean(left)
    right_mean = _mean(right)
    left_dev = [value - left_mean for value in left]
    right_dev = [value - right_mean for value in right]
    denominator = math.sqrt(sum(value * value for value in left_dev) * sum(value * value for value in right_dev))
    return None if denominator == 0.0 else sum(a * b for a, b in zip(left_dev, right_dev)) / denominator


def _metrics(rows: Sequence[Mapping[str, Any]], *, return_key: str, equity_key: str, start_equity: float) -> dict[str, Any]:
    _require(rows, "cannot calculate metrics without equity rows")
    returns = [float(row[return_key]) for row in rows]
    equities = [float(row[equity_key]) for row in rows]
    first_date = str(rows[0]["period_start"])
    last_date = str(rows[-1]["period_end"])
    elapsed_days = max(1, (date.fromisoformat(last_date) - date.fromisoformat(first_date)).days)
    total_return = equities[-1] / start_equity - 1.0
    cagr = (equities[-1] / start_equity) ** (365.25 / elapsed_days) - 1.0 if equities[-1] > 0.0 else -1.0
    daily_std = _sample_std(returns)
    sharpe = _mean(returns) / daily_std * math.sqrt(252.0) if daily_std > 0.0 else None
    downside = [min(value, 0.0) for value in returns]
    downside_std = _sample_std(downside)
    sortino = _mean(returns) / downside_std * math.sqrt(252.0) if downside_std > 0.0 else None
    max_dd, max_dd_days = _drawdown(equities)
    return {
        "first_period_start": first_date,
        "last_period_end": last_date,
        "periods": len(rows),
        "elapsed_days": elapsed_days,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "max_drawdown_periods": max_dd_days,
        "sharpe_zero_rf": sharpe,
        "sortino_zero_rf": sortino,
        "daily_win_rate": sum(value > 0.0 for value in returns) / len(returns),
        "average_daily_return": _mean(returns),
        "daily_volatility": daily_std * math.sqrt(252.0),
        "final_equity": equities[-1],
    }


def _annual_metrics(rows: Sequence[Mapping[str, Any]], *, return_key: str, equity_key: str, label: str) -> list[dict[str, Any]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(date.fromisoformat(str(row["period_end"])).year, []).append(row)
    result: list[dict[str, Any]] = []
    for year, year_rows in sorted(grouped.items()):
        returns = [float(row[return_key]) for row in year_rows]
        equity = 1.0
        curve: list[float] = []
        for value in returns:
            equity *= 1.0 + value
            curve.append(equity)
        first = date.fromisoformat(str(year_rows[0]["period_start"]))
        last = date.fromisoformat(str(year_rows[-1]["period_end"]))
        days = max(1, (last - first).days)
        period_return = equity - 1.0
        annualized = (equity ** (365.25 / days) - 1.0) if equity > 0.0 else -1.0
        max_dd, duration = _drawdown(curve)
        result.append(
            {
                "series": label,
                "year": year,
                "periods": len(year_rows),
                "period_start": first.isoformat(),
                "period_end": last.isoformat(),
                "partial_year": first.month != 1 or first.day != 1 or last.month != 12 or last.day != 31,
                "return": period_return,
                "annualized_return": annualized,
                "max_drawdown": max_dd,
                "max_drawdown_periods": duration,
            }
        )
    return result


def _simulate(
    dataset: CausalMarketDataset,
    config: ReplayConfig,
    signals_by_execution: Mapping[str, Mapping[str, Any]],
    calendar: TradingCalendar,
) -> tuple[Mapping[float, tuple[Mapping[str, Any], ...]], tuple[Mapping[str, Any], ...], dict[str, Any], list[dict[str, Any]]]:
    dates = dataset.performance_dates(calendar, start_date=config.start_date)
    _require(len(dates) >= 2, "fewer than two common execution sessions are available")
    periods = list(zip(dates, dates[1:]))
    previous_weights = {symbol: 0.0 for symbol in _EXECUTION_ASSETS}
    equity = {float(cost): config.initial_capital for cost in config.cost_bps}
    qqq_equity = config.initial_capital
    equity_rows: dict[float, list[Mapping[str, Any]]] = {float(cost): [] for cost in config.cost_bps}
    transactions: list[Mapping[str, Any]] = []
    for period_start, period_end in periods:
        target = signals_by_execution.get(period_start)
        _require(target is not None, f"missing signal target for execution date {period_start}")
        raw_weights = dict(target["target_weights"])
        current_weights = {symbol: float(raw_weights.get(symbol, 0.0)) for symbol in _EXECUTION_ASSETS}
        turnover = sum(abs(current_weights[symbol] - previous_weights[symbol]) for symbol in _EXECUTION_ASSETS)
        price_returns = {
            symbol: _safe_return(float(dataset.prices[symbol][period_start]), float(dataset.prices[symbol][period_end]), symbol)
            for symbol in ("QQQ", "BIL")
        }
        _require(period_start in dataset.vxx and period_end in dataset.vxx, f"VXX price missing in {period_start}->{period_end}")
        price_returns["VXX"] = _safe_return(float(dataset.vxx[period_start]), float(dataset.vxx[period_end]), "VXX")
        gross_return = sum(current_weights[symbol] * price_returns[symbol] for symbol in _EXECUTION_ASSETS)
        qqq_return = price_returns["QQQ"]
        if turnover > 1e-12:
            transactions.append(
                {
                    "execution_date": period_start,
                    "signal_date": target["signal_date"],
                    "state": target["state"],
                    "from_weights": dict(previous_weights),
                    "to_weights": dict(current_weights),
                    "turnover": turnover,
                    "reason_codes": list(target["reason_codes"]),
                }
            )
        for cost in config.cost_bps:
            cost_fraction = turnover * float(cost) / 10_000.0
            net_return = gross_return - cost_fraction
            equity[float(cost)] *= 1.0 + net_return
            equity_rows[float(cost)].append(
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "signal_date": target["signal_date"],
                    "execution_date": period_start,
                    "state": target["state"],
                    "temperature": target["temperature"],
                    "gross_return": gross_return,
                    "net_return": net_return,
                    "qqq_return": qqq_return,
                    "turnover": turnover,
                    "cost_bps": float(cost),
                    "cost_drag": cost_fraction,
                    "QQQ_weight": current_weights["QQQ"],
                    "BIL_weight": current_weights["BIL"],
                    "VXX_weight": current_weights["VXX"],
                    "strategy_equity": equity[float(cost)],
                    "QQQ_equity": qqq_equity * (1.0 + qqq_return),
                    "data_version": dataset.data_version,
                }
            )
        qqq_equity *= 1.0 + qqq_return
        # The qqq_equity value in rows above is corrected below so every row
        # carries the post-period benchmark value, not the pre-period value.
        for cost in config.cost_bps:
            equity_rows[float(cost)][-1] = dict(equity_rows[float(cost)][-1], QQQ_equity=qqq_equity)
        previous_weights = current_weights
    benchmark_rows = equity_rows[float(config.cost_bps[0])]
    benchmark = _metrics(benchmark_rows, return_key="qqq_return", equity_key="QQQ_equity", start_equity=config.initial_capital)
    series_summary: dict[str, Any] = {"benchmark_QQQ": benchmark}
    for cost in config.cost_bps:
        rows = equity_rows[float(cost)]
        metrics = _metrics(rows, return_key="net_return", equity_key="strategy_equity", start_equity=config.initial_capital)
        metrics["correlation_to_QQQ"] = _correlation(
            [float(row["net_return"]) for row in rows],
            [float(row["qqq_return"]) for row in rows],
        )
        metrics["total_turnover"] = sum(float(row["turnover"]) for row in rows)
        metrics["total_cost_drag"] = sum(float(row["cost_drag"]) for row in rows)
        metrics["annual"] = _annual_metrics(rows, return_key="net_return", equity_key="strategy_equity", label=f"strategy_{cost:g}bps")
        series_summary[f"strategy_{cost:g}bps"] = metrics
    series_summary["benchmark_QQQ"]["annual"] = _annual_metrics(benchmark_rows, return_key="qqq_return", equity_key="QQQ_equity", label="QQQ")
    checks = {
        "execution_price_coverage": True,
        "missing_vxx_policy": "fail_closed",
        "benchmark_uses_same_periods": True,
        "cost_scenarios": list(config.cost_bps),
        "period_count": len(periods),
    }
    return {cost: tuple(rows) for cost, rows in equity_rows.items()}, tuple(transactions), series_summary, checks


def run_walk_forward(config: ReplayConfig) -> ReplayRun:
    """Run the causal signal replay and the next-session return simulation."""

    dataset = load_dataset(config)
    calendar = _replay_calendar(config)
    signal_dates = dataset.signal_dates(calendar)
    requested = tuple(day for day in signal_dates if day >= config.start_date and (config.end_date is None or day <= config.end_date))
    _require(requested, "no requested signal sessions are available")
    context_date = _previous_session(calendar, requested[0], dataset.signal_context_start)
    context = _build_chain_context(dataset, calendar=calendar)
    crosscheck_dates = tuple(dict.fromkeys((requested[0], requested[len(requested) // 2], requested[-1])))
    prefix_crosscheck_dates = _prefix_indicator_crosscheck(dataset, context, signal_dates=crosscheck_dates, calendar=calendar)
    bootstrap, bootstrap_audit = _run_signal(dataset, context, signal_date=context_date, calendar=calendar)
    signals: list[Mapping[str, Any]] = []
    audits: list[Mapping[str, Any]] = [bootstrap_audit]
    for signal_date in requested:
        record, audit = _run_signal(dataset, context, signal_date=signal_date, calendar=calendar)
        signals.append(record)
        audits.append(audit)
    signals_by_execution = {str(bootstrap["execution_date"]): bootstrap}
    signals_by_execution.update({str(item["execution_date"]): item for item in signals})
    equity_by_cost, transactions, summary, simulation_checks = _simulate(dataset, config, signals_by_execution, calendar)
    all_weights_ok = all(abs(float(item["target_weights"].get("QQQ", 0.0)) + float(item["target_weights"].get("BIL", 0.0)) + float(item["target_weights"].get("VXX", 0.0)) - 1.0) <= 1e-8 for item in (bootstrap, *signals))
    checks = {
        "replay_version": REPLAY_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "prefix_request_end_equals_signal": all(item["request_end_dates"] == [item["signal_date"]] * len(item["request_end_dates"]) for item in audits),
        "no_future_bar_in_prefix": all(not item["future_input_dates"] and item["max_visible_bar_date"] <= item["signal_date"] for item in audits),
        "execution_date_is_next_session": all(bool(item["execution_date_is_next_session"]) for item in audits),
        "target_weight_sum": all_weights_ok,
        "prefix_indicator_crosscheck": prefix_crosscheck_dates == crosscheck_dates,
        "prefix_indicator_crosscheck_dates": list(prefix_crosscheck_dates),
        "daily_signal_count": len(signals),
        "context_start": dataset.signal_context_start,
        "requested_signal_start": requested[0],
        "requested_signal_end": requested[-1],
        "bootstrap_signal_date": context_date,
        "audit_hash": _sha256_json(audits),
        **simulation_checks,
    }
    _require(all(bool(value) for key, value in checks.items() if key in {"prefix_request_end_equals_signal", "no_future_bar_in_prefix", "execution_date_is_next_session", "target_weight_sum", "prefix_indicator_crosscheck", "execution_price_coverage"}), "causal replay checks failed")
    manifest = {
        "schema": "qqq-v12.2-causal-walk-forward-manifest/v1",
        "replay_version": REPLAY_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "strategy_definition_note": "No independent v12.2 strategy contract exists in this repository; v12.2 is the causal audit/replay wrapper and v10 remains the frozen strategy source of truth.",
        "strategy_contract_hash": context.registry.strategy_config_hash(STRATEGY_VERSION),
        "config": config.as_dict(),
        "config_hash": _sha256_json(config.as_dict()),
        "data_version": dataset.data_version,
        "signal_context_start": dataset.signal_context_start,
        "signal_start": requested[0],
        "signal_end": requested[-1],
        "performance_start": min(dataset.performance_dates(calendar, start_date=config.start_date)),
        "performance_end": max(dataset.performance_dates(calendar, start_date=config.start_date)),
        "source_manifest": list(dataset.source_manifest),
        "prefix_calculation_mode": "chronological_causal_M04_stream_with_daily_raw_prefixes_and_exact_spot_checks",
        "ohlc_signal_limitations": [
            "QQQ and index M02 bars are close-only adapters with open/high/low repeated from close.",
            "The frozen M04 catalogue consumes close-derived values only; no high/low-derived signal is claimed.",
            "VXX is an independently supplied execution-price series and is never replaced by VIX index data.",
        ],
        "checks": checks,
        "summary": summary,
    }
    return ReplayRun(config, dataset, tuple(signals), bootstrap, equity_by_cost, transactions, {"manifest": manifest, **summary}, checks)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return _json_text(value)
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _report(run: ReplayRun) -> str:
    manifest = dict(run.summary["manifest"])
    lines = [
        "# v12.2 因果 Walk-Forward 回放报告",
        "",
        "> 本报告是历史审计/回放结果，不是未来收益承诺，也不是实盘授权。",
        "",
        "## 结论先行",
        "",
        f"- 回放标签：`{REPLAY_VERSION}`。实际策略版本：`{STRATEGY_VERSION}`。",
        "- 当前仓库没有独立的 v12.2 策略合同，因此本版本没有重新发明阈值或权重；所有指标、状态机和候选仓位均由冻结 v10 合同及现有 M02–M07 服务产生。",
        f"- 信号区间：`{manifest['signal_start']}` 至 `{manifest['signal_end']}`；执行净值区间：`{manifest['performance_start']}` 至 `{manifest['performance_end']}`。",
        f"- 逐日信号数：{run.checks['daily_signal_count']}；预热/状态上下文从 `{manifest['signal_context_start']}` 开始；首个请求窗口外的上下文信号为 `{run.bootstrap_signal['signal_date']}`。",
        "- 每个回放日都重新构建到当日的 raw 输入前缀；M04 先按时间顺序运行其已声明的因果计算，再用三个完整前缀做逐值复算。未来数据不会进入当日指标、状态或目标权重。",
        "",
        "## 成本压力测试",
        "",
        "| 系列 | CAGR | 最大回撤 | Sharpe(0利率) | 日胜率 | 与QQQ日相关性 | 最终净值 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cost in run.config.cost_bps:
        metrics = run.summary[f"strategy_{cost:g}bps"]
        lines.append(
            f"| 策略 {cost:g} bps | {_pct(metrics['cagr'])} | {_pct(metrics['max_drawdown'])} | {metrics['sharpe_zero_rf'] if metrics['sharpe_zero_rf'] is not None else '—'} | {_pct(metrics['daily_win_rate'])} | {metrics['correlation_to_QQQ'] if metrics['correlation_to_QQQ'] is not None else '—'} | {metrics['final_equity']:.2f} |"
        )
    benchmark = run.summary["benchmark_QQQ"]
    lines.append(f"| QQQ 基准 | {_pct(benchmark['cagr'])} | {_pct(benchmark['max_drawdown'])} | {benchmark['sharpe_zero_rf'] if benchmark['sharpe_zero_rf'] is not None else '—'} | — | 1.0000 | {benchmark['final_equity']:.2f} |")
    lines.extend(
        [
            "",
            "## 每年结果",
            "",
            "`annual_metrics.csv` 同时保存策略和 QQQ；2026 年如果未覆盖全年，会明确标记 `partial_year=true`，不把阶段收益冒充完整年度收益。",
            "",
        ]
    )
    for cost in run.config.cost_bps:
        lines.append(f"### 策略 {cost:g} bps")
        lines.append("")
        lines.append("| 年份 | 区间收益 | 年化表示 | 年内最大回撤 | 部分年度 |")
        lines.append("|---:|---:|---:|---:|:---:|")
        for row in run.summary[f"strategy_{cost:g}bps"]["annual"]:
            lines.append(f"| {row['year']} | {_pct(row['return'])} | {_pct(row['annualized_return'])} | {_pct(row['max_drawdown'])} | {'是' if row['partial_year'] else '否'} |")
        lines.append("")
    lines.append("### QQQ 基准")
    lines.append("")
    lines.append("| 年份 | 区间收益 | 年化表示 | 年内最大回撤 | 部分年度 |")
    lines.append("|---:|---:|---:|---:|:---:|")
    for row in benchmark["annual"]:
        lines.append(f"| {row['year']} | {_pct(row['return'])} | {_pct(row['annualized_return'])} | {_pct(row['max_drawdown'])} | {'是' if row['partial_year'] else '否'} |")
    lines.extend(
        [
            "",
            "## 数据口径与限制",
            "",
            "- QQQ/BIL 信号和执行回报使用本地 `prices_adj_close.csv` 的 adjusted close；它本身没有完整 OHLC，因此 M02 只做 close-only 兼容封装，不能据此宣称完成高低开盘价审计。",
            "- VIX/VIX3M 使用本地 CBOE 日线缓存，VIX/VIX3M 只作为信号，绝不被当成可持有资产。",
            "- VXX 使用单独的本地缓存 OHLCV close；缺失时本回放 fail-closed，不用 VIX、SVXY 或 BIL 静默替换。VXX 的价格口径不是与 QQQ/BIL 完全相同的总回报口径，结果必须按“混合价格口径审计”解读。",
            "- 回报模拟是“信号日收盘 → 下一交易日执行 → 执行日到下一交易日 close-to-close”代理；没有盘中成交、税费、分红现金流、滑点和整数份额。",
            "- 现有 v10 仍是 research candidate / contract_only，不代表已经获得产品默认或真实交易资格。",
            "",
            "## 可复现文件",
            "",
            "- `manifest.json`：数据文件 hash、策略合同 hash、时点、路径和全部验收检查。",
            "- `signals.csv`：逐日温度、状态、指标、证据、目标权重和 prefix hash。",
            "- `weights.csv`：下一交易日执行的目标权重。",
            "- `equity_curve_5bps.csv`、`equity_curve_10bps.csv`、`equity_curve_25bps.csv`：逐周期净值和 QQQ 同期基准。",
            "- `transactions.csv`：目标权重变化和换手。",
            "- `annual_metrics.csv`：逐年/阶段收益和最大回撤。",
            "- `checks.json`：无前视、执行时点、权重和数据覆盖检查。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_artifacts(run: ReplayRun, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(run.summary["manifest"])
    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "checks.json", dict(run.checks))
    signal_fields = [
        "replay_version", "strategy_version", "strategy_config_hash", "signal_date", "execution_date", "as_of", "run_id", "data_version",
        "data_quality", "normalization_quality", "indicator_ready", "state", "previous_state", "transition", "regime_confirmed", "formal_publication_status",
        "candidate_only", "execution_eligible", "temperature", "trend", "signal_agreement", "reason_codes", "regime_evidence",
        "indicator_values", "target_weights", "raw_snapshot_ids", "raw_manifest", "max_visible_bar_date", "prefix_hash",
    ]
    _write_csv(output_dir / "signals.csv", run.signals, signal_fields)
    weights_rows = [run.bootstrap_signal, *run.signals]
    _write_csv(output_dir / "weights.csv", weights_rows, ["signal_date", "execution_date", "state", "temperature", "data_quality", "target_weights", "reason_codes", "candidate_only", "execution_eligible", "prefix_hash"])
    transactions = list(run.transactions)
    _write_csv(output_dir / "transactions.csv", transactions, ["execution_date", "signal_date", "state", "from_weights", "to_weights", "turnover", "reason_codes"])
    equity_fields = [
        "period_start", "period_end", "signal_date", "execution_date", "state", "temperature", "gross_return", "net_return", "qqq_return",
        "turnover", "cost_bps", "cost_drag", "QQQ_weight", "BIL_weight", "VXX_weight", "strategy_equity", "QQQ_equity", "data_version",
    ]
    all_annual: list[Mapping[str, Any]] = []
    for cost in run.config.cost_bps:
        _write_csv(output_dir / f"equity_curve_{cost:g}bps.csv", run.equity_by_cost[float(cost)], equity_fields)
        all_annual.extend(run.summary[f"strategy_{cost:g}bps"]["annual"])
    benchmark_rows = run.equity_by_cost[float(run.config.cost_bps[0])]
    all_annual.extend(run.summary["benchmark_QQQ"]["annual"])
    _write_csv(output_dir / "annual_metrics.csv", all_annual, ["series", "year", "periods", "period_start", "period_end", "partial_year", "return", "annualized_return", "max_drawdown", "max_drawdown_periods"])
    (output_dir / "REPORT.md").write_text(_report(run), encoding="utf-8")
    return output_dir


def load_config(path: Path) -> ReplayConfig:
    _require(path.exists(), f"config does not exist: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(raw, Mapping), "config root must be an object")
    return ReplayConfig.from_mapping(raw)


def _default_output(config: ReplayConfig, run: ReplayRun) -> Path:
    if config.output_root:
        root = _resolve_path(config.output_root)
    else:
        root = _PROJECT_ROOT.parent / "_quant_artifacts" / _PROJECT_ROOT.name
    assert root is not None
    digest = str(run.dataset.data_version).replace("local-causal-", "")
    return root / f"v12.2-causal-walk-forward-{config.start_date}-{run.dataset.signal_end}-{digest}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local causal v12.2 Walk-Forward replay.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--prices-csv", type=str)
    parser.add_argument("--vix-csv", type=str)
    parser.add_argument("--vxx-csv", type=str)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    overrides: dict[str, Any] = {}
    if args.prices_csv:
        overrides["prices_adj_close_csv"] = args.prices_csv
    if args.vix_csv:
        overrides["vix_indices_csv"] = args.vix_csv
    if args.vxx_csv:
        overrides["vxx_ohlcv_csv"] = args.vxx_csv
    if args.start_date:
        overrides["start_date"] = args.start_date
    if args.end_date:
        overrides["end_date"] = args.end_date
    if overrides:
        config = ReplayConfig.from_mapping({**config.as_dict(), **overrides})
    run = run_walk_forward(config)
    output_dir = args.output_dir or _default_output(config, run)
    write_artifacts(run, output_dir)
    print(f"v12.2 causal walk-forward: {output_dir}")
    print(f"signals={len(run.signals)} performance_periods={run.checks['period_count']} data_version={run.dataset.data_version}")
    for cost in config.cost_bps:
        metrics = run.summary[f"strategy_{cost:g}bps"]
        print(f"strategy_{cost:g}bps CAGR={metrics['cagr']:.6f} MDD={metrics['max_drawdown']:.6f} final_equity={metrics['final_equity']:.2f}")
    benchmark = run.summary["benchmark_QQQ"]
    print(f"QQQ CAGR={benchmark['cagr']:.6f} MDD={benchmark['max_drawdown']:.6f} final_equity={benchmark['final_equity']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CausalMarketDataset",
    "ReplayConfig",
    "ReplayRun",
    "REPLAY_VERSION",
    "STRATEGY_VERSION",
    "WalkForwardError",
    "build_prefix_snapshots",
    "load_config",
    "load_dataset",
    "run_walk_forward",
    "write_artifacts",
]
