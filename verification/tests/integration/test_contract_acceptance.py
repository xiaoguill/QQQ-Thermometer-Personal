import json
import os
import unittest
from pathlib import Path

from src.thermometer.contracts import load_contract


ROOT = Path(os.environ.get("QQQ_CANDIDATE_ROOT", Path(__file__).resolve().parents[3]))


class ContractAcceptanceIntegrationTests(unittest.TestCase):
    def test_runtime_contract_matches_frozen_file(self) -> None:
        raw = json.loads((ROOT / "configs" / "frozen" / "strategy_contract.json").read_text(encoding="utf-8"))
        self.assertEqual(load_contract().as_dict(), raw)

    def test_valid_target_snapshot_has_explicit_dates_and_input_version(self) -> None:
        registry = load_contract()
        snapshot = {
            "strategy_version": "v10_preserve_shock_recovery",
            "signal_date": "2020-03-16",
            "execution_date": "2020-03-17",
            "state": "warming",
            "target_weights": {"BIL": 1.0},
            "indicators": {"ready": False},
            "reason_codes": ["warmup_insufficient_history"],
            "input_data_version": "acceptance-integration-v1",
        }
        result = registry.validate_target_snapshot(snapshot)
        self.assertEqual(result["input_data_version"], "acceptance-integration-v1")


if __name__ == "__main__":
    unittest.main()
