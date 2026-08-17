"""Versioned, replayable indicator snapshots for the M04 data boundary.

This module consumes only M03 :class:`NormalizedBar` objects.  It deliberately
does not fetch data, infer missing prices, choose a regime, or calculate
portfolio weights.  Every value is tied to a signal date and carries enough
input metadata to audit the no-lookahead boundary.
"""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .market_data import (
    DataContractError,
    _as_iso_date,
    _as_utc_timestamp,
    _canonical_json,
)
from .normalization import (
    QUALITY_STATUSES,
    NormalizedBar,
    NormalizationResult,
    TradingCalendar,
)


INDICATOR_VERSION = "m04-indicators/v1"
"""Immutable catalogue version; changing a definition requires a new version."""

INDICATOR_NAMES = (
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
_INDICATOR_NAME_SET = frozenset(INDICATOR_NAMES)
_QUALITY_PRIORITY = {status: index for index, status in enumerate(QUALITY_STATUSES)}
_QUALITY_SET = frozenset(QUALITY_STATUSES)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataContractError(message)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _quality_max(*statuses: str) -> str:
    present = [status for status in statuses if status]
    return max(present, key=lambda status: _QUALITY_PRIORITY[status]) if present else "OK"


def _normalise_names(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    _require(isinstance(values, Sequence) and not isinstance(values, (str, bytes)), f"{field_name} must be a sequence")
    names = tuple(str(value).strip() for value in values)
    _require(all(names), f"{field_name} cannot contain empty values")
    _require(len(set(names)) == len(names), f"{field_name} cannot contain duplicates")
    return names


@dataclass(frozen=True)
class IndicatorDefinition:
    """Machine-readable definition of one permitted M04 indicator."""

    name: str
    symbols: tuple[str, ...]
    window: int | None
    price_field: str
    formula: str
    warmup_rule: str
    version: str = INDICATOR_VERSION

    def __post_init__(self) -> None:
        _require(isinstance(self.name, str) and self.name.strip(), "indicator definition name must be non-empty")
        symbols = tuple(symbol.strip().upper() for symbol in self.symbols)
        _require(symbols and all(symbols), "indicator definition symbols must be non-empty")
        _require(len(set(symbols)) == len(symbols), "indicator definition symbols cannot contain duplicates")
        if self.window is not None:
            _require(isinstance(self.window, int) and not isinstance(self.window, bool) and self.window > 0, "indicator definition window must be a positive integer")
        for field_name in ("price_field", "formula", "warmup_rule", "version"):
            _require(isinstance(getattr(self, field_name), str) and getattr(self, field_name).strip(), f"indicator definition {field_name} must be non-empty")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "price_field", self.price_field.strip())
        object.__setattr__(self, "formula", self.formula.strip())
        object.__setattr__(self, "warmup_rule", self.warmup_rule.strip())
        object.__setattr__(self, "version", self.version.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbols": list(self.symbols),
            "window": self.window,
            "price_field": self.price_field,
            "formula": self.formula,
            "warmup_rule": self.warmup_rule,
            "version": self.version,
        }


INDICATOR_DEFINITIONS = (
    IndicatorDefinition(
        "qqq_return_5d",
        ("QQQ",),
        5,
        "close",
        "close[t] / close[t-5] - 1",
        "requires 6 QQQ closes; signal date is the last close",
    ),
    IndicatorDefinition(
        "qqq_return_10d",
        ("QQQ",),
        10,
        "close",
        "close[t] / close[t-10] - 1",
        "requires 11 QQQ closes; signal date is the last close",
    ),
    IndicatorDefinition(
        "qqq_return_20d",
        ("QQQ",),
        20,
        "close",
        "close[t] / close[t-20] - 1",
        "requires 21 QQQ closes; signal date is the last close",
    ),
    IndicatorDefinition(
        "qqq_ema10",
        ("QQQ",),
        10,
        "close",
        "EMA seed=mean(first 10 closes); EMA[t]=2/(10+1)*close[t]+9/(10+1)*EMA[t-1]",
        "requires 10 QQQ closes; the seed is reported on the tenth close",
    ),
    IndicatorDefinition(
        "qqq_sma150",
        ("QQQ",),
        150,
        "close",
        "mean(close[t-149:t+1])",
        "requires 150 QQQ closes; signal date is the last close",
    ),
    IndicatorDefinition(
        "qqq_momentum126",
        ("QQQ",),
        126,
        "close",
        "close[t] / close[t-126] - 1",
        "requires 127 QQQ closes; signal date is the last close",
    ),
    IndicatorDefinition(
        "qqq_rv20",
        ("QQQ",),
        20,
        "close",
        "sample_std(ddof=1, close-to-close returns over 20 returns) * sqrt(252)",
        "requires 21 QQQ closes; returns end on the signal date",
    ),
    IndicatorDefinition(
        "vix",
        ("VIX",),
        None,
        "close",
        "same-date VIX index close",
        "requires a same-date VIX observation; no forward fill",
    ),
    IndicatorDefinition(
        "vix3m",
        ("VIX3M",),
        None,
        "close",
        "same-date VIX3M index close",
        "requires a same-date VIX3M observation; no forward fill",
    ),
    IndicatorDefinition(
        "vix_term_ratio",
        ("VIX", "VIX3M"),
        None,
        "close",
        "VIX[t] / VIX3M[t]",
        "requires same-date VIX and positive VIX3M; no forward fill",
    ),
)
_DEFINITION_BY_NAME = {definition.name: definition for definition in INDICATOR_DEFINITIONS}
_require(tuple(_DEFINITION_BY_NAME) == INDICATOR_NAMES, "M04 definition catalogue order is invalid")


def _parse_as_of_local_date(as_of: str, calendar: TradingCalendar) -> date:
    try:
        zone = ZoneInfo(calendar.timezone)
    except ZoneInfoNotFoundError as exc:
        raise DataContractError(f"unknown calendar timezone: {calendar.timezone}") from exc
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(zone).date()


def _finite(value: float, field_name: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{field_name} must be numeric")
    number = float(value)
    _require(math.isfinite(number), f"{field_name} must be finite")
    return number


def _mean(values: Sequence[float]) -> float:
    _require(values, "mean requires at least one value")
    return sum(values) / len(values)


def _sample_std(values: Sequence[float]) -> float:
    _require(len(values) > 1, "sample standard deviation requires at least two values")
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _window_dates(dates: tuple[str, ...], end_index: int, lookback: int) -> tuple[str, ...]:
    return dates[max(0, end_index - lookback) : end_index + 1]


def _window_quality(bars: tuple[NormalizedBar, ...], start: int, end: int) -> str:
    return _quality_max(*(bars[index].quality for index in range(start, end + 1)))


@dataclass(frozen=True)
class IndicatorSnapshot:
    """One signal-date snapshot with explicit warmup and provenance."""

    signal_date: str
    as_of: str
    calendar_id: str
    indicator_version: str
    quality: str
    ready: bool
    values: Mapping[str, float | None]
    warmup_indicators: tuple[str, ...] = ()
    input_bar_dates: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    price_basis_by_symbol: Mapping[str, str] = field(default_factory=dict)
    timezone_by_symbol: Mapping[str, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _as_iso_date(self.signal_date, "indicator.signal_date")
        normalized_as_of = _as_utc_timestamp(self.as_of, "indicator.as_of")
        _require(isinstance(self.calendar_id, str) and self.calendar_id.strip(), "indicator.calendar_id must be non-empty")
        _require(self.indicator_version == INDICATOR_VERSION, "unsupported M04 indicator version")
        _require(self.quality in _QUALITY_SET, f"unsupported indicator quality: {self.quality}")
        _require(isinstance(self.ready, bool), "indicator.ready must be boolean")
        _require(isinstance(self.values, Mapping), "indicator.values must be an object")
        values = dict(self.values)
        _require(set(values) == _INDICATOR_NAME_SET, "indicator.values must contain exactly the M04 catalogue")
        for name, value in values.items():
            if value is not None:
                values[name] = _finite(value, f"indicator.values.{name}")
        warmup = _normalise_names(self.warmup_indicators, "indicator.warmup_indicators")
        _require(set(warmup) <= _INDICATOR_NAME_SET, "indicator.warmup_indicators contains an unknown indicator")
        input_dates: dict[str, tuple[str, ...]] = {}
        for symbol, dates in self.input_bar_dates.items():
            normalized_symbol = str(symbol).strip().upper()
            _require(normalized_symbol in {"QQQ", "VIX", "VIX3M"}, "indicator input_bar_dates contains an unsupported symbol")
            normalized_dates = tuple(_as_iso_date(value, f"indicator.input_bar_dates.{normalized_symbol}") for value in dates)
            _require(normalized_dates == tuple(sorted(set(normalized_dates))), f"indicator.input_bar_dates.{normalized_symbol} must be sorted and unique")
            _require(all(value <= self.signal_date for value in normalized_dates), "indicator input contains a future bar")
            input_dates[normalized_symbol] = normalized_dates
        basis = {str(symbol).strip().upper(): str(value).strip() for symbol, value in self.price_basis_by_symbol.items()}
        zones = {str(symbol).strip().upper(): str(value).strip() for symbol, value in self.timezone_by_symbol.items()}
        _require(set(basis) <= {"QQQ", "VIX", "VIX3M"}, "indicator price_basis_by_symbol contains an unsupported symbol")
        _require(set(zones) <= {"QQQ", "VIX", "VIX3M"}, "indicator timezone_by_symbol contains an unsupported symbol")
        _require(all(basis.values()), "indicator price basis values must be non-empty")
        _require(all(zones.values()), "indicator timezone values must be non-empty")
        reasons = _normalise_names(self.reasons, "indicator.reasons")
        expected_ready = self.quality == "OK" and not warmup and all(value is not None for value in values.values())
        _require(self.ready == expected_ready, "indicator.ready is inconsistent with quality, warmup, or values")
        object.__setattr__(self, "as_of", normalized_as_of)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "warmup_indicators", warmup)
        object.__setattr__(self, "input_bar_dates", input_dates)
        object.__setattr__(self, "price_basis_by_symbol", basis)
        object.__setattr__(self, "timezone_by_symbol", zones)
        object.__setattr__(self, "reasons", reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-indicator-snapshot/v1",
            "signal_date": self.signal_date,
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "indicator_version": self.indicator_version,
            "quality": self.quality,
            "ready": self.ready,
            "values": copy.deepcopy(dict(self.values)),
            "warmup_indicators": list(self.warmup_indicators),
            "input_bar_dates": {key: list(value) for key, value in sorted(self.input_bar_dates.items())},
            "price_basis_by_symbol": dict(sorted(self.price_basis_by_symbol.items())),
            "timezone_by_symbol": dict(sorted(self.timezone_by_symbol.items())),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class IndicatorRun:
    """A deterministic collection of indicator snapshots for one input result."""

    as_of: str
    calendar_id: str
    source_normalization_version: str
    indicator_version: str
    input_quality: str
    quality: str
    definitions: tuple[IndicatorDefinition, ...]
    snapshots: tuple[IndicatorSnapshot, ...]
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_as_of = _as_utc_timestamp(self.as_of, "indicator_run.as_of")
        _require(self.input_quality in _QUALITY_SET, f"unsupported indicator input quality: {self.input_quality}")
        _require(self.quality in _QUALITY_SET, f"unsupported indicator run quality: {self.quality}")
        _require(self.indicator_version == INDICATOR_VERSION, "unsupported M04 indicator version")
        _require(tuple(definition.name for definition in self.definitions) == INDICATOR_NAMES, "indicator run definitions are incomplete or out of order")
        _require(all(definition.version == self.indicator_version for definition in self.definitions), "indicator definition version mismatch")
        dates = tuple(snapshot.signal_date for snapshot in self.snapshots)
        _require(dates == tuple(sorted(set(dates))), "indicator snapshots must be sorted and unique")
        _require(all(snapshot.calendar_id == self.calendar_id for snapshot in self.snapshots), "indicator snapshot calendar mismatch")
        _require(all(snapshot.indicator_version == self.indicator_version for snapshot in self.snapshots), "indicator snapshot version mismatch")
        reasons = _normalise_names(self.reasons, "indicator_run.reasons")
        object.__setattr__(self, "as_of", normalized_as_of)
        object.__setattr__(self, "reasons", reasons)

    @property
    def ready(self) -> bool:
        return bool(self.snapshots and self.snapshots[-1].ready)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-indicator-run/v1",
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "source_normalization_version": self.source_normalization_version,
            "indicator_version": self.indicator_version,
            "input_quality": self.input_quality,
            "quality": self.quality,
            "ready": self.ready,
            "definitions": [definition.as_dict() for definition in self.definitions],
            "snapshots": [snapshot.as_dict() for snapshot in self.snapshots],
            "reasons": list(self.reasons),
        }

    @property
    def content_hash(self) -> str:
        return _sha256_text(_canonical_json(self.as_dict()))

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.as_dict()).encode("utf-8")


def _merge_normalization_inputs(
    inputs: NormalizationResult | Sequence[NormalizationResult],
) -> NormalizationResult:
    """Join separately normalized price-basis groups without deduplication."""

    if isinstance(inputs, NormalizationResult):
        return inputs
    _require(isinstance(inputs, Sequence) and not isinstance(inputs, (str, bytes)), "indicator inputs must be a NormalizationResult or a sequence of results")
    results = tuple(inputs)
    _require(results and all(isinstance(item, NormalizationResult) for item in results), "indicator inputs must contain NormalizationResult objects")
    reference = results[0]
    normalized_as_of = _as_utc_timestamp(reference.as_of, "result.as_of")
    merged_bars: list[NormalizedBar] = []
    merged_events = []
    qualities: list[str] = []
    for item in results:
        _require(_as_utc_timestamp(item.as_of, "result.as_of") == normalized_as_of, "indicator input results must share the same as_of")
        _require(item.calendar_id == reference.calendar_id, "indicator input results must share the same calendar")
        _require(item.normalization_version == reference.normalization_version, "indicator input results must share the same normalization version")
        merged_bars.extend(item.bars)
        merged_events.extend(item.quality_events)
        qualities.append(item.quality)
    return NormalizationResult(
        normalized_as_of,
        reference.calendar_id,
        reference.normalization_version,
        _quality_max(*qualities),
        tuple(merged_bars),
        tuple(sorted(merged_events, key=lambda event: _canonical_json(event.as_dict()))),
    )


def _validate_input_bars(result: NormalizationResult, calendar: TradingCalendar) -> dict[str, tuple[NormalizedBar, ...]]:
    as_of_dt = datetime.fromisoformat(_as_utc_timestamp(result.as_of, "result.as_of").replace("Z", "+00:00"))
    as_of_local = _parse_as_of_local_date(result.as_of, calendar)
    by_key: dict[tuple[str, str], NormalizedBar] = {}
    metadata: dict[str, tuple[str, str]] = {}
    for bar in result.bars:
        _require(isinstance(bar, NormalizedBar), "indicator input bars must be NormalizedBar objects")
        _require(bar.bar_date <= as_of_local.isoformat(), "indicator input contains a bar after result.as_of")
        for _, retrieved_at in bar.retrieved_at_by_source:
            retrieved_dt = datetime.fromisoformat(_as_utc_timestamp(retrieved_at, "normalized.retrieved_at").replace("Z", "+00:00"))
            _require(retrieved_dt <= as_of_dt, "indicator input contains retrieval metadata after result.as_of")
        key = (bar.symbol, bar.bar_date)
        _require(key not in by_key, f"duplicate normalized bar: {bar.symbol} {bar.bar_date}")
        by_key[key] = bar
        current_metadata = (bar.price_basis, bar.timezone)
        if bar.symbol in metadata:
            _require(metadata[bar.symbol] == current_metadata, f"inconsistent price metadata for {bar.symbol}")
        else:
            metadata[bar.symbol] = current_metadata
    result_by_symbol: dict[str, list[NormalizedBar]] = {}
    for bar in by_key.values():
        result_by_symbol.setdefault(bar.symbol, []).append(bar)
    return {symbol: tuple(sorted(bars, key=lambda item: item.bar_date)) for symbol, bars in result_by_symbol.items()}


def _append_reason(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def calculate_indicator_snapshots(
    result: NormalizationResult | Sequence[NormalizationResult],
    *,
    calendar: TradingCalendar | None = None,
    indicator_version: str = INDICATOR_VERSION,
) -> IndicatorRun:
    """Calculate the frozen first-batch indicators without lookahead.

    Separate M03 results may be supplied for ETF bars and index bars because
    the M02 request contract keeps those price bases separate.  They are joined
    only when their ``as_of``, calendar, and normalization version agree.
    The returned snapshots are signal-date observations.  Values are computed
    only from bars on or before their signal date; missing, warmup, stale, or
    non-OK inputs remain visible and never become zero or a forward-filled value.
    """

    _require(indicator_version == INDICATOR_VERSION, "unsupported M04 indicator version")
    result = _merge_normalization_inputs(result)
    selected_calendar = calendar or TradingCalendar()
    _require(result.calendar_id == selected_calendar.calendar_id, "indicator calendar does not match normalization result")
    bars_by_symbol = _validate_input_bars(result, selected_calendar)
    qqq_bars = bars_by_symbol.get("QQQ", ())
    if not qqq_bars:
        return IndicatorRun(
            result.as_of,
            result.calendar_id,
            result.normalization_version,
            indicator_version,
            result.quality,
            _quality_max(result.quality, "FAILED"),
            INDICATOR_DEFINITIONS,
            (),
            ("qqq_bars_missing",),
        )

    qqq_dates = tuple(bar.bar_date for bar in qqq_bars)
    expected_sessions = selected_calendar.sessions(qqq_dates[0], qqq_dates[-1])
    missing_sessions = tuple(session for session in expected_sessions if session not in set(qqq_dates))
    gap_start = missing_sessions[0] if missing_sessions else None
    qqq_closes = tuple(_finite(bar.close, "QQQ close") for bar in qqq_bars)
    vix_by_date = {bar.bar_date: bar for bar in bars_by_symbol.get("VIX", ())}
    vix3m_by_date = {bar.bar_date: bar for bar in bars_by_symbol.get("VIX3M", ())}
    metadata = {}
    for symbol in ("QQQ", "VIX", "VIX3M"):
        symbol_bars = bars_by_symbol.get(symbol, ())
        if symbol_bars:
            metadata[symbol] = (symbol_bars[-1].price_basis, symbol_bars[-1].timezone)

    snapshots: list[IndicatorSnapshot] = []
    for index, signal_date in enumerate(qqq_dates):
        values: dict[str, float | None] = {name: None for name in INDICATOR_NAMES}
        warmup: list[str] = []
        reasons: list[str] = []
        quality = result.quality
        if result.quality != "OK":
            _append_reason(reasons, f"normalization_quality:{result.quality}")

        input_dates = {
            "QQQ": _window_dates(qqq_dates, index, 126),
            "VIX": (signal_date,) if signal_date in vix_by_date else (),
            "VIX3M": (signal_date,) if signal_date in vix3m_by_date else (),
        }
        gap_applies = gap_start is not None and signal_date >= gap_start
        if gap_applies:
            quality = _quality_max(quality, "NEEDS_REVIEW")
            _append_reason(reasons, "qqq_session_gap")

        def qqq_value(name: str, minimum_index: int, start_index: int, value: float) -> None:
            nonlocal quality
            if index < minimum_index:
                warmup.append(name)
                return
            if gap_applies or _window_quality(qqq_bars, start_index, index) != "OK":
                quality = _quality_max(quality, _window_quality(qqq_bars, start_index, index), "NEEDS_REVIEW")
                _append_reason(reasons, "qqq_input_quality")
                return
            values[name] = _finite(value, name)

        qqq_value("qqq_return_5d", 5, index - 5, qqq_closes[index] / qqq_closes[index - 5] - 1.0)
        qqq_value("qqq_return_10d", 10, index - 10, qqq_closes[index] / qqq_closes[index - 10] - 1.0)
        qqq_value("qqq_return_20d", 20, index - 20, qqq_closes[index] / qqq_closes[index - 20] - 1.0)

        ema10_value: float | None = None
        if index >= 9:
            ema10_value = _mean(qqq_closes[:10])
            alpha = 2.0 / 11.0
            for ema_index in range(10, index + 1):
                ema10_value = alpha * qqq_closes[ema_index] + (1.0 - alpha) * ema10_value
        qqq_value("qqq_ema10", 9, 0, float(ema10_value) if ema10_value is not None else 0.0)

        qqq_value("qqq_sma150", 149, index - 149, _mean(qqq_closes[index - 149 : index + 1]) if index >= 149 else 0.0)
        qqq_value("qqq_momentum126", 126, index - 126, qqq_closes[index] / qqq_closes[index - 126] - 1.0)
        rv20_value: float | None = None
        if index >= 20:
            returns = tuple(qqq_closes[return_index] / qqq_closes[return_index - 1] - 1.0 for return_index in range(index - 19, index + 1))
            rv20_value = _sample_std(returns) * math.sqrt(252.0)
        qqq_value("qqq_rv20", 20, index - 20, float(rv20_value) if rv20_value is not None else 0.0)

        vix_bar = vix_by_date.get(signal_date)
        vix3m_bar = vix3m_by_date.get(signal_date)
        if vix_bar is None:
            quality = _quality_max(quality, "NEEDS_REVIEW")
            _append_reason(reasons, "vix_missing")
        elif vix_bar.quality != "OK":
            quality = _quality_max(quality, vix_bar.quality)
            _append_reason(reasons, "vix_input_quality")
        elif not gap_applies:
            values["vix"] = _finite(vix_bar.close, "vix")

        if vix3m_bar is None:
            quality = _quality_max(quality, "NEEDS_REVIEW")
            _append_reason(reasons, "vix3m_missing")
        elif vix3m_bar.quality != "OK":
            quality = _quality_max(quality, vix3m_bar.quality)
            _append_reason(reasons, "vix3m_input_quality")
        elif not gap_applies:
            values["vix3m"] = _finite(vix3m_bar.close, "vix3m")

        if values["vix"] is not None and values["vix3m"] is not None:
            if values["vix3m"] <= 0.0:
                quality = _quality_max(quality, "NEEDS_REVIEW")
                _append_reason(reasons, "vix3m_non_positive")
            else:
                values["vix_term_ratio"] = values["vix"] / values["vix3m"]
        else:
            _append_reason(reasons, "vix_term_ratio_unavailable")

        snapshot_ready = quality == "OK" and not warmup and all(value is not None for value in values.values())
        snapshot = IndicatorSnapshot(
            signal_date=signal_date,
            as_of=result.as_of,
            calendar_id=result.calendar_id,
            indicator_version=indicator_version,
            quality=quality,
            ready=snapshot_ready,
            values=values,
            warmup_indicators=tuple(warmup),
            input_bar_dates=input_dates,
            price_basis_by_symbol={symbol: basis for symbol, (basis, _) in metadata.items()},
            timezone_by_symbol={symbol: timezone for symbol, (_, timezone) in metadata.items()},
            reasons=tuple(reasons),
        )
        snapshots.append(snapshot)

    run_quality = _quality_max(result.quality, *(snapshot.quality for snapshot in snapshots))
    run_reasons: list[str] = []
    if result.quality != "OK":
        _append_reason(run_reasons, f"normalization_quality:{result.quality}")
    if missing_sessions:
        _append_reason(run_reasons, "qqq_session_gap")
    if not vix_by_date:
        _append_reason(run_reasons, "vix_missing")
    if not vix3m_by_date:
        _append_reason(run_reasons, "vix3m_missing")
    return IndicatorRun(
        result.as_of,
        result.calendar_id,
        result.normalization_version,
        indicator_version,
        result.quality,
        run_quality,
        INDICATOR_DEFINITIONS,
        tuple(snapshots),
        tuple(run_reasons),
    )


__all__ = [
    "INDICATOR_DEFINITIONS",
    "INDICATOR_NAMES",
    "INDICATOR_VERSION",
    "IndicatorDefinition",
    "IndicatorRun",
    "IndicatorSnapshot",
    "calculate_indicator_snapshots",
]
