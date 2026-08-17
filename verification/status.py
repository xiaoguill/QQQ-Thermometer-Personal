"""Fail-closed task status transitions.

The product may record an implementation status, but it may not promote an
implementation to VERIFIED from natural-language output.  Only an evidence
record satisfying the mechanical contract can cross that boundary.
"""

from __future__ import annotations

from typing import Any, Mapping

import hashlib
import json
from pathlib import Path


_HEX40 = set("0123456789abcdef")
_HEX64 = set("0123456789abcdef")
_REQUIRED_GATES = {
    "candidate_commit",
    "workspace_clean",
    "trusted_ref_separation",
    "policy_integrity",
    "ownership_scope",
    "protected_scope",
    "protected_manifest",
    "test_integrity",
    "developer_tests",
    "independent_tests",
    "golden_dataset",
    "negative_tests",
    "negative_fault_inputs",
    "fault_injection",
    "controlled_environment",
    "evidence_integrity",
}


class StatusTransitionError(ValueError):
    """Raised when a task tries to cross the verification boundary improperly."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StatusTransitionError(message)


def _payload_hash(evidence: Mapping[str, Any]) -> str:
    payload = dict(evidence)
    payload.pop("evidence_payload_sha256", None)
    payload.pop("status_transition", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_evidence_bundle(evidence_path: str | Path, checksum_path: str | Path | None = None) -> dict[str, Any]:
    """Load Evidence only when its payload and companion file hash match."""

    evidence_file = Path(evidence_path)
    checksum_file = Path(checksum_path) if checksum_path is not None else evidence_file.with_name("evidence.sha256")
    _require(evidence_file.is_file(), "evidence file is missing")
    _require(checksum_file.is_file(), "evidence checksum file is missing")
    raw = evidence_file.read_bytes()
    expected_file_hash = checksum_file.read_text(encoding="ascii").strip().split()[0]
    actual_file_hash = hashlib.sha256(raw).hexdigest()
    _require(actual_file_hash == expected_file_hash, "evidence file hash does not match checksum artifact")
    try:
        evidence = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusTransitionError("evidence JSON is invalid") from exc
    _require(isinstance(evidence, dict), "evidence root must be an object")
    _require(evidence.get("evidence_payload_sha256") == _payload_hash(evidence), "evidence payload hash is invalid")
    return evidence


def _require_zero_count(counts: Mapping[str, Any], field: str) -> None:
    value = counts.get(field)
    _require(isinstance(value, int) and not isinstance(value, bool), f"evidence test count {field} is missing or not an integer")
    _require(value == 0, f"evidence test count {field} is not zero")


def _validate_mechanical_evidence(evidence: Mapping[str, Any]) -> None:
    _require(evidence.get("schema") == "qqq-independent-evidence/v1", "unsupported evidence schema")
    candidate_sha = evidence.get("candidate_sha")
    _require(isinstance(candidate_sha, str) and len(candidate_sha) == 40 and set(candidate_sha) <= _HEX40, "candidate SHA is not a full hexadecimal commit SHA")
    _require(candidate_sha == evidence.get("verified_head_sha"), "evidence SHA is not bound to verified HEAD")
    _require(evidence.get("result") == "PASS", "evidence result is not PASS")

    quality_gate = evidence.get("quality_gate")
    _require(isinstance(quality_gate, Mapping), "quality gate is missing")
    _require(quality_gate.get("result") == "PASS", "quality gate did not PASS")
    _require(quality_gate.get("all_required_gates_passed") is True, "required gates did not all pass")
    _require(quality_gate.get("fail_closed") is True, "quality gate is not fail-closed")

    gates = evidence.get("gates")
    _require(isinstance(gates, list) and gates, "evidence gates are missing")
    gate_names = [gate.get("name") for gate in gates if isinstance(gate, Mapping)]
    _require(len(gate_names) == len(set(gate_names)), "evidence gate names are not unique")
    _require(set(gate_names) == _REQUIRED_GATES, "evidence gate set is incomplete or contains unknown gates")
    _require(all(isinstance(gate, Mapping) and gate.get("status") == "PASS" for gate in gates), "one or more evidence gates are not PASS")

    workspace = evidence.get("workspace")
    _require(isinstance(workspace, Mapping), "workspace evidence is missing")
    _require(workspace.get("trusted_clean") is True and workspace.get("candidate_clean") is True, "trusted or candidate workspace is not clean")

    environment = evidence.get("environment")
    _require(isinstance(environment, Mapping) and environment.get("controlled") is True, "controlled environment evidence is missing")

    tests = evidence.get("tests")
    _require(isinstance(tests, Mapping), "test evidence is missing")
    collected = tests.get("tests_collected")
    _require(isinstance(collected, int) and not isinstance(collected, bool) and collected > 0, "no tests were collected")
    for field in ("tests_failed", "tests_skipped", "tests_errors", "tests_expected_failures"):
        _require_zero_count(tests, field)
    _require(tests.get("tests_passed") == collected, "passed test count does not equal collected test count")

    reverse_proof = tests.get("reverse_proof")
    _require(isinstance(reverse_proof, Mapping), "reverse proof evidence is missing")
    _require(reverse_proof.get("negative_tests") is True and reverse_proof.get("fault_injection") is True, "reverse proof is incomplete")

    for suite_name in ("developer_tests", "independent_tests"):
        suite = tests.get(suite_name)
        _require(isinstance(suite, Mapping), f"{suite_name} evidence is missing")
        _require(suite.get("exit_code") == 0, f"{suite_name} exit code is not zero")
        _require(isinstance(suite.get("tests_collected"), int) and suite["tests_collected"] > 0, f"{suite_name} collected no tests")
        for field in ("tests_failed", "tests_skipped", "tests_errors", "tests_expected_failures"):
            _require_zero_count(suite, field)
        _require(set(suite.get("executed_layers", [])) == {"unit", "integration", "e2e"}, f"{suite_name} did not execute all required layers")

    artifact_hashes = evidence.get("artifact_hashes")
    _require(isinstance(artifact_hashes, Mapping) and artifact_hashes, "artifact hashes are missing")
    _require(all(isinstance(digest, str) and len(digest) == 64 and set(digest) <= _HEX64 for digest in artifact_hashes.values()), "artifact hashes are incomplete or malformed")
    _require(isinstance(evidence.get("evidence_payload_sha256"), str) and len(evidence["evidence_payload_sha256"]) == 64, "evidence payload hash is missing")


def promote_from_evidence(current_status: str, evidence_path: str | Path, checksum_path: str | Path | None = None) -> str:
    """Promote status using only a verified, hash-checked Evidence bundle."""

    evidence = load_evidence_bundle(evidence_path, checksum_path)
    _require(evidence.get("status_transition") == "VERIFIED", "evidence was not issued as VERIFIED by the harness")
    return transition_status(current_status, evidence)


def transition_status(current_status: str, evidence: Mapping[str, Any], *, requested: str = "VERIFIED") -> str:
    """Return the only status permitted by a mechanically valid evidence record.

    `ACCEPTED` is deliberately not granted by a text flag.  A later approval
    workflow may explicitly promote a VERIFIED record, but it must retain the
    evidence pointer and cannot bypass VERIFIED.
    """

    _require(current_status == "IMPLEMENTED", "only IMPLEMENTED may cross the verification boundary")
    _require(requested in {"VERIFIED", "ACCEPTED"}, "unsupported requested status")
    _validate_mechanical_evidence(evidence)
    _require(evidence.get("evidence_payload_sha256") == _payload_hash(evidence), "evidence payload hash is invalid")

    # The verifier grants VERIFIED. ACCEPTED requires a separate, explicit
    # approval event; accepting a caller-provided string here would recreate
    # the self-attestation problem this package is designed to prevent.
    if requested == "ACCEPTED":
        raise StatusTransitionError("ACCEPTED requires a separate approval event after VERIFIED")
    return "VERIFIED"
