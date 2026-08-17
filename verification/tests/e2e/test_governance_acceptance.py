import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(os.environ.get("QQQ_CANDIDATE_ROOT", Path(__file__).resolve().parents[3]))


class GovernanceAcceptanceE2ETests(unittest.TestCase):
    def test_clean_python_process_can_load_context_contracts(self) -> None:
        code = """
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
router = json.loads((root / 'AI_CONTEXT_ROUTER.json').read_text(encoding='utf-8'))
task = json.loads((root / 'tasks' / 'M00.5.json').read_text(encoding='utf-8'))
registry = json.loads((root / 'docs' / 'DOCUMENT_REGISTRY.json').read_text(encoding='utf-8'))
assert router['routes'][task['route_id']]['default_role'] == task['role']
assert any(entry['path'] == 'configs/frozen/strategy_contract.json' and entry['authority'] == 'L1' for entry in registry['entries'])
print(json.dumps({'task_id': task['task_id'], 'route_id': task['route_id']}, sort_keys=True))
"""
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
            "LC_ALL": "C",
        }
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task_id"], "M00.5")


if __name__ == "__main__":
    unittest.main()
