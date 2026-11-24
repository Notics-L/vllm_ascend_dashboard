"""Classify a GitHub Actions failure and optionally locate introducing PRs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import AnalysisError, AnalysisRequest, DailyFailureAgent, HarnessOptions
from .agent import DEFAULT_MODEL, DEFAULT_PROVIDER


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("failure_url", help="GitHub Actions failed Run or Job URL")
    parser.add_argument(
        "--case-name", help="Identify a case if the Run contains multiple failed Jobs"
    )
    parser.add_argument("--success-url", help="Optional comparable successful Run or Job URL")
    parser.add_argument(
        "--harness-home", type=Path, required=True, help="Harness profile and session directory"
    )
    parser.add_argument(
        "--workspace", type=Path, help="Dedicated directory with prepared logs or a Git checkout"
    )
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER, help="Provider route in the Harness profile"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model ID for the selected provider")
    parser.add_argument(
        "--harness-patch",
        type=Path,
        action="append",
        default=[],
        help="Extra Harness profile patch",
    )
    args = parser.parse_args()

    try:
        request = AnalysisRequest(
            failure_url=args.failure_url, case_name=args.case_name, success_url=args.success_url
        )
        options = HarnessOptions(
            harness_home=args.harness_home,
            workspace=args.workspace,
            provider=args.provider,
            model=args.model,
            patches=tuple(args.harness_patch),
        )
        result = DailyFailureAgent(options).analyze(request)
    except (AnalysisError, OSError, ValueError) as exc:
        print(f"Daily failure analysis failed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
