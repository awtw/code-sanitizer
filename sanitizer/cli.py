"""Command line interface.

    sanitizer identities INPUT --job-dir JOB
    sanitizer scan --job-dir JOB [--policy POLICY]
    sanitizer apply --job-dir JOB --output OUT
    sanitizer verify --job-dir JOB [--profile TEST_PROFILE]
    sanitizer export --job-dir JOB --package OUT.zip [--accept-review] [--allow-unqualified]
    sanitizer run INPUT --job-dir JOB --output OUT [--policy POLICY]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import step_export
from .pipeline import IngestError, JobError, step_apply, step_identities, step_scan
from .verify import step_verify


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


def _verify(args) -> int:
    report = step_verify(args.job_dir, args.profile)
    for c in [report["syntax"], *report["comparisons"]]:
        print(f"  {(c['project'] or '.'):<12} {c['step']:<9} {c['level']}  "
              f"baseline={c['baseline']:<5} sanitized={c['sanitized']:<5} {c['verdict']}")
    print(f"delivery status: {report['delivery_status']}  (isolation: {report['isolation']})")
    for r in report["reasons"]:
        print(f"  - {r}")
    print(f"details: {args.job_dir / 'verify.json'}")
    return 0 if report["delivery_status"] in {"qualified", "incomplete"} else 1


def _export(args) -> int:
    result = step_export(args.job_dir, args.package, args.accept_review, args.allow_unqualified)
    print(f"report (internal): {result['report']}")
    if result["blocked"]:
        print("export blocked:")
        for b in result["blocked"]:
            print(f"  - {b}")
        return 1
    print(f"package: {result['package']}")
    print(f"sha256:  {result['package_sha256']}")
    return 0


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

    p = sub.add_parser("verify", help="build and test the original and the de-identified copy, then compare")
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--profile", type=Path, help="test profile (required levels, env, per-project settings)")
    p.set_defaults(func=_verify)

    p = sub.add_parser("export", help="final leak scan and deliverable ZIP")
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--package", type=Path, required=True, help="ZIP file to create")
    p.add_argument("--accept-review", action="store_true", help="findings marked review were checked by a person")
    p.add_argument("--allow-unqualified", action="store_true",
                   help="export even if verification is not qualified (package is labelled UNQUALIFIED)")
    p.set_defaults(func=_export)

    p = sub.add_parser("run", help="identities + scan + apply without stopping for review")
    p.add_argument("input", type=Path)
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--policy", type=Path)
    p.set_defaults(func=_run)

    args = ap.parse_args(argv)
    try:
        return args.func(args) or 0
    except (IngestError, JobError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
