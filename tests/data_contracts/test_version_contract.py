import copy
import unittest

from src.thermometer import StrategyVersionContract
from src.thermometer.contracts import ContractError, load_contract


class StrategyVersionContractTests(unittest.TestCase):
    def test_version_views_have_distinct_names_hashes_and_schemas(self) -> None:
        registry = load_contract()
        v10 = registry.get_version_contract("v10_preserve_shock_recovery")
        legacy = registry.get_version_contract("final_regime_ensemble")

        self.assertIsInstance(v10, StrategyVersionContract)
        self.assertNotEqual(v10.display_name, legacy.display_name)
        self.assertNotEqual(v10.config_hash, legacy.config_hash)
        self.assertEqual(v10.state_schema["enum"], list(v10.states))
        self.assertIn("QQQ", v10.strategy_assets)
        self.assertIn("QQQ", v10.benchmark_assets)
        self.assertEqual(v10.weight_schema["sum_target"], 1.0)
        self.assertEqual(v10.signal_execution_schema["signal_date"]["cutoff"], "close")
        self.assertEqual(v10.signal_execution_schema["execution_date"]["delay_trading_days"], 1)

        serialized = v10.as_dict()
        for field in ("version", "display_name", "config_hash", "state_schema", "asset_schema", "signal_execution_schema"):
            self.assertIn(field, serialized)
        self.assertEqual(serialized["config_hash"], registry.strategy_config_hash(v10.version))
        self.assertEqual(registry.version_config_hash(v10.version), v10.config_hash)

    def test_version_contract_is_deterministic_and_defensive(self) -> None:
        registry = load_contract()
        first = registry.get_version_contract("v10_preserve_shock_recovery")
        second = registry.get_version_contract("v10_preserve_shock_recovery")

        self.assertEqual(first.as_dict(), second.as_dict())
        mutated = first.as_dict()
        mutated["states"].append("unapproved_state")
        mutated["weight_schema"]["warmup_default_weights"]["BIL"] = 0.5
        self.assertNotIn("unapproved_state", first.states)
        self.assertEqual(first.weight_schema["warmup_default_weights"]["BIL"], 1.0)

    def test_versioned_snapshot_requires_matching_config_hash(self) -> None:
        registry = load_contract()
        snapshot = {
            "strategy_version": "v10_preserve_shock_recovery",
            "strategy_config_hash": registry.strategy_config_hash("v10_preserve_shock_recovery"),
            "signal_date": "2020-03-16",
            "execution_date": "2020-03-17",
            "state": "shock",
            "target_weights": {"BIL": 1.0},
            "indicators": {"ready": True},
            "reason_codes": ["shock_entry"],
            "input_data_version": "contract-fixture-v1",
        }

        validated = registry.validate_versioned_target_snapshot(snapshot)
        self.assertEqual(validated["strategy_config_hash"], snapshot["strategy_config_hash"])

        invalid = copy.deepcopy(snapshot)
        invalid["strategy_config_hash"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "does not match"):
            registry.validate_versioned_target_snapshot(invalid)

    def test_version_contract_unknown_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "unknown strategy version"):
            load_contract().get_version_contract("v99_not_registered")


if __name__ == "__main__":
    unittest.main()
