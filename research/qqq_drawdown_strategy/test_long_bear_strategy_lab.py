import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import annual_strategy_lab as annual  # noqa: E402
import long_bear_strategy_lab as lab  # noqa: E402


class LongBearStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices = annual.load_prices()
        cls.vix = lab.load_vix(index=cls.prices.index)
        cls.volume = lab.load_volume(cls.prices, lab.OUTPUT_DIR)
        cls.base = lab.load_base_targets(cls.prices)
        cls.targets, cls.signals = lab.build_target_weights(
            cls.prices, cls.vix, cls.volume, lab.PARAMETERS, cls.base
        )

    def test_rsi_and_obv_are_bounded_and_aligned(self):
        rsi = lab.rsi_series(self.prices["QQQ"])
        obv, obv_ema = lab.obv_series(self.prices["QQQ"], self.volume)
        self.assertEqual(len(rsi), len(self.prices))
        self.assertEqual(len(obv), len(self.prices))
        self.assertEqual(len(obv_ema), len(self.prices))
        valid = rsi.dropna()
        self.assertTrue(((valid >= 0.0) & (valid <= 100.0)).all())

    def test_weights_are_fully_invested(self):
        self.assertTrue(np.allclose(self.targets.sum(axis=1).to_numpy(), 1.0))
        self.assertGreaterEqual(float(self.targets.min().min()), -1e-10)

    def test_signals_are_applied_next_day(self):
        active = self.signals["risk_off"] & self.signals["severe_risk"]
        active = active & self.signals["risk_off"].shift(1).fillna(False).eq(False)
        candidates = self.signals.index[active]
        checked = False
        for date in candidates:
            position = self.prices.index.get_loc(date)
            if position + 1 >= len(self.prices):
                continue
            next_date = self.prices.index[position + 1]
            # The close that creates a signal cannot change the same day's target.
            np.testing.assert_allclose(self.targets.loc[date], self.base.loc[date])
            if not np.allclose(self.targets.loc[next_date], self.base.loc[next_date]):
                checked = True
                break
        self.assertTrue(checked, "no active day produced a next-day overlay")

    def test_v1_output_is_separate(self):
        self.assertTrue((HERE / "output_annual_v2" / "selected_target_weights.csv").exists())
        self.assertFalse((HERE / "output_annual_v2" / "selected_signals.csv").exists())
        self.assertTrue((lab.OUTPUT_DIR / "selected_target_weights.csv").exists())

    def test_selected_annual_objective(self):
        annual_frame = pd.read_csv(lab.OUTPUT_DIR / "annual_comparison.csv")
        complete = annual_frame[
            (annual_frame["year"] >= 2008) & (annual_frame["year"] <= 2025)
        ]
        self.assertEqual(len(complete), 18)
        self.assertTrue(complete["mdd_no_worse"].all())
        self.assertGreaterEqual(int(complete["both_within_5pp"].sum()), 15)

    def test_indicator_neighbors_pass_eighty_percent_gate(self):
        neighbors = pd.read_csv(lab.OUTPUT_DIR / "indicator_stability.csv")
        self.assertEqual(len(neighbors), 27)
        self.assertTrue(neighbors["pass_80pct_objective"].all())


if __name__ == "__main__":
    unittest.main(verbosity=2)
