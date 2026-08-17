import copy
import unittest
from datetime import date, timedelta

from src.storage.indicators import INDICATOR_NAMES, INDICATOR_VERSION, IndicatorSnapshot
from src.storage.normalization import NormalizedBar, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.regime import (
    REGIME_IMPLEMENTATION_VERSION,
    RegimeConfig,
    RegimeError,
    RegimeInput,
    RegimeState,
    evaluate_regime,
    replay_regimes,
)


class RegimeUnitTests(unittest.TestCase):
    calendar = TradingCalendar()

    @classmethod
    def setUpClass(cls):
        cls.config = RegimeConfig.from_registry(load_contract())

    def _sessions(self, count: int, start: str = "2024-01-02") -> tuple[str, ...]:
        first = date.fromisoformat(start)
        return self.calendar.sessions(start, (first + timedelta(days=count * 3)).isoformat())[:count]

    def _input(
        self,
        signal_date: str,
        *,
        close: float = 100.0,
        return_5d: float = 0.0,
        return_10d: float = 0.0,
        ema10: float = 99.0,
        sma150: float = 90.0,
        momentum126: float = 0.10,
        rv20: float = 0.20,
        vix: float = 20.0,
        vix3m: float = 22.0,
        quality: str = "OK",
        ready: bool = True,
        bar_quality: str = "OK",
    ) -> RegimeInput:
        values = {
            "qqq_return_5d": return_5d,
            "qqq_return_10d": return_10d,
            "qqq_return_20d": 0.0,
            "qqq_ema10": ema10,
            "qqq_sma150": sma150,
            "qqq_momentum126": momentum126,
            "qqq_rv20": rv20,
            "vix": vix,
            "vix3m": vix3m,
            "vix_term_ratio": vix / vix3m,
        }
        warmup = () if ready else INDICATOR_NAMES
        if not ready:
            values = {name: None for name in INDICATOR_NAMES}
        snapshot = IndicatorSnapshot(
            signal_date=signal_date,
            as_of=f"{signal_date}T22:00:00Z",
            calendar_id=self.calendar.calendar_id,
            indicator_version=INDICATOR_VERSION,
            quality=quality,
            ready=ready,
            values=values,
            warmup_indicators=warmup,
            input_bar_dates={"QQQ": (signal_date,), "VIX": (signal_date,), "VIX3M": (signal_date,)},
            price_basis_by_symbol={"QQQ": "adjusted_ohlcv", "VIX": "index_level", "VIX3M": "index_level"},
            timezone_by_symbol={"QQQ": "America/New_York", "VIX": "America/New_York", "VIX3M": "America/New_York"},
        )
        bar = NormalizedBar(
            symbol="QQQ",
            bar_date=signal_date,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=None,
            sources=("unit-fixture",),
            snapshot_ids=(f"bar-{signal_date}",),
            retrieved_at_by_source=(("unit-fixture", f"{signal_date}T22:00:00Z"),),
            price_basis="adjusted_ohlcv",
            timezone="America/New_York",
            quality=bar_quality,
        )
        return RegimeInput(snapshot, bar)

    def test_config_reads_frozen_recovery_definitions(self):
        self.assertEqual(self.config.strategy_version, "v10_preserve_shock_recovery")
        self.assertEqual(self.config.recovery_rebound_5d_min, 0.03)
        self.assertEqual(self.config.recovery_rebound_10d_min, 0.05)
        self.assertEqual(self.config.recovery_rv_comparison_days, 5)
        self.assertEqual(REGIME_IMPLEMENTATION_VERSION, "m05-regime/v1")

    def test_normal_state_is_confirmed_and_execution_is_next_session(self):
        current = self._input("2024-01-02")
        snapshot = evaluate_regime(current, config=self.config, calendar=self.calendar)
        self.assertEqual(snapshot.state, "normal")
        self.assertTrue(snapshot.confirmed)
        self.assertEqual(snapshot.execution_date, "2024-01-03")
        evidence = {item.code: item for item in snapshot.evidence}
        self.assertFalse(evidence["fresh_shock"].passed)
        self.assertIn("fresh_shock_not_confirmed", snapshot.reason_codes)

    def test_shock_requires_qqq_loss_and_vix_or_term_pressure(self):
        base = self._input("2024-01-02", return_5d=-0.10, return_10d=-0.10, close=90.0, ema10=100.0, vix=40.0, vix3m=30.0)
        vix_only = self._input("2024-01-02", return_5d=0.0, vix=40.0, vix3m=50.0)
        loss_only = self._input("2024-01-02", return_5d=-0.10, return_10d=-0.10, vix=20.0, vix3m=22.0, close=90.0, ema10=100.0)
        self.assertEqual(evaluate_regime(base, config=self.config, calendar=self.calendar).state, "shock")
        self.assertEqual(evaluate_regime(vix_only, config=self.config, calendar=self.calendar).state, "normal")
        self.assertEqual(evaluate_regime(loss_only, config=self.config, calendar=self.calendar).state, "normal")

    def test_recovery_needs_two_confirmations_and_minimum_shock_hold(self):
        dates = self._sessions(7)
        inputs = [self._input(dates[0])]
        inputs.append(self._input(dates[1], close=90.0, return_5d=-0.10, return_10d=-0.10, ema10=100.0, sma150=100.0, momentum126=-0.10, rv20=0.40, vix=40.0, vix3m=30.0))
        for index in range(2, 6):
            inputs.append(self._input(dates[index], close=100.0, return_5d=0.04, return_10d=0.06, ema10=99.0, rv20=0.40, vix=35.0, vix3m=30.0))
        inputs.append(self._input(dates[6], close=100.0, return_5d=0.04, return_10d=0.06, ema10=99.0, rv20=0.20, vix=35.0, vix3m=30.0))
        run = replay_regimes(inputs, config=self.config, calendar=self.calendar)
        self.assertEqual(run.snapshots[1].state, "shock")
        self.assertEqual(run.snapshots[1].elapsed_state_sessions, 0)
        self.assertEqual(run.snapshots[5].state, "shock")
        self.assertEqual(run.snapshots[6].state, "recovery")
        self.assertTrue(run.snapshots[6].transition)
        self.assertIn("transition_to_recovery", run.snapshots[6].reason_codes)
        evidence = {item.code: item for item in run.snapshots[6].evidence}
        self.assertTrue(evidence["recovery_confirmation_count"].passed)

    def test_vix_alone_does_not_interrupt_recovery(self):
        current = self._input("2024-01-02", vix=55.0, vix3m=30.0, return_5d=0.0, return_10d=0.0)
        snapshot = evaluate_regime(
            current,
            config=self.config,
            calendar=self.calendar,
            previous_state=RegimeState("recovery", elapsed_state_sessions=5),
        )
        self.assertEqual(snapshot.state, "recovery")
        self.assertFalse({item.code: item for item in snapshot.evidence}["fresh_shock"].passed)

    def test_actual_short_loss_can_reenter_shock_after_hysteresis(self):
        current = self._input("2024-01-02", return_5d=-0.06, return_10d=-0.08, vix=40.0, vix3m=30.0)
        snapshot = evaluate_regime(
            current,
            config=self.config,
            calendar=self.calendar,
            previous_state=RegimeState("recovery", elapsed_state_sessions=5),
        )
        self.assertEqual(snapshot.state, "shock")
        self.assertEqual(snapshot.elapsed_state_sessions, 0)

    def test_quality_and_warmup_never_become_confirmed(self):
        warmup = self._input("2024-01-02", ready=False)
        review = self._input("2024-01-03", quality="NEEDS_REVIEW", ready=False, bar_quality="NEEDS_REVIEW")
        warmup_snapshot = evaluate_regime(warmup, config=self.config, calendar=self.calendar)
        review_snapshot = evaluate_regime(review, config=self.config, calendar=self.calendar)
        self.assertEqual(warmup_snapshot.state, "warming")
        self.assertFalse(warmup_snapshot.confirmed)
        self.assertEqual(review_snapshot.state, "needs_review")
        self.assertFalse(review_snapshot.confirmed)

    def test_execution_skips_exchange_holiday(self):
        snapshot = evaluate_regime(self._input("2024-07-03"), config=self.config, calendar=self.calendar)
        self.assertEqual(snapshot.execution_date, "2024-07-05")

    def test_prefix_and_reversed_replays_are_identical(self):
        dates = self._sessions(7)
        inputs = [self._input(d) for d in dates]
        full = replay_regimes(inputs, config=self.config, calendar=self.calendar)
        prefix = replay_regimes(inputs[:5], config=self.config, calendar=self.calendar)
        reversed_run = replay_regimes(tuple(reversed(inputs)), config=self.config, calendar=self.calendar)
        self.assertEqual(
            [item.as_dict() for item in full.snapshots[:5]],
            [item.as_dict() for item in prefix.snapshots],
        )
        self.assertEqual(full.as_dict(), reversed_run.as_dict())

    def test_missing_session_duplicate_and_mismatched_bar_are_rejected(self):
        dates = self._sessions(3)
        first = self._input(dates[0])
        third = self._input(dates[2])
        with self.assertRaises(RegimeError):
            replay_regimes((first, third), config=self.config, calendar=self.calendar)
        with self.assertRaises(RegimeError):
            replay_regimes((first, first), config=self.config, calendar=self.calendar)
        mismatched = self._input(dates[1])
        bad_bar = copy.copy(mismatched.qqq_bar)
        object.__setattr__(bad_bar, "bar_date", dates[2])
        with self.assertRaises(RegimeError):
            RegimeInput(mismatched.indicators, bad_bar)

    def test_run_is_idempotent_and_has_stable_content_hash(self):
        inputs = [self._input(d) for d in self._sessions(4)]
        first = replay_regimes(inputs, config=self.config, calendar=self.calendar)
        second = replay_regimes(inputs, config=self.config, calendar=self.calendar)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())


if __name__ == "__main__":
    unittest.main()
