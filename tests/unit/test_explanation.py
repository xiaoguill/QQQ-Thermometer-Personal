import copy
import unittest
from datetime import date, timedelta

from src.storage.indicators import INDICATOR_NAMES, INDICATOR_VERSION, IndicatorSnapshot
from src.storage.normalization import NormalizedBar, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.explanation import (
    CLOSE_CONFIRMED,
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_PROVISIONAL,
    CONFIDENCE_UNAVAILABLE,
    INTRADAY_PROVISIONAL,
    PUBLICATION_CONFIRMED,
    PUBLICATION_NEEDS_REVIEW,
    PUBLICATION_PROVISIONAL,
    ExplanationError,
    ExplanationInput,
    build_explanation,
)
from src.thermometer.regime import RegimeConfig, RegimeError, RegimeInput, evaluate_regime, replay_regimes


class ExplanationUnitTests(unittest.TestCase):
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
    ) -> tuple[RegimeInput, IndicatorSnapshot]:
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
            sources=("m06-unit-fixture",),
            snapshot_ids=(f"bar-{signal_date}",),
            retrieved_at_by_source=(("m06-unit-fixture", f"{signal_date}T22:00:00Z"),),
            price_basis="adjusted_ohlcv",
            timezone="America/New_York",
            quality=bar_quality,
        )
        return RegimeInput(indicators, bar), indicators

    def _explanation(self, input_value: RegimeInput, indicators: IndicatorSnapshot, *, phase: str = CLOSE_CONFIRMED):
        regime = evaluate_regime(input_value, config=self.config, calendar=self.calendar)
        return build_explanation(ExplanationInput(regime, indicators, phase))

    def test_normal_mapping_is_confirmed_green_and_bullish(self):
        input_value, indicators = self._input("2024-01-02")
        model = self._explanation(input_value, indicators)
        self.assertEqual(model.state, "normal")
        self.assertEqual(model.temperature, 80)
        self.assertEqual(model.color_token, "green")
        self.assertEqual(model.state_label, "Normal")
        self.assertEqual(model.publication_status, PUBLICATION_CONFIRMED)
        self.assertTrue(model.confirmed)
        self.assertEqual(model.confidence_label, CONFIDENCE_CONFIRMED)
        self.assertEqual(model.trend, "bullish")
        self.assertAlmostEqual(model.signal_agreement, 1.0)
        self.assertIn("explanation_close_confirmed", model.reason_codes)

    def test_shock_mapping_preserves_regime_evidence_and_direction(self):
        input_value, indicators = self._input(
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
        model = self._explanation(input_value, indicators)
        self.assertEqual(model.state, "shock")
        self.assertEqual(model.temperature, 15)
        self.assertEqual(model.color_token, "red")
        self.assertEqual(model.trend, "bearish")
        self.assertAlmostEqual(model.signal_agreement, 1.0)
        codes = {item.code: item for item in model.evidence}
        self.assertTrue(codes["regime.fresh_shock"].passed)
        self.assertTrue(codes["regime.shock_price_drop"].passed)

    def test_recovery_mapping_is_yellow_and_not_a_weight_decision(self):
        dates = self._sessions(7)
        inputs = [self._input(dates[0], return_5d=0.0, return_10d=0.0)[0]]
        inputs.append(
            self._input(
                dates[1],
                close=90.0,
                return_5d=-0.10,
                return_10d=-0.10,
                ema10=100.0,
                sma150=100.0,
                momentum126=-0.10,
                rv20=0.40,
                vix=40.0,
                vix3m=30.0,
            )[0]
        )
        for index in range(2, 6):
            inputs.append(self._input(dates[index], rv20=0.40)[0])
        inputs.append(self._input(dates[6], rv20=0.20)[0])
        run = replay_regimes(inputs, config=self.config, calendar=self.calendar)
        model = build_explanation(ExplanationInput(run.snapshots[6], inputs[6].indicators))
        self.assertEqual(run.snapshots[6].state, "recovery")
        self.assertEqual(model.color_token, "yellow")
        self.assertEqual(model.temperature, 50)
        self.assertEqual(model.state_label, "Recovery")
        self.assertNotIn("target_weights", model.as_dict())

    def test_mixed_directional_signals_are_not_a_probability(self):
        input_value, indicators = self._input(
            "2024-01-02",
            return_5d=0.03,
            return_10d=-0.02,
            momentum126=0.0,
        )
        model = self._explanation(input_value, indicators)
        self.assertEqual(model.trend, "mixed")
        self.assertAlmostEqual(model.signal_agreement, 1.0 / 3.0)
        self.assertEqual(
            [item.name for item in model.directional_signals],
            ["qqq_return_5d", "qqq_return_10d", "qqq_momentum126"],
        )
        self.assertEqual(
            {item.code: item for item in model.evidence}["explanation.directional_signals"].source,
            "explanation",
        )

    def test_intraday_is_provisional_even_when_close_snapshot_would_confirm(self):
        input_value, indicators = self._input("2024-01-02")
        model = self._explanation(input_value, indicators, phase=INTRADAY_PROVISIONAL)
        self.assertEqual(model.publication_status, PUBLICATION_PROVISIONAL)
        self.assertFalse(model.confirmed)
        self.assertEqual(model.confidence_label, CONFIDENCE_PROVISIONAL)
        self.assertIn("explanation_intraday_provisional", model.reason_codes)
        self.assertFalse({item.code: item for item in model.evidence}["explanation.close_observation"].passed)

    def test_warmup_and_quality_failure_are_not_final(self):
        warmup_input, warmup_indicators = self._input("2024-01-02", ready=False)
        warmup = self._explanation(warmup_input, warmup_indicators)
        self.assertEqual(warmup.state, "warming")
        self.assertIsNone(warmup.temperature)
        self.assertEqual(warmup.publication_status, PUBLICATION_PROVISIONAL)
        self.assertFalse(warmup.confirmed)
        self.assertEqual(warmup.confidence_label, CONFIDENCE_UNAVAILABLE)

        review_input, review_indicators = self._input(
            "2024-01-03",
            quality="NEEDS_REVIEW",
            ready=False,
            bar_quality="NEEDS_REVIEW",
        )
        review = self._explanation(review_input, review_indicators)
        self.assertEqual(review.state, "needs_review")
        self.assertEqual(review.data_quality, "NEEDS_REVIEW")
        self.assertEqual(review.publication_status, PUBLICATION_NEEDS_REVIEW)
        self.assertFalse(review.confirmed)
        self.assertEqual(review.trend, "unavailable")
        self.assertIsNone(review.signal_agreement)

    def test_missing_indicator_is_explicit_and_cannot_be_confirmed(self):
        input_value, indicators = self._input("2024-01-02")
        regime = evaluate_regime(input_value, config=self.config, calendar=self.calendar)
        model = build_explanation(ExplanationInput(regime, None))
        self.assertEqual(model.indicator_quality, "MISSING")
        self.assertEqual(model.data_quality, "NEEDS_REVIEW")
        self.assertEqual(model.publication_status, PUBLICATION_NEEDS_REVIEW)
        self.assertEqual(model.confidence_label, CONFIDENCE_UNAVAILABLE)
        self.assertEqual(model.trend, "unavailable")
        self.assertIsNone(model.signal_agreement)
        self.assertFalse({item.code: item for item in model.evidence}["explanation.indicator_available"].passed)

    def test_mismatched_date_or_indicator_hash_is_rejected(self):
        input_value, indicators = self._input("2024-01-02")
        regime = evaluate_regime(input_value, config=self.config, calendar=self.calendar)
        mismatched = copy.copy(indicators)
        object.__setattr__(mismatched, "signal_date", "2024-01-03")
        with self.assertRaises(ExplanationError):
            ExplanationInput(regime, mismatched)

        changed = copy.copy(indicators)
        values = dict(changed.values)
        values["qqq_return_5d"] = 0.99
        object.__setattr__(changed, "values", values)
        with self.assertRaises(ExplanationError):
            ExplanationInput(regime, changed)

    def test_explanation_is_idempotent_and_json_compatible(self):
        input_value, indicators = self._input("2024-01-02")
        regime = evaluate_regime(input_value, config=self.config, calendar=self.calendar)
        first = build_explanation(ExplanationInput(regime, indicators))
        second = build_explanation(ExplanationInput(regime, indicators))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertNotIn("target_weights", first.as_dict())


if __name__ == "__main__":
    unittest.main()
