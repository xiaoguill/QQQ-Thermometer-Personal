import unittest
from datetime import date, timedelta

from src.storage.indicators import calculate_indicator_snapshots
from src.storage.normalization import NormalizedBar, NormalizationResult, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.explanation import (
    CLOSE_CONFIRMED,
    PUBLICATION_CONFIRMED,
    PUBLICATION_NEEDS_REVIEW,
    ExplanationInput,
    build_explanation,
)
from src.thermometer.regime import RegimeConfig, RegimeInput, evaluate_regime


class ExplanationIntegrationTests(unittest.TestCase):
    calendar = TradingCalendar()

    def _bars(self, symbol: str, dates: tuple[str, ...], *, quality: str = "OK") -> tuple[NormalizedBar, ...]:
        result = []
        for index, signal_date in enumerate(dates):
            if symbol == "QQQ":
                close = 100.0 + index * 0.20
                basis = "adjusted_ohlcv"
            elif symbol == "VIX":
                close = 18.0
                basis = "index_level"
            else:
                close = 22.0
                basis = "index_level"
            result.append(
                NormalizedBar(
                    symbol=symbol,
                    bar_date=signal_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=None,
                    sources=("m06-integration-fixture",),
                    snapshot_ids=(f"{symbol}-{signal_date}",),
                    retrieved_at_by_source=(("m06-integration-fixture", f"{signal_date}T22:00:00Z"),),
                    price_basis=basis,
                    timezone="America/New_York",
                    quality=quality,
                )
            )
        return tuple(result)

    def _normalization(self, symbol: str, dates: tuple[str, ...], *, quality: str = "OK") -> NormalizationResult:
        return NormalizationResult(
            as_of=f"{dates[-1]}T22:00:00Z",
            calendar_id=self.calendar.calendar_id,
            normalization_version="m03-normalized-bars/v1",
            quality=quality,
            bars=self._bars(symbol, dates, quality=quality),
            quality_events=(),
        )

    def test_m03_m04_m05_chain_produces_confirmed_explanation(self):
        first = date.fromisoformat("2023-01-03")
        dates = self.calendar.sessions("2023-01-03", (first + timedelta(days=260)).isoformat())[:160]
        qqq = self._normalization("QQQ", dates)
        vix = self._normalization("VIX", dates)
        vix3m = self._normalization("VIX3M", dates)
        indicator_run = calculate_indicator_snapshots((qqq, vix, vix3m), calendar=self.calendar)
        latest = indicator_run.snapshots[-1]
        regime = evaluate_regime(
            RegimeInput(latest, qqq.bars[-1]),
            config=RegimeConfig.from_registry(load_contract()),
            calendar=self.calendar,
        )
        model = build_explanation(ExplanationInput(regime, latest, CLOSE_CONFIRMED))
        self.assertEqual(model.state, "normal")
        self.assertEqual(model.publication_status, PUBLICATION_CONFIRMED)
        self.assertTrue(model.confirmed)
        self.assertEqual(model.indicator_version, indicator_run.indicator_version)
        self.assertEqual(model.source_indicator_snapshot_hash, regime.indicator_snapshot_hash)
        self.assertTrue(model.evidence)
        self.assertTrue(any(item.code.startswith("regime.") for item in model.evidence))

    def test_m04_quality_failure_becomes_needs_review_explanation(self):
        first = date.fromisoformat("2023-01-03")
        dates = self.calendar.sessions("2023-01-03", (first + timedelta(days=260)).isoformat())[:160]
        qqq = self._normalization("QQQ", dates, quality="NEEDS_REVIEW")
        vix = self._normalization("VIX", dates)
        vix3m = self._normalization("VIX3M", dates)
        indicator_run = calculate_indicator_snapshots((qqq, vix, vix3m), calendar=self.calendar)
        latest = indicator_run.snapshots[-1]
        regime = evaluate_regime(
            RegimeInput(latest, qqq.bars[-1]),
            config=RegimeConfig.from_registry(load_contract()),
            calendar=self.calendar,
        )
        model = build_explanation(ExplanationInput(regime, latest))
        self.assertEqual(regime.state, "needs_review")
        self.assertEqual(model.publication_status, PUBLICATION_NEEDS_REVIEW)
        self.assertFalse(model.confirmed)
        self.assertEqual(model.data_quality, "NEEDS_REVIEW")
        self.assertIn("explanation_indicator_quality_needs_review", model.reason_codes)


if __name__ == "__main__":
    unittest.main()
