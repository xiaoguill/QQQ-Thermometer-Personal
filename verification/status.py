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


def promote_from_evidence(current_status: str, evidence_path: str | Path, checksum_path: str | Path | None = None) -> str:
    """Promote status using only a verified, hash-checked Evidence bundle."""

    evidence = load_evidence_bundle(evidence_path, checksum_path)
    return transition_status(current_status, evidence)


def transition_status(current_status: str, evidence: Mapping[str, Any], *, requested: str = "VERIFIED") -> str:
    """Return the only status permitted by a mechanically valid evidence record.

    `ACCEPTED` is deliberately not granted by a text flag.  A later approval
    workflow may explicitly promote a VERIFIED record, but it must retain the
    evidence pointer and cannot bypass VERIFIED.
    """

    _require(current_status == "IMPLEMENTED", "only IMPLEMENTED may cross the verification boundary")
    _require(requested in {"VERIFIED", "ACCEPTED"}, "unsupported requested status")
    _require(evidence.get("result") == "PASS", "evidence result is not PASS")
    _require(evidence.get("quality_gate", {}).get("result") == "PASS", "quality gate did not PASS")
    _require(evidence.get("candidate_sha"), "candidate SHA is missing")
    _require(evidence.get("candidate_sha") == evidence.get("verified_head_sha"), "evidence SHA is not bound to verified HEAD")
    _require(evidence.get("workspace", {}).get("candidate_clean") is True, "candidate workspace is not clean")
    _require(evidence.get("tests", {}).get("tests_failed") == 0, "failed tests are present")
    _require(evidence.get("tests", {}).get("tests_skipped") == 0, "skipped tests are present")
    _require(evidence.get("quality_gate", {}).get("all_required_gates_passed") is True, "required gates did not all pass")
    _require(evidence.get("artifact_hashes"), "artifact hashes are missing")
    _require(evidence.get("evidence_payload_sha256") == _payload_hash(evidence), "evidence payload hash is invalid")

    # The verifier grants VERIFIED. ACCEPTED requires a separate, explicit
    # approval event; accepting a caller-provided string here would recreate
    # the self-attestation problem this package is designed to prevent.
    if requested == "ACCEPTED":
        raise StatusTransitionError("ACCEPTED requires a separate approval event after VERIFIED")
    return "VERIFIED"
