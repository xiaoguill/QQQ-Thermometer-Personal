import math
import unittest
from dataclasses import replace

from src.storage import (
    INDICATOR_NAMES,
    INDICATOR_VERSION,
    MarketDataRequest,
    RawSnapshot,
    TradingCalendar,
    calculate_indicator_snapshots,
    normalize_snapshots,
)
from src.storage.market_data import DataContractError


class IndicatorIntegrationTests(unittest.TestCase):
    calendar = TradingCalendar()

    def _sessions(self, count=160):
        return self.calendar.sessions("2020-01-02", "2020-09-30")[:count]

    def _result(self, symbol_group, sessions, as_of, *, drop_dates=(), quality_status="success"):
        symbols = tuple(symbol_group)
        is_index = all(symbol in {"VIX", "VIX3M"} for symbol in symbols)
        request = MarketDataRequest(
            source="fixture-" + "-".join(symbols),
            symbols=symbols,
            start_date=sessions[0],
            end_date=sessions[-1],
            price_basis="index_level" if is_index else "adjusted_ohlcv",
            timezone="America/New_York",
        )
        rows = []
        for index, day in enumerate(sessions):
            if day in set(drop_dates):
                continue
            for symbol in symbols:
                if symbol == "QQQ":
                    close = 100.0 + index
                elif symbol == "VIX":
                    close = 20.0 + (index % 5) * 0.1
                else:
                    close = 25.0 + (index % 3) * 0.1
                rows.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "open": close,
                        "high": close + 1.0,
                        "low": close - 1.0,
                        "close": close,
                        "volume": 1000.0,
                    }
                )
        snapshot = RawSnapshot.capture(
            source=request.source,
            request=request,
            retrieved_at=as_of,
            payload={"bars": rows},
            status=quality_status,
        )
        return normalize_snapshots([snapshot], as_of=as_of)

    def _run(self, count=160, *, qqq_drop=(), index_drop=(), as_of_override=None):
        sessions = self._sessions(count)
        as_of = as_of_override or f"{sessions[-1]}T20:00:00Z"
        qqq = self._result(("QQQ",), sessions, as_of, drop_dates=qqq_drop)
        indices = self._result(("VIX", "VIX3M"), sessions, as_of, drop_dates=index_drop)
        return calculate_indicator_snapshots((qqq, indices)), qqq, indices, sessions, as_of

    def test_catalogue_is_explicit_and_does_not_include_research_indicators(self):
        self.assertEqual(
            INDICATOR_NAMES,
            (
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
            ),
        )
        self.assertNotIn("rsi", INDICATOR_NAMES)
        self.assertNotIn("macd", INDICATOR_NAMES)
        self.assertNotIn("kdj", INDICATOR_NAMES)
        self.assertNotIn("obv", INDICATOR_NAMES)

    def test_warmup_is_explicit_and_latest_snapshot_is_ready(self):
        run, _, _, _, _ = self._run()
        first = run.snapshots[0]
        self.assertEqual(first.indicator_version, INDICATOR_VERSION)
        self.assertIn("qqq_return_5d", first.warmup_indicators)
        self.assertIsNone(first.values["qqq_return_5d"])
        self.assertFalse(first.ready)
        self.assertTrue(run.snapshots[-1].ready)
        self.assertTrue(run.ready)
        self.assertEqual(run.quality, "OK")

    def test_hand_calculation_uses_declared_price_and_window_rules(self):
        run, _, _, sessions, _ = self._run()
        by_date = {snapshot.signal_date: snapshot for snapshot in run.snapshots}

        day5 = by_date[sessions[5]]
        self.assertAlmostEqual(day5.values["qqq_return_5d"], 105.0 / 100.0 - 1.0)
        day10 = by_date[sessions[10]]
        self.assertAlmostEqual(day10.values["qqq_return_10d"], 110.0 / 100.0 - 1.0)
        self.assertAlmostEqual(day10.values["qqq_ema10"], (sum(100.0 + i for i in range(10)) / 10.0) * 9.0 / 11.0 + 110.0 * 2.0 / 11.0)

        day20 = by_date[sessions[20]]
        returns = tuple((100.0 + i) / (100.0 + i - 1) - 1.0 for i in range(1, 21))
        mean = sum(returns) / len(returns)
        expected_rv = math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)) * math.sqrt(252.0)
        self.assertAlmostEqual(day20.values["qqq_return_20d"], 120.0 / 100.0 - 1.0)
        self.assertAlmostEqual(day20.values["qqq_rv20"], expected_rv)

        day126 = by_date[sessions[126]]
        self.assertAlmostEqual(day126.values["qqq_momentum126"], 226.0 / 100.0 - 1.0)
        day149 = by_date[sessions[149]]
        self.assertAlmostEqual(day149.values["qqq_sma150"], sum(100.0 + i for i in range(150)) / 150.0)

    def test_vix_term_structure_requires_same_date_and_never_forward_fills(self):
        missing_day = self._sessions()[150]
        run, _, _, sessions, _ = self._run(index_drop=(missing_day,))
        missing = next(snapshot for snapshot in run.snapshots if snapshot.signal_date == missing_day)
        following = next(snapshot for snapshot in run.snapshots if snapshot.signal_date == sessions[151])
        self.assertIsNone(missing.values["vix"])
        self.assertIsNone(missing.values["vix3m"])
        self.assertIsNone(missing.values["vix_term_ratio"])
        self.assertEqual(missing.input_bar_dates["VIX"], ())
        self.assertEqual(missing.input_bar_dates["VIX3M"], ())
        self.assertIn("vix_missing", missing.reasons)
        self.assertIsNotNone(following.values["vix_term_ratio"])
        self.assertNotEqual(missing.values["vix"], following.values["vix"])
        self.assertFalse(missing.ready)

    def test_missing_qqq_session_is_not_compressed_into_a_shorter_window(self):
        missing_day = self._sessions()[130]
        run, _, _, sessions, _ = self._run(qqq_drop=(missing_day,))
        after_gap = next(snapshot for snapshot in run.snapshots if snapshot.signal_date == sessions[131])
        self.assertEqual(run.quality, "NEEDS_REVIEW")
        self.assertIn("qqq_session_gap", run.reasons)
        self.assertIsNone(after_gap.values["qqq_return_5d"])
        self.assertIn("qqq_session_gap", after_gap.reasons)
        self.assertFalse(after_gap.ready)

    def test_non_ok_normalization_quality_cannot_become_ready(self):
        run, qqq, indices, _, _ = self._run()
        stale_qqq = replace(qqq, quality="STALE")
        stale_run = calculate_indicator_snapshots((stale_qqq, indices))
        self.assertEqual(stale_run.input_quality, "STALE")
        self.assertEqual(stale_run.quality, "STALE")
        self.assertFalse(stale_run.snapshots[-1].ready)
        self.assertIn("normalization_quality:STALE", stale_run.snapshots[-1].reasons)

    def test_reversed_input_is_deterministic_and_earlier_output_ignores_future_rows(self):
        full_run, qqq, indices, sessions, as_of = self._run()
        reversed_run = calculate_indicator_snapshots(
            (
                replace(qqq, bars=tuple(reversed(qqq.bars))),
                replace(indices, bars=tuple(reversed(indices.bars))),
            )
        )
        self.assertEqual(full_run.content_hash, reversed_run.content_hash)

        truncated_run, _, _, truncated_sessions, _ = self._run(150, as_of_override=as_of)
        self.assertEqual(full_run.snapshots[149].as_dict(), truncated_run.snapshots[-1].as_dict())
        for snapshot in full_run.snapshots:
            for dates in snapshot.input_bar_dates.values():
                self.assertTrue(all(value <= snapshot.signal_date for value in dates))

    def test_future_bar_and_future_retrieval_metadata_are_rejected(self):
        run, qqq, indices, _, as_of = self._run()
        future_bar = replace(qqq.bars[-1], bar_date="2099-01-05")
        future_result = replace(qqq, bars=qqq.bars[:-1] + (future_bar,))
        with self.assertRaises(DataContractError):
            calculate_indicator_snapshots((future_result, indices))

        future_retrieval = replace(qqq.bars[-1], retrieved_at_by_source=((qqq.bars[-1].sources[0], "2099-01-05T20:00:00Z"),))
        future_metadata_result = replace(qqq, bars=qqq.bars[:-1] + (future_retrieval,))
        with self.assertRaises(DataContractError):
            calculate_indicator_snapshots((future_metadata_result, indices))

    def test_price_basis_timezone_and_provenance_are_retained(self):
        run, _, _, sessions, _ = self._run()
        snapshot = run.snapshots[-1]
        self.assertEqual(snapshot.price_basis_by_symbol["QQQ"], "adjusted_ohlcv")
        self.assertEqual(snapshot.price_basis_by_symbol["VIX"], "index_level")
        self.assertEqual(snapshot.price_basis_by_symbol["VIX3M"], "index_level")
        self.assertEqual(snapshot.timezone_by_symbol["QQQ"], "America/New_York")
        self.assertEqual(snapshot.input_bar_dates["QQQ"][-1], sessions[-1])
        self.assertEqual(snapshot.input_bar_dates["VIX"], (sessions[-1],))

    def test_mismatched_normalized_results_are_rejected(self):
        _, qqq, indices, _, as_of = self._run()
        other_run, other_qqq, _, _, _ = self._run(159, as_of_override=as_of)
        self.assertTrue(other_run.snapshots)
        with self.assertRaises(DataContractError):
            calculate_indicator_snapshots((qqq, other_qqq, indices))

    def test_empty_qqq_is_explicitly_failed(self):
        _, qqq, indices, _, as_of = self._run()
        empty_qqq = replace(qqq, bars=(), quality="FAILED")
        run = calculate_indicator_snapshots((empty_qqq, indices))
        self.assertEqual(run.quality, "FAILED")
        self.assertEqual(run.snapshots, ())
        self.assertIn("qqq_bars_missing", run.reasons)


if __name__ == "__main__":
    unittest.main()
