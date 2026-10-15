"""Final gate and packaging.

1. Re-scan the apply output with the same identities and policy. Anything
   that would still be changed or removed is a leak: export is blocked and
   cannot be forced.
2. Findings marked ``review`` (unknown e-mail addresses, binary files with
   matches) block export until accepted with ``--accept-review``.
3. Verification must be ``qualified``; ``--allow-unqualified`` exports anyway
   but the package and notes are labelled UNQUALIFIED.
4. The ZIP is deterministic (sorted entries, fixed timestamps and modes).
   Internal files (manifest, report) stay in the job folder.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .ingest import tree_sha256
from .pipeline import JobError, read_meta, write_meta
from .report import write_html_report
from .scan import Plan, build_plan
from .terms import load_identities, load_policy

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
NOTES_NAME = "SANITIZATION-NOTES.md"


def _leak_scan(output: Path, job: Path, meta: dict) -> tuple[Plan, list[str]]:
    policy = load_policy(load_identities(job / "identities.yaml"),
                         Path(meta["policy"]) if meta.get("policy") else None)
    plan = build_plan(output, policy)
    leaks = []
    for rel, edits in sorted(plan.edits.items()):
        leaks.append(f"{rel}: {len(edits)} sensitive match(es) still present")
    for rel in plan.removals:
        leaks.append(f"{rel}: leftover file still present")
    return plan, leaks


def _notes(meta: dict, verify: dict, secrets: list[dict], qualified: bool) -> bytes:
    lines = ["# Sanitization notes", ""]
    if not qualified:
        lines += [f"**UNQUALIFIED**: verification status is `{verify.get('delivery_status', 'not run')}`.", ""]
    lines += [
        "This copy was de-identified for security testing. Developer names, e-mail addresses, accounts,",
        "company names, internal domains and system codes were replaced in comments, strings and",
        "configuration files. Code identifiers, file names and private package names were not changed.",
        "",
        "## Hard-coded secrets",
        "",
        "Each value below was replaced by its placeholder. The finding itself (a secret in source code)",
        "is real; the value is not.",
        "",
        "| Placeholder | File | Line | Kind |",
        "| --- | --- | --- | --- |",
    ]
    for s in secrets:
        if s.get("id"):
            lines.append(f"| `{s['id']}` | `{s['path']}` | {s['line']} | {s['kind']} |")
    env_files = sorted({s["path"] for s in secrets if not s.get("id")})
    if env_files:
        lines += ["", "Local environment files were removed; only their keys remain in `.env.example`:", ""]
        lines += [f"- `{p}`" for p in env_files]
    lines += ["", "## Verification", "", f"Status: `{verify.get('delivery_status', 'not run')}`"]
    for r in verify.get("reasons", []):
        lines.append(f"- {r}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_zip(output: Path, zip_path: Path, extra: dict[str, bytes]) -> dict[str, str]:
    files: dict[str, bytes] = {}
    for p in sorted(output.rglob("*")):
        if p.is_file():
            files[p.relative_to(output).as_posix()] = p.read_bytes()
    files.update(extra)
    manifest = {}
    tmp = zip_path.with_name(zip_path.name + ".partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, FIXED_ZIP_TIME)
            info.external_attr = (0o100644 << 16)
            info.create_system = 3  # unix: same bytes from every OS
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[rel])
            manifest[rel] = hashlib.sha256(files[rel]).hexdigest()
    tmp.replace(zip_path)
    return manifest


def step_export(job: Path, zip_path: Path, accept_review: bool = False, allow_unqualified: bool = False) -> dict:
    meta = read_meta(job)
    if "output" not in meta:
        raise JobError("run apply first")
    output = Path(meta["output"])
    if tree_sha256(output) != meta["output_sha256"]:
        raise JobError("the output folder changed after apply; run apply again in a new output folder")
    zip_path = zip_path.resolve()
    if zip_path.exists():
        raise JobError(f"package already exists: {zip_path}")
    if output in zip_path.parents:
        raise JobError("the package must not be written inside the output folder")

    plan_doc = json.loads((job / "plan.json").read_text(encoding="utf-8"))
    verify_path = job / "verify.json"
    verify = json.loads(verify_path.read_text(encoding="utf-8")) if verify_path.exists() else {}
    if verify and meta.get("verify", {}).get("output_sha256") != meta["output_sha256"]:
        verify = {}  # stale: belongs to an earlier output

    _, leaks = _leak_scan(output, job, meta)
    review = [f for f in plan_doc["findings"] if f["action"] == "review"]
    status = verify.get("delivery_status", "not verified")
    blockers = []
    if leaks:
        blockers.append("leak scan: " + "; ".join(leaks[:20]))
    if review and not accept_review:
        blockers.append(f"{len(review)} finding(s) need review (see plan.json); re-run with --accept-review "
                        "after checking them")
    qualified = status == "qualified"
    if not qualified and not allow_unqualified:
        blockers.append(f"verification status is '{status}'; run verify, or use --allow-unqualified "
                        "to export a labelled UNQUALIFIED package")

    result = {"package": None, "blocked": blockers, "leaks": leaks, "delivery_status": status,
              "review_accepted": len(review) if accept_review else 0}
    report = write_html_report(job, plan_doc, verify, result)
    result["report"] = str(report)
    if blockers:
        meta["export"] = {"status": "blocked", "blockers": blockers}
        write_meta(job, meta)
        return result

    if not qualified:
        zip_path = zip_path.with_name(zip_path.stem + "-UNQUALIFIED" + zip_path.suffix)
    notes = _notes(meta, verify, plan_doc["secrets"], qualified)
    manifest = _write_zip(output, zip_path, {NOTES_NAME: notes})
    package_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (job / "manifest.json").write_text(json.dumps(
        {"package": zip_path.name, "package_sha256": package_sha, "input_sha256": meta["input_sha256"],
         "delivery_status": status, "qualified": qualified, "files": manifest}, indent=2), encoding="utf-8")
    result["package"] = str(zip_path)
    result["package_sha256"] = package_sha
    meta["export"] = {"status": "exported", "package": str(zip_path), "package_sha256": package_sha,
                      "qualified": qualified, "review_accepted": result["review_accepted"]}
    write_meta(job, meta)
    return result
