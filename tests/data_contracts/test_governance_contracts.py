import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GovernanceContractTests(unittest.TestCase):
    def _read_json(self, relative: str) -> dict:
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))

    def test_context_router_declares_routes_and_machine_truth(self) -> None:
        router = self._read_json("AI_CONTEXT_ROUTER.json")

        self.assertEqual(router["$schema"], "qqq-ai-context-router/v1")
        self.assertEqual(router["path_match_semantics"], "exact_or_directory_prefix")
        self.assertIn("governance_change", router["routes"])
        self.assertEqual(router["protected_truth"]["strategy"], "configs/frozen/strategy_contract.json")
        self.assertEqual(router["protected_truth"]["api"], "contracts/openapi.json")

    def test_m00_5_task_contract_is_scoped_and_cannot_touch_strategy(self) -> None:
        task = self._read_json("tasks/M00.5.json")

        self.assertEqual(task["task_id"], "M00.5")
        self.assertEqual(task["role"], "governance_maintainer")
        self.assertTrue(task["baseline_promotion_required"])
        self.assertTrue(any(path == "verification/" or path.startswith("verification/") for path in task["allowed_write_paths"]))
        self.assertTrue(any(path == "verification/" or path.startswith("verification/") for path in task["protected_write_paths"]))
        self.assertIn("configs/frozen/", task["forbidden_write_paths"])
        self.assertIn("contracts/", task["forbidden_write_paths"])
        self.assertIn("strategy_contract_unchanged", task["invariants"])

    def test_document_registry_points_to_existing_files_and_marks_references(self) -> None:
        registry = self._read_json("docs/DOCUMENT_REGISTRY.json")
        entries = registry["entries"]
        paths = {entry["path"] for entry in entries}

        self.assertEqual(registry["$schema"], "qqq-document-registry/v1")
        self.assertIn("configs/frozen/strategy_contract.json", paths)
        self.assertIn("docs/V10_CANDIDATE_CONTRACT.md", paths)
        self.assertIn("docs/REPO_CLEANUP_AUDIT_20260812.md", paths)
        for entry in entries:
            self.assertTrue((ROOT / entry["path"]).exists(), entry["path"])
        reference = next(entry for entry in entries if entry["path"] == "docs/V10_CANDIDATE_CONTRACT.md")
        self.assertTrue(reference["not_normative"])


if __name__ == "__main__":
    unittest.main()
