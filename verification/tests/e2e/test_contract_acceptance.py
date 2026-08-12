import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(os.environ.get("QQQ_CANDIDATE_ROOT", Path(__file__).resolve().parents[3]))


class ContractAcceptanceE2ETests(unittest.TestCase):
    def test_candidate_contract_runs_in_clean_subprocess(self) -> None:
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
    'input_data_version': 'acceptance-e2e-v1',
}
print(json.dumps(registry.validate_target_snapshot(snapshot), sort_keys=True))
"""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
            "LC_ALL": "C",
        }
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["target_weights"]["BIL"], 1.0)


if __name__ == "__main__":
    unittest.main()
