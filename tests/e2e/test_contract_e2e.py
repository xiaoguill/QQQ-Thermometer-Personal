import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ContractE2ETests(unittest.TestCase):
    def test_clean_python_process_can_load_and_validate_contract(self) -> None:
        code = """
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from src.thermometer.contracts import load_contract
registry = load_contract(root / 'configs' / 'frozen' / 'strategy_contract.json')
snapshot = {
    'strategy_version': 'v10_preserve_shock_recovery',
    'signal_date': '2020-03-16',
    'execution_date': '2020-03-17',
    'state': 'warming',
    'target_weights': {'BIL': 1.0},
    'indicators': {'ready': False},
    'reason_codes': ['warmup_insufficient_history'],
    'input_data_version': 'e2e-fixture-v1',
}
print(json.dumps(registry.validate_target_snapshot(snapshot), sort_keys=True))
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
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target_weights"]["BIL"], 1.0)


if __name__ == "__main__":
    unittest.main()
