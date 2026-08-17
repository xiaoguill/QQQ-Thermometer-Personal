import unittest

from src.thermometer.contracts import load_contract


class ContractUnitTests(unittest.TestCase):
    def test_contract_hash_is_stable(self) -> None:
        first = load_contract()
        second = load_contract()
        self.assertEqual(first.contract_hash, second.contract_hash)

    def test_warmup_weights_are_fully_invested(self) -> None:
        registry = load_contract()
        strategy = registry.get_strategy("v10_preserve_shock_recovery")
        weights = strategy["weight_schema"]["warmup_default_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)


if __name__ == "__main__":
    unittest.main()
