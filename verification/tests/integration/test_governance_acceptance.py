import json
import os
import unittest
from pathlib import Path


ROOT = Path(os.environ.get("QQQ_CANDIDATE_ROOT", Path(__file__).resolve().parents[3]))


class GovernanceAcceptanceIntegrationTests(unittest.TestCase):
    def test_task_contract_and_router_have_matching_route_and_role(self) -> None:
        router = json.loads((ROOT / "AI_CONTEXT_ROUTER.json").read_text(encoding="utf-8"))
        task = json.loads((ROOT / "tasks" / "M00.5.json").read_text(encoding="utf-8"))

        route = router["routes"][task["route_id"]]
        self.assertEqual(task["role"], route["default_role"])
        self.assertIn("verification/", route["path_prefixes"])
        self.assertTrue(any(path == "verification/" or path.startswith("verification/") for path in task["allowed_write_paths"]))
        self.assertNotIn("configs/frozen/", task["allowed_write_paths"])

    def test_registry_machine_truth_points_to_frozen_sources(self) -> None:
        registry = json.loads((ROOT / "docs" / "DOCUMENT_REGISTRY.json").read_text(encoding="utf-8"))
        entries = {entry["path"]: entry for entry in registry["entries"]}

        self.assertEqual(entries["configs/frozen/strategy_contract.json"]["authority"], "L1")
        self.assertEqual(entries["contracts/openapi.json"]["authority"], "L1")
        self.assertEqual(entries["docs/V10_CANDIDATE_CONTRACT.md"]["authority"], "L6")


if __name__ == "__main__":
    unittest.main()
