from __future__ import annotations

import unittest

from src.jobs.m18.full_chain import M18FullChainPipeline, M18PipelineRequest
from src.jobs.m18.read_model import ProvisionalObservation
from src.storage.market_data import MarketDataRequest, RawSnapshot
from src.storage.normalization import TradingCalendar


class M18FullChainPipelineTests(unittest.TestCase):
    calendar = TradingCalendar()

    def _snapshots(self, *, include_failed_indices: bool = False) -> tuple[RawSnapshot, ...]:
        sessions = self.calendar.sessions("2020-01-02", "2020-09-30")[:160]
        as_of = f"{sessions[-1]}T20:00:00Z"
        etf_request = MarketDataRequest(
            source="m18-fixture-etf",
            symbols=("QQQ",),
            start_date=sessions[0],
            end_date=sessions[-1],
            price_basis="adjusted_ohlcv",
            timezone="America/New_York",
        )
        index_request = MarketDataRequest(
            source="m18-fixture-index",
            symbols=("VIX", "VIX3M"),
            start_date=sessions[0],
            end_date=sessions[-1],
            price_basis="index_level",
            timezone="America/New_York",
        )
        qqq_rows = []
        index_rows = []
        for index, day in enumerate(sessions):
            qqq_close = 100.0 + index
            qqq_rows.append({"symbol": "QQQ", "date": day, "open": qqq_close, "high": qqq_close + 1.0, "low": qqq_close - 1.0, "close": qqq_close, "volume": 1000.0})
            index_rows.extend(
                [
                    {"symbol": "VIX", "date": day, "open": 20.0, "high": 20.1, "low": 19.9, "close": 20.0, "volume": 1000.0},
                    {"symbol": "VIX3M", "date": day, "open": 25.0, "high": 25.1, "low": 24.9, "close": 25.0, "volume": 1000.0},
                ]
            )
        qqq = RawSnapshot.capture(source=etf_request.source, request=etf_request, retrieved_at=as_of, payload={"bars": qqq_rows})
        if include_failed_indices:
            indices = RawSnapshot.failed(
                source=index_request.source,
                request=index_request,
                retrieved_at=as_of,
                error_code="NOT_ENTITLED",
                error_message="fixture index entitlement failure",
            )
        else:
            indices = RawSnapshot.capture(source=index_request.source, request=index_request, retrieved_at=as_of, payload={"bars": index_rows})
        return qqq, indices

    def test_pipeline_calls_m02_to_m07_and_publishes_only_confirmed_output(self) -> None:
        output = M18FullChainPipeline(calendar=self.calendar).run(
            M18PipelineRequest(
                run_id="m18-healthy",
                as_of="2020-08-19T20:00:00Z",
                data_version="m18-fixture-v1",
                raw_snapshots=self._snapshots(),
            )
        )

        self.assertEqual(output.normalization.quality, "OK")
        self.assertTrue(output.indicators.ready)
        self.assertIsNotNone(output.regimes)
        self.assertIsNotNone(output.latest_explanation)
        self.assertIsNotNone(output.latest_target)
        self.assertEqual(output.snapshot.confirmed_strategy.status, "READY")
        self.assertAlmostEqual(sum(output.snapshot.target_weights.values()), 1.0)
        self.assertEqual(output.snapshot.modules[6].module_id, "M06")
        self.assertEqual(output.snapshot.modules[10].status, "NOT_RUN")  # M10 is wired in M18.3.

    def test_failed_source_is_visible_and_cannot_publish_confirmed_targets(self) -> None:
        output = M18FullChainPipeline(calendar=self.calendar).run(
            M18PipelineRequest(
                run_id="m18-failed-source",
                as_of="2020-08-19T20:00:00Z",
                data_version="m18-fixture-failed-v1",
                raw_snapshots=self._snapshots(include_failed_indices=True),
            )
        )

        self.assertEqual(output.snapshot.modules[2].quality, "FAILED")
        self.assertIn(output.snapshot.modules[3].status, {"FAILED", "NEEDS_REVIEW"})
        self.assertNotEqual(output.snapshot.confirmed_strategy.status, "READY")
        self.assertEqual(output.snapshot.target_weights, {})
        self.assertIn("confirmed_strategy_unavailable", output.snapshot.reason_codes)

    def test_provisional_observation_never_changes_confirmed_target(self) -> None:
        base = M18PipelineRequest(
            run_id="m18-provisional-base",
            as_of="2020-08-19T20:00:00Z",
            data_version="m18-fixture-v1",
            raw_snapshots=self._snapshots(),
        )
        provisional = ProvisionalObservation(
            status="READY",
            quality="OK",
            as_of="2020-09-30T20:01:00Z",
            signal_date="2020-09-30",
            temperature=73.0,
            state="normal",
            source_version="m16-fixture/v1",
            run_id="m16-observation-1",
        )
        with_provisional = M18PipelineRequest(
            run_id="m18-provisional-live",
            as_of=base.as_of,
            data_version=base.data_version,
            raw_snapshots=base.raw_snapshots,
            provisional_observation=provisional,
        )
        first = M18FullChainPipeline(calendar=self.calendar).run(base)
        second = M18FullChainPipeline(calendar=self.calendar).run(with_provisional)
        self.assertEqual(first.snapshot.target_weights, second.snapshot.target_weights)
        self.assertEqual(second.snapshot.provisional_observation.temperature, 73.0)
        self.assertEqual(second.snapshot.modules[16].publication, "PROVISIONAL")


if __name__ == "__main__":
    unittest.main()
