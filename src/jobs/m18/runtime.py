"""M18 M11 runtime adapter with paper-only simulation."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from src.jobs.orchestrator import (
    JOB_IMPLEMENTATION_VERSION,
    JobOrchestrator,
    JobRequest,
    JobRunResult,
    JobStages,
    StageContext,
    StageResult,
)
from src.storage.market_data import RawSnapshot
from src.storage.m18.chain_persistence import M18ChainPersistence, M18PersistenceResult
from src.storage.m18.read_model_store import M18ReadModelStore
from src.storage.normalization import TradingCalendar
from src.storage.paper_portfolio import (
    PaperDayInput,
    PaperExecutionConfig,
    PaperPortfolioError,
    PaperPortfolioService,
    PaperPrice,
)
from src.storage.sqlite_store import SQLiteRepository

from .full_chain import M18FullChainPipeline, M18PipelineRequest
from .read_model import FullChainSnapshot, PaperPlanView


class M18RuntimeError(ValueError):
    """Raised when the local M18 runtime request is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M18RuntimeError(message)


def _next_session(calendar: TradingCalendar, signal_date: str) -> str:
    start = date.fromisoformat(signal_date)
    sessions = calendar.sessions(signal_date, (start + timedelta(days=14)).isoformat())
    _require(len(sessions) >= 2 and sessions[0] == signal_date, "no next trading session after signal date")
    return sessions[1]


def _signal_date(raw_snapshots: Sequence[RawSnapshot]) -> str:
    candidates = []
    for snapshot in raw_snapshots:
        request = dict(snapshot.request)
        if "QQQ" in tuple(request.get("symbols", ())):
            candidates.append(str(request["end_date"]))
    if not candidates:
        candidates = [str(snapshot.request["end_date"]) for snapshot in raw_snapshots]
    return max(candidates)


def _quality_reason(output: Any) -> tuple[str, ...]:
    snapshot = getattr(output, "snapshot", None)
    if snapshot is None:
        return ()
    values = list(snapshot.reason_codes)
    values.append(f"data_quality:{snapshot.overall_quality}")
    return tuple(dict.fromkeys(values))


def _stage_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Make stage records acceptable to M08's sensitive-field guard."""

    result = copy.deepcopy(dict(payload))
    explanation = result.get("explanation")
    if isinstance(explanation, Mapping) and "color_token" in explanation:
        explanation = dict(explanation)
        explanation["state_color"] = explanation.pop("color_token")
        result["explanation"] = explanation
    return result


@dataclass(frozen=True)
class M18RuntimeResult:
    job: JobRunResult
    snapshot: FullChainSnapshot | None
    paper_plan: PaperPlanView | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job.as_dict(),
            "snapshot": None if self.snapshot is None else self.snapshot.as_dict(),
            "paper_plan": None if self.paper_plan is None else self.paper_plan.as_dict(),
        }


class M18RuntimeService:
    """Run M18 through the existing four-stage M11 orchestrator."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        pipeline: M18FullChainPipeline | None = None,
        calendar: TradingCalendar | None = None,
        paper_config: PaperExecutionConfig | None = None,
        portfolio_id: str = "m18-paper",
    ) -> None:
        _require(isinstance(repository, SQLiteRepository), "repository must be SQLiteRepository")
        _require(repository.store.initialized, "repository store must be initialized")
        self.repository = repository
        self.calendar = calendar or TradingCalendar()
        self.pipeline = pipeline or M18FullChainPipeline(calendar=self.calendar)
        self.paper_config = paper_config or PaperExecutionConfig()
        _require(isinstance(portfolio_id, str) and portfolio_id.strip() and "|" not in portfolio_id, "portfolio_id is invalid")
        self.portfolio_id = portfolio_id.strip()
        self.paper = PaperPortfolioService(repository, config=self.paper_config, calendar=self.calendar)
        self.persistence = M18ChainPersistence(repository)
        self.read_model = M18ReadModelStore(repository)
        self._requests: dict[str, M18PipelineRequest] = {}
        self._outputs: dict[str, Any] = {}
        self._paper_plans: dict[str, PaperPlanView] = {}
        self._published: dict[str, M18PersistenceResult] = {}
        self.orchestrator = JobOrchestrator(
            repository,
            JobStages(
                refresh=self._refresh,
                calculate=self._calculate,
                simulate=self._simulate,
                publish=self._publish,
            ),
        )

    def run(self, request: M18PipelineRequest, *, idempotency_key: str | None = None) -> M18RuntimeResult:
        _require(isinstance(request, M18PipelineRequest), "request must be M18PipelineRequest")
        key = idempotency_key or f"m18|{request.run_id}"
        _require(isinstance(key, str) and key.strip(), "idempotency_key is invalid")
        signal = _signal_date(request.raw_snapshots)
        execution = _next_session(self.calendar, signal)
        manifest = {
            snapshot.snapshot_id: snapshot.payload_sha256 or snapshot.snapshot_id
            for snapshot in request.raw_snapshots
        }
        job_request = JobRequest(
            idempotency_key=key.strip(),
            strategy_version="v10_preserve_shock_recovery",
            data_version=request.data_version,
            signal_date=signal,
            execution_date=execution,
            as_of=request.as_of,
            input_manifest=manifest,
        )
        self._requests[job_request.idempotency_key] = request
        job = self.orchestrator.run(job_request)
        published = self._published.get(job.run_id)
        snapshot = published.snapshot if published is not None else self.read_model.get(job.run_id)
        paper_plan = self._paper_plans.get(job.run_id)
        return M18RuntimeResult(job=job, snapshot=snapshot, paper_plan=paper_plan)

    def status(self, *, run_id: str | None = None, idempotency_key: str | None = None) -> M18RuntimeResult:
        job = self.orchestrator.get_run_status(run_id=run_id, idempotency_key=idempotency_key)
        published = self._published.get(job.run_id)
        snapshot = published.snapshot if published is not None else self.read_model.get(job.run_id)
        return M18RuntimeResult(job=job, snapshot=snapshot, paper_plan=self._paper_plans.get(job.run_id))

    def _request_for(self, context: StageContext) -> M18PipelineRequest:
        request = self._requests.get(context.request.idempotency_key)
        _require(request is not None, "M18 request context is unavailable")
        return request

    def _refresh(self, context: StageContext) -> StageResult:
        request = self._request_for(context)
        quality = ",".join(sorted({item.quality for item in request.raw_snapshots}))
        return StageResult(
            stage="refresh",
            quality="OK",
            manifest=context.request.input_manifest,
            snapshot={"snapshot_count": len(request.raw_snapshots), "source_qualities": quality},
            reason_codes=tuple(f"raw_quality:{item.quality}" for item in request.raw_snapshots if item.quality != "OK"),
        )

    def _calculate(self, context: StageContext) -> StageResult:
        request = replace(self._request_for(context), run_id=context.run_id)
        output = self.pipeline.run(request)
        self._outputs[context.run_id] = output
        return StageResult(
            stage="calculate",
            quality="OK",
            manifest=context.request.input_manifest,
            snapshot=_stage_snapshot(output.snapshot.as_dict()),
            reason_codes=_quality_reason(output),
        )

    def _simulate(self, context: StageContext) -> StageResult:
        output = self._outputs.get(context.run_id)
        _require(output is not None, "calculate output is unavailable")
        plan = self._simulate_paper(output, context.run_id)
        self._paper_plans[context.run_id] = plan
        return StageResult(
            stage="simulate",
            quality="OK",
            manifest=context.request.input_manifest,
            snapshot={"paper_plan": plan.as_dict()},
            reason_codes=plan.reason_codes,
        )

    def _publish(self, context: StageContext) -> StageResult:
        output = self._outputs.get(context.run_id)
        _require(output is not None, "calculate output is unavailable")
        request = self._request_for(context)
        plan = self._paper_plans.get(context.run_id)
        persisted = self.persistence.persist(
            output,
            request.raw_snapshots,
            paper_plan=plan,
            orchestration_run_id=context.run_id,
        )
        self._published[context.run_id] = persisted
        return StageResult(
            stage="publish",
            quality="OK",
            manifest=context.request.input_manifest,
            snapshot=_stage_snapshot(persisted.snapshot.as_dict()),
            reason_codes=_quality_reason(persisted),
        )

    def _simulate_paper(self, output: Any, run_id: str) -> PaperPlanView:
        target = output.latest_target
        if target is None or output.snapshot.confirmed_strategy.status != "READY":
            return PaperPlanView(status="NOT_GENERATED", reason_codes=("confirmed_target_unavailable",))

        bars = {
            str(bar["symbol"]): bar
            for bar in output.normalized_bars
            if bar.get("bar_date") == target.execution_date and float(target.target_weights.get(str(bar.get("symbol")), 0.0)) > 0.0
        }
        prices: dict[str, PaperPrice] = {}
        for symbol, bar in bars.items():
            if bar.get("price_basis") != self.paper_config.price_basis or bar.get("quality") != "OK":
                continue
            prices[symbol] = PaperPrice(
                symbol=symbol,
                session_date=target.execution_date,
                price=float(bar["close"]),
                price_basis=str(bar["price_basis"]),
                quality=str(bar["quality"]),
            )
        missing = sorted(symbol for symbol, weight in target.target_weights.items() if weight > 0.0 and symbol not in prices)
        plan_ref = f"paper:{self.portfolio_id}:{target.execution_date}"
        if missing:
            return PaperPlanView(
                status="NEEDS_REVIEW",
                signal_date=target.signal_date,
                execution_date=target.execution_date,
                target_weights=target.target_weights,
                current_positions={},
                plan_ref=plan_ref,
                reason_codes=("execution_prices_missing", *tuple(f"missing_price:{item}" for item in missing)),
            )
        try:
            result = self.paper.simulate_day(
                PaperDayInput.from_prices(
                    self.portfolio_id,
                    run_id,
                    target,
                    prices,
                    as_of=target.as_of,
                )
            )
        except PaperPortfolioError as exc:
            return PaperPlanView(
                status="NEEDS_REVIEW",
                signal_date=target.signal_date,
                execution_date=target.execution_date,
                target_weights=target.target_weights,
                current_positions={},
                plan_ref=plan_ref,
                reason_codes=("paper_simulation_failed", str(exc)[:160]),
            )
        return PaperPlanView(
            status="READY" if result.state.data_quality == "OK" else "NEEDS_REVIEW",
            signal_date=target.signal_date,
            execution_date=target.execution_date,
            target_weights=target.target_weights,
            current_positions=result.state.positions,
            plan_ref=plan_ref,
            reason_codes=("paper_simulation_only", "order_created_false", "idempotent_replay" if result.idempotent else "paper_ledger_appended"),
        )


__all__ = ["M18RuntimeError", "M18RuntimeResult", "M18RuntimeService"]
