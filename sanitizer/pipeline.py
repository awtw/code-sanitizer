"""Job workflow: ingest → identities → scan → apply.

Job folder layout (internal, never delivered):

    job.json          input hash, policy path, tool version
    snapshot/         untouched copy of the input
    identities.yaml   harvested identities, reviewed by a person
    plan.json         findings and planned changes
    changes.patch     unified diff of every modified text file
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import yaml

from . import __version__
from .identity import harvest, to_document
from .ingest import IngestError, ingest, tree_sha256
from .scan import Plan, apply_edits, build_plan
from .terms import load_identities, load_policy


class JobError(Exception):
    pass


def _meta_path(job: Path) -> Path:
    return job / "job.json"


def read_meta(job: Path) -> dict:
    p = _meta_path(job)
    if not p.exists():
        raise JobError(f"not a job folder (missing job.json): {job}")
    return json.loads(p.read_text(encoding="utf-8"))


def write_meta(job: Path, meta: dict) -> None:
    _meta_path(job).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def step_identities(source: Path, job: Path) -> dict:
    job.mkdir(parents=True, exist_ok=True)
    if _meta_path(job).exists():
        raise JobError(f"job folder already used: {job}")
    snap = ingest(source, job / "snapshot")
    h = harvest(snap.root)
    doc = to_document(h)
    header = ("# Developer identities found in Git data. Review before scanning:\n"
              "#  - remove entries that are not real people, add missing names/accounts\n"
              "#  - organisation terms with enabled: false are generic words; enable only if needed\n"
              "# Every person is replaced by the same 'developer' identity.\n")
    (job / "identities.yaml").write_text(header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                                         encoding="utf-8")
    meta = {"tool_version": __version__, "input": str(source.resolve()), "input_sha256": snap.input_sha256,
            "snapshot_sha256": tree_sha256(snap.root), "skipped_on_ingest": snap.skipped}
    write_meta(job, meta)
    return {"people": len(doc["people"]), "organization": len(doc["organization"]),
            "credentials": len(doc["credentials_found"]), "warnings": doc["warnings"], "skipped": snap.skipped}


def _check_snapshot(job: Path, meta: dict) -> Path:
    snap = job / "snapshot"
    if tree_sha256(snap) != meta["snapshot_sha256"]:
        raise JobError("snapshot changed since identities were collected; start a new job")
    return snap


def step_scan(job: Path, policy_path: Path | None) -> Plan:
    meta = read_meta(job)
    snap = _check_snapshot(job, meta)
    policy = load_policy(load_identities(job / "identities.yaml"), policy_path)
    plan = build_plan(snap, policy)
    (job / "plan.json").write_text(json.dumps(plan.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    meta["policy"] = str(policy_path.resolve()) if policy_path else None
    write_meta(job, meta)
    return plan


def step_apply(job: Path, output: Path) -> Plan:
    meta = read_meta(job)
    if "policy" not in meta:
        raise JobError("run scan first")
    snap = _check_snapshot(job, meta)
    policy_path = Path(meta["policy"]) if meta["policy"] else None
    plan = build_plan(snap, load_policy(load_identities(job / "identities.yaml"), policy_path))
    output = output.resolve()
    if output.exists():
        raise JobError(f"output already exists: {output}")
    if job.resolve() in output.parents or snap.resolve() in output.parents:
        raise JobError("output must be outside the job folder")
    tmp = output.with_name(output.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    removed = set(plan.removals)
    patch: list[str] = []
    for p in sorted(snap.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(snap).as_posix()
        if rel in removed or any(rel.startswith(r) for r in removed if r.endswith("/")):
            continue
        data = p.read_bytes()
        if rel in plan.edits:
            new = apply_edits(data, plan.edits[rel])
            patch.extend(difflib.unified_diff(
                data.decode("utf-8").splitlines(keepends=True), new.decode("utf-8").splitlines(keepends=True),
                f"a/{rel}", f"b/{rel}"))
            data = new
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for rel, data in plan.generated.items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_bytes(data)
    tmp.rename(output)
    (job / "changes.patch").write_text("".join(patch), encoding="utf-8", newline="")
    meta["output"] = str(output)
    meta["output_sha256"] = tree_sha256(output)
    write_meta(job, meta)
    return plan


__all__ = ["IngestError", "JobError", "step_apply", "step_identities", "step_scan"]
