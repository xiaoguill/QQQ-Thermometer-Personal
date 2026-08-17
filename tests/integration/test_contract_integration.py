import json
import unittest
from pathlib import Path

from src.thermometer.contracts import load_contract


ROOT = Path(__file__).resolve().parents[2]


class ContractIntegrationTests(unittest.TestCase):
    def test_frozen_json_is_the_runtime_contract_source(self) -> None:
        raw = json.loads((ROOT / "configs" / "frozen" / "strategy_contract.json").read_text(encoding="utf-8"))
        registry = load_contract()
        self.assertEqual(raw["contract_id"], "qqq_thermometer_strategy_contract")
        self.assertEqual(registry.as_dict(), raw)

    def test_contract_accepts_a_next_day_execution_boundary(self) -> None:
        registry = load_contract()
        snapshot = {
            "strategy_version": "v10_preserve_shock_recovery",
            "signal_date": "2020-03-16",
            "execution_date": "2020-03-17",
            "state": "warming",
            "target_weights": {"BIL": 1.0},
            "indicators": {"ready": False},
            "reason_codes": ["warmup_insufficient_history"],
            "input_data_version": "integration-fixture-v1",
        }
        self.assertEqual(registry.validate_target_snapshot(snapshot)["execution_date"], "2020-03-17")


if __name__ == "__main__":
    unittest.main()
