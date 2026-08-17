import copy
import json
import unittest

from src.thermometer.contracts import ContractError, load_contract


class StrategyContractTests(unittest.TestCase):
    def test_frozen_contract_loads_with_no_product_default(self) -> None:
        registry = load_contract()

        self.assertIsNone(registry.product_default_strategy_version)
        self.assertEqual(registry.get_strategy("v10_preserve_shock_recovery")["status"], "research_candidate")
        self.assertEqual(registry.get_strategy("final_regime_ensemble")["status"], "legacy_reference")
        self.assertEqual(registry.contract_hash, load_contract().contract_hash)
        self.assertTrue(registry.unresolved_decisions)

    def test_unknown_strategy_version_is_rejected(self) -> None:
        registry = load_contract()

        with self.assertRaisesRegex(ContractError, "unknown strategy version"):
            registry.get_strategy("v99_not_registered")

    def test_target_weights_are_normalized_to_registered_assets(self) -> None:
        registry = load_contract()

        weights = registry.validate_weights(
            "v10_preserve_shock_recovery",
            {"BIL": 1.0},
        )

        self.assertEqual(weights["BIL"], 1.0)
        self.assertEqual(set(weights), set(registry.get_strategy("v10_preserve_shock_recovery")["strategy_assets"]))
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)

    def test_invalid_target_weights_are_rejected(self) -> None:
        registry = load_contract()

        invalid_cases = [
            ({"QQQ": 0.4}, "sum to 1"),
            ({"QQQ": 1.0, "NOT_AN_ASSET": 0.0}, "unknown assets"),
            ({"QQQ": -0.1, "BIL": 1.1}, "cannot be negative"),
        ]
        for weights, message in invalid_cases:
            with self.subTest(weights=weights):
                with self.assertRaisesRegex(ContractError, message):
                    registry.validate_weights("v10_preserve_shock_recovery", weights)

    def test_target_snapshot_requires_signal_before_execution(self) -> None:
        registry = load_contract()
        snapshot = {
            "strategy_version": "v10_preserve_shock_recovery",
            "signal_date": "2020-03-16",
            "execution_date": "2020-03-17",
            "state": "shock",
            "target_weights": {"BIL": 1.0},
            "indicators": {"vix": {"value": 40.0, "ready": True}},
            "reason_codes": ["shock_entry"],
            "input_data_version": "fixture-2020-03-16",
        }

        validated = registry.validate_target_snapshot(snapshot)

        self.assertEqual(validated["target_weights"]["BIL"], 1.0)

        invalid = dict(snapshot)
        invalid["execution_date"] = "2020-03-16"
        with self.assertRaisesRegex(ContractError, "after signal_date"):
            registry.validate_target_snapshot(invalid)

    def test_duplicate_strategy_versions_are_rejected(self) -> None:
        source = load_contract().as_dict()
        source["strategy_versions"].append(copy.deepcopy(source["strategy_versions"][0]))
        path = self._temp_path("duplicate.json")
        try:
            path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ContractError, "duplicate versions"):
                load_contract(path)
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _temp_path(name: str):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp(prefix="qqq-contract-test-")) / name


if __name__ == "__main__":
    unittest.main()
