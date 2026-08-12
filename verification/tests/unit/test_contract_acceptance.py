import unittest

from src.thermometer.contracts import ContractError, load_contract


class ContractAcceptanceUnitTests(unittest.TestCase):
    def test_registered_versions_are_distinct_and_not_auto_approved(self) -> None:
        registry = load_contract()
        self.assertIsNone(registry.product_default_strategy_version)
        self.assertNotEqual(
            registry.get_strategy("v10_preserve_shock_recovery")["version"],
            registry.get_strategy("final_regime_ensemble")["version"],
        )

    def test_invalid_weight_is_rejected(self) -> None:
        registry = load_contract()
        with self.assertRaises(ContractError):
            registry.validate_weights("v10_preserve_shock_recovery", {"BIL": 0.9})


if __name__ == "__main__":
    unittest.main()
