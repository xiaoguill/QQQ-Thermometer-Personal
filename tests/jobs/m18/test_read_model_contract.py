from __future__ import annotations

import unittest
from dataclasses import replace

from src.jobs.m18.read_model import (
    CONFIRMED,
    FullChainSnapshot,
    M18ReadModelError,
    ModuleStatus,
    PaperPlanView,
    ProvisionalObservation,
    RuntimeBoundary,
    default_module_statuses,
)


class M18ReadModelContractTests(unittest.TestCase):
    def _snapshot(self) -> FullChainSnapshot:
        modules = list(default_module_statuses())
        for module_id, version, dependency in (("M07", "m07-target/v1", "M05"), ("M10", "m10-read/v1", "M08")):
            index = next(index for index, item in enumerate(modules) if item.module_id == module_id)
            modules[index] = ModuleStatus(
                module_id=module_id,
                name=modules[index].name,
                responsibility=modules[index].responsibility,
                status="READY",
                publication=CONFIRMED,
                version=version,
                run_id="run-healthy",
                as_of="2026-08-18T13:00:00Z",
                signal_date="2026-08-17",
                execution_date="2026-08-18",
                quality="OK",
                artifact_ref=f"artifacts/{module_id.lower()}/run-healthy.json",
                depends_on=(dependency,),
            )
        from src.jobs.m18.read_model import ConfirmedStrategy

        confirmed = ConfirmedStrategy(
            status="READY",
            quality="OK",
            as_of="2026-08-18T13:00:00Z",
            signal_date="2026-08-17",
            execution_date="2026-08-18",
            temperature=66.0,
            state="normal",
            strategy_version="v10_preserve_shock_recovery",
            run_id="run-healthy",
            target_weights={"BIL": 0.16, "QQQ": 0.60, "TLT": 0.06, "XLU": 0.04, "IAU": 0.14},
            evidence_ref="run-healthy:m10",
        )
        return FullChainSnapshot(
            run_id="run-healthy",
            run_status="published",
            created_at="2026-08-18T13:00:00Z",
            as_of="2026-08-18T13:00:00Z",
            signal_date="2026-08-17",
            execution_date="2026-08-18",
            strategy_version="v10_preserve_shock_recovery",
            data_version="fixture-quality-v1",
            overall_quality="PARTIAL",
            modules=tuple(modules),
            provisional_observation=ProvisionalObservation(
                status="FAILED",
                quality="FAILED",
                as_of="2026-08-18T12:59:00Z",
                signal_date="2026-08-18",
                temperature=None,
                state=None,
                source_version="m16-massive/v1",
                run_id="m16-1",
                reason_codes=("NOT_ENTITLED",),
                source_symbols=("QQQ", "I:VIX"),
            ),
            confirmed_strategy=confirmed,
            paper_plan=PaperPlanView(
                status="READY",
                signal_date="2026-08-17",
                execution_date="2026-08-18",
                target_weights=confirmed.target_weights,
                current_positions={"QQQ": 10.5, "BIL": 20.0},
                plan_ref="paper:run-healthy",
            ),
            runtime_boundary=RuntimeBoundary(
                source="Massive",
                source_configured=True,
                refresh_interval_seconds=900,
                display_timezone="Asia/Shanghai",
                source_status="DEGRADED",
                last_refresh_at="2026-08-18T12:59:00Z",
                reason_codes=("NOT_ENTITLED",),
            ),
            latest_data_quality=({"module_id": "M16", "status": "FAILED", "reason": "NOT_ENTITLED"},),
            reason_codes=("provisional_unavailable",),
        )

    def test_default_inventory_is_complete_and_ordered(self) -> None:
        modules = default_module_statuses()
        self.assertEqual(tuple(item.module_id for item in modules), tuple([
            "M00.5", "M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08",
            "M09", "M10", "M11", "M12", "M13", "M14", "M15", "M16", "M17",
        ]))
        self.assertTrue(all(item.status == "NOT_RUN" and item.quality == "NOT_RUN" for item in modules))

    def test_round_trip_preserves_confirmed_targets_and_separates_provisional(self) -> None:
        snapshot = self._snapshot()
        payload = snapshot.as_dict()
        self.assertEqual(payload["target_weights"], payload["confirmed_strategy"]["target_weights"])
        self.assertNotIn("target_weights", payload["provisional_observation"])
        restored = FullChainSnapshot.from_dict(payload)
        self.assertEqual(restored.content_hash, snapshot.content_hash)
        self.assertEqual(restored.confirmed_strategy.status, "READY")

    def test_unhealthy_provisional_temperature_is_rejected(self) -> None:
        with self.assertRaises(M18ReadModelError):
            ProvisionalObservation(
                status="FAILED",
                quality="FAILED",
                as_of="2026-08-18T13:00:00Z",
                signal_date="2026-08-18",
                temperature=50.0,
                state="normal",
                source_version="m16-massive/v1",
                run_id="run-1",
            )

    def test_paper_plan_cannot_create_orders(self) -> None:
        with self.assertRaises(M18ReadModelError):
            PaperPlanView(
                status="READY",
                target_weights={"QQQ": 1.0},
                order_created=True,
            )


if __name__ == "__main__":
    unittest.main()
