import copy
import unittest

from src.storage.indicators import INDICATOR_NAMES, INDICATOR_VERSION, IndicatorSnapshot
from src.storage.normalization import NormalizedBar, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.regime import RegimeConfig, RegimeInput, RegimeState, evaluate_regime
from src.thermometer.target_weights import (
    TARGET_WEIGHT_IMPLEMENTATION_VERSION,
    WEIGHT_STATUS_CANDIDATE_ONLY,
    TargetWeightError,
    TargetWeightService,
    build_target_weights,
)


class TargetWeightUnitTests(unittest.TestCase):
    calendar = TradingCalendar()

    @classmethod
    def setUpClass(cls):
        cls.config = RegimeConfig.from_registry(load_contract())
        cls.registry = load_contract()

    def _input(
        self,
        signal_date: str,
        *,
        close: float = 100.0,
        return_5d: float = 0.03,
        return_10d: float = 0.04,
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
        indicators = IndicatorSnapshot(
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
            sources=("m07-unit-fixture",),
            snapshot_ids=(f"bar-{signal_date}",),
            retrieved_at_by_source=(("m07-unit-fixture", f"{signal_date}T22:00:00Z"),),
            price_basis="adjusted_ohlcv",
            timezone="America/New_York",
            quality=bar_quality,
        )
        return RegimeInput(indicators, bar)

    def _regime(self, input_value: RegimeInput, previous_state: RegimeState | None = None):
        return evaluate_regime(
            input_value,
            config=self.config,
            calendar=self.calendar,
            previous_state=previous_state,
        )

    def test_initial_normal_before_medium_gate_is_fail_closed(self):
        regime = self._regime(self._input("2024-01-02"))
        result = build_target_weights(regime)
        self.assertEqual(regime.state, "normal")
        self.assertEqual(regime.medium_gate_streak, 1)
        self.assertEqual(result.target_weights["BIL"], 1.0)
        self.assertEqual(result.target_weights["QQQ"], 0.0)
        self.assertIn("medium_gate_confirmation_pending", result.change_reason_codes)

    def test_normal_after_declared_medium_gate_uses_existing_candidate_profile(self):
        regime = self._regime(
            self._input("2024-01-02"),
            RegimeState("normal", elapsed_state_sessions=4, medium_gate_streak=4),
        )
        result = build_target_weights(regime)
        self.assertEqual(regime.medium_gate_streak, 5)
        self.assertAlmostEqual(result.target_weights["QQQ"], 0.6)
        self.assertAlmostEqual(result.target_weights["BIL"], 0.4)
        self.assertEqual(result.target_weights["QLD"], 0.0)

    def test_shock_recovery_warmup_and_review_profiles_are_explicit(self):
        shock = self._regime(
            self._input(
                "2024-01-02",
                close=90.0,
                return_5d=-0.10,
                return_10d=-0.10,
                ema10=100.0,
                sma150=100.0,
                momentum126=-0.10,
                vix=40.0,
                vix3m=30.0,
            )
        )
        shock_result = build_target_weights(shock)
        self.assertEqual(shock.state, "shock")
        self.assertAlmostEqual(shock_result.target_weights["VXX"], 0.25)
        self.assertAlmostEqual(shock_result.target_weights["BIL"], 0.75)
        self.assertNotIn("SVXY", shock_result.target_weights)

        recovery = self._regime(
            self._input("2024-01-03", vix=45.0, vix3m=30.0),
            RegimeState("recovery", elapsed_state_sessions=5, medium_gate_streak=0),
        )
        recovery_result = build_target_weights(recovery)
        self.assertEqual(recovery.state, "recovery")
        self.assertAlmostEqual(recovery_result.target_weights["QQQ"], 0.5)
        self.assertAlmostEqual(recovery_result.target_weights["BIL"], 0.5)

        warming = self._regime(self._input("2024-01-04", ready=False))
        warming_result = build_target_weights(warming)
        self.assertEqual(warming.state, "warming")
        self.assertEqual(warming_result.target_weights["BIL"], 1.0)

        review = self._regime(self._input("2024-01-05", quality="NEEDS_REVIEW", ready=False, bar_quality="NEEDS_REVIEW"))
        review_result = build_target_weights(review)
        self.assertEqual(review.state, "needs_review")
        self.assertEqual(review_result.target_weights["BIL"], 1.0)
        self.assertEqual(review_result.data_quality, "NEEDS_REVIEW")

    def test_output_is_candidate_only_full_asset_vector_and_next_day(self):
        regime = self._regime(self._input("2024-07-03"))
        result = build_target_weights(regime)
        strategy_assets = set(self.registry.get_version_contract("v10_preserve_shock_recovery").strategy_assets)
        self.assertEqual(set(result.target_weights), strategy_assets)
        self.assertAlmostEqual(sum(result.target_weights.values()), 1.0)
        self.assertEqual(result.execution_date, "2024-07-05")
        self.assertEqual(result.weight_status, WEIGHT_STATUS_CANDIDATE_ONLY)
        self.assertTrue(result.candidate_only)
        self.assertFalse(result.execution_eligible)
        self.assertIsNone(result.product_default_strategy_version)
        self.assertEqual(result.implementation_version, TARGET_WEIGHT_IMPLEMENTATION_VERSION)
        self.assertNotIn("temperature", result.as_dict())

    def test_service_facade_and_content_hash_are_idempotent(self):
        regime = self._regime(self._input("2024-01-02"))
        service = TargetWeightService(self.registry, self.calendar)
        first = service.from_regime(regime)
        second = service.from_regime(regime)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_non_regime_input_and_tampered_hash_are_rejected(self):
        with self.assertRaises(TargetWeightError):
            build_target_weights({"state": "normal"})
        regime = self._regime(self._input("2024-01-02"))
        tampered = copy.copy(regime)
        object.__setattr__(tampered, "strategy_config_hash", "0" * 64)
        with self.assertRaises(TargetWeightError):
            build_target_weights(tampered)


if __name__ == "__main__":
    unittest.main()
