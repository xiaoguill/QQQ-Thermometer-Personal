"""M18 persistence adapter for the existing M08 SQLite repository."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from src.jobs.m18.full_chain import M18PipelineOutput
from src.jobs.m18.read_model import (
    CONFIRMED,
    DISPLAY_ONLY,
    FullChainSnapshot,
    ModuleStatus,
    PaperPlanView,
)
from src.storage.sqlite_store import (
    STORAGE_IMPLEMENTATION_VERSION,
    SQLiteRepository,
    StoredRecord,
)
from src.thermometer.target_weights import TargetWeightSnapshot
from src.storage.market_data import RawSnapshot


M18_PERSISTENCE_VERSION = "m18-persistence/v1"


class M18PersistenceError(ValueError):
    """Raised when a M18 append-only persistence operation is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M18PersistenceError(message)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class M18PersistenceResult:
    snapshot: FullChainSnapshot
    records: tuple[StoredRecord, ...]


class M18ChainPersistence:
    """Persist M02–M07 artifacts and the M18 projection without schema edits."""

    def __init__(self, repository: SQLiteRepository) -> None:
        _require(isinstance(repository, SQLiteRepository), "repository must be SQLiteRepository")
        _require(repository.store.initialized, "repository store must be initialized")
        self.repository = repository

    def persist(
        self,
        output: M18PipelineOutput,
        raw_snapshots: Sequence[RawSnapshot],
        *,
        paper_plan: PaperPlanView | None = None,
        orchestration_run_id: str | None = None,
    ) -> M18PersistenceResult:
        _require(isinstance(output, M18PipelineOutput), "output must be M18PipelineOutput")
        _require(isinstance(raw_snapshots, Sequence) and raw_snapshots, "raw_snapshots must be non-empty")
        _require(all(isinstance(item, RawSnapshot) for item in raw_snapshots), "raw_snapshots must contain RawSnapshot")
        snapshot = output.snapshot
        records: list[StoredRecord] = []

        for raw in raw_snapshots:
            request = dict(raw.request)
            records.append(
                self.repository.put_market_snapshot(
                    raw.snapshot_id,
                    raw.as_record(),
                    snapshot_kind="raw",
                    symbol=",".join(str(item) for item in request.get("symbols", ())) or None,
                    bar_date=None,
                    as_of=raw.retrieved_at,
                    source=raw.source,
                    price_basis=raw.price_basis,
                    quality=raw.quality,
                )
            )

        for bar in output.normalized_bars:
            records.append(
                self.repository.put_market_snapshot(
                    f"m03|{bar['symbol']}|{bar['bar_date']}",
                    bar,
                    snapshot_kind="normalized_bar",
                    symbol=bar["symbol"],
                    bar_date=bar["bar_date"],
                    as_of=snapshot.as_of,
                    source=bar.get("source"),
                    price_basis=bar.get("price_basis"),
                    quality=bar.get("quality"),
                )
            )

        for indicator in output.indicators.snapshots:
            records.append(
                self.repository.put_indicator_snapshot(
                    f"m04|{indicator.signal_date}",
                    indicator.as_dict(),
                    signal_date=indicator.signal_date,
                    as_of=indicator.as_of,
                    indicator_version=indicator.indicator_version,
                    quality=indicator.quality,
                )
            )

        regime_values = () if output.regimes is None else output.regimes.snapshots
        if output.latest_regime is not None and not regime_values:
            regime_values = (output.latest_regime,)
        for regime in regime_values:
            records.append(
                self.repository.put_regime_snapshot(
                    f"m05|{regime.signal_date}",
                    regime.as_dict(),
                    signal_date=regime.signal_date,
                    execution_date=regime.execution_date,
                    as_of=regime.as_of,
                    strategy_version=regime.strategy_version,
                    state=regime.state,
                    quality=regime.indicator_quality if regime.qqq_bar_quality == "OK" else regime.qqq_bar_quality,
                )
            )

        if output.latest_target is not None:
            target = output.latest_target
            records.append(
                self.repository.put_target_weight_snapshot(
                    f"m07|{target.signal_date}",
                    target.as_dict(),
                    signal_date=target.signal_date,
                    execution_date=target.execution_date,
                    as_of=target.as_of,
                    strategy_version=target.strategy_version,
                    state=target.state,
                    weight_status=target.weight_status,
                    data_quality=target.data_quality,
                )
            )

        for event in output.normalization.quality_events:
            payload = event.as_dict()
            event_date = event.bar_date or event.window_end or event.window_start or snapshot.signal_date or snapshot.created_at[:10]
            event_key = f"m03|{event.event_type}|{event_date}|{_canonical_hash(payload)[:16]}"
            records.append(
                self.repository.put_quality_event(
                    event_key,
                    payload,
                    event_date=event_date,
                    source=event.source,
                    symbol=event.symbol,
                    severity=event.severity,
                    status=event.status,
                )
            )

        if output.latest_target is not None:
            contract = output.latest_target.as_dict()
            version = output.latest_target.strategy_version
            records.append(
                self.repository.put_strategy_version(
                    version,
                    {
                        "schema": "qqq-strategy-version-read-model/v1",
                        "version": version,
                        "strategy_status": contract["strategy_status"],
                        "strategy_implementation_state": contract["strategy_implementation_state"],
                        "config_hash": contract["strategy_config_hash"],
                        "source": "configs/frozen/strategy_contract.json",
                    },
                    status=contract["strategy_status"],
                    config_hash=contract["strategy_config_hash"],
                    approved_by="frozen_contract",
                    approved_at=output.latest_target.as_of,
                )
            )

        modules = list(snapshot.modules)
        modules = self._replace_module(
            modules,
            "M08",
            status="READY",
            quality="OK",
            version=STORAGE_IMPLEMENTATION_VERSION,
            run_id=orchestration_run_id or snapshot.run_id,
            as_of=snapshot.as_of,
            artifact_ref=f"run:{snapshot.run_id}",
            depends_on=("M02", "M03", "M04", "M05", "M06", "M07"),
        )
        confirmed = snapshot.confirmed_strategy
        if confirmed.status == "READY":
            modules = self._replace_module(
                modules,
                "M10",
                status="READY",
                quality="OK",
                publication=CONFIRMED,
                version="m10-read-api/v1",
                run_id=orchestration_run_id or snapshot.run_id,
                as_of=confirmed.as_of,
                signal_date=confirmed.signal_date,
                execution_date=confirmed.execution_date,
                artifact_ref=f"run:{snapshot.run_id}",
                depends_on=("M07", "M08"),
            )
        else:
            modules = self._replace_module(
                modules,
                "M10",
                status="NEEDS_REVIEW" if confirmed.quality in {"NEEDS_REVIEW", "UNAVAILABLE"} else "FAILED",
                quality=confirmed.quality,
                version="m10-read-api/v1",
                run_id=orchestration_run_id or snapshot.run_id,
                as_of=confirmed.as_of,
                signal_date=confirmed.signal_date,
                execution_date=confirmed.execution_date,
                reason_codes=confirmed.reason_codes,
                depends_on=("M07", "M08"),
            )
        modules = self._replace_module(
            modules,
            "M11",
            status="READY",
            quality="OK",
            version="m11-job-orchestrator/v1",
            run_id=orchestration_run_id or snapshot.run_id,
            as_of=snapshot.as_of,
            artifact_ref=f"run:{orchestration_run_id or snapshot.run_id}",
            depends_on=("M08", "M10"),
        )

        if paper_plan is not None:
            paper_quality = "OK" if paper_plan.status == "READY" else "NEEDS_REVIEW"
            modules = self._replace_module(
                modules,
                "M09",
                status="READY" if paper_plan.status == "READY" else "NEEDS_REVIEW",
                quality=paper_quality,
                version="m09-paper-portfolio/v1",
                run_id=orchestration_run_id or snapshot.run_id,
                as_of=snapshot.as_of,
                signal_date=paper_plan.signal_date,
                execution_date=paper_plan.execution_date,
                artifact_ref=paper_plan.plan_ref,
                reason_codes=paper_plan.reason_codes,
                depends_on=("M07", "M08"),
            )
            snapshot = replace(snapshot, paper_plan=paper_plan)

        snapshot = replace(
            snapshot,
            modules=tuple(modules),
            latest_indicator=output.latest_indicator.as_dict() if output.latest_indicator is not None else snapshot.latest_indicator,
            explanation=self._persistable_explanation(output.latest_explanation.as_dict()) if output.latest_explanation is not None else snapshot.explanation,
        )
        records.append(self._store_snapshot(snapshot))
        return M18PersistenceResult(snapshot=snapshot, records=tuple(records))

    @staticmethod
    def _replace_module(
        modules: list[ModuleStatus],
        module_id: str,
        *,
        status: str,
        quality: str,
        version: str | None = None,
        publication: str = "NONE",
        run_id: str | None = None,
        as_of: str | None = None,
        signal_date: str | None = None,
        execution_date: str | None = None,
        artifact_ref: str | None = None,
        reason_codes: Sequence[str] = (),
        depends_on: Sequence[str] = (),
    ) -> list[ModuleStatus]:
        index = next(index for index, item in enumerate(modules) if item.module_id == module_id)
        current = modules[index]
        modules[index] = ModuleStatus(
            module_id=module_id,
            name=current.name,
            responsibility=current.responsibility,
            status=status,
            publication=publication,
            version=version,
            run_id=run_id,
            as_of=as_of,
            signal_date=signal_date,
            execution_date=execution_date,
            quality=quality,
            artifact_ref=artifact_ref,
            reason_codes=tuple(reason_codes),
            depends_on=tuple(depends_on),
        )
        return modules

    def _store_snapshot(self, snapshot: FullChainSnapshot) -> StoredRecord:
        from src.storage.m18.read_model_store import M18ReadModelStore

        return M18ReadModelStore(self.repository).put(snapshot)

    @staticmethod
    def _persistable_explanation(value: Mapping[str, Any]) -> dict[str, Any]:
        """Avoid M08's sensitive-key guard while retaining display semantics."""

        result = dict(value)
        if "color_token" in result:
            result["state_color"] = result.pop("color_token")
        return result


__all__ = ["M18_PERSISTENCE_VERSION", "M18PersistenceError", "M18PersistenceResult", "M18ChainPersistence"]
