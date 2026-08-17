import unittest
from datetime import date, timedelta

from src.storage.indicators import calculate_indicator_snapshots
from src.storage.normalization import NormalizedBar, NormalizationResult, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.regime import RegimeConfig, RegimeInput, evaluate_regime


class RegimeReplayIntegrationTests(unittest.TestCase):
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
                    sources=("m05-integration-fixture",),
                    snapshot_ids=(f"{symbol}-{signal_date}",),
                    retrieved_at_by_source=(("m05-integration-fixture", f"{signal_date}T22:00:00Z"),),
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

    def test_real_m04_snapshot_feeds_m05_without_recomputing_indicators(self):
        first = date.fromisoformat("2023-01-03")
        dates = self.calendar.sessions("2023-01-03", (first + timedelta(days=260)).isoformat())[:160]
        qqq = self._normalization("QQQ", dates)
        vix = self._normalization("VIX", dates)
        vix3m = self._normalization("VIX3M", dates)
        indicator_run = calculate_indicator_snapshots((qqq, vix, vix3m), calendar=self.calendar)
        latest = indicator_run.snapshots[-1]
        self.assertTrue(latest.ready)
        regime = evaluate_regime(
            RegimeInput(latest, qqq.bars[-1]),
            config=RegimeConfig.from_registry(load_contract()),
            calendar=self.calendar,
        )
        self.assertEqual(regime.state, "normal")
        self.assertEqual(regime.indicator_version, indicator_run.indicator_version)
        self.assertEqual(regime.qqq_price_basis, "adjusted_ohlcv")
        self.assertTrue(regime.confirmed)

    def test_m04_quality_failure_reaches_needs_review_in_m05(self):
        first = date.fromisoformat("2023-01-03")
        dates = self.calendar.sessions("2023-01-03", (first + timedelta(days=260)).isoformat())[:160]
        qqq = self._normalization("QQQ", dates, quality="NEEDS_REVIEW")
        vix = self._normalization("VIX", dates, quality="OK")
        vix3m = self._normalization("VIX3M", dates, quality="OK")
        indicator_run = calculate_indicator_snapshots((qqq, vix, vix3m), calendar=self.calendar)
        latest = indicator_run.snapshots[-1]
        regime = evaluate_regime(
            RegimeInput(latest, qqq.bars[-1]),
            config=RegimeConfig.from_registry(load_contract()),
            calendar=self.calendar,
        )
        self.assertEqual(regime.state, "needs_review")
        self.assertFalse(regime.confirmed)
        self.assertIn("data_quality_needs_review", regime.reason_codes)


if __name__ == "__main__":
    unittest.main()
