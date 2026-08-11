import sys
import unittest
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import annual_strategy_lab as annual  # noqa: E402
import cash_proxy_lab as lab  # noqa: E402


class CashProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices = annual.load_prices()
        cls.targets = lab.load_v2_targets(cls.prices)
        cls.proxies = lab.load_proxy_prices(lab.OUTPUT_DIR)
        cls.summary = pd.read_csv(lab.OUTPUT_DIR / "cash_proxy_summary.csv")

    def test_first_available_bars_are_used(self):
        self.assertEqual(self.proxies["BOXX"].first_valid_index().strftime("%Y-%m-%d"), "2022-12-28")
        self.assertEqual(self.proxies["SGOV"].first_valid_index().strftime("%Y-%m-%d"), "2020-06-01")

    def test_two_proxy_results_exist(self):
        self.assertEqual(set(self.summary["proxy"]), {"BOXX", "SGOV"})
        self.assertTrue((self.summary["observations"] > 0).all())

    def test_cash_substitution_keeps_weights_invested(self):
        for proxy in lab.PROXIES:
            bil, result = lab.backtest_cash_proxy(
                self.prices, self.targets, self.proxies[proxy], proxy
            )
            self.assertTrue((result["targets"].sum(axis=1).round(10) == 1.0).all())
            self.assertEqual(len(bil["returns"]), len(result["returns"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
