"""Independent, fail-closed verification harness.

The harness is intentionally standard-library-only.  It runs candidate code
in a separate isolated Python process, while the expected values, protected
path policy, and gate logic come from the trusted verification checkout.

Normal operation uses two clean checkouts:

    trusted_repo  = checkout of a protected verification baseline
    candidate_repo = checkout of the exact candidate commit

The candidate is never allowed to provide the expected values or to alter the
trusted fixtures during verification.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from verification import VERIFIER_VERSION
from verification.status import StatusTransitionError, transition_status


ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = Path("verification/control/policy.json")
PROTECTED_PATHS_REL = Path("verification/control/protected_paths.json")
MANIFEST_REL = Path("verification/control/protected_manifest.json")
GOLDEN_REL = Path("verification/golden/contract_cases.json")
NEGATIVE_REL = Path("verification/golden/negative_cases.json")
CONTRACT_REL = Path("configs/frozen/strategy_contract.json")


@dataclass
class GateResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message, "details": self.details}


class VerificationFailure(RuntimeError):
    """Raised only for verifier configuration or execution failures."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_hash(value: Any) -> str:
    return sha256_bytes(_canonical_bytes(value))


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"cannot read JSON fixture: {path}") from exc
    if not isinstance(value, dict):
        raise VerificationFailure(f"JSON fixture must be an object: {path}")
    return value


def _json_git_file(repo: Path, ref: str, relative: str) -> dict[str, Any]:
    try:
        raw = _git_bytes(repo, "show", f"{ref}:{relative}")
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, VerificationFailure) as exc:
        raise VerificationFailure(f"cannot read trusted JSON fixture at {ref}:{relative}") from exc
    if not isinstance(value, dict):
        raise VerificationFailure(f"trusted JSON fixture must be an object: {ref}:{relative}")
    return value


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise VerificationFailure(f"git command failed: git -C {repo} {' '.join(args)}\n{result.stderr.strip()}")
    return result


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise VerificationFailure(f"git byte command failed: git -C {repo} {' '.join(args)}")
    return result.stdout


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _resolve_ref(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _clean(repo: Path) -> tuple[bool, str]:
    result = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    output = result.stdout.strip()
    return output == "", output


def _tracked_files(repo: Path, ref: str) -> set[str]:
    result = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def _controlled_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    # Do not pass the caller's arbitrary environment into candidate code.
    path = os.environ.get("PATH", "")
    environment = {
        "PATH": path,
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
        "LC_ALL": "C",
    }
    if extra:
        environment.update(extra)
    return environment


def _run_isolated(candidate_repo: Path, code: str, payload: Mapping[str, Any], *, extra_env: Mapping[str, str] | None = None) -> tuple[int, str, str]:
    envelope = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # The child runs from the candidate checkout.  Passing the absolute
    # Windows path as argv is unsafe for non-ASCII workspace names under some
    # PowerShell/Python combinations, so the child resolves its root from
    # cwd instead.
    command = [sys.executable, "-I", "-c", code, "."]
    result = subprocess.run(
        command,
        cwd=str(candidate_repo),
        input=envelope,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=_controlled_env(extra_env),
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_marker(stdout: str, marker: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(marker):
            try:
                value = json.loads(line[len(marker):])
            except json.JSONDecodeError as exc:
                raise VerificationFailure(f"candidate emitted invalid {marker} JSON") from exc
            if not isinstance(value, dict):
                raise VerificationFailure(f"candidate emitted non-object {marker} result")
            return value
    raise VerificationFailure(f"candidate did not emit {marker}")


def _candidate_probe(candidate_repo: Path, snapshots: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> tuple[dict[str, Any], str, str]:
    code = r'''
import json
import os
import sys
from pathlib import Path

root = Path.cwd().resolve()
sys.path.insert(0, str(root))
from src.thermometer.contracts import ContractError, load_contract
from src.thermometer.policy import generate_target_snapshot

payload = json.loads(sys.stdin.read())
registry = load_contract(root / "configs" / "frozen" / "strategy_contract.json")
valid_results = []
for item in payload["valid"]:
    try:
        result = generate_target_snapshot(item["snapshot"])
        valid_results.append({"case_id": item["case_id"], "accepted": True, "snapshot": result})
    except Exception as exc:
        valid_results.append({"case_id": item["case_id"], "accepted": False, "exception": type(exc).__name__, "message": str(exc)})

negative_results = []
for item in payload["negative"]:
    try:
        registry.validate_target_snapshot(item["snapshot"])
        negative_results.append({"case_id": item["case_id"], "accepted": True})
    except Exception as exc:
        negative_results.append({"case_id": item["case_id"], "accepted": False, "exception": type(exc).__name__, "message": str(exc)})

print("__QQQ_PROBE__" + json.dumps({"valid": valid_results, "negative": negative_results}, ensure_ascii=False, sort_keys=True))
'''
    code_exit, stdout, stderr = _run_isolated(candidate_repo, code, {"valid": snapshots, "negative": negatives})
    if code_exit != 0:
        raise VerificationFailure(f"candidate probe exited {code_exit}: {stderr.strip() or stdout.strip()}")
    return _parse_marker(stdout, "__QQQ_PROBE__"), stdout, stderr


def _test_suite(candidate_repo: Path, suite_name: str, relative_dir: str, *, test_root: Path | None = None) -> tuple[dict[str, Any], str, str]:
    code = r'''
import json
import importlib.util
import os
import sys
import unittest
from pathlib import Path

root = Path.cwd().resolve()
sys.path.insert(0, str(root))
payload = json.loads(sys.stdin.read())
os.environ["QQQ_CANDIDATE_ROOT"] = payload["candidate_root"]
test_root = Path(payload.get("test_root", "."))
if not test_root.is_absolute():
    test_root = (root / test_root).resolve()
else:
    test_root = test_root.resolve()
test_dir = test_root / payload["relative_dir"]
test_files = sorted(test_dir.rglob("test_*.py"))
suite = unittest.TestSuite()
loader = unittest.defaultTestLoader
for index, test_file in enumerate(test_files):
    module_name = f"_qqq_independent_test_{index}"
    spec = importlib.util.spec_from_file_location(module_name, test_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load test module: {test_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    suite.addTests(loader.loadTestsFromModule(module))
stream = unittest.TextTestRunner(verbosity=0).run(suite)
result = {
    "suite": payload["relative_dir"],
    "tests_collected": stream.testsRun,
    "tests_failed": len(stream.failures),
    "tests_errors": len(stream.errors),
    "tests_skipped": len(getattr(stream, "skipped", [])),
    "tests_expected_failures": len(getattr(stream, "expectedFailures", [])),
}
print("__QQQ_UNIT__" + json.dumps(result, sort_keys=True))
sys.exit(0 if stream.wasSuccessful() else 1)
'''
    test_root = test_root or candidate_repo
    test_root_arg = "." if test_root == candidate_repo else os.path.relpath(test_root, candidate_repo)
    exit_code, stdout, stderr = _run_isolated(
        candidate_repo,
        code,
        {
            "suite": suite_name,
            "relative_dir": relative_dir,
            "test_root": test_root_arg,
            "candidate_root": ".",
        },
        extra_env={"QQQ_CANDIDATE_ROOT": "."},
    )
    try:
        result = _parse_marker(stdout, "__QQQ_UNIT__")
    except VerificationFailure as exc:
        diagnostic = {
            "suite": suite_name,
            "relative_dir": relative_dir,
            "tests_collected": 0,
            "tests_failed": 0,
            "tests_errors": 1,
            "tests_skipped": 0,
            "tests_expected_failures": 0,
            "exit_code": exit_code,
            "marker_found": False,
            "diagnostic": {
                "error": str(exc),
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
            },
        }
        return diagnostic, stdout, stderr
    result["exit_code"] = exit_code
    return result, stdout, stderr


def _run_test_suites(repo: Path, suites: list[str]) -> tuple[dict[str, Any], str, str]:
    results: dict[str, Any] = {}
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    for relative_dir in suites:
        result, stdout, stderr = _test_suite(repo, relative_dir, relative_dir)
        results[relative_dir] = result
        all_stdout.append(stdout)
        all_stderr.append(stderr)
    flattened = list(results.values())
    aggregate = {
        "suites": results,
        "tests_collected": sum(int(item.get("tests_collected", 0)) for item in flattened),
        "tests_failed": sum(int(item.get("tests_failed", 0)) for item in flattened),
        "tests_errors": sum(int(item.get("tests_errors", 0)) for item in flattened),
        "tests_skipped": sum(int(item.get("tests_skipped", 0)) for item in flattened),
        "tests_expected_failures": sum(int(item.get("tests_expected_failures", 0)) for item in flattened),
        "exit_code": 0 if all(int(item.get("exit_code", 1)) == 0 for item in flattened) else 1,
        "required_layers": ["unit", "integration", "e2e"],
        "executed_layers": [
            layer for layer, path in (("unit", "tests/unit"), ("integration", "tests/integration"), ("e2e", "tests/e2e"))
            if path in suites
        ],
    }
    return aggregate, "\n".join(all_stdout), "\n".join(all_stderr)


def _developer_tests(candidate_repo: Path, policy: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    suites = list(policy.get("developer_test_suites", []))
    return _run_test_suites(candidate_repo, suites)


def _independent_tests(trusted_repo: Path, candidate_repo: Path, policy: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    suite_map = policy.get("independent_test_suites", {})
    suites = [suite_map[layer] for layer in ("unit", "integration", "e2e") if layer in suite_map]
    results: dict[str, Any] = {}
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    for relative_dir in suites:
        # Execute the trusted test files against Candidate source.  The
        # expected assertions remain in the trusted checkout; imports of the
        # product package resolve from candidate_repo.
        result, stdout, stderr = _test_suite(candidate_repo, relative_dir, relative_dir, test_root=trusted_repo)
        results[relative_dir] = result
        all_stdout.append(stdout)
        all_stderr.append(stderr)
    flattened = list(results.values())
    result = {
        "suites": results,
        "tests_collected": sum(int(item.get("tests_collected", 0)) for item in flattened),
        "tests_failed": sum(int(item.get("tests_failed", 0)) for item in flattened),
        "tests_errors": sum(int(item.get("tests_errors", 0)) for item in flattened),
        "tests_skipped": sum(int(item.get("tests_skipped", 0)) for item in flattened),
        "tests_expected_failures": sum(int(item.get("tests_expected_failures", 0)) for item in flattened),
        "exit_code": 0 if all(int(item.get("exit_code", 1)) == 0 for item in flattened) else 1,
        "required_layers": ["unit", "integration", "e2e"],
        "executed_layers": [
            layer for layer, path in (("unit", "verification/tests/unit"), ("integration", "verification/tests/integration"), ("e2e", "verification/tests/e2e"))
            if path in suites
        ],
    }
    result["suite_map"] = suite_map
    return result, "\n".join(all_stdout), "\n".join(all_stderr)


def _test_integrity_gate(trusted_repo: Path, candidate_repo: Path, policy: Mapping[str, Any]) -> GateResult:
    forbidden_markers = ("@unittest.skip", "pytest.mark.skip", "pytest.mark.xfail", "expected == actual", "assert actual is not None")
    checked: list[str] = []
    violations: list[str] = []
    for relative_dir in policy.get("developer_test_suites", []):
        root = candidate_repo / Path(relative_dir)
        if not root.exists():
            violations.append(f"missing test suite: {relative_dir}")
            continue
        for path in root.rglob("test_*.py"):
            checked.append(path.relative_to(candidate_repo).as_posix())
            content = path.read_text(encoding="utf-8", errors="replace")
            for marker in forbidden_markers:
                if marker in content:
                    violations.append(f"{path.relative_to(candidate_repo).as_posix()}: forbidden marker {marker}")
    if not checked:
        violations.append("no test files were discovered")
    trusted_independent_files: list[str] = []
    for relative_dir in policy.get("independent_test_suites", {}).values():
        root = trusted_repo / Path(relative_dir)
        for path in root.rglob("test_*.py") if root.exists() else []:
            trusted_independent_files.append(path.relative_to(trusted_repo).as_posix())
            content = path.read_text(encoding="utf-8", errors="replace")
            for marker in forbidden_markers:
                if marker in content:
                    violations.append(f"{path.relative_to(trusted_repo).as_posix()}: forbidden marker {marker}")
    if not trusted_independent_files:
        violations.append("no trusted independent test files were discovered")
    return GateResult("test_integrity", "PASS" if not violations else "BLOCKED", "test suites pass integrity scan" if not violations else "test integrity scan failed", {"checked_candidate_files": checked, "checked_trusted_files": trusted_independent_files, "violations": violations})


def _faults(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    faults: list[dict[str, Any]] = []
    same_day = json.loads(json.dumps(snapshot))
    same_day["execution_date"] = same_day["signal_date"]
    faults.append({"case_id": "FAULT-SAME-DAY-EXECUTION", "snapshot": same_day})

    bad_sum = json.loads(json.dumps(snapshot))
    bad_sum["target_weights"] = {"BIL": 0.99}
    faults.append({"case_id": "FAULT-WEIGHT-SUM", "snapshot": bad_sum})

    unknown_state = json.loads(json.dumps(snapshot))
    unknown_state["state"] = "green"
    faults.append({"case_id": "FAULT-UNKNOWN-STATE", "snapshot": unknown_state})

    unknown_asset = json.loads(json.dumps(snapshot))
    unknown_asset["target_weights"] = {"NOT_AN_ASSET": 1.0}
    faults.append({"case_id": "FAULT-UNKNOWN-ASSET", "snapshot": unknown_asset})
    return faults


def _compare_weights(actual: Mapping[str, Any], expected: Mapping[str, Any], tolerance: float) -> tuple[bool, str]:
    if set(actual) != set(expected):
        return False, f"weight keys differ: actual={sorted(actual)} expected={sorted(expected)}"
    for symbol in expected:
        try:
            difference = abs(float(actual[symbol]) - float(expected[symbol]))
        except (TypeError, ValueError) as exc:
            return False, f"weight {symbol} is not numeric: {exc}"
        if difference > tolerance:
            return False, f"weight {symbol} differs by {difference}, tolerance {tolerance}"
    return True, "weights match"


def _write_readonly(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if os.name == "nt":
        subprocess.run(["attrib", "+R", str(path)], capture_output=True, check=False)


def _artifact_hashes(repo: Path, ref: str, paths: Iterable[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in paths:
        content = _git_bytes(repo, "show", f"{ref}:{relative}")
        hashes[relative] = sha256_bytes(content)
    return hashes


def _expected_artifact_hashes(trusted_repo: Path, trusted_ref: str, paths: Iterable[str]) -> dict[str, str]:
    return {relative: sha256_bytes(_git_bytes(trusted_repo, "show", f"{trusted_ref}:{relative}")) for relative in paths}


def _scope_gate(trusted_repo: Path, candidate_repo: Path, trusted_ref: str, candidate_sha: str, protected: Mapping[str, Any]) -> GateResult:
    prefixes = tuple(protected["prefixes"])
    trusted_files = _tracked_files(trusted_repo, trusted_ref)
    candidate_files = _tracked_files(candidate_repo, candidate_sha)
    relevant = sorted({path for path in trusted_files | candidate_files if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)})
    changed: list[str] = []
    for relative in relevant:
        try:
            trusted_content = _git_bytes(trusted_repo, "show", f"{trusted_ref}:{relative}")
        except VerificationFailure:
            trusted_content = None
        try:
            candidate_content = _git_bytes(candidate_repo, "show", f"{candidate_sha}:{relative}")
        except VerificationFailure:
            candidate_content = None
        if trusted_content != candidate_content:
            changed.append(relative)
    if changed:
        return GateResult("protected_scope", "BLOCKED", "protected files differ from trusted verification baseline", {"changed_paths": changed})
    return GateResult("protected_scope", "PASS", "protected paths are unchanged", {"checked_paths": relevant})


def _manifest_gate(trusted_repo: Path, candidate_repo: Path, trusted_ref: str, candidate_sha: str) -> GateResult:
    manifest = _json_git_file(trusted_repo, trusted_ref, MANIFEST_REL.as_posix())
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or not entries:
        return GateResult("protected_manifest", "BLOCKED", "trusted protected manifest has no entries", {})
    mismatches: list[str] = []
    for relative, expected_hash in entries.items():
        try:
            actual_hash = sha256_bytes(_git_bytes(candidate_repo, "show", f"{candidate_sha}:{relative}"))
        except VerificationFailure:
            actual_hash = ""
        if actual_hash != expected_hash:
            mismatches.append(relative)
    if mismatches:
        return GateResult("protected_manifest", "BLOCKED", "candidate artifact hash differs from trusted manifest", {"mismatches": mismatches})
    return GateResult("protected_manifest", "PASS", "candidate protected artifacts match trusted manifest", {"artifact_count": len(entries)})


def _quality_result(gates: list[GateResult], required_names: Iterable[str]) -> tuple[str, bool]:
    observed = {gate.name: gate for gate in gates}
    required = set(required_names)
    missing = sorted(required - set(observed))
    if missing:
        return "BLOCKED", False
    bad = [gate for gate in gates if gate.status != "PASS"]
    if bad:
        if any(gate.status == "BLOCKED" for gate in bad):
            return "BLOCKED", False
        return "FAIL", False
    return "PASS", True


def _evidence_integrity_gate(artifact_hashes: Mapping[str, str], candidate_sha: str, verified_head_sha: str) -> GateResult:
    invalid = [path for path, digest in artifact_hashes.items() if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)]
    if invalid:
        return GateResult("evidence_integrity", "BLOCKED", "artifact hash set is incomplete or malformed", {"invalid_paths": invalid})
    if candidate_sha != verified_head_sha:
        return GateResult("evidence_integrity", "BLOCKED", "evidence candidate SHA is not bound to verified HEAD", {})
    return GateResult("evidence_integrity", "PASS", "evidence prerequisites and artifact hashes are structurally valid", {"artifact_count": len(artifact_hashes)})


def _fault_injection(candidate_repo: Path, negative_payload: list[dict[str, Any]]) -> tuple[bool, dict[str, Any], str, str]:
    """Mutate candidate code in a disposable copy and require the faults to be caught."""

    with tempfile.TemporaryDirectory(prefix="qqq-fault-") as temp_dir:
        mutated_repo = Path(temp_dir) / "candidate"
        shutil.copytree(candidate_repo, mutated_repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        target = mutated_repo / "src" / "thermometer" / "contracts.py"
        source = target.read_text(encoding="utf-8")
        original = '_require(execution_date > signal_date, "execution_date must be after signal_date")'
        mutation = '_require(execution_date >= signal_date, "execution_date must be after signal_date")'
        if original not in source:
            return False, {"mutation": "same_day_execution_acceptance", "applied": False}, "", "mutation target not found"
        target.write_text(source.replace(original, mutation, 1), encoding="utf-8")
        probe_result, stdout, stderr = _candidate_probe(mutated_repo, [], negative_payload)
        result_map = {item["case_id"]: item for item in probe_result.get("negative", [])}
        detected = [item["case_id"] for item in negative_payload if result_map.get(item["case_id"], {}).get("accepted") is True]
        details = {"mutation": "same_day_execution_acceptance", "applied": True, "detected_by_negative_tests": bool(detected), "detected_fault_cases": detected}
        return bool(detected), details, stdout, stderr


def run_verification(
    *,
    trusted_repo: str | Path,
    candidate_repo: str | Path,
    trusted_ref: str,
    candidate_sha: str,
    output_dir: str | Path,
    bootstrap: bool = False,
) -> tuple[int, Path]:
    """Verify one exact candidate and write a tamper-evident evidence bundle.

    Returns `(process_exit_code, evidence_path)`.  Exit code is zero only when
    every required gate passes and status transition to VERIFIED is legal.
    """

    trusted_repo = Path(trusted_repo).resolve()
    candidate_repo = Path(candidate_repo).resolve()
    output_dir = Path(output_dir).resolve()
    run_id = f"verify-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    gates: list[GateResult] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    evidence_path = output_dir / "evidence.json"

    try:
        trusted_head = _head(trusted_repo)
        candidate_head = _head(candidate_repo)
        resolved_trusted_ref = _resolve_ref(trusted_repo, trusted_ref)
        resolved_candidate_sha = _resolve_ref(candidate_repo, candidate_sha)

        if resolved_candidate_sha != candidate_head:
            gates.append(GateResult("candidate_commit", "BLOCKED", "candidate SHA is not the candidate repository HEAD", {"candidate_sha": resolved_candidate_sha, "head": candidate_head}))
        elif not all(char in "0123456789abcdef" for char in resolved_candidate_sha) or len(resolved_candidate_sha) != 40:
            gates.append(GateResult("candidate_commit", "BLOCKED", "candidate SHA is not a full commit SHA", {}))
        else:
            gates.append(GateResult("candidate_commit", "PASS", "candidate SHA resolves to the candidate HEAD", {"candidate_sha": resolved_candidate_sha}))

        trusted_clean, trusted_status = _clean(trusted_repo)
        candidate_clean, candidate_status = _clean(candidate_repo)
        if not trusted_clean or not candidate_clean:
            gates.append(GateResult("workspace_clean", "BLOCKED", "trusted or candidate workspace is dirty", {"trusted_status": trusted_status, "candidate_status": candidate_status}))
        else:
            gates.append(GateResult("workspace_clean", "PASS", "trusted and candidate workspaces are clean", {}))

        if trusted_head != resolved_trusted_ref:
            gates.append(GateResult("trusted_ref_separation", "BLOCKED", "trusted checkout HEAD is not the requested trusted ref", {"trusted_head": trusted_head, "trusted_ref": resolved_trusted_ref}))
        elif resolved_trusted_ref == resolved_candidate_sha:
            gates.append(GateResult("trusted_ref_separation", "BLOCKED", "trusted ref equals candidate; bootstrap mode cannot grant verification", {"trusted_ref": resolved_trusted_ref, "candidate_sha": resolved_candidate_sha, "bootstrap_requested": bootstrap}))
        else:
            gates.append(GateResult("trusted_ref_separation", "PASS", "trusted verification ref is accepted", {"trusted_ref": resolved_trusted_ref, "bootstrap": bootstrap}))

        policy = _json_git_file(trusted_repo, resolved_trusted_ref, POLICY_REL.as_posix())
        protected = _json_git_file(trusted_repo, resolved_trusted_ref, PROTECTED_PATHS_REL.as_posix())
        if policy.get("fail_closed") is not True:
            gates.append(GateResult("policy_integrity", "BLOCKED", "trusted policy is not fail-closed", {}))
        else:
            gates.append(GateResult("policy_integrity", "PASS", "trusted policy is fail-closed", {"policy_id": policy.get("policy_id")}))

        gates.append(_scope_gate(trusted_repo, candidate_repo, resolved_trusted_ref, resolved_candidate_sha, protected))
        gates.append(_manifest_gate(trusted_repo, candidate_repo, resolved_trusted_ref, resolved_candidate_sha))
        gates.append(_test_integrity_gate(trusted_repo, candidate_repo, policy))

        golden = _json_git_file(trusted_repo, resolved_trusted_ref, GOLDEN_REL.as_posix())
        negative = _json_git_file(trusted_repo, resolved_trusted_ref, NEGATIVE_REL.as_posix())
        valid_cases = golden.get("cases", [])
        negative_cases = negative.get("cases", [])
        if not isinstance(valid_cases, list) or not valid_cases or not isinstance(negative_cases, list) or not negative_cases:
            raise VerificationFailure("trusted Golden or Negative Dataset is empty")

        developer_result, developer_stdout, developer_stderr = _developer_tests(candidate_repo, policy)
        stdout_parts.extend([developer_stdout])
        stderr_parts.extend([developer_stderr])
        developer_suites = developer_result.get("suites", {})
        developer_layers_nonempty = all(int(developer_suites.get(path, {}).get("tests_collected", 0)) > 0 for path in ("tests/unit", "tests/integration", "tests/e2e"))
        developer_pass = developer_result.get("exit_code") == 0 and developer_result.get("tests_collected", 0) > 0 and developer_layers_nonempty and developer_result.get("tests_failed", 0) == 0 and developer_result.get("tests_errors", 0) == 0 and developer_result.get("tests_skipped", 0) == 0 and developer_result.get("tests_expected_failures", 0) == 0
        gates.append(GateResult("developer_tests", "PASS" if developer_pass else "FAIL", "developer test suite executed" if developer_pass else "developer test suite failed or was empty", developer_result))

        independent_result, independent_stdout, independent_stderr = _independent_tests(trusted_repo, candidate_repo, policy)
        stdout_parts.append(independent_stdout)
        stderr_parts.append(independent_stderr)
        independent_suites = independent_result.get("suites", {})
        independent_layers_nonempty = all(int(independent_suites.get(path, {}).get("tests_collected", 0)) > 0 for path in ("verification/tests/unit", "verification/tests/integration", "verification/tests/e2e"))
        independent_pass = (
            independent_result.get("exit_code") == 0
            and independent_result.get("tests_collected", 0) > 0
            and independent_layers_nonempty
            and independent_result.get("tests_failed", 0) == 0
            and independent_result.get("tests_errors", 0) == 0
            and independent_result.get("tests_skipped", 0) == 0
            and independent_result.get("tests_expected_failures", 0) == 0
            and set(independent_result.get("executed_layers", [])) == {"unit", "integration", "e2e"}
        )
        gates.append(GateResult("independent_tests", "PASS" if independent_pass else "FAIL", "trusted independent test suites executed" if independent_pass else "trusted independent test suites failed or incomplete", independent_result))

        expected_fields = {"case_id", "expected_target_weights", "expected_state", "expected_reason_codes"}
        valid_payload = []
        for case in valid_cases:
            snapshot = {key: value for key, value in case.items() if key not in expected_fields}
            snapshot["strategy_version"] = golden["strategy_version"]
            valid_payload.append({"case_id": case["case_id"], "snapshot": snapshot})
        negative_payload = [{"case_id": case["case_id"], "snapshot": case["snapshot"]} for case in negative_cases]
        probe_result, probe_stdout, probe_stderr = _candidate_probe(candidate_repo, valid_payload, negative_payload)
        stdout_parts.extend([probe_stdout])
        stderr_parts.extend([probe_stderr])

        candidate_valid = {item["case_id"]: item for item in probe_result.get("valid", [])}
        golden_failures: list[str] = []
        tolerance = 1e-8
        for case in valid_cases:
            actual = candidate_valid.get(case["case_id"])
            if not actual or actual.get("accepted") is not True:
                golden_failures.append(f"{case['case_id']}: candidate rejected valid case")
                continue
            matches, message = _compare_weights(actual["snapshot"].get("target_weights", {}), case["expected_target_weights"], tolerance)
            if not matches:
                golden_failures.append(f"{case['case_id']}: {message}")
            if actual["snapshot"].get("state") != case.get("expected_state"):
                golden_failures.append(f"{case['case_id']}: state differs: actual={actual['snapshot'].get('state')} expected={case.get('expected_state')}")
            if actual["snapshot"].get("reason_codes") != case.get("expected_reason_codes"):
                golden_failures.append(f"{case['case_id']}: reason codes differ")
            if actual["snapshot"].get("strategy_version") != golden.get("strategy_version"):
                golden_failures.append(f"{case['case_id']}: strategy version changed")
        gates.append(GateResult("golden_dataset", "PASS" if not golden_failures else "FAIL", "independent Golden cases match" if not golden_failures else "Golden case mismatch", {"case_count": len(valid_cases), "failures": golden_failures}))

        negative_results = {item["case_id"]: item for item in probe_result.get("negative", [])}
        negative_failures = [case["case_id"] for case in negative_cases if negative_results.get(case["case_id"], {}).get("accepted") is not False]
        gates.append(GateResult("negative_tests", "PASS" if not negative_failures else "FAIL", "invalid states were rejected" if not negative_failures else "invalid state was accepted", {"case_count": len(negative_cases), "failures": negative_failures}))

        fault_inputs = _faults({
            "strategy_version": "v10_preserve_shock_recovery",
            "signal_date": "2020-03-16",
            "execution_date": "2020-03-16",
            "indicators": {"ready": True},
            "input_data_version": "fault-fixture-v1",
        })
        fault_probe, fault_stdout, fault_stderr = _candidate_probe(candidate_repo, [], fault_inputs)
        stdout_parts.extend([fault_stdout])
        stderr_parts.extend([fault_stderr])
        fault_results = {item["case_id"]: item for item in fault_probe.get("negative", [])}
        fault_failures = [fault["case_id"] for fault in fault_inputs if fault_results.get(fault["case_id"], {}).get("accepted") is not False]
        gates.append(GateResult("negative_fault_inputs", "PASS" if not fault_failures else "FAIL", "invalid fault inputs were rejected" if not fault_failures else "invalid fault input was accepted", {"fault_count": len(fault_inputs), "failures": fault_failures}))

        mutation_killed, mutation_details, mutation_stdout, mutation_stderr = _fault_injection(candidate_repo, negative_payload)
        stdout_parts.append(mutation_stdout)
        stderr_parts.append(mutation_stderr)
        gates.append(GateResult("fault_injection", "PASS" if mutation_killed else "FAIL", "mutated candidate code was caught by protected negative tests" if mutation_killed else "mutated candidate code survived protected negative tests", mutation_details))

        final_trusted_clean, final_trusted_status = _clean(trusted_repo)
        final_candidate_clean, final_candidate_status = _clean(candidate_repo)
        if not final_trusted_clean or not final_candidate_clean:
            workspace_gate = next(gate for gate in gates if gate.name == "workspace_clean")
            workspace_gate.status = "BLOCKED"
            workspace_gate.message = "trusted or candidate workspace became dirty during verification"
            workspace_gate.details.update({"trusted_status_after_tests": final_trusted_status, "candidate_status_after_tests": final_candidate_status})

        layers_pass = developer_pass and independent_pass and set(developer_result.get("executed_layers", [])) == {"unit", "integration", "e2e"}
        gates.append(GateResult("controlled_environment", "PASS", "candidate code ran in isolated Python subprocesses with deterministic environment", {"python": sys.version, "platform": platform.platform(), "python_executable": sys.executable, "test_layers": ["unit", "integration", "e2e"], "layers_pass": layers_pass}))

        artifact_paths = list(protected.get("exact_paths", []))
        artifact_hashes = _artifact_hashes(candidate_repo, resolved_candidate_sha, artifact_paths)
        expected_artifact_hashes = _expected_artifact_hashes(trusted_repo, resolved_trusted_ref, artifact_paths)
        artifact_mismatches = sorted(path for path in artifact_paths if artifact_hashes.get(path) != expected_artifact_hashes.get(path))
        if artifact_mismatches:
            gates.append(GateResult("evidence_integrity", "BLOCKED", "candidate artifact hashes differ from trusted protected artifacts", {"mismatches": artifact_mismatches}))
        else:
            gates.append(_evidence_integrity_gate(artifact_hashes, resolved_candidate_sha, candidate_head))
        quality, all_required = _quality_result(gates, policy.get("required_gates", []))
        test_failures = int(developer_result.get("tests_failed", 0)) + int(developer_result.get("tests_errors", 0)) + int(developer_result.get("tests_expected_failures", 0)) + int(independent_result.get("tests_failed", 0)) + int(independent_result.get("tests_errors", 0)) + int(independent_result.get("tests_expected_failures", 0)) + len(golden_failures) + len(negative_failures) + len(fault_failures) + (0 if mutation_killed else 1)
        test_skips = int(developer_result.get("tests_skipped", 0)) + int(independent_result.get("tests_skipped", 0))
        test_collected = int(developer_result.get("tests_collected", 0)) + int(independent_result.get("tests_collected", 0)) + len(valid_cases) + len(negative_cases) + len(fault_inputs) + 1
        test_counts = {
            "tests_collected": test_collected,
            "tests_passed": test_collected - test_failures - test_skips,
            "tests_failed": test_failures,
            "tests_skipped": test_skips,
            "tests_errors": int(developer_result.get("tests_errors", 0)) + int(independent_result.get("tests_errors", 0)),
            "tests_expected_failures": int(developer_result.get("tests_expected_failures", 0)) + int(independent_result.get("tests_expected_failures", 0)),
            "developer_tests": developer_result,
            "independent_tests": independent_result,
            "golden_cases": len(valid_cases),
            "negative_cases": len(negative_cases),
            "fault_cases": len(fault_inputs),
            "reverse_proof": {"negative_tests": True, "fault_injection": mutation_killed}
        }
        evidence: dict[str, Any] = {
            "schema": "qqq-independent-evidence/v1",
            "run_id": run_id,
            "candidate_sha": resolved_candidate_sha,
            "verified_head_sha": candidate_head,
            "trusted_ref": resolved_trusted_ref,
            "trusted_head_sha": trusted_head,
            "verifier_version": VERIFIER_VERSION,
            "policy_id": policy.get("policy_id"),
            "policy_version": policy.get("policy_version"),
            "required_gates": list(policy.get("required_gates", [])),
            "command": "verification/cli.py run",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "environment": {"python": sys.version, "platform": platform.platform(), "python_executable": sys.executable, "controlled": True, "network_access": "not_requested"},
            "ci_context": {
                "is_ci": os.environ.get("CI", "").lower() == "true",
                "provider": os.environ.get("GITHUB_ACTIONS", "") and "github-actions" or "local",
                "workflow": os.environ.get("GITHUB_WORKFLOW"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "event": os.environ.get("GITHUB_EVENT_NAME"),
                "runner_os": os.environ.get("RUNNER_OS"),
            },
            "workspace": {"trusted_clean": final_trusted_clean, "candidate_clean": final_candidate_clean, "candidate_repo": str(candidate_repo)},
            "tests": test_counts,
            "gates": [gate.as_dict() for gate in gates],
            "quality_gate": {"result": quality, "all_required_gates_passed": all_required and quality == "PASS", "fail_closed": True},
            "artifact_hashes": artifact_hashes,
            "stdout_sha256": sha256_bytes("\n".join(stdout_parts).encode("utf-8")),
            "stderr_sha256": sha256_bytes("\n".join(stderr_parts).encode("utf-8")),
            "result": "PASS" if quality == "PASS" else quality,
            "status_transition": "UNVERIFIED"
        }
        try:
            # Hash the canonical evidence payload excluding the self-hash and
            # status-transition fields; the final file hash is stored in the
            # companion checksum artifact.
            payload_for_hash = dict(evidence)
            payload_for_hash.pop("evidence_payload_sha256", None)
            payload_for_hash.pop("status_transition", None)
            evidence["evidence_payload_sha256"] = sha256_bytes(_canonical_bytes(payload_for_hash))
            evidence["status_transition"] = transition_status("IMPLEMENTED", evidence)
        except StatusTransitionError as exc:
            evidence["status_transition_error"] = str(exc)
            if quality == "PASS":
                evidence["result"] = "FAIL"
                quality = "FAIL"
                evidence["quality_gate"]["result"] = quality

        final_bytes = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        _write_readonly(evidence_path, final_bytes)
        _write_readonly(output_dir / "evidence.sha256", (sha256_bytes(final_bytes) + "  evidence.json\n").encode("ascii"))
        _write_readonly(output_dir / "stdout.txt", "\n".join(stdout_parts).encode("utf-8"))
        _write_readonly(output_dir / "stderr.txt", "\n".join(stderr_parts).encode("utf-8"))
        return (0 if evidence["result"] == "PASS" and evidence["status_transition"] == "VERIFIED" else 1), evidence_path
    except Exception as exc:
        fallback = {
            "schema": "qqq-independent-evidence/v1",
            "run_id": run_id,
            "candidate_sha": candidate_sha,
            "trusted_ref": trusted_ref,
            "verifier_version": VERIFIER_VERSION,
            "result": "BLOCKED",
            "status_transition": "UNVERIFIED",
            "quality_gate": {"result": "BLOCKED", "all_required_gates_passed": False, "fail_closed": True},
            "error": f"{type(exc).__name__}: {exc}",
            "gates": [gate.as_dict() for gate in gates],
            "environment": {"python": sys.version, "platform": platform.platform(), "controlled": True, "network_access": "not_requested"},
        }
        fallback_payload = dict(fallback)
        fallback_payload.pop("evidence_payload_sha256", None)
        fallback_payload.pop("status_transition", None)
        fallback["evidence_payload_sha256"] = sha256_bytes(_canonical_bytes(fallback_payload))
        final_bytes = json.dumps(fallback, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        _write_readonly(evidence_path, final_bytes)
        _write_readonly(output_dir / "evidence.sha256", (sha256_bytes(final_bytes) + "  evidence.json\n").encode("ascii"))
        _write_readonly(output_dir / "stdout.txt", "\n".join(stdout_parts).encode("utf-8"))
        _write_readonly(output_dir / "stderr.txt", "\n".join(stderr_parts).encode("utf-8"))
        return 1, evidence_path
