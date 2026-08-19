import unittest

from src.jobs.m18.v12_2_walk_forward import (
    CausalMarketDataset,
    ReplayConfig,
    _next_session,
    build_prefix_snapshots,
)
from src.storage.normalization import TradingCalendar


class V122WalkForwardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = TradingCalendar()
        dates = ("2025-01-02", "2025-01-03", "2025-01-06")
        self.dataset = CausalMarketDataset(
            prices={
                "QQQ": {day: 100.0 + index for index, day in enumerate(dates)},
                "BIL": {day: 90.0 + index * 0.01 for index, day in enumerate(dates)},
            },
            vix={day: 15.0 + index for index, day in enumerate(dates)},
            vix3m={day: 17.0 + index for index, day in enumerate(dates)},
            vxx={day: 20.0 - index * 0.1 for index, day in enumerate(dates)},
            source_manifest=(),
            data_version="test-data",
            signal_context_start=dates[0],
            signal_end=dates[-1],
        )

    def test_prefix_request_and_payload_stop_at_signal_date(self) -> None:
        snapshots = build_prefix_snapshots(self.dataset, signal_date="2025-01-03", calendar=self.calendar)
        for snapshot in snapshots:
            self.assertEqual(snapshot.request["end_date"], "2025-01-03")
            self.assertTrue(all(row["date"] <= "2025-01-03" for row in snapshot.payload["bars"]))

    def test_future_rows_do_not_change_an_earlier_prefix(self) -> None:
        extended = CausalMarketDataset(
            prices={
                **self.dataset.prices,
                "QQQ": {**self.dataset.prices["QQQ"], "2025-01-07": 140.0},
                "BIL": {**self.dataset.prices["BIL"], "2025-01-07": 90.04},
            },
            vix={**self.dataset.vix, "2025-01-07": 40.0},
            vix3m={**self.dataset.vix3m, "2025-01-07": 18.0},
            vxx={**self.dataset.vxx, "2025-01-07": 25.0},
            source_manifest=(),
            data_version="test-data-extended",
            signal_context_start=self.dataset.signal_context_start,
            signal_end="2025-01-07",
        )
        base_snapshots = build_prefix_snapshots(self.dataset, signal_date="2025-01-03", calendar=self.calendar)
        extended_snapshots = build_prefix_snapshots(extended, signal_date="2025-01-03", calendar=self.calendar)
        self.assertEqual(
            [snapshot.payload_sha256 for snapshot in base_snapshots],
            [snapshot.payload_sha256 for snapshot in extended_snapshots],
        )

    def test_execution_date_is_next_nyse_session(self) -> None:
        self.assertEqual(_next_session(self.calendar, "2025-01-03"), "2025-01-06")

    def test_config_keeps_vxx_fail_closed_by_default(self) -> None:
        config = ReplayConfig.from_mapping({})
        self.assertTrue(config.require_vxx_for_returns)


if __name__ == "__main__":
    unittest.main()
