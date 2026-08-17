import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ExplanationE2ETests(unittest.TestCase):
    def test_clean_process_emits_confirmed_explanation_without_weights(self):
        code = r'''
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from src.storage.indicators import INDICATOR_VERSION, IndicatorSnapshot
from src.storage.normalization import NormalizedBar, TradingCalendar
from src.thermometer.contracts import load_contract
from src.thermometer.explanation import ExplanationInput, build_explanation
from src.thermometer.regime import RegimeConfig, RegimeInput, evaluate_regime

calendar = TradingCalendar()
signal_date = "2024-01-02"
values = {
    "qqq_return_5d": 0.03,
    "qqq_return_10d": 0.04,
    "qqq_return_20d": 0.05,
    "qqq_ema10": 99.0,
    "qqq_sma150": 90.0,
    "qqq_momentum126": 0.1,
    "qqq_rv20": 0.2,
    "vix": 20.0,
    "vix3m": 22.0,
    "vix_term_ratio": 20.0 / 22.0,
}
snapshot = IndicatorSnapshot(
    signal_date=signal_date,
    as_of="2024-01-02T22:00:00Z",
    calendar_id=calendar.calendar_id,
    indicator_version=INDICATOR_VERSION,
    quality="OK",
    ready=True,
    values=values,
    input_bar_dates={"QQQ": (signal_date,), "VIX": (signal_date,), "VIX3M": (signal_date,)},
    price_basis_by_symbol={"QQQ": "adjusted_ohlcv", "VIX": "index_level", "VIX3M": "index_level"},
    timezone_by_symbol={"QQQ": "America/New_York", "VIX": "America/New_York", "VIX3M": "America/New_York"},
)
bar = NormalizedBar(
    symbol="QQQ", bar_date=signal_date, open=100.0, high=100.0, low=100.0, close=100.0,
    volume=None, sources=("m06-e2e",), snapshot_ids=("m06-e2e-bar",),
    retrieved_at_by_source=(("m06-e2e", "2024-01-02T22:00:00Z"),),
    price_basis="adjusted_ohlcv", timezone="America/New_York", quality="OK",
)
regime = evaluate_regime(
    RegimeInput(snapshot, bar),
    config=RegimeConfig.from_registry(load_contract()),
    calendar=calendar,
)
model = build_explanation(ExplanationInput(regime, snapshot))
result = model.as_dict()
print(json.dumps({
    "confirmed": result["confirmed"],
    "publication_status": result["publication_status"],
    "state": result["state"],
    "temperature": result["temperature"],
    "trend": result["trend"],
    "has_target_weights": "target_weights" in result,
}, sort_keys=True))
'''
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
            "LC_ALL": "C",
        }
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "confirmed": True,
                "has_target_weights": False,
                "publication_status": "CONFIRMED",
                "state": "normal",
                "temperature": 80,
                "trend": "bullish",
            },
        )


if __name__ == "__main__":
    unittest.main()
