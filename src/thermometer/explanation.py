"""Pure presentation model for the v10 thermometer.

M06 explains an already-computed M05 regime snapshot.  It deliberately does
not recalculate a regime, choose weights, estimate returns, or infer a future
outcome.  The numeric temperature and semantic colour are stable display
conventions, while the directional agreement is a description of the
available M04 signals rather than a probability.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from src.storage.indicators import INDICATOR_VERSION, IndicatorSnapshot
from src.storage.normalization import QUALITY_STATUSES

from .regime import (
    ALL_STATES,
    REGIME_IMPLEMENTATION_VERSION,
    RegimeEvidence,
    RegimeSnapshot,
    STRATEGY_VERSION,
)


EXPLANATION_IMPLEMENTATION_VERSION = "m06-explanation/v1"
EXPLANATION_SCHEMA = "qqq-thermometer-explanation/v1"

CLOSE_CONFIRMED = "CLOSE_CONFIRMED"
INTRADAY_PROVISIONAL = "INTRADAY_PROVISIONAL"
OBSERVATION_PHASES = (CLOSE_CONFIRMED, INTRADAY_PROVISIONAL)

PUBLICATION_CONFIRMED = "CONFIRMED"
PUBLICATION_PROVISIONAL = "PROVISIONAL"
PUBLICATION_NEEDS_REVIEW = "NEEDS_REVIEW"
PUBLICATION_STATUSES = (
    PUBLICATION_CONFIRMED,
    PUBLICATION_PROVISIONAL,
    PUBLICATION_NEEDS_REVIEW,
)

CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_PROVISIONAL = "provisional"
CONFIDENCE_UNAVAILABLE = "unavailable"
CONFIDENCE_LABELS = (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_PROVISIONAL,
    CONFIDENCE_UNAVAILABLE,
)

TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"
TREND_MIXED = "mixed"
TREND_NEUTRAL = "neutral"
TREND_UNAVAILABLE = "unavailable"
TRENDS = (
    TREND_BULLISH,
    TREND_BEARISH,
    TREND_MIXED,
    TREND_NEUTRAL,
    TREND_UNAVAILABLE,
)

_DIRECTIONAL_INDICATORS = (
    "qqq_return_5d",
    "qqq_return_10d",
    "qqq_momentum126",
)

# These values are display conventions only.  They are not strategy
# thresholds and never participate in regime or target-weight calculation.
_STATE_PRESENTATION = {
    "warming": {"temperature": None, "color_token": "neutral", "label": "Warming"},
    "normal": {"temperature": 80, "color_token": "green", "label": "Normal"},
    "shock": {"temperature": 15, "color_token": "red", "label": "Shock"},
    "recovery": {"temperature": 50, "color_token": "yellow", "label": "Recovery"},
    "needs_review": {"temperature": None, "color_token": "neutral", "label": "Needs review"},
}
_DIRECTIONAL_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})
_QUALITY_PRIORITY = {status: index for index, status in enumerate(QUALITY_STATUSES)}


class ExplanationError(ValueError):
    """Raised when an M06 input or presentation model is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExplanationError(message)


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


def _json_copy(value: Any, field_name: str) -> Any:
    try:
        copied = copy.deepcopy(value)
        json.dumps(copied, ensure_ascii=False, allow_nan=False)
        return copied
    except (TypeError, ValueError) as exc:
        raise ExplanationError(f"{field_name} must be JSON-compatible and finite") from exc


def _unique_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    _require(
        isinstance(values, tuple) and all(isinstance(value, str) for value in values),
        f"{field_name} must be a tuple of strings",
    )
    cleaned = tuple(value.strip() for value in values)
    _require(all(cleaned), f"{field_name} cannot contain empty values")
    _require(len(cleaned) == len(set(cleaned)), f"{field_name} cannot contain duplicates")
    return cleaned


def _quality_max(*statuses: str) -> str:
    present = tuple(status for status in statuses if status)
    _require(all(status in _QUALITY_PRIORITY for status in present), "unknown data quality status")
    return max(present, key=lambda status: _QUALITY_PRIORITY[status]) if present else "OK"


def _direction(value: float) -> str:
    if value > 0.0:
        return "bullish"
    if value < 0.0:
        return "bearish"
    return "neutral"


@dataclass(frozen=True)
class ExplanationInput:
    """M05 output plus the matching M04 snapshot and observation phase."""

    regime: RegimeSnapshot
    indicators: IndicatorSnapshot | None
    observation_phase: str = CLOSE_CONFIRMED

    def __post_init__(self) -> None:
        _require(isinstance(self.regime, RegimeSnapshot), "explanation regime must be a RegimeSnapshot")
        _require(
            self.indicators is None or isinstance(self.indicators, IndicatorSnapshot),
            "explanation indicators must be an IndicatorSnapshot or None",
        )
        _require(self.observation_phase in OBSERVATION_PHASES, "unsupported observation phase")
        if self.indicators is not None:
            _require(
                self.indicators.signal_date == self.regime.signal_date,
                "regime and indicator signal dates must match",
            )
            _require(
                self.indicators.calendar_id == self.regime.calendar_id,
                "regime and indicator calendars must match",
            )
            _require(
                self.indicators.indicator_version == self.regime.indicator_version == INDICATOR_VERSION,
                "regime and indicator versions must match M04",
            )
            _require(
                self.indicators.as_of == self.regime.as_of,
                "regime and indicator as_of values must match",
            )
            _require(
                _sha256(self.indicators.as_dict()) == self.regime.indicator_snapshot_hash,
                "regime indicator hash does not match the supplied indicator snapshot",
            )


@dataclass(frozen=True)
class DirectionalSignal:
    """One M04 directional input used only to explain agreement."""

    name: str
    value: float
    direction: str

    def __post_init__(self) -> None:
        _require(self.name in _DIRECTIONAL_INDICATORS, f"unsupported directional indicator: {self.name}")
        _require(isinstance(self.value, (int, float)) and not isinstance(self.value, bool), "directional value must be numeric")
        _require(math.isfinite(float(self.value)), "directional value must be finite")
        _require(self.direction in _DIRECTIONAL_DIRECTIONS, "unsupported directional signal direction")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "direction": self.direction,
        }


@dataclass(frozen=True)
class ExplanationEvidence:
    """A presentation-safe copy of a regime or explanation condition."""

    code: str
    source: str
    passed: bool
    observed: Any
    threshold: Any
    message: str

    def __post_init__(self) -> None:
        _require(isinstance(self.code, str) and self.code.strip(), "explanation evidence code is required")
        _require(self.source in {"regime", "explanation"}, "unsupported explanation evidence source")
        _require(isinstance(self.passed, bool), "explanation evidence passed must be boolean")
        _require(isinstance(self.message, str) and self.message.strip(), "explanation evidence message is required")
        _json_copy(self.observed, "explanation evidence observed")
        _json_copy(self.threshold, "explanation evidence threshold")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "status": "passed" if self.passed else "not_met",
            "passed": self.passed,
            "observed": _json_copy(self.observed, "explanation evidence observed"),
            "threshold": _json_copy(self.threshold, "explanation evidence threshold"),
            "message": self.message,
        }


@dataclass(frozen=True)
class ExplanationModel:
    """Stable, JSON-compatible presentation model with no target weights."""

    schema: str
    explanation_version: str
    strategy_version: str
    strategy_config_hash: str
    regime_version: str
    indicator_version: str
    signal_date: str
    execution_date: str
    as_of: str
    calendar_id: str
    data_quality: str
    indicator_quality: str
    indicator_ready: bool | None
    observation_phase: str
    publication_status: str
    confirmed: bool
    confidence_label: str
    state: str
    state_label: str
    color_token: str
    temperature: int | None
    trend: str
    signal_agreement: float | None
    directional_signals: tuple[DirectionalSignal, ...]
    reason_codes: tuple[str, ...]
    evidence: tuple[ExplanationEvidence, ...]
    source_regime_snapshot_hash: str
    source_indicator_snapshot_hash: str | None

    def __post_init__(self) -> None:
        _require(self.schema == EXPLANATION_SCHEMA, "unsupported M06 explanation schema")
        _require(self.explanation_version == EXPLANATION_IMPLEMENTATION_VERSION, "unsupported M06 explanation version")
        _require(self.strategy_version == STRATEGY_VERSION, "explanation must use the frozen v10 strategy")
        _require(self.regime_version == REGIME_IMPLEMENTATION_VERSION, "explanation regime version mismatch")
        _require(self.indicator_version == INDICATOR_VERSION, "explanation indicator version mismatch")
        _require(isinstance(self.strategy_config_hash, str) and len(self.strategy_config_hash) == 64, "strategy config hash must be SHA-256")
        _require(isinstance(self.signal_date, str) and isinstance(self.execution_date, str), "explanation dates must be strings")
        try:
            signal = date.fromisoformat(self.signal_date)
            execution = date.fromisoformat(self.execution_date)
        except ValueError as exc:
            raise ExplanationError("explanation dates must be ISO dates") from exc
        _require(execution > signal, "explanation execution date must be after signal date")
        _require(isinstance(self.as_of, str) and self.as_of.strip(), "explanation as_of is required")
        _require(isinstance(self.calendar_id, str) and self.calendar_id.strip(), "explanation calendar is required")
        _require(self.data_quality in _QUALITY_PRIORITY, "unsupported explanation data quality")
        _require(self.indicator_quality == "MISSING" or self.indicator_quality in _QUALITY_PRIORITY, "unsupported indicator quality")
        _require(self.indicator_ready is None or isinstance(self.indicator_ready, bool), "indicator_ready must be boolean or null")
        _require(self.observation_phase in OBSERVATION_PHASES, "unsupported explanation observation phase")
        _require(self.publication_status in PUBLICATION_STATUSES, "unsupported publication status")
        _require(self.confidence_label in CONFIDENCE_LABELS, "unsupported confidence label")
        _require(self.state in ALL_STATES, "unsupported explanation state")
        presentation = _STATE_PRESENTATION[self.state]
        _require(self.state_label == presentation["label"], "state label does not match state")
        _require(self.color_token == presentation["color_token"], "state colour does not match state")
        _require(self.temperature == presentation["temperature"], "state temperature does not match state")
        _require(self.trend in TRENDS, "unsupported explanation trend")
        if self.signal_agreement is not None:
            _require(isinstance(self.signal_agreement, (int, float)) and not isinstance(self.signal_agreement, bool), "signal agreement must be numeric or null")
            _require(math.isfinite(float(self.signal_agreement)), "signal agreement must be finite")
            _require(0.0 <= float(self.signal_agreement) <= 1.0, "signal agreement must be between 0 and 1")
        _require(isinstance(self.confirmed, bool), "explanation confirmed must be boolean")
        _require(self.confirmed == (self.publication_status == PUBLICATION_CONFIRMED), "confirmed must match publication status")
        _require(self.state not in ("warming", "needs_review") or not self.confirmed, "warming/needs_review cannot be confirmed")
        signals = tuple(self.directional_signals)
        _require(all(isinstance(signal, DirectionalSignal) for signal in signals), "directional signals are invalid")
        _require(len({signal.name for signal in signals}) == len(signals), "directional signals cannot repeat")
        evidence = tuple(self.evidence)
        _require(all(isinstance(item, ExplanationEvidence) for item in evidence), "explanation evidence is invalid")
        _require(len({item.code for item in evidence}) == len(evidence), "explanation evidence codes must be unique")
        reasons = _unique_strings(self.reason_codes, "explanation reason codes")
        _require(isinstance(self.source_regime_snapshot_hash, str) and len(self.source_regime_snapshot_hash) == 64, "source regime hash must be SHA-256")
        if self.source_indicator_snapshot_hash is not None:
            _require(len(self.source_indicator_snapshot_hash) == 64, "source indicator hash must be SHA-256")
        object.__setattr__(self, "directional_signals", signals)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "reason_codes", reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "explanation_version": self.explanation_version,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "regime_version": self.regime_version,
            "indicator_version": self.indicator_version,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "as_of": self.as_of,
            "calendar_id": self.calendar_id,
            "data_quality": self.data_quality,
            "indicator_quality": self.indicator_quality,
            "indicator_ready": self.indicator_ready,
            "observation_phase": self.observation_phase,
            "publication_status": self.publication_status,
            "confirmed": self.confirmed,
            "confidence_label": self.confidence_label,
            "state": self.state,
            "state_label": self.state_label,
            "color_token": self.color_token,
            "temperature": self.temperature,
            "trend": self.trend,
            "signal_agreement": self.signal_agreement,
            "directional_signals": [signal.as_dict() for signal in self.directional_signals],
            "reason_codes": list(self.reason_codes),
            "evidence": [item.as_dict() for item in self.evidence],
            "source_regime_snapshot_hash": self.source_regime_snapshot_hash,
            "source_indicator_snapshot_hash": self.source_indicator_snapshot_hash,
        }

    @property
    def content_hash(self) -> str:
        return _sha256(self.as_dict())

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_dict())


def _regime_evidence(item: RegimeEvidence) -> ExplanationEvidence:
    return ExplanationEvidence(
        code=f"regime.{item.code}",
        source="regime",
        passed=item.passed,
        observed=item.observed,
        threshold=item.threshold,
        message=item.message,
    )


def _directional_signals(indicators: IndicatorSnapshot | None) -> tuple[DirectionalSignal, ...]:
    if indicators is None or indicators.quality != "OK" or not indicators.ready:
        return ()
    signals: list[DirectionalSignal] = []
    for name in _DIRECTIONAL_INDICATORS:
        value = indicators.values.get(name)
        if value is None:
            continue
        number = float(value)
        signals.append(DirectionalSignal(name, number, _direction(number)))
    return tuple(signals)


def _trend_and_agreement(
    signals: tuple[DirectionalSignal, ...],
) -> tuple[str, float | None]:
    if not signals:
        return TREND_UNAVAILABLE, None
    counts = {direction: 0 for direction in _DIRECTIONAL_DIRECTIONS}
    for signal in signals:
        counts[signal.direction] += 1
    positive = counts["bullish"]
    negative = counts["bearish"]
    neutral = counts["neutral"]
    if positive and not negative and not neutral:
        trend = TREND_BULLISH
    elif negative and not positive and not neutral:
        trend = TREND_BEARISH
    elif not positive and not negative:
        trend = TREND_NEUTRAL
    elif positive and not negative:
        trend = TREND_BULLISH
    elif negative and not positive:
        trend = TREND_BEARISH
    else:
        trend = TREND_MIXED
    agreement = max(counts.values()) / len(signals)
    return trend, float(agreement)


def _quality_and_publication(
    input_value: ExplanationInput,
) -> tuple[str, str, bool | None, str, bool, str]:
    regime = input_value.regime
    indicators = input_value.indicators
    if indicators is None:
        return (
            "NEEDS_REVIEW",
            "MISSING",
            None,
            PUBLICATION_NEEDS_REVIEW,
            False,
            CONFIDENCE_UNAVAILABLE,
        )
    data_quality = _quality_max(regime.indicator_quality, regime.qqq_bar_quality, indicators.quality)
    if data_quality != "OK":
        return (
            data_quality,
            indicators.quality,
            indicators.ready,
            PUBLICATION_NEEDS_REVIEW,
            False,
            CONFIDENCE_UNAVAILABLE,
        )
    if not indicators.ready or not regime.confirmed:
        return (
            data_quality,
            indicators.quality,
            indicators.ready,
            PUBLICATION_PROVISIONAL,
            False,
            CONFIDENCE_UNAVAILABLE,
        )
    if input_value.observation_phase == INTRADAY_PROVISIONAL:
        return (
            data_quality,
            indicators.quality,
            indicators.ready,
            PUBLICATION_PROVISIONAL,
            False,
            CONFIDENCE_PROVISIONAL,
        )
    return (
        data_quality,
        indicators.quality,
        indicators.ready,
        PUBLICATION_CONFIRMED,
        True,
        CONFIDENCE_CONFIRMED,
    )


def _append_reason(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def _explanation_evidence(
    input_value: ExplanationInput,
    data_quality: str,
    publication_status: str,
    directional_signals: tuple[DirectionalSignal, ...],
) -> tuple[ExplanationEvidence, ...]:
    regime = input_value.regime
    indicators = input_value.indicators
    evidence = [_regime_evidence(item) for item in regime.evidence]
    evidence.extend(
        (
            ExplanationEvidence(
                "explanation.indicator_available",
                "explanation",
                indicators is not None,
                indicators is not None,
                True,
                "matching M04 indicator snapshot is available",
            ),
            ExplanationEvidence(
                "explanation.data_quality_ok",
                "explanation",
                data_quality == "OK",
                data_quality,
                "OK",
                "all source quality gates are OK",
            ),
            ExplanationEvidence(
                "explanation.indicator_ready",
                "explanation",
                indicators is not None and indicators.ready,
                None if indicators is None else indicators.ready,
                True,
                "M04 warmup and value completeness gate",
            ),
            ExplanationEvidence(
                "explanation.close_observation",
                "explanation",
                input_value.observation_phase == CLOSE_CONFIRMED,
                input_value.observation_phase,
                CLOSE_CONFIRMED,
                "only a close observation can be published as final",
            ),
            ExplanationEvidence(
                "explanation.regime_confirmed",
                "explanation",
                regime.confirmed,
                regime.confirmed,
                True,
                "M05 regime confirmation is preserved without recalculation",
            ),
            ExplanationEvidence(
                "explanation.directional_signals",
                "explanation",
                bool(directional_signals),
                [signal.name for signal in directional_signals],
                list(_DIRECTIONAL_INDICATORS),
                "directional agreement basis is explicit",
            ),
            ExplanationEvidence(
                "explanation.final_publication",
                "explanation",
                publication_status == PUBLICATION_CONFIRMED,
                publication_status,
                PUBLICATION_CONFIRMED,
                "final publication requires clean close-confirmed inputs",
            ),
        )
    )
    return tuple(evidence)


def build_explanation(input_value: ExplanationInput) -> ExplanationModel:
    """Convert a regime snapshot into a deterministic page-facing model."""

    _require(isinstance(input_value, ExplanationInput), "explanation input must be an ExplanationInput")
    regime = input_value.regime
    indicators = input_value.indicators
    presentation = _STATE_PRESENTATION[regime.state]
    data_quality, indicator_quality, indicator_ready, publication_status, confirmed, confidence = _quality_and_publication(input_value)
    directional_signals = _directional_signals(indicators)
    trend, agreement = _trend_and_agreement(directional_signals)

    reasons = list(regime.reason_codes)
    if indicators is None:
        _append_reason(reasons, "explanation_indicator_missing")
    elif indicators.quality != "OK":
        _append_reason(reasons, f"explanation_indicator_quality_{indicators.quality.lower()}")
    elif not indicators.ready:
        _append_reason(reasons, "explanation_indicator_not_ready")
    if input_value.observation_phase == INTRADAY_PROVISIONAL:
        _append_reason(reasons, "explanation_intraday_provisional")
    if not directional_signals:
        _append_reason(reasons, "explanation_direction_unavailable")
    if confirmed:
        _append_reason(reasons, "explanation_close_confirmed")
    else:
        _append_reason(reasons, "explanation_final_confirmation_pending")

    return ExplanationModel(
        schema=EXPLANATION_SCHEMA,
        explanation_version=EXPLANATION_IMPLEMENTATION_VERSION,
        strategy_version=regime.strategy_version,
        strategy_config_hash=regime.strategy_config_hash,
        regime_version=regime.regime_version,
        indicator_version=regime.indicator_version,
        signal_date=regime.signal_date,
        execution_date=regime.execution_date,
        as_of=regime.as_of,
        calendar_id=regime.calendar_id,
        data_quality=data_quality,
        indicator_quality=indicator_quality,
        indicator_ready=indicator_ready,
        observation_phase=input_value.observation_phase,
        publication_status=publication_status,
        confirmed=confirmed,
        confidence_label=confidence,
        state=regime.state,
        state_label=presentation["label"],
        color_token=presentation["color_token"],
        temperature=presentation["temperature"],
        trend=trend,
        signal_agreement=agreement,
        directional_signals=directional_signals,
        reason_codes=tuple(reasons),
        evidence=_explanation_evidence(input_value, data_quality, publication_status, directional_signals),
        source_regime_snapshot_hash=_sha256(regime.as_dict()),
        source_indicator_snapshot_hash=None if indicators is None else _sha256(indicators.as_dict()),
    )


__all__ = [
    "CLOSE_CONFIRMED",
    "CONFIDENCE_CONFIRMED",
    "CONFIDENCE_PROVISIONAL",
    "CONFIDENCE_UNAVAILABLE",
    "DirectionalSignal",
    "EXPLANATION_IMPLEMENTATION_VERSION",
    "EXPLANATION_SCHEMA",
    "ExplanationError",
    "ExplanationEvidence",
    "ExplanationInput",
    "ExplanationModel",
    "INTRADAY_PROVISIONAL",
    "PUBLICATION_CONFIRMED",
    "PUBLICATION_NEEDS_REVIEW",
    "PUBLICATION_PROVISIONAL",
    "build_explanation",
]
