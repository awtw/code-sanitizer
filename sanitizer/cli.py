"""Command line interface.

    sanitizer identities INPUT --job-dir JOB
    sanitizer scan --job-dir JOB [--policy POLICY]
    sanitizer apply --job-dir JOB --output OUT
    sanitizer run INPUT --job-dir JOB --output OUT [--policy POLICY]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import IngestError, JobError, step_apply, step_identities, step_scan


def _print_summary(plan) -> None:
    for key, n in plan.summary().items():
        print(f"  {key:<28} {n}")
    review = [f for f in plan.findings if f.action == "review"]
    for f in review:
        print(f"  REVIEW {f.path}: {f.detail}")


def _identities(args) -> None:
    info = step_identities(args.input, args.job_dir)
    print(f"people: {info['people']}, organisation terms: {info['organization']}, "
          f"credentials in Git config: {info['credentials']}")
    for w in info["warnings"] + info["skipped"]:
        print(f"  note: {w}")
    print(f"review {args.job_dir / 'identities.yaml'}, then run: sanitizer scan --job-dir {args.job_dir}")


def _scan(args) -> None:
    plan = step_scan(args.job_dir, args.policy)
    print(f"plan written to {args.job_dir / 'plan.json'}")
    _print_summary(plan)


def _apply(args) -> None:
    plan = step_apply(args.job_dir, args.output)
    print(f"output written to {args.output} ({len(plan.edits)} files changed, {len(plan.removals)} removed)")
    print(f"diff: {args.job_dir / 'changes.patch'}")


def _run(args) -> None:
    _identities(args)
    _scan(args)
    _apply(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sanitizer", description="De-identify source code for security testing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("identities", help="snapshot the input and collect developer identities from Git")
    p.add_argument("input", type=Path, help="project folder, .zip or single file")
    p.add_argument("--job-dir", type=Path, required=True)
    p.set_defaults(func=_identities)

    p = sub.add_parser("scan", help="build the change plan (dry run)")
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--policy", type=Path)
    p.set_defaults(func=_scan)

    p = sub.add_parser("apply", help="write the de-identified copy")
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=_apply)

    p = sub.add_parser("run", help="identities + scan + apply without stopping for review")
    p.add_argument("input", type=Path)
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--policy", type=Path)
    p.set_defaults(func=_run)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (IngestError, JobError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
