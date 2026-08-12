"""Command-line entry point for the trusted verification runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.harness import run_verification
from verification.status import promote_from_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fail-closed independent verification for one candidate commit.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--trusted-repo", required=True)
    run.add_argument("--candidate-repo", required=True)
    run.add_argument("--trusted-ref", required=True)
    run.add_argument("--candidate-sha", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--bootstrap", action="store_true", help="allow trusted ref == candidate only for initial bootstrap review")
    promote = subparsers.add_parser("promote")
    promote.add_argument("--current-status", required=True)
    promote.add_argument("--evidence", required=True)
    promote.add_argument("--checksum")
    args = parser.parse_args(argv)

    if args.command == "run":
        code, evidence_path = run_verification(
            trusted_repo=args.trusted_repo,
            candidate_repo=args.candidate_repo,
            trusted_ref=args.trusted_ref,
            candidate_sha=args.candidate_sha,
            output_dir=args.output,
            bootstrap=args.bootstrap,
        )
        print(json.dumps({"exit_code": code, "evidence": str(evidence_path)}, ensure_ascii=False, sort_keys=True))
        return code
    if args.command == "promote":
        try:
            status = promote_from_evidence(args.current_status, args.evidence, args.checksum)
        except Exception as exc:
            print(json.dumps({"status": "UNVERIFIED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps({"status": status}, ensure_ascii=False, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
