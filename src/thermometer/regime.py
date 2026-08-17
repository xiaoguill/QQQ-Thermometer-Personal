"""Pure v10 regime state machine and historical replay for M05.

The state machine consumes an M04 indicator snapshot and the same-date
normalized QQQ bar.  It does not fetch data, read a database, calculate
weights, or place orders.  All thresholds are read from the frozen strategy
contract through :class:`RegimeConfig`; the implementation does not maintain a
second set of strategy parameters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from src.storage.indicators import IndicatorSnapshot
from src.storage.normalization import (
    QUALITY_STATUSES,
    NormalizedBar,
    TradingCalendar,
)

from .contracts import StrategyContractRegistry


REGIME_IMPLEMENTATION_VERSION = "m05-regime/v1"
STRATEGY_VERSION = "v10_preserve_shock_recovery"
ACTIVE_STATES = ("normal", "shock", "recovery")
ALL_STATES = ("warming", "normal", "shock", "recovery", "needs_review")
_QUALITY_SET = frozenset(QUALITY_STATUSES)
_RECOVERY_OPTIONS = frozenset(
    {
        "qqq_short_term_rebound",
        "qqq_above_ema10",
        "qqq_realized_volatility_20d_declining",
    }
)


class RegimeError(ValueError):
    """Raised when a regime input or replay violates the M05 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegimeError(message)


def _finite_number(value: Any, field_name: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{field_name} must be numeric",
    )
    result = float(value)
    _require(math.isfinite(result), f"{field_name} must be finite")
    return result


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field_name)


def _positive_int(value: Any, field_name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field_name} must be an integer")
    _require(value > 0, f"{field_name} must be positive")
    return int(value)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _unique_strings(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes)),
        f"{field_name} must be a sequence",
    )
    result = tuple(str(value).strip() for value in values)
    _require(all(result), f"{field_name} cannot contain empty values")
    _require(len(result) == len(set(result)), f"{field_name} cannot contain duplicates")
    return result


@dataclass(frozen=True)
class RegimeConfig:
    """Immutable executable view of the frozen v10 parameters."""

    strategy_version: str
    strategy_config_hash: str
    shock_qqq_return_window_days: int
    shock_qqq_return_max: float
    shock_vix_min: float
    shock_vix_term_ratio_min: float
    recovery_confirmation_required: int
    recovery_confirmation_options: tuple[str, ...]
    recovery_rebound_5d_min: float
    recovery_rebound_10d_min: float
    recovery_rv_comparison_days: int
    medium_gate_trend_window_days: int
    medium_gate_momentum_window_days: int
    medium_gate_confirmation_days: int
    shock_min_hold_days: int
    recovery_min_hold_days: int

    def __post_init__(self) -> None:
        _require(self.strategy_version == STRATEGY_VERSION, "M05 only supports the frozen v10 strategy")
        _require(isinstance(self.strategy_config_hash, str) and len(self.strategy_config_hash) == 64, "strategy_config_hash must be a SHA-256 string")
        for field_name in (
            "shock_qqq_return_window_days",
            "recovery_rv_comparison_days",
            "medium_gate_trend_window_days",
            "medium_gate_momentum_window_days",
            "medium_gate_confirmation_days",
            "shock_min_hold_days",
            "recovery_min_hold_days",
        ):
            _positive_int(getattr(self, field_name), field_name)
        for field_name in (
            "shock_qqq_return_max",
            "shock_vix_min",
            "shock_vix_term_ratio_min",
            "recovery_rebound_5d_min",
            "recovery_rebound_10d_min",
        ):
            _finite_number(getattr(self, field_name), field_name)
        _require(
            1 <= self.recovery_confirmation_required <= len(_RECOVERY_OPTIONS),
            "recovery_confirmation_required is outside the declared option set",
        )
        options = _unique_strings(self.recovery_confirmation_options, "recovery_confirmation_options")
        _require(frozenset(options) == _RECOVERY_OPTIONS, "recovery confirmation options do not match the frozen v10 set")
        object.__setattr__(self, "recovery_confirmation_options", options)

    @classmethod
    def from_registry(
        cls,
        registry: StrategyContractRegistry,
        strategy_version: str = STRATEGY_VERSION,
    ) -> "RegimeConfig":
        """Build the executable state-machine config from the frozen registry."""

        version = registry.get_version_contract(strategy_version)
        parameters = version.parameters
        required = {
            "shock_qqq_return_window_days",
            "shock_qqq_return_max",
            "shock_vix_min",
            "shock_vix_term_ratio_min",
            "recovery_confirmation_required",
            "recovery_confirmation_options",
            "recovery_rebound_5d_min",
            "recovery_rebound_10d_min",
            "recovery_rv_comparison_days",
            "medium_gate_trend_window_days",
            "medium_gate_momentum_window_days",
            "medium_gate_confirmation_days",
            "shock_min_hold_days",
            "recovery_min_hold_days",
        }
        missing = sorted(required - set(parameters))
        _require(not missing, f"frozen v10 contract is missing M05 parameters: {missing}")
        return cls(
            strategy_version=version.version,
            strategy_config_hash=version.config_hash,
            shock_qqq_return_window_days=_positive_int(parameters["shock_qqq_return_window_days"], "shock_qqq_return_window_days"),
            shock_qqq_return_max=_finite_number(parameters["shock_qqq_return_max"], "shock_qqq_return_max"),
            shock_vix_min=_finite_number(parameters["shock_vix_min"], "shock_vix_min"),
            shock_vix_term_ratio_min=_finite_number(parameters["shock_vix_term_ratio_min"], "shock_vix_term_ratio_min"),
            recovery_confirmation_required=_positive_int(parameters["recovery_confirmation_required"], "recovery_confirmation_required"),
            recovery_confirmation_options=_unique_strings(parameters["recovery_confirmation_options"], "recovery_confirmation_options"),
            recovery_rebound_5d_min=_finite_number(parameters["recovery_rebound_5d_min"], "recovery_rebound_5d_min"),
            recovery_rebound_10d_min=_finite_number(parameters["recovery_rebound_10d_min"], "recovery_rebound_10d_min"),
            recovery_rv_comparison_days=_positive_int(parameters["recovery_rv_comparison_days"], "recovery_rv_comparison_days"),
            medium_gate_trend_window_days=_positive_int(parameters["medium_gate_trend_window_days"], "medium_gate_trend_window_days"),
            medium_gate_momentum_window_days=_positive_int(parameters["medium_gate_momentum_window_days"], "medium_gate_momentum_window_days"),
            medium_gate_confirmation_days=_positive_int(parameters["medium_gate_confirmation_days"], "medium_gate_confirmation_days"),
            shock_min_hold_days=_positive_int(parameters["shock_min_hold_days"], "shock_min_hold_days"),
            recovery_min_hold_days=_positive_int(parameters["recovery_min_hold_days"], "recovery_min_hold_days"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime_version": REGIME_IMPLEMENTATION_VERSION,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "parameters": {
                "shock_qqq_return_window_days": self.shock_qqq_return_window_days,
                "shock_qqq_return_max": self.shock_qqq_return_max,
                "shock_vix_min": self.shock_vix_min,
                "shock_vix_term_ratio_min": self.shock_vix_term_ratio_min,
                "recovery_confirmation_required": self.recovery_confirmation_required,
                "recovery_confirmation_options": list(self.recovery_confirmation_options),
                "recovery_rebound_5d_min": self.recovery_rebound_5d_min,
                "recovery_rebound_10d_min": self.recovery_rebound_10d_min,
                "recovery_rv_comparison_days": self.recovery_rv_comparison_days,
                "medium_gate_trend_window_days": self.medium_gate_trend_window_days,
                "medium_gate_momentum_window_days": self.medium_gate_momentum_window_days,
                "medium_gate_confirmation_days": self.medium_gate_confirmation_days,
                "shock_min_hold_days": self.shock_min_hold_days,
                "recovery_min_hold_days": self.recovery_min_hold_days,
            },
        }


@dataclass(frozen=True)
class RegimeInput:
    """One signal-date input: M04 indicators plus the same-date QQQ bar."""

    indicators: IndicatorSnapshot
    qqq_bar: NormalizedBar

    def __post_init__(self) -> None:
        _require(isinstance(self.indicators, IndicatorSnapshot), "regime indicators must be an IndicatorSnapshot")
        _require(isinstance(self.qqq_bar, NormalizedBar), "regime qqq_bar must be a NormalizedBar")
        _require(self.qqq_bar.symbol == "QQQ", "regime input must use a QQQ normalized bar")
        _require(self.qqq_bar.bar_date == self.indicators.signal_date, "QQQ bar and indicator signal date must match")
        if "QQQ" in self.indicators.price_basis_by_symbol:
            _require(self.qqq_bar.price_basis == self.indicators.price_basis_by_symbol["QQQ"], "QQQ bar price basis differs from indicator provenance")
        if "QQQ" in self.indicators.timezone_by_symbol:
            _require(self.qqq_bar.timezone == self.indicators.timezone_by_symbol["QQQ"], "QQQ bar timezone differs from indicator provenance")


@dataclass(frozen=True)
class RegimeState:
    """Minimal immutable state carried from one signal date to the next."""

    state: str
    elapsed_state_sessions: int = 0
    medium_gate_streak: int = 0

    def __post_init__(self) -> None:
        _require(self.state in ALL_STATES, f"unsupported regime state: {self.state}")
        _require(isinstance(self.elapsed_state_sessions, int) and self.elapsed_state_sessions >= 0, "elapsed_state_sessions must be a non-negative integer")
        _require(isinstance(self.medium_gate_streak, int) and self.medium_gate_streak >= 0, "medium_gate_streak must be a non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "elapsed_state_sessions": self.elapsed_state_sessions,
            "medium_gate_streak": self.medium_gate_streak,
        }


@dataclass(frozen=True)
class RegimeEvidence:
    """One auditable condition, including both passed and failed outcomes."""

    code: str
    passed: bool
    observed: Any
    threshold: Any
    message: str

    def __post_init__(self) -> None:
        _require(isinstance(self.code, str) and self.code.strip(), "regime evidence code must be non-empty")
        _require(isinstance(self.passed, bool), "regime evidence passed must be boolean")
        _require(isinstance(self.message, str) and self.message.strip(), "regime evidence message must be non-empty")
        try:
            json.dumps({"observed": self.observed, "threshold": self.threshold}, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RegimeError("regime evidence must be JSON-compatible and finite") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "passed": self.passed,
            "observed": copy.deepcopy(self.observed),
            "threshold": copy.deepcopy(self.threshold),
            "message": self.message,
        }


@dataclass(frozen=True)
class RegimeSnapshot:
    """State and evidence for one signal date."""

    strategy_version: str
    strategy_config_hash: str
    regime_version: str
    signal_date: str
    execution_date: str
    as_of: str
    calendar_id: str
    indicator_version: str
    indicator_quality: str
    indicator_ready: bool
    qqq_bar_quality: str
    state: str
    previous_state: str | None
    elapsed_state_sessions: int
    medium_gate_streak: int
    transition: bool
    confirmed: bool
    reason_codes: tuple[str, ...]
    evidence: tuple[RegimeEvidence, ...]
    indicator_snapshot_hash: str
    qqq_snapshot_ids: tuple[str, ...]
    qqq_price_basis: str
    qqq_timezone: str

    def __post_init__(self) -> None:
        _require(self.strategy_version == STRATEGY_VERSION, "regime snapshot must use the frozen v10 strategy")
        _require(self.regime_version == REGIME_IMPLEMENTATION_VERSION, "unsupported M05 regime version")
        _require(isinstance(self.signal_date, str) and isinstance(self.execution_date, str), "signal and execution dates must be strings")
        try:
            signal = date.fromisoformat(self.signal_date)
            execution = date.fromisoformat(self.execution_date)
        except ValueError as exc:
            raise RegimeError("signal and execution dates must be ISO dates") from exc
        _require(execution > signal, "execution_date must be after signal_date")
        _require(isinstance(self.calendar_id, str) and self.calendar_id.strip(), "calendar_id must be non-empty")
        _require(isinstance(self.indicator_version, str) and self.indicator_version.strip(), "indicator_version must be non-empty")
        _require(self.indicator_quality in _QUALITY_SET, f"unsupported indicator quality: {self.indicator_quality}")
        _require(self.qqq_bar_quality in _QUALITY_SET, f"unsupported QQQ bar quality: {self.qqq_bar_quality}")
        _require(self.state in ALL_STATES, f"unsupported regime state: {self.state}")
        _require(isinstance(self.previous_state, (str, type(None))), "previous_state must be a string or null")
        if self.previous_state is not None:
            _require(self.previous_state in ALL_STATES, "previous_state is not a valid regime state")
        _require(isinstance(self.indicator_ready, bool), "indicator_ready must be boolean")
        _require(isinstance(self.elapsed_state_sessions, int) and self.elapsed_state_sessions >= 0, "elapsed_state_sessions must be non-negative")
        _require(isinstance(self.medium_gate_streak, int) and self.medium_gate_streak >= 0, "medium_gate_streak must be non-negative")
        _require(isinstance(self.transition, bool) and isinstance(self.confirmed, bool), "transition and confirmed must be boolean")
        _require(self.state not in ("warming", "needs_review") or not self.confirmed, "warming/needs_review cannot be confirmed")
        reasons = _unique_strings(self.reason_codes, "reason_codes")
        evidence = tuple(self.evidence)
        _require(all(isinstance(item, RegimeEvidence) for item in evidence), "evidence must contain RegimeEvidence objects")
        _require(len({item.code for item in evidence}) == len(evidence), "evidence codes must be unique")
        _require(isinstance(self.indicator_snapshot_hash, str) and len(self.indicator_snapshot_hash) == 64, "indicator_snapshot_hash must be SHA-256")
        snapshots = _unique_strings(self.qqq_snapshot_ids, "qqq_snapshot_ids")
        _require(isinstance(self.qqq_price_basis, str) and self.qqq_price_basis.strip(), "qqq_price_basis must be non-empty")
        _require(isinstance(self.qqq_timezone, str) and self.qqq_timezone.strip(), "qqq_timezone must be non-empty")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "qqq_snapshot_ids", snapshots)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-regime-snapshot/v1",
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "regime_version": self.regime_version,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "indicator_version": self.indicator_version,
            "indicator_quality": self.indicator_quality,
            "indicator_ready": self.indicator_ready,
            "qqq_bar_quality": self.qqq_bar_quality,
            "state": self.state,
            "previous_state": self.previous_state,
            "elapsed_state_sessions": self.elapsed_state_sessions,
            "medium_gate_streak": self.medium_gate_streak,
            "transition": self.transition,
            "confirmed": self.confirmed,
            "reason_codes": list(self.reason_codes),
            "evidence": [item.as_dict() for item in self.evidence],
            "indicator_snapshot_hash": self.indicator_snapshot_hash,
            "qqq_snapshot_ids": list(self.qqq_snapshot_ids),
            "qqq_price_basis": self.qqq_price_basis,
            "qqq_timezone": self.qqq_timezone,
        }


@dataclass(frozen=True)
class RegimeRun:
    """Deterministic ordered replay output."""

    regime_version: str
    strategy_version: str
    strategy_config_hash: str
    calendar_id: str
    indicator_version: str
    quality: str
    snapshots: tuple[RegimeSnapshot, ...]

    def __post_init__(self) -> None:
        _require(self.regime_version == REGIME_IMPLEMENTATION_VERSION, "unsupported M05 regime version")
        _require(self.strategy_version == STRATEGY_VERSION, "unsupported strategy version")
        _require(self.quality in _QUALITY_SET, f"unsupported replay quality: {self.quality}")
        _require(self.snapshots and isinstance(self.snapshots, Sequence), "regime replay must contain snapshots")
        dates = tuple(item.signal_date for item in self.snapshots)
        _require(dates == tuple(sorted(set(dates))), "regime snapshots must be sorted and unique")
        _require(all(item.calendar_id == self.calendar_id for item in self.snapshots), "regime snapshot calendar mismatch")
        _require(all(item.indicator_version == self.indicator_version for item in self.snapshots), "regime snapshot indicator version mismatch")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "qqq-regime-run/v1",
            "regime_version": self.regime_version,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "calendar_id": self.calendar_id,
            "indicator_version": self.indicator_version,
            "quality": self.quality,
            "snapshots": [item.as_dict() for item in self.snapshots],
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self.as_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_dict())


def _indicator_value(snapshot: IndicatorSnapshot, name: str) -> float | None:
    return _optional_number(snapshot.values.get(name), f"indicator.values.{name}")


def _evidence(code: str, passed: bool, observed: Any, threshold: Any, message: str) -> RegimeEvidence:
    return RegimeEvidence(code, bool(passed), observed, threshold, message)


def _next_session(signal_date: str, calendar: TradingCalendar) -> str:
    current = date.fromisoformat(signal_date)
    for offset in range(1, 15):
        candidate = current + timedelta(days=offset)
        if calendar.is_trading_day(candidate):
            return candidate.isoformat()
    raise RegimeError(f"no next trading session found after {signal_date}")


def _validate_replay_inputs(
    inputs: Sequence[RegimeInput],
    calendar: TradingCalendar,
) -> tuple[RegimeInput, ...]:
    _require(isinstance(inputs, Sequence) and not isinstance(inputs, (str, bytes)), "regime inputs must be a sequence")
    values = tuple(inputs)
    _require(values, "regime inputs cannot be empty")
    _require(all(isinstance(item, RegimeInput) for item in values), "regime inputs must contain RegimeInput objects")
    ordered = tuple(sorted(values, key=lambda item: item.indicators.signal_date))
    dates = tuple(item.indicators.signal_date for item in ordered)
    _require(len(dates) == len(set(dates)), "regime input signal dates must be unique")
    reference_calendar = ordered[0].indicators.calendar_id
    reference_indicator_version = ordered[0].indicators.indicator_version
    _require(reference_calendar == calendar.calendar_id, "regime input calendar does not match replay calendar")
    _require(all(item.indicators.calendar_id == reference_calendar for item in ordered), "regime input calendar ids must match")
    _require(all(item.indicators.indicator_version == reference_indicator_version for item in ordered), "regime input indicator versions must match")
    for item in ordered:
        _require(calendar.is_trading_day(item.indicators.signal_date), "regime signal date must be an exchange session")
    for previous, current in zip(ordered, ordered[1:]):
        sessions = calendar.sessions(previous.indicators.signal_date, current.indicators.signal_date)
        _require(
            sessions == (previous.indicators.signal_date, current.indicators.signal_date),
            "regime replay cannot compress a missing trading session",
        )
    return ordered


def _conditions(
    current: RegimeInput,
    prior_inputs: Sequence[RegimeInput],
    config: RegimeConfig,
) -> dict[str, Any]:
    values = current.indicators
    return_values = {
        "qqq_return_5d": _indicator_value(values, "qqq_return_5d"),
        "qqq_return_10d": _indicator_value(values, "qqq_return_10d"),
        "qqq_ema10": _indicator_value(values, "qqq_ema10"),
        "qqq_sma150": _indicator_value(values, "qqq_sma150"),
        "qqq_momentum126": _indicator_value(values, "qqq_momentum126"),
        "qqq_rv20": _indicator_value(values, "qqq_rv20"),
        "vix": _indicator_value(values, "vix"),
        "vix3m": _indicator_value(values, "vix3m"),
        "vix_term_ratio": _indicator_value(values, "vix_term_ratio"),
    }
    qqq_return_5d = return_values["qqq_return_5d"]
    qqq_return_10d = return_values["qqq_return_10d"]
    qqq_ema10 = return_values["qqq_ema10"]
    qqq_sma150 = return_values["qqq_sma150"]
    qqq_momentum126 = return_values["qqq_momentum126"]
    qqq_rv20 = return_values["qqq_rv20"]
    vix = return_values["vix"]
    term_ratio = return_values["vix_term_ratio"]

    short_shock = qqq_return_5d is not None and qqq_return_5d <= config.shock_qqq_return_max
    vix_high = vix is not None and vix >= config.shock_vix_min
    term_high = term_ratio is not None and term_ratio >= config.shock_vix_term_ratio_min
    fresh_shock = short_shock and (vix_high or term_high)

    price_rebound = (
        (qqq_return_5d is not None and qqq_return_5d >= config.recovery_rebound_5d_min)
        or (qqq_return_10d is not None and qqq_return_10d >= config.recovery_rebound_10d_min)
    )
    above_ema10 = qqq_ema10 is not None and current.qqq_bar.close > qqq_ema10
    prior_rv20: float | None = None
    comparison_ready = len(prior_inputs) >= config.recovery_rv_comparison_days
    if comparison_ready:
        prior_rv20 = _indicator_value(
            prior_inputs[-config.recovery_rv_comparison_days].indicators,
            "qqq_rv20",
        )
    rv_declining = qqq_rv20 is not None and prior_rv20 is not None and qqq_rv20 < prior_rv20
    confirmation_count = sum((price_rebound, above_ema10, rv_declining))
    recovery_unlock = confirmation_count >= config.recovery_confirmation_required and not short_shock

    above_sma150 = qqq_sma150 is not None and current.qqq_bar.close > qqq_sma150
    momentum_positive = qqq_momentum126 is not None and qqq_momentum126 > 0.0
    medium_gate = above_sma150 and momentum_positive

    return {
        **return_values,
        "short_shock": short_shock,
        "vix_high": vix_high,
        "term_high": term_high,
        "fresh_shock": fresh_shock,
        "price_rebound": price_rebound,
        "above_ema10": above_ema10,
        "prior_rv20": prior_rv20,
        "comparison_ready": comparison_ready,
        "rv_declining": rv_declining,
        "confirmation_count": confirmation_count,
        "recovery_unlock": recovery_unlock,
        "above_sma150": above_sma150,
        "momentum_positive": momentum_positive,
        "medium_gate": medium_gate,
    }


def _condition_evidence(conditions: Mapping[str, Any], config: RegimeConfig) -> tuple[RegimeEvidence, ...]:
    return (
        _evidence(
            "shock_price_drop",
            conditions["short_shock"],
            conditions["qqq_return_5d"],
            {"operator": "<=", "value": config.shock_qqq_return_max},
            "QQQ short-term loss condition",
        ),
        _evidence(
            "shock_vix_level",
            conditions["vix_high"],
            conditions["vix"],
            {"operator": ">=", "value": config.shock_vix_min},
            "VIX level pressure condition",
        ),
        _evidence(
            "shock_term_structure",
            conditions["term_high"],
            conditions["vix_term_ratio"],
            {"operator": ">=", "value": config.shock_vix_term_ratio_min},
            "VIX/VIX3M term pressure condition",
        ),
        _evidence(
            "fresh_shock",
            conditions["fresh_shock"],
            {"short_shock": conditions["short_shock"], "vix_high": conditions["vix_high"], "term_high": conditions["term_high"]},
            "short_shock AND (vix_high OR term_high)",
            "new shock entry condition",
        ),
        _evidence(
            "recovery_price_rebound",
            conditions["price_rebound"],
            {"return_5d": conditions["qqq_return_5d"], "return_10d": conditions["qqq_return_10d"]},
            {"return_5d_min": config.recovery_rebound_5d_min, "return_10d_min": config.recovery_rebound_10d_min},
            "price rebound confirmation",
        ),
        _evidence(
            "recovery_above_ema10",
            conditions["above_ema10"],
            {"qqq_close": conditions.get("qqq_close"), "ema10": conditions["qqq_ema10"]},
            "qqq_close > ema10",
            "short trend confirmation",
        ),
        _evidence(
            "recovery_rv20_declining",
            conditions["rv_declining"],
            {"rv20": conditions["qqq_rv20"], "comparison_rv20": conditions["prior_rv20"], "comparison_ready": conditions["comparison_ready"]},
            {"comparison_sessions": config.recovery_rv_comparison_days, "operator": "<"},
            "realized volatility decline confirmation",
        ),
        _evidence(
            "recovery_confirmation_count",
            conditions["confirmation_count"] >= config.recovery_confirmation_required,
            conditions["confirmation_count"],
            {"operator": ">=", "value": config.recovery_confirmation_required},
            "number of independent recovery confirmations",
        ),
        _evidence(
            "recovery_not_short_shock",
            not conditions["short_shock"],
            conditions["short_shock"],
            False,
            "recovery cannot unlock during an actual short-term QQQ shock",
        ),
        _evidence(
            "recovery_unlock",
            conditions["recovery_unlock"],
            {"confirmation_count": conditions["confirmation_count"], "short_shock": conditions["short_shock"]},
            "two confirmations and no short shock",
            "combined recovery unlock condition",
        ),
        _evidence(
            "medium_above_sma150",
            conditions["above_sma150"],
            {"qqq_close": conditions.get("qqq_close"), "sma150": conditions["qqq_sma150"]},
            "qqq_close > sma150",
            "medium trend condition",
        ),
        _evidence(
            "medium_momentum_positive",
            conditions["momentum_positive"],
            conditions["qqq_momentum126"],
            {"operator": ">", "value": 0.0},
            "medium momentum condition",
        ),
        _evidence(
            "medium_gate",
            conditions["medium_gate"],
            {"above_sma150": conditions["above_sma150"], "momentum_positive": conditions["momentum_positive"]},
            "above_sma150 AND momentum_positive",
            "combined medium gate condition",
        ),
    )


def _reason_codes(
    state: str,
    conditions: Mapping[str, Any],
    elapsed: int,
    medium_gate_streak: int,
    config: RegimeConfig,
    transition: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if transition:
        reasons.append(f"transition_to_{state}")
    if state == "shock":
        if conditions["fresh_shock"]:
            reasons.append("fresh_shock_confirmed")
        if elapsed < config.shock_min_hold_days:
            reasons.append("shock_minimum_hold_active")
        if not conditions["recovery_unlock"]:
            reasons.append("recovery_confirmation_pending")
    elif state == "recovery":
        reasons.append("recovery_state_active")
        if elapsed < config.recovery_min_hold_days:
            reasons.append("recovery_minimum_hold_active")
        if medium_gate_streak < config.medium_gate_confirmation_days:
            reasons.append("medium_gate_streak_pending")
        if conditions["fresh_shock"]:
            reasons.append("reentry_shock_waits_for_hysteresis")
    elif state == "normal":
        if not conditions["fresh_shock"]:
            reasons.append("fresh_shock_not_confirmed")
        if conditions["medium_gate"]:
            reasons.append("medium_gate_confirmed")
        else:
            reasons.append("medium_gate_pending")
    elif state == "warming":
        reasons.append("warmup_insufficient_history")
    elif state == "needs_review":
        reasons.append("data_quality_needs_review")
    return _unique_strings(reasons, "reason_codes")


def evaluate_regime(
    current: RegimeInput,
    *,
    config: RegimeConfig,
    calendar: TradingCalendar | None = None,
    previous_state: RegimeState | None = None,
    prior_inputs: Sequence[RegimeInput] = (),
) -> RegimeSnapshot:
    """Evaluate one signal date from current data and an optional prior state."""

    selected_calendar = calendar or TradingCalendar()
    _validate_replay_inputs((current,), selected_calendar)
    prior = tuple(prior_inputs)
    if prior:
        ordered_prior = _validate_replay_inputs(prior, selected_calendar)
        _require(ordered_prior[-1].indicators.signal_date < current.indicators.signal_date, "prior inputs must be earlier than current input")
        all_inputs = ordered_prior + (current,)
        _validate_replay_inputs(all_inputs, selected_calendar)
        prior = ordered_prior
    conditions = _conditions(current, prior, config)
    conditions["qqq_close"] = current.qqq_bar.close
    evidence = list(_condition_evidence(conditions, config))

    previous_name = previous_state.state if previous_state is not None else None
    data_quality_ok = current.indicators.quality == "OK" and current.qqq_bar.quality == "OK"
    if not data_quality_ok:
        state = "needs_review"
        elapsed = 0
        medium_gate_streak = 0
        transition = previous_name != state
    elif not current.indicators.ready:
        state = "warming"
        elapsed = 0
        medium_gate_streak = 0
        transition = previous_name != state
    else:
        prior_active = previous_state is not None and previous_state.state in ACTIVE_STATES
        base_state = previous_state.state if prior_active else "normal"
        base_elapsed = previous_state.elapsed_state_sessions if prior_active else 0
        if conditions["medium_gate"]:
            medium_gate_streak = (previous_state.medium_gate_streak + 1) if prior_active else 1
        else:
            medium_gate_streak = 0

        if base_state == "normal":
            if conditions["fresh_shock"]:
                state = "shock"
                elapsed = 0
                transition = previous_name != state
            else:
                state = "normal"
                elapsed = base_elapsed + 1 if previous_name == "normal" else 0
                transition = previous_name not in (None, "normal")
        elif base_state == "shock":
            elapsed_after_today = base_elapsed + 1
            if elapsed_after_today >= config.shock_min_hold_days and conditions["recovery_unlock"]:
                state = "recovery"
                elapsed = 0
                transition = previous_name != state
            else:
                state = "shock"
                elapsed = elapsed_after_today
                transition = previous_name != state
        else:  # recovery
            elapsed_after_today = base_elapsed + 1
            if elapsed_after_today >= config.shock_min_hold_days and conditions["fresh_shock"]:
                state = "shock"
                elapsed = 0
                transition = previous_name != state
            elif elapsed_after_today >= config.recovery_min_hold_days and medium_gate_streak >= config.medium_gate_confirmation_days:
                state = "normal"
                elapsed = 0
                transition = previous_name != state
            else:
                state = "recovery"
                elapsed = elapsed_after_today
                transition = previous_name != state

    if not data_quality_ok or not current.indicators.ready:
        medium_gate_streak = 0
    confirmed = data_quality_ok and current.indicators.ready and state in ACTIVE_STATES
    reasons = _reason_codes(state, conditions, elapsed, medium_gate_streak, config, transition)
    return RegimeSnapshot(
        strategy_version=config.strategy_version,
        strategy_config_hash=config.strategy_config_hash,
        regime_version=REGIME_IMPLEMENTATION_VERSION,
        signal_date=current.indicators.signal_date,
        execution_date=_next_session(current.indicators.signal_date, selected_calendar),
        as_of=current.indicators.as_of,
        calendar_id=current.indicators.calendar_id,
        indicator_version=current.indicators.indicator_version,
        indicator_quality=current.indicators.quality,
        indicator_ready=current.indicators.ready,
        qqq_bar_quality=current.qqq_bar.quality,
        state=state,
        previous_state=previous_name,
        elapsed_state_sessions=elapsed,
        medium_gate_streak=medium_gate_streak,
        transition=transition,
        confirmed=confirmed,
        reason_codes=reasons,
        evidence=tuple(evidence),
        indicator_snapshot_hash=_sha256(current.indicators.as_dict()),
        qqq_snapshot_ids=current.qqq_bar.snapshot_ids,
        qqq_price_basis=current.qqq_bar.price_basis,
        qqq_timezone=current.qqq_bar.timezone,
    )


def replay_regimes(
    inputs: Sequence[RegimeInput],
    *,
    config: RegimeConfig,
    calendar: TradingCalendar | None = None,
    initial_state: RegimeState | None = None,
) -> RegimeRun:
    """Replay a sorted session sequence without using any future row."""

    selected_calendar = calendar or TradingCalendar()
    ordered = _validate_replay_inputs(inputs, selected_calendar)
    state = initial_state
    snapshots: list[RegimeSnapshot] = []
    for index, current in enumerate(ordered):
        snapshot = evaluate_regime(
            current,
            config=config,
            calendar=selected_calendar,
            previous_state=state,
            prior_inputs=ordered[:index],
        )
        snapshots.append(snapshot)
        state = RegimeState(
            snapshot.state,
            snapshot.elapsed_state_sessions,
            snapshot.medium_gate_streak,
        )
    quality = "OK"
    priority = {status: index for index, status in enumerate(QUALITY_STATUSES)}
    for snapshot in snapshots:
        observed = snapshot.indicator_quality
        if priority[observed] > priority[quality]:
            quality = observed
        if priority[snapshot.qqq_bar_quality] > priority[quality]:
            quality = snapshot.qqq_bar_quality
    return RegimeRun(
        regime_version=REGIME_IMPLEMENTATION_VERSION,
        strategy_version=config.strategy_version,
        strategy_config_hash=config.strategy_config_hash,
        calendar_id=selected_calendar.calendar_id,
        indicator_version=ordered[0].indicators.indicator_version,
        quality=quality,
        snapshots=tuple(snapshots),
    )


__all__ = [
    "ACTIVE_STATES",
    "ALL_STATES",
    "REGIME_IMPLEMENTATION_VERSION",
    "STRATEGY_VERSION",
    "RegimeConfig",
    "RegimeError",
    "RegimeEvidence",
    "RegimeInput",
    "RegimeRun",
    "RegimeSnapshot",
    "RegimeState",
    "evaluate_regime",
    "replay_regimes",
]
