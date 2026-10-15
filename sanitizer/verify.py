"""Run the same validation on the baseline and the de-identified copy, then compare.

Baseline   = snapshot with the same files removed / generated, but no text edits.
Sanitized  = the apply output.

Both are copied into ``JOB/verify/<variant>/src`` so build output
(node_modules, dist, venvs) never lands in the deliverable. Steps run as
subprocesses with an allow-listed environment; the project's code is
executed (installs, imports, tests), so the report records that the run was
not isolated in a container.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .adapters import Project, detect
from .ingest import tree_sha256
from .pipeline import JobError, read_meta, write_meta
from .scan import build_plan
from .syntax import language_for, regions_for
from .terms import load_identities, load_policy

LEVELS = ["L0", "L1", "L2", "L3"]
DEFAULT_PROFILE = {"required_levels": ["L0", "L1", "L2"], "timeout_seconds": 900, "env": {}, "projects": {}}

# Only these variables reach project code: enough for package managers and
# internal mirrors, but no cloud credentials or tokens from the host shell.
ENV_ALLOW = {
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "SYSTEMROOT", "SYSTEMDRIVE",
    "COMSPEC", "PATHEXT", "WINDIR", "PROGRAMFILES", "PROGRAMDATA", "LANG", "LC_ALL", "LC_CTYPE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CONFIG_FILE",
    "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "UV_INDEX", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
    "NPM_CONFIG_REGISTRY", "npm_config_registry", "NPM_CONFIG_CACHE", "npm_config_cache", "NPM_CONFIG_USERCONFIG",
    "CHROME_BIN",
}

# OpenAPI fields that may differ because their text was de-identified.
OPENAPI_TEXT_KEYS = {"description", "summary"}
OPENAPI_IGNORED_TOP = {"servers"}
OPENAPI_INFO_KEEP = {"version"}


@dataclass
class StepResult:
    variant: str
    project: str
    kind: str
    step: str
    level: str
    status: str  # pass | fail | skip | error
    exit_code: int | None = None
    seconds: float = 0.0
    log: str = ""
    reason: str = ""


@dataclass
class Comparison:
    project: str
    step: str
    level: str
    baseline: str
    sanitized: str
    verdict: str  # ok | regression | baseline-failure | skipped | improved
    detail: list[str] = field(default_factory=list)


def load_profile(path: Path | None) -> dict:
    prof = dict(DEFAULT_PROFILE)
    if path:
        prof.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return prof


def _env(profile: dict, extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW}
    env.update({k: str(v) for k, v in profile.get("env", {}).items()})
    env.update({k: str(v) for k, v in extra.items()})
    env.setdefault("CI", "1")  # non-interactive tools (ng analytics prompt, npm progress)
    env["NG_CLI_ANALYTICS"] = "false"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _slug(project: str) -> str:
    return project.replace("/", "_") or "root"


def _copy(src: Path, dest: Path, skip: set[str] | None = None, generated: dict[str, bytes] | None = None) -> None:
    skip = skip or set()
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        if rel in skip or any(rel.startswith(s) for s in skip if s.endswith("/")):
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest / rel)
    for rel, data in (generated or {}).items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(data)


def _syntax_errors(root: Path) -> set[str]:
    bad = set()
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and language_for(rel) and not any(part in {"node_modules", "dist"} for part in p.parts):
            if regions_for(rel, p.read_bytes()).parse_errors:
                bad.add(rel)
    return bad


def _fill(argv: list[str], ph: dict[str, str]) -> list[str]:
    out = []
    for a in argv:
        for k, v in ph.items():
            a = a.replace("{" + k + "}", v)
        out.append(a)
    return out


def _run_project(variant: str, src: Path, work: Path, project: Project, profile: dict) -> list[StepResult]:
    cwd = src / project.path if project.path else src
    slug = _slug(project.path)
    out_dir = work / "artifacts" / slug
    log_dir = work / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    venv = work / "venvs" / slug
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    ph = {"venv": str(venv), "python": str(python), "out": str(out_dir)}
    conf = profile.get("projects", {}).get(project.path, {})
    env = _env(profile, conf.get("env", {}))
    timeout = int(conf.get("timeout_seconds", profile.get("timeout_seconds", 900)))
    results: list[StepResult] = []
    failed_setup = False
    for step in project.steps:
        res = StepResult(variant, project.path, project.kind, step.name, step.level, "skip")
        if step.skip_reason:
            res.reason = step.skip_reason
        elif failed_setup:
            res.reason = "an earlier install/build step failed"
        else:
            if step.probe:
                probe = subprocess.run(_fill(step.probe, ph), cwd=cwd, env=env, capture_output=True, timeout=120)
                if probe.returncode != 0:
                    res.reason = step.probe_reason
                    results.append(res)
                    continue
            log = log_dir / f"{slug}__{step.name}.log"
            res.log = str(log)
            start = time.monotonic()
            try:
                proc = subprocess.run(_fill(step.argv, ph), cwd=cwd, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=timeout)
                log.write_bytes(proc.stdout)
                res.exit_code = proc.returncode
                res.status = "pass" if proc.returncode == 0 else "fail"
            except subprocess.TimeoutExpired as e:
                log.write_bytes((e.stdout or b"") + f"\n[timeout after {timeout}s]\n".encode())
                res.status, res.reason = "error", f"timeout after {timeout}s"
            except OSError as e:
                res.status, res.reason = "error", str(e)
            res.seconds = round(time.monotonic() - start, 2)
            if res.status != "pass" and step.name in {"venv", "install"}:
                failed_setup = True
        results.append(res)
    return results


def _normalise_openapi(doc: dict) -> dict:
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in OPENAPI_TEXT_KEYS}
        if isinstance(node, list):
            return [strip(x) for x in node]
        return node

    doc = {k: v for k, v in doc.items() if k not in OPENAPI_IGNORED_TOP}
    if "info" in doc:
        doc["info"] = {k: v for k, v in doc["info"].items() if k in OPENAPI_INFO_KEEP}
    return strip(doc)


def _json_diff(a, b, path: str = "$") -> list[str]:
    if type(a) is not type(b):
        return [f"{path}: {type(a).__name__} -> {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in b:
                out.append(f"{path}.{k}: removed")
            elif k not in a:
                out.append(f"{path}.{k}: added")
            else:
                out += _json_diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items -> {len(b)} items"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _json_diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]


def _compare(base: list[StepResult], san: list[StepResult], work: Path, projects: list[Project]) -> list[Comparison]:
    by = {(r.project, r.step): r for r in base}
    artifacts = {(p.path, s.name): s.artifact for p in projects for s in p.steps if s.artifact}
    out = []
    for s in san:
        b = by.get((s.project, s.step))
        if b is None:
            continue
        c = Comparison(s.project, s.step, s.level, b.status, s.status, "ok")
        if b.status == "pass" and s.status == "pass":
            art = artifacts.get((s.project, s.step))
            if art:
                slug = _slug(s.project)
                da = json.loads((work / "baseline" / "artifacts" / slug / art).read_text(encoding="utf-8"))
                db = json.loads((work / "sanitized" / "artifacts" / slug / art).read_text(encoding="utf-8"))
                diff = _json_diff(_normalise_openapi(da), _normalise_openapi(db))
                if diff:
                    c.verdict, c.detail = "regression", diff[:50]
        elif b.status == "pass":
            c.verdict = "regression"
            c.detail = [s.reason] if s.reason else []
        elif b.status in {"fail", "error"}:
            c.verdict = "baseline-failure" if s.status != "pass" else "improved"
        else:
            c.verdict = "skipped"
            c.detail = [b.reason]
        out.append(c)
    return out


def _delivery_status(comparisons: list[Comparison], syntax: Comparison, projects: list[Project],
                     profile: dict) -> tuple[str, list[str]]:
    reasons = []
    required = set(profile.get("required_levels", DEFAULT_PROFILE["required_levels"]))
    if not projects:
        reasons.append("no supported framework detected: only the L0 syntax check ran")
    if syntax.verdict == "regression":
        return "regression", ["new syntax errors: " + ", ".join(syntax.detail)]
    regressions = [c for c in comparisons if c.verdict == "regression"]
    if regressions:
        return "regression", [f"{c.project or '.'} {c.step}: {c.baseline} -> {c.sanitized}" for c in regressions]
    for p in projects:
        conf = profile.get("projects", {}).get(p.path, {})
        req = set(conf.get("required_levels", required))
        for c in comparisons:
            if c.project != p.path or c.level not in req:
                continue
            if c.verdict == "baseline-failure":
                reasons.append(f"{p.path or '.'} {c.step}: fails on the original code too")
            elif c.verdict == "skipped":
                reasons.append(f"{p.path or '.'} {c.step} ({c.level}) skipped: {'; '.join(c.detail)}")
    return ("incomplete" if reasons else "qualified"), reasons


def step_verify(job: Path, profile_path: Path | None) -> dict:
    meta = read_meta(job)
    if "output" not in meta:
        raise JobError("run apply first")
    output = Path(meta["output"])
    if tree_sha256(output) != meta["output_sha256"]:
        raise JobError("the output folder changed after apply; run apply again in a new output folder")
    profile = load_profile(profile_path)
    snap = job / "snapshot"
    plan = build_plan(snap, load_policy(load_identities(job / "identities.yaml"),
                                        Path(meta["policy"]) if meta.get("policy") else None))
    work = job / "verify"
    if work.exists():
        shutil.rmtree(work)
    base_src, san_src = work / "baseline" / "src", work / "sanitized" / "src"
    _copy(snap, base_src, set(plan.removals), plan.generated)
    _copy(output, san_src)

    base_bad, san_bad = _syntax_errors(base_src), _syntax_errors(san_src)
    new_bad = sorted(san_bad - base_bad)
    syntax = Comparison("", "syntax", "L0", "fail" if base_bad else "pass", "fail" if san_bad else "pass",
                        "regression" if new_bad else "ok", new_bad)

    projects = detect(san_src, profile)
    base_results: list[StepResult] = []
    san_results: list[StepResult] = []
    base_projects = {(p.kind, p.path): p for p in detect(base_src, profile)}
    for p in projects:
        bp = base_projects.get((p.kind, p.path), p)
        base_results += _run_project("baseline", base_src, work / "baseline", bp, profile)
        san_results += _run_project("sanitized", san_src, work / "sanitized", p, profile)
    comparisons = _compare(base_results, san_results, work, projects)
    status, reasons = _delivery_status(comparisons, syntax, projects, profile)
    report = {
        "delivery_status": status,
        "reasons": reasons,
        "isolation": "none (local subprocess, allow-listed environment)",
        "profile": str(profile_path.resolve()) if profile_path else None,
        "required_levels": profile.get("required_levels"),
        "projects": [{"kind": p.kind, "path": p.path or ".", "evidence": p.evidence} for p in projects],
        "syntax": asdict(syntax),
        "comparisons": [asdict(c) for c in comparisons],
        "results": [asdict(r) for r in base_results + san_results],
    }
    (job / "verify.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    meta["verify"] = {"delivery_status": status, "output_sha256": meta["output_sha256"]}
    write_meta(job, meta)
    return report
