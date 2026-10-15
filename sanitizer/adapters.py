"""Framework adapters: find sub-projects and describe how to validate them.

An adapter only *describes* steps (argv lists with placeholders); the runner
executes them. Placeholders:

    {python}   interpreter of the per-variant virtual environment
    {venv}     that virtual environment's folder
    {out}      folder for files a step produces (e.g. openapi.json)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", ".angular", "__pycache__"}
FASTAPI_IMPORT_RE = re.compile(rb"^\s*(?:from\s+fastapi\s+import|import\s+fastapi)\b", re.M)
FASTAPI_APP_RE = re.compile(rb"^(?P<var>[A-Za-z_]\w*)\s*(?::\s*\w+\s*)?=\s*FastAPI\(", re.M)

OPENAPI_SCRIPT = r"""
import importlib, json, sys
sys.path.insert(0, ".")
module, attr = sys.argv[1].split(":")
app = getattr(importlib.import_module(module), attr)
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(app.openapi(), f, sort_keys=True, ensure_ascii=False, indent=1)
"""

SMOKE_SCRIPT = r"""
import importlib, json, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
module, attr = sys.argv[1].split(":")
app = getattr(importlib.import_module(module), attr)
client = TestClient(app)
results = {p: client.get(p).status_code for p in json.loads(sys.argv[2])}
print(json.dumps(results))
sys.exit(1 if any(code >= 500 for code in results.values()) else 0)
"""


@dataclass
class Step:
    name: str
    level: str  # L0 | L1 | L2 | L3
    argv: list[str] = field(default_factory=list)
    skip_reason: str = ""
    artifact: str = ""  # file name under {out} that is compared between variants
    probe: list[str] = field(default_factory=list)  # must succeed first, otherwise the step is skipped
    probe_reason: str = ""


@dataclass
class Project:
    kind: str  # fastapi | angular
    path: str  # relative to the source root ("" = root)
    steps: list[Step]
    evidence: str = ""
    needs_venv: bool = False


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        yield Path(dirpath), filenames


def _own_files(d: Path):
    """Walk ``d`` but not into nested Python projects (folders with their own pyproject/requirements)."""
    for dirpath, dirnames, filenames in os.walk(d):
        here = Path(dirpath)
        dirnames[:] = sorted(
            x for x in dirnames
            if x not in SKIP_DIRS and not any((here / x / m).exists() for m in ("requirements.txt", "pyproject.toml"))
        )
        yield here, filenames


def _python_launcher(version: str | None) -> list[str]:
    """Command that creates a virtual environment at {venv}."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "venv", "-q", "-p", version or f"{sys.version_info.major}.{sys.version_info.minor}", "{venv}"]
    return [sys.executable, "-m", "venv", "{venv}"]


def _pip_install(args: list[str]) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "-q", "-p", "{python}", *args]
    return ["{python}", "-m", "pip", "install", "-q", *args]


def detect_fastapi(root: Path, profile: dict) -> list[Project]:
    projects = []
    for d, files in _walk(root):
        markers = [f for f in files if f in {"requirements.txt", "pyproject.toml"}]
        if not markers:
            continue
        app_ref = None
        uses_fastapi = False
        for sub, sub_files in _own_files(d):
            for fn in sub_files:
                if not fn.endswith(".py"):
                    continue
                data = (sub / fn).read_bytes()
                if FASTAPI_IMPORT_RE.search(data):
                    uses_fastapi = True
                m = FASTAPI_APP_RE.search(data)
                if m and app_ref is None:
                    module = (sub / fn).relative_to(d).with_suffix("").as_posix().replace("/", ".")
                    app_ref = f"{module.removesuffix('.__init__')}:{m['var'].decode()}"
        if not uses_fastapi:
            continue
        rel = "" if d == root else d.relative_to(root).as_posix()
        conf = profile.get("projects", {}).get(rel, {})
        app_ref = conf.get("app", app_ref)
        projects.append(Project("fastapi", rel, _fastapi_steps(d, app_ref, conf, profile),
                                f"FastAPI import, app {app_ref or 'not found'}", needs_venv=True))
    return projects


def _fastapi_steps(d: Path, app_ref: str | None, conf: dict, profile: dict) -> list[Step]:
    steps = [Step("venv", "L1", _python_launcher(profile.get("python")))]
    reqs = sorted(f.name for f in d.glob("requirements*.txt"))
    if reqs:
        args = [a for r in reqs for a in ("-r", r)]
        steps.append(Step("install", "L1", _pip_install(args)))
    else:
        steps.append(Step("install", "L1", _pip_install(["."])))
    steps.append(Step("compile", "L0", ["{python}", "-m", "compileall", "-q", "."]))
    has_tests = any(d.glob("tests/**/test_*.py")) or any(d.glob("test_*.py")) or any(d.glob("tests/**/*_test.py"))
    if has_tests:
        steps.append(Step("pytest", "L2", ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                          probe=["{python}", "-c", "import pytest"], probe_reason="pytest is not installed"))
    else:
        steps.append(Step("pytest", "L2", skip_reason="no tests found"))
    if app_ref:
        steps.append(Step("openapi", "L2", ["{python}", "-c", OPENAPI_SCRIPT, app_ref, "{out}/openapi.json"],
                          artifact="openapi.json"))
    else:
        steps.append(Step("openapi", "L2", skip_reason="FastAPI app object not found; set projects.<path>.app"))
    smoke = conf.get("smoke")
    if smoke and app_ref:
        steps.append(Step("smoke", "L3", ["{python}", "-c", SMOKE_SCRIPT, app_ref, json.dumps(smoke)],
                          probe=["{python}", "-c", "import httpx"], probe_reason="httpx is not installed"))
    else:
        steps.append(Step("smoke", "L3", skip_reason="no smoke paths in the test profile"))
    return steps


def detect_angular(root: Path, profile: dict) -> list[Project]:
    projects = []
    for d, files in _walk(root):
        if "angular.json" not in files:
            continue
        rel = "" if d == root else d.relative_to(root).as_posix()
        conf = profile.get("projects", {}).get(rel, {})
        npm = shutil.which("npm") or "npm"
        npx = shutil.which("npx") or "npx"
        install = [npm, "ci", "--no-audit", "--no-fund"] if (d / "package-lock.json").exists() \
            else [npm, "install", "--no-audit", "--no-fund"]
        build = [npx, "ng", "build"]
        if conf.get("configuration"):
            build += ["--configuration", conf["configuration"]]
        try:
            workspace = json.loads((d / "angular.json").read_text(encoding="utf-8"))
        except ValueError:
            workspace = {}
        has_test = any("test" in p.get("architect", {}) for p in workspace.get("projects", {}).values())
        steps = [
            Step("install", "L1", install),
            Step("build", "L1", build),
            Step("test", "L2", [npx, "ng", "test", "--watch=false", "--browsers=ChromeHeadless"])
            if has_test else Step("test", "L2", skip_reason="no test target in angular.json"),
            Step("smoke", "L3", skip_reason="browser smoke test not supported yet"),
        ]
        projects.append(Project("angular", rel, steps, "angular.json"))
    return projects


ADAPTERS = [detect_fastapi, detect_angular]


def detect(root: Path, profile: dict) -> list[Project]:
    found = []
    for adapter in ADAPTERS:
        found.extend(adapter(root, profile))
    return sorted(found, key=lambda p: (p.path, p.kind))
