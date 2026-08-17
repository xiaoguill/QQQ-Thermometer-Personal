"""Candidate-only target weight service for the frozen v10 regime.

M07 consumes the M05 regime snapshot and the frozen version contract.  It
does not accept client weights, recalculate indicators, use M06 colours, or
place orders.  The current v10 contract is still a research candidate, so
every result is explicitly marked candidate-only and not order-ready.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from src.storage.normalization import QUALITY_STATUSES, TradingCalendar

from .contracts import StrategyContractRegistry, load_contract
from .policy import (
    CANDIDATE_POLICY_PROFILE_ID,
    candidate_profile_weights,
)
from .regime import ALL_STATES, RegimeSnapshot, STRATEGY_VERSION


TARGET_WEIGHT_IMPLEMENTATION_VERSION = "m07-target-weights/v1"
TARGET_WEIGHT_SCHEMA = "qqq-target-weight-snapshot/v1"
WEIGHT_STATUS_CANDIDATE_ONLY = "CANDIDATE_ONLY"

_QUALITY_PRIORITY = {status: index for index, status in enumerate(QUALITY_STATUSES)}
_PROFILE_BY_STATE = {
    "warming": "warming",
    "needs_review": "needs_review",
    "shock": "shock",
    "recovery": "recovery",
}


class TargetWeightError(ValueError):
    """Raised when a target-weight request violates the M07 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TargetWeightError(message)


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


def _quality_max(*statuses: str) -> str:
    present = tuple(status for status in statuses if status)
    _require(all(status in _QUALITY_PRIORITY for status in present), "unknown data quality status")
    return max(present, key=lambda status: _QUALITY_PRIORITY[status]) if present else "OK"


def _unique_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    _require(isinstance(values, tuple) and all(isinstance(value, str) for value in values), f"{field_name} must be a tuple of strings")
    cleaned = tuple(value.strip() for value in values)
    _require(all(cleaned), f"{field_name} cannot contain empty values")
    _require(len(cleaned) == len(set(cleaned)), f"{field_name} cannot contain duplicates")
    return cleaned


@dataclass(frozen=True)
class TargetWeightSnapshot:
    """Validated target weights with explicit candidate-only status."""

    schema: str
    implementation_version: str
    strategy_version: str
    strategy_config_hash: str
    strategy_status: str
    strategy_implementation_state: str
    product_default_strategy_version: str | None
    weight_status: str
    candidate_only: bool
    execution_eligible: bool
    profile_id: str
    signal_date: str
    execution_date: str
    as_of: str
    calendar_id: str
    data_quality: str
    state: str
    previous_state: str | None
    medium_gate_streak: int
    target_weights: Mapping[str, float]
    change_reason_codes: tuple[str, ...]
    regime_reason_codes: tuple[str, ...]
    regime_snapshot_hash: str

    def __post_init__(self) -> None:
        _require(self.schema == TARGET_WEIGHT_SCHEMA, "unsupported target-weight schema")
        _require(self.implementation_version == TARGET_WEIGHT_IMPLEMENTATION_VERSION, "unsupported target-weight implementation")
        _require(self.strategy_version == STRATEGY_VERSION, "target weights must use frozen v10")
        _require(isinstance(self.strategy_config_hash, str) and len(self.strategy_config_hash) == 64, "strategy config hash must be SHA-256")
        _require(self.strategy_status == "research_candidate", "only the v10 research candidate is supported")
        _require(self.strategy_implementation_state == "contract_only", "strategy is not allowed to be promoted by M07")
        _require(self.product_default_strategy_version is None, "M07 cannot mark a product default strategy")
        _require(self.weight_status == WEIGHT_STATUS_CANDIDATE_ONLY, "target weights must remain candidate-only")
        _require(self.candidate_only is True and self.execution_eligible is False, "candidate-only execution boundary was weakened")
        _require(self.profile_id == CANDIDATE_POLICY_PROFILE_ID, "unexpected target-weight profile")
        _require(isinstance(self.signal_date, str) and isinstance(self.execution_date, str), "target-weight dates must be strings")
        try:
            signal = date.fromisoformat(self.signal_date)
            execution = date.fromisoformat(self.execution_date)
        except ValueError as exc:
            raise TargetWeightError("target-weight dates must be ISO dates") from exc
        _require(execution > signal, "execution_date must be after signal_date")
        _require(isinstance(self.as_of, str) and self.as_of.strip(), "target-weight as_of is required")
        _require(isinstance(self.calendar_id, str) and self.calendar_id.strip(), "target-weight calendar is required")
        _require(self.data_quality in _QUALITY_PRIORITY, "unsupported target-weight data quality")
        _require(self.state in ALL_STATES, "unsupported target-weight state")
        _require(self.previous_state is None or self.previous_state in ALL_STATES, "unsupported previous state")
        _require(isinstance(self.medium_gate_streak, int) and self.medium_gate_streak >= 0, "medium gate streak must be non-negative")
        _require(isinstance(self.target_weights, Mapping), "target weights must be an object")
        normalized = {}
        for symbol, value in self.target_weights.items():
            _require(isinstance(symbol, str) and symbol.strip(), "target-weight asset names must be non-empty")
            _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"target weight for {symbol} must be numeric")
            number = float(value)
            _require(math.isfinite(number) and number >= 0.0, f"target weight for {symbol} must be finite and non-negative")
            normalized[symbol] = number
        _require(abs(sum(normalized.values()) - 1.0) <= 1e-8, "target weights must sum to one")
        reasons = _unique_strings(self.change_reason_codes, "change_reason_codes")
        regime_reasons = _unique_strings(self.regime_reason_codes, "regime_reason_codes")
        _require(isinstance(self.regime_snapshot_hash, str) and len(self.regime_snapshot_hash) == 64, "regime snapshot hash must be SHA-256")
        object.__setattr__(self, "target_weights", normalized)
        object.__setattr__(self, "change_reason_codes", reasons)
        object.__setattr__(self, "regime_reason_codes", regime_reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "implementation_version": self.implementation_version,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "strategy_status": self.strategy_status,
            "strategy_implementation_state": self.strategy_implementation_state,
            "product_default_strategy_version": self.product_default_strategy_version,
            "weight_status": self.weight_status,
            "candidate_only": self.candidate_only,
            "execution_eligible": self.execution_eligible,
            "profile_id": self.profile_id,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "data_quality": self.data_quality,
            "state": self.state,
            "previous_state": self.previous_state,
            "medium_gate_streak": self.medium_gate_streak,
            "target_weights": dict(self.target_weights),
            "change_reason_codes": list(self.change_reason_codes),
            "regime_reason_codes": list(self.regime_reason_codes),
            "regime_snapshot_hash": self.regime_snapshot_hash,
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self.as_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_dict())


def _profile_for_regime(regime: RegimeSnapshot, confirmation_days: int) -> tuple[str, tuple[str, ...]]:
    if regime.state in _PROFILE_BY_STATE:
        profile = _PROFILE_BY_STATE[regime.state]
        reasons = {
            "warming": ("warmup_default_weights",),
            "needs_review": ("data_quality_fail_closed",),
            "shock": ("shock_candidate_profile",),
            "recovery": ("recovery_candidate_profile",),
        }
        return profile, reasons[regime.state]
    _require(regime.state == "normal", "M07 has no target profile for this state")
    if regime.confirmed and regime.medium_gate_streak >= confirmation_days:
        return "normal", ("normal_medium_gate_confirmed",)
    return "normal_unconfirmed", ("medium_gate_confirmation_pending",)


def build_target_weights(
    regime: RegimeSnapshot,
    *,
    registry: StrategyContractRegistry | None = None,
    calendar: TradingCalendar | None = None,
) -> TargetWeightSnapshot:
    """Build candidate-only target weights from an existing M05 snapshot."""

    _require(isinstance(regime, RegimeSnapshot), "M07 input must be a RegimeSnapshot")
    selected_registry = registry or load_contract()
    version = selected_registry.get_version_contract(regime.strategy_version)
    _require(version.version == STRATEGY_VERSION, "unsupported target-weight strategy version")
    _require(regime.strategy_config_hash == version.config_hash, "regime strategy hash differs from frozen contract")
    _require(version.status == "research_candidate", "M07 cannot use a non-candidate status")
    _require(version.implementation_state == "contract_only", "M07 cannot promote a strategy implementation")
    _require(selected_registry.product_default_strategy_version is None, "M07 cannot use a product default strategy")

    selected_calendar = calendar or TradingCalendar()
    _require(regime.calendar_id == selected_calendar.calendar_id, "regime calendar differs from target-weight calendar")
    _require(selected_calendar.is_trading_day(regime.signal_date), "signal_date must be a trading session")
    sessions = selected_calendar.sessions(regime.signal_date, regime.execution_date)
    _require(sessions == (regime.signal_date, regime.execution_date), "execution_date must be the next trading session")

    confirmation_days = version.parameters.get("medium_gate_confirmation_days")
    _require(isinstance(confirmation_days, int) and confirmation_days > 0, "medium gate confirmation parameter is missing")
    profile, profile_reasons = _profile_for_regime(regime, confirmation_days)
    raw_weights = candidate_profile_weights(profile)
    weights = selected_registry.validate_weights(STRATEGY_VERSION, raw_weights)
    change_reasons = tuple(profile_reasons) + ("candidate_strategy_not_product_default",)
    data_quality = _quality_max(regime.indicator_quality, regime.qqq_bar_quality)
    return TargetWeightSnapshot(
        schema=TARGET_WEIGHT_SCHEMA,
        implementation_version=TARGET_WEIGHT_IMPLEMENTATION_VERSION,
        strategy_version=version.version,
        strategy_config_hash=version.config_hash,
        strategy_status=version.status,
        strategy_implementation_state=version.implementation_state,
        product_default_strategy_version=selected_registry.product_default_strategy_version,
        weight_status=WEIGHT_STATUS_CANDIDATE_ONLY,
        candidate_only=True,
        execution_eligible=False,
        profile_id=CANDIDATE_POLICY_PROFILE_ID,
        signal_date=regime.signal_date,
        execution_date=regime.execution_date,
        as_of=regime.as_of,
        calendar_id=regime.calendar_id,
        data_quality=data_quality,
        state=regime.state,
        previous_state=regime.previous_state,
        medium_gate_streak=regime.medium_gate_streak,
        target_weights=weights,
        change_reason_codes=change_reasons,
        regime_reason_codes=regime.reason_codes,
        regime_snapshot_hash=_sha256(regime.as_dict()),
    )


@dataclass(frozen=True)
class TargetWeightService:
    """Dependency-injectable pure service façade for later API integration."""

    registry: StrategyContractRegistry
    calendar: TradingCalendar

    def from_regime(self, regime: RegimeSnapshot) -> TargetWeightSnapshot:
        return build_target_weights(regime, registry=self.registry, calendar=self.calendar)


__all__ = [
    "CANDIDATE_POLICY_PROFILE_ID",
    "TARGET_WEIGHT_IMPLEMENTATION_VERSION",
    "TARGET_WEIGHT_SCHEMA",
    "WEIGHT_STATUS_CANDIDATE_ONLY",
    "TargetWeightError",
    "TargetWeightService",
    "TargetWeightSnapshot",
    "build_target_weights",
]
