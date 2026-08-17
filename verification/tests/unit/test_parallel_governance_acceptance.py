import json
import os
import unittest
from pathlib import Path


ROOT = Path(os.environ.get("QQQ_CANDIDATE_ROOT", Path(__file__).resolve().parents[3]))


class ParallelGovernanceAcceptanceTests(unittest.TestCase):
    def test_api_contract_is_frozen_and_paper_only(self) -> None:
        contract = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))

        self.assertEqual(contract["x-qqq-contract"]["mode"], "local_paper_only")
        self.assertEqual(contract["x-qqq-contract"]["contract_version"], "1.0.0")
        self.assertEqual(contract["paths"]["/api/paper/confirm"]["post"]["operationId"], "confirmPaperObservation")

    def test_ownership_declares_isolated_builder_roles(self) -> None:
        ownership = json.loads((ROOT / "OWNERSHIP.yaml").read_text(encoding="utf-8"))
        roles = ownership["roles"]

        self.assertEqual(ownership["schema"], "qqq-path-ownership/v1")
        self.assertEqual(set(roles), {"frontend_builder", "backend_builder", "domain_builder", "integrator", "governance_maintainer"})
        self.assertIn("frontend/", roles["frontend_builder"]["write"])
        self.assertIn("src/api/", roles["backend_builder"]["write"])
        self.assertIn("verification/", ownership["protected_read_only"])

    def test_context_governance_contract_is_protected_and_has_maintainer_role(self) -> None:
        ownership = json.loads((ROOT / "OWNERSHIP.yaml").read_text(encoding="utf-8"))
        router = json.loads((ROOT / "AI_CONTEXT_ROUTER.json").read_text(encoding="utf-8"))
        task = json.loads((ROOT / "tasks" / "M00.5.json").read_text(encoding="utf-8"))

        self.assertIn("governance_maintainer", ownership["roles"])
        self.assertIn("tasks/", ownership["protected_read_only"])
        self.assertEqual(router["routes"][task["route_id"]]["default_role"], "governance_maintainer")
        self.assertTrue(ownership["roles"]["governance_maintainer"]["protected_write"])


if __name__ == "__main__":
    unittest.main()
