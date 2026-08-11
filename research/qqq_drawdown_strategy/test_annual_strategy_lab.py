from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import annual_strategy_lab as lab


class AnnualStrategyLabTests(unittest.TestCase):
    def test_monthly_signal_is_lagged_one_trading_day(self) -> None:
        index = pd.date_range("2024-01-02", periods=4, freq="B")
        prices = pd.DataFrame(100.0, index=index, columns=lab.ASSETS)
        prices["QQQ"] = [100.0, 101.0, 102.0, 103.0]

        targets = lab.apply_lagged_monthly_signals(prices, lambda _date: {"QQQ": 1.0})

        self.assertEqual(targets.iloc[0]["BIL"], 1.0)
        self.assertEqual(targets.iloc[0]["QQQ"], 0.0)
        self.assertEqual(targets.iloc[1]["QQQ"], 1.0)
        self.assertEqual(targets.iloc[1]["BIL"], 0.0)

    def test_annual_drawdown_resets_at_calendar_year_boundary(self) -> None:
        index = pd.to_datetime(["2020-12-31", "2021-01-04", "2021-01-05"])
        returns = pd.Series([0.10, -0.20, 0.10], index=index)

        annual = lab.annual_metrics(returns)

        year_2020 = annual.loc[annual["year"] == 2020].iloc[0]
        year_2021 = annual.loc[annual["year"] == 2021].iloc[0]
        self.assertTrue(np.isclose(year_2020["return"], 0.10))
        self.assertTrue(np.isclose(year_2020["max_drawdown"], 0.0))
        self.assertTrue(np.isclose(year_2021["return"], -0.12))
        self.assertTrue(np.isclose(year_2021["max_drawdown"], -0.20))

    def test_selected_candidate_passes_complete_year_hard_constraints(self) -> None:
        output_dir = HERE / "output_annual_v2"
        annual = pd.read_csv(output_dir / "selected_annual_results.csv")
        complete = annual[annual["year"].between(2008, 2025)]

        self.assertEqual(len(complete), 18)
        self.assertTrue(bool(complete["mdd_no_worse"].all()))
        self.assertTrue(bool(complete["mdd_strictly_better"].all()))
        self.assertTrue(bool(complete["return_within_5pp"].all()))
        self.assertTrue(bool(complete["both_within_5pp"].all()))

    def test_selected_has_material_full_history_drawdown_improvement(self) -> None:
        daily = pd.read_csv(
            HERE / "output_annual_v2" / "selected_daily_returns.csv",
            index_col=0,
            parse_dates=True,
        )
        selected = lab.metrics(daily.iloc[:, 1])
        qqq = lab.metrics(daily.iloc[:, 0])

        self.assertGreater(selected["max_drawdown"], qqq["max_drawdown"] + 0.05)
        self.assertGreaterEqual(selected["sharpe"], 0.90)


if __name__ == "__main__":
    unittest.main()
