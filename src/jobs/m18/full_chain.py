"""M18 orchestration of the existing M02–M07 pure services.

The pipeline accepts already captured M02 raw snapshots. It never calls a
provider, changes the frozen contract, or calculates an alternative signal.
Its only new decision is whether the existing outputs are healthy enough to
publish as ``CONFIRMED``; otherwise diagnostic outputs remain visible and the
confirmed target is empty.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.storage.indicators import IndicatorRun, IndicatorSnapshot, calculate_indicator_snapshots
from src.storage.market_data import RawSnapshot
from src.storage.normalization import NormalizationResult, QUALITY_STATUSES, TradingCalendar, normalize_snapshots
from src.thermometer.contracts import StrategyContractRegistry, load_contract
from src.thermometer.explanation import (
    CLOSE_CONFIRMED,
    ExplanationInput,
    ExplanationModel,
    build_explanation,
)
from src.thermometer.regime import (
    RegimeConfig,
    RegimeInput,
    RegimeRun,
    RegimeSnapshot,
    RegimeState,
    evaluate_regime,
    replay_regimes,
)
from src.thermometer.target_weights import TargetWeightSnapshot, build_target_weights

from .read_model import (
    CONFIRMED,
    DISPLAY_ONLY,
    M18_READ_MODEL_VERSION,
    NONE_PUBLICATION,
    PROVISIONAL,
    ConfirmedStrategy,
    FullChainSnapshot,
    ModuleStatus,
    PaperPlanView,
    ProvisionalObservation,
    RuntimeBoundary,
    QUALITY_VALUES,
    default_module_statuses,
)


M18_PIPELINE_VERSION = "m18-pipeline/v1"
_QUALITY_PRIORITY = {
    "OK": 0,
    "PARTIAL": 1,
    "NOT_RUN": 1,
    "STALE": 2,
    "FAILED": 3,
    "UNAVAILABLE": 3,
    "NEEDS_REVIEW": 4,
}


class M18PipelineError(ValueError):
    """Raised when M18 input or publication invariants fail."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M18PipelineError(message)


def _text(value: Any, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field_name} must be non-empty")
    return value.strip()


def _utc_timestamp(value: Any, field_name: str) -> str:
    candidate = _text(value, field_name).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise M18PipelineError(f"{field_name} must be an ISO timestamp") from exc
    _require(parsed.tzinfo is not None, f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _quality_max(*values: str | None) -> str:
    present = [value for value in values if value]
    _require(all(value in QUALITY_VALUES or value in QUALITY_STATUSES for value in present), "unknown quality value")
    return max(present, key=lambda value: _QUALITY_PRIORITY[value]) if present else "OK"


def _module_status_for_quality(quality: str, *, ready: bool = True) -> str:
    if quality == "NOT_RUN":
        return "NOT_RUN"
    if quality == "OK":
        return "READY" if ready else "DEGRADED"
    if quality == "PARTIAL":
        return "PARTIAL"
    if quality == "STALE":
        return "STALE"
    if quality == "FAILED":
        return "FAILED"
    if quality == "NEEDS_REVIEW":
        return "NEEDS_REVIEW"
    return "FAILED"


def _reason_codes(*values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for collection in values:
        for value in collection:
            if value not in result:
                result.append(str(value))
    return tuple(result)


def _raw_quality(snapshots: Sequence[RawSnapshot]) -> str:
    return _quality_max(*(snapshot.quality for snapshot in snapshots))


def _request_group_key(snapshot: RawSnapshot) -> str:
    """Keep M03 request semantics separate so M04 can join them safely."""

    return json.dumps(dict(snapshot.request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_request_groups(
    snapshots: Sequence[RawSnapshot],
    *,
    as_of: str,
    calendar: TradingCalendar,
) -> tuple[NormalizationResult, tuple[NormalizationResult, ...]]:
    groups: dict[str, list[RawSnapshot]] = {}
    for snapshot in snapshots:
        groups.setdefault(_request_group_key(snapshot), []).append(snapshot)
    results = tuple(
        normalize_snapshots(group, as_of=as_of, calendar=calendar)
        for group in (groups[key] for key in sorted(groups))
    )
    _require(results, "M03 produced no normalization results")
    quality = _quality_max(*(result.quality for result in results))
    bars = tuple(sorted((bar for result in results for bar in result.bars), key=lambda item: (item.symbol, item.bar_date)))
    events = tuple(sorted((event for result in results for event in result.quality_events), key=lambda item: item.as_dict()["event_type"]))
    reference = results[0]
    combined = NormalizationResult(
        as_of=reference.as_of,
        calendar_id=reference.calendar_id,
        normalization_version=reference.normalization_version,
        quality=quality,
        bars=bars,
        quality_events=events,
    )
    return combined, results


def _default_runtime() -> RuntimeBoundary:
    return RuntimeBoundary(
        source="Massive",
        source_configured=False,
        refresh_interval_seconds=900,
        display_timezone="Asia/Shanghai",
        source_status="NOT_CONFIGURED",
        last_refresh_at=None,
        reason_codes=("m16_observation_not_supplied",),
    )


def _default_provisional() -> ProvisionalObservation:
    return ProvisionalObservation(
        status="NOT_RUN",
        quality="NOT_RUN",
        as_of=None,
        signal_date=None,
        temperature=None,
        state=None,
        source_version=None,
        run_id=None,
        reason_codes=("m16_observation_not_supplied",),
    )


@dataclass(frozen=True)
class M18PipelineRequest:
    """Pure M18 input; raw snapshots must already have passed M02 validation."""

    run_id: str
    as_of: str
    data_version: str
    raw_snapshots: tuple[RawSnapshot, ...]
    runtime_boundary: RuntimeBoundary | None = None
    provisional_observation: ProvisionalObservation | None = None

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _utc_timestamp(self.as_of, "as_of")
        _text(self.data_version, "data_version")
        _require(isinstance(self.raw_snapshots, tuple) and self.raw_snapshots, "raw_snapshots must be a non-empty tuple")
        _require(all(isinstance(item, RawSnapshot) for item in self.raw_snapshots), "raw_snapshots must contain RawSnapshot objects")
        _require(self.runtime_boundary is None or isinstance(self.runtime_boundary, RuntimeBoundary), "invalid runtime_boundary")
        _require(self.provisional_observation is None or isinstance(self.provisional_observation, ProvisionalObservation), "invalid provisional_observation")
        object.__setattr__(self, "as_of", _utc_timestamp(self.as_of, "as_of"))


@dataclass(frozen=True)
class M18PipelineOutput:
    """Diagnostic output plus the page-facing immutable snapshot."""

    snapshot: FullChainSnapshot
    normalization: NormalizationResult
    indicators: IndicatorRun
    regimes: RegimeRun | None
    latest_indicator: IndicatorSnapshot | None
    latest_regime: RegimeSnapshot | None
    latest_explanation: ExplanationModel | None
    latest_target: TargetWeightSnapshot | None
    normalized_bars: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": M18_PIPELINE_VERSION,
            "snapshot": self.snapshot.as_dict(),
            "normalization": self.normalization.as_dict(),
            "indicators": self.indicators.as_dict(),
            "regimes": None if self.regimes is None else self.regimes.as_dict(),
            "latest_indicator": None if self.latest_indicator is None else self.latest_indicator.as_dict(),
            "latest_regime": None if self.latest_regime is None else self.latest_regime.as_dict(),
            "latest_explanation": None if self.latest_explanation is None else self.latest_explanation.as_dict(),
            "latest_target": None if self.latest_target is None else self.latest_target.as_dict(),
            "normalized_bars": [copy.deepcopy(dict(item)) for item in self.normalized_bars],
        }


class M18FullChainPipeline:
    """Call the frozen M02–M07 services in their declared order."""

    def __init__(
        self,
        *,
        registry: StrategyContractRegistry | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self.registry = registry or load_contract()
        self.calendar = calendar or TradingCalendar()
        self.regime_config = RegimeConfig.from_registry(self.registry)

    def run(self, request: M18PipelineRequest) -> M18PipelineOutput:
        if not isinstance(request, M18PipelineRequest):
            raise M18PipelineError("request must be M18PipelineRequest")

        normalization, normalization_results = _normalize_request_groups(
            request.raw_snapshots,
            as_of=request.as_of,
            calendar=self.calendar,
        )
        indicators = calculate_indicator_snapshots(normalization_results, calendar=self.calendar)
        latest_indicator = indicators.snapshots[-1] if indicators.snapshots else None
        qqq_bars = {bar.bar_date: bar for bar in normalization.bars if bar.symbol == "QQQ"}
        latest_bar = None if latest_indicator is None else qqq_bars.get(latest_indicator.signal_date)

        regimes: RegimeRun | None = None
        latest_regime: RegimeSnapshot | None = None
        if latest_indicator is not None and latest_bar is not None:
            current_input = RegimeInput(latest_indicator, latest_bar)
            if indicators.quality == "OK":
                inputs = tuple(
                    RegimeInput(snapshot, qqq_bars[snapshot.signal_date])
                    for snapshot in indicators.snapshots
                    if snapshot.signal_date in qqq_bars
                )
                if inputs and len(inputs) == len(indicators.snapshots):
                    regimes = replay_regimes(inputs, config=self.regime_config, calendar=self.calendar)
                    latest_regime = regimes.snapshots[-1]
            if latest_regime is None:
                latest_regime = evaluate_regime(
                    current_input,
                    config=self.regime_config,
                    calendar=self.calendar,
                    previous_state=None,
                    prior_inputs=(),
                )

        latest_explanation: ExplanationModel | None = None
        latest_target: TargetWeightSnapshot | None = None
        if latest_regime is not None:
            latest_explanation = build_explanation(ExplanationInput(latest_regime, latest_indicator, CLOSE_CONFIRMED))
            latest_target = build_target_weights(latest_regime, registry=self.registry, calendar=self.calendar)

        provisional = request.provisional_observation or _default_provisional()
        runtime = request.runtime_boundary or _default_runtime()
        modules = self._module_statuses(
            request,
            normalization,
            indicators,
            latest_indicator,
            regimes,
            latest_regime,
            latest_explanation,
            latest_target,
            provisional,
        )
        confirmed = self._confirmed_strategy(request.run_id, latest_regime, latest_explanation, latest_target)
        paper_plan = PaperPlanView(
            status="NOT_GENERATED",
            reason_codes=("m09_paper_plan_not_supplied",),
        )
        quality = _quality_max(
            _raw_quality(request.raw_snapshots),
            normalization.quality,
            indicators.quality,
            None if latest_regime is None else latest_regime.indicator_quality,
            "PARTIAL" if provisional.quality == "NOT_RUN" else provisional.quality,
        )
        signal_date = None if latest_regime is None else latest_regime.signal_date
        execution_date = None if latest_regime is None else latest_regime.execution_date
        strategy_version = None if confirmed.status != "READY" else confirmed.strategy_version
        reason_codes = list(normalization_event.event_type for normalization_event in normalization.quality_events)
        if not reason_codes and latest_indicator is not None:
            reason_codes.extend(latest_indicator.reasons)
        if confirmed.status != "READY":
            reason_codes.append("confirmed_strategy_unavailable")
        snapshot = FullChainSnapshot(
            run_id=request.run_id,
            run_status="published",
            created_at=request.as_of,
            as_of=request.as_of,
            signal_date=signal_date,
            execution_date=execution_date,
            strategy_version=strategy_version,
            data_version=request.data_version,
            overall_quality=quality,
            modules=tuple(modules),
            provisional_observation=provisional,
            confirmed_strategy=confirmed,
            paper_plan=paper_plan,
            runtime_boundary=runtime,
            latest_data_quality=tuple(event.as_dict() for event in normalization.quality_events),
            reason_codes=_reason_codes((str(value) for value in reason_codes),),
            latest_indicator=None if latest_indicator is None else latest_indicator.as_dict(),
            explanation=None if latest_explanation is None else latest_explanation.as_dict(),
        )
        return M18PipelineOutput(
            snapshot=snapshot,
            normalization=normalization,
            indicators=indicators,
            regimes=regimes,
            latest_indicator=latest_indicator,
            latest_regime=latest_regime,
            latest_explanation=latest_explanation,
            latest_target=latest_target,
            normalized_bars=tuple(bar.as_dict() for bar in normalization.bars),
        )

    def _confirmed_strategy(
        self,
        run_id: str,
        regime: RegimeSnapshot | None,
        explanation: ExplanationModel | None,
        target: TargetWeightSnapshot | None,
    ) -> ConfirmedStrategy:
        if regime is None or explanation is None or target is None:
            return ConfirmedStrategy(
                status="UNAVAILABLE",
                quality="UNAVAILABLE",
                as_of=None,
                signal_date=None,
                execution_date=None,
                temperature=None,
                state=None,
                strategy_version=None,
                run_id=None,
                target_weights={},
                reason_codes=("m04_to_m07_output_missing",),
            )
        if not (regime.confirmed and explanation.confirmed and target.data_quality == "OK"):
            reasons = _reason_codes(
                regime.reason_codes,
                explanation.reason_codes,
                target.change_reason_codes,
                ("formal_confirmation_pending",),
            )
            quality = _quality_max(target.data_quality, explanation.data_quality, regime.indicator_quality, regime.qqq_bar_quality)
            return ConfirmedStrategy(
                status="NEEDS_REVIEW" if quality == "NEEDS_REVIEW" else "UNAVAILABLE",
                quality=quality,
                as_of=regime.as_of,
                signal_date=regime.signal_date,
                execution_date=regime.execution_date,
                temperature=None,
                state=regime.state,
                strategy_version=None,
                run_id=None,
                target_weights={},
                reason_codes=reasons,
            )
        return ConfirmedStrategy(
            status="READY",
            quality="OK",
            as_of=regime.as_of,
            signal_date=regime.signal_date,
            execution_date=regime.execution_date,
            temperature=float(explanation.temperature),
            state=regime.state,
            strategy_version=target.strategy_version,
            run_id=run_id,
            target_weights=target.target_weights,
            reason_codes=(),
        )

    def _module_statuses(
        self,
        request: M18PipelineRequest,
        normalization: NormalizationResult,
        indicators: IndicatorRun,
        latest_indicator: IndicatorSnapshot | None,
        regimes: RegimeRun | None,
        latest_regime: RegimeSnapshot | None,
        explanation: ExplanationModel | None,
        target: TargetWeightSnapshot | None,
        provisional: ProvisionalObservation,
    ) -> tuple[ModuleStatus, ...]:
        modules = {item.module_id: item for item in default_module_statuses()}
        common = {
            "run_id": request.run_id,
            "as_of": request.as_of,
        }
        modules["M00.5"] = ModuleStatus(
            module_id="M00.5", name=modules["M00.5"].name, responsibility=modules["M00.5"].responsibility,
            status="READY", quality="OK", version="verification-baseline-v3.29", **common,
        )
        modules["M01"] = ModuleStatus(
            module_id="M01", name=modules["M01"].name, responsibility=modules["M01"].responsibility,
            status="READY", quality="OK", version=self.regime_config.strategy_version, **common,
        )
        raw_quality = _raw_quality(request.raw_snapshots)
        modules["M02"] = ModuleStatus(
            module_id="M02", name=modules["M02"].name, responsibility=modules["M02"].responsibility,
            status=_module_status_for_quality(raw_quality), quality=raw_quality, version="m02-raw-snapshot/v1",
            reason_codes=_reason_codes(tuple(snapshot.error_code for snapshot in request.raw_snapshots if snapshot.error_code)), **common,
        )
        modules["M03"] = ModuleStatus(
            module_id="M03", name=modules["M03"].name, responsibility=modules["M03"].responsibility,
            status=_module_status_for_quality(normalization.quality, ready=bool(normalization.bars)),
            quality=normalization.quality, version=normalization.normalization_version,
            reason_codes=_reason_codes(tuple(event.event_type for event in normalization.quality_events)), **common,
        )
        indicator_ready = latest_indicator is not None and latest_indicator.ready
        modules["M04"] = ModuleStatus(
            module_id="M04", name=modules["M04"].name, responsibility=modules["M04"].responsibility,
            status=_module_status_for_quality(indicators.quality, ready=indicator_ready), quality=indicators.quality,
            version=indicators.indicator_version, reason_codes=indicators.reasons, **common,
            signal_date=None if latest_indicator is None else latest_indicator.signal_date,
        )
        regime_quality = "FAILED" if latest_regime is None else _quality_max(latest_regime.indicator_quality, latest_regime.qqq_bar_quality)
        regime_ready = latest_regime is not None and latest_regime.confirmed
        modules["M05"] = ModuleStatus(
            module_id="M05", name=modules["M05"].name, responsibility=modules["M05"].responsibility,
            status=_module_status_for_quality(regime_quality, ready=regime_ready), quality=regime_quality,
            version="m05-regime/v1", reason_codes=() if latest_regime is None else latest_regime.reason_codes, **common,
            signal_date=None if latest_regime is None else latest_regime.signal_date,
            execution_date=None if latest_regime is None else latest_regime.execution_date,
        )
        explanation_quality = "FAILED" if explanation is None else explanation.data_quality
        modules["M06"] = ModuleStatus(
            module_id="M06", name=modules["M06"].name, responsibility=modules["M06"].responsibility,
            status=_module_status_for_quality(explanation_quality, ready=explanation is not None and explanation.confirmed),
            quality=explanation_quality, version=None if explanation is None else explanation.explanation_version,
            reason_codes=() if explanation is None else explanation.reason_codes, **common,
            signal_date=None if explanation is None else explanation.signal_date,
            execution_date=None if explanation is None else explanation.execution_date,
        )
        target_quality = "FAILED" if target is None else target.data_quality
        modules["M07"] = ModuleStatus(
            module_id="M07", name=modules["M07"].name, responsibility=modules["M07"].responsibility,
            status=_module_status_for_quality(target_quality, ready=target is not None and target.data_quality == "OK" and regime_ready),
            quality=target_quality, version=None if target is None else target.implementation_version,
            reason_codes=() if target is None else target.change_reason_codes, **common,
            signal_date=None if target is None else target.signal_date,
            execution_date=None if target is None else target.execution_date,
        )
        for module_id, version in (("M12", "m12-frontend-shell/v1"), ("M13", "m13-dashboard/v1"), ("M14", "m14-replay-audit/v1"), ("M15", "m15-private-release/v1"), ("M17", "m17-unified-portal/v2")):
            modules[module_id] = ModuleStatus(
                module_id=module_id, name=modules[module_id].name, responsibility=modules[module_id].responsibility,
                status="READY", quality="OK", publication=DISPLAY_ONLY, version=version, **common,
            )
        modules["M16"] = ModuleStatus(
            module_id="M16", name=modules["M16"].name, responsibility=modules["M16"].responsibility,
            status=provisional.status, quality=provisional.quality, publication=PROVISIONAL,
            version=provisional.source_version, run_id=provisional.run_id, as_of=provisional.as_of,
            signal_date=provisional.signal_date, reason_codes=provisional.reason_codes, **({} if provisional.as_of is None else {}),
        )
        return tuple(modules[module_id] for module_id in default_module_statuses_ids())


def default_module_statuses_ids() -> tuple[str, ...]:
    """Internal helper kept separate to make the inventory order explicit."""

    return tuple(item.module_id for item in default_module_statuses())


__all__ = [
    "M18_PIPELINE_VERSION",
    "M18PipelineError",
    "M18PipelineOutput",
    "M18PipelineRequest",
    "M18FullChainPipeline",
]
