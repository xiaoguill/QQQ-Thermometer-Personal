import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ApiContractTests(unittest.TestCase):
    def test_frozen_api_contract_is_local_paper_only(self) -> None:
        contract = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))

        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertEqual(contract["x-qqq-contract"]["contract_version"], "1.0.0")
        self.assertEqual(contract["x-qqq-contract"]["mode"], "local_paper_only")
        self.assertIn("/api/thermometer/latest", contract["paths"])
        self.assertIn("/api/paper/confirm", contract["paths"])

    def test_frontend_metadata_cannot_omit_audit_fields(self) -> None:
        contract = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
        required = set(contract["components"]["schemas"]["ApiMetadata"]["required"])

        self.assertTrue({"contract_version", "strategy_version", "as_of", "signal_date"}.issubset(required))
        self.assertTrue({"execution_date", "data_quality", "run_id"}.issubset(required))


if __name__ == "__main__":
    unittest.main()
