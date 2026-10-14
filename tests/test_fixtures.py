"""Sanity checks for the fixtures themselves (phase A).

These make sure the oracle is meaningful: every forbidden string and every
snippet to preserve really occurs in the raw fixtures, the raw fixtures fail
the oracle, and the Git trap is armed but not fired.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_fixtures import FIXTURE_NAMES, build_all  # noqa: E402
from oracle import _sections, check_deliverable, load_expected  # noqa: E402


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("fixtures"), make_zip=True)


def _all_text(root: Path) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace") for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_forbidden_string_occurs_in_raw_fixture(built, name):
    text = _all_text(built[name]).lower()
    expected = load_expected()
    missing = [f for f in expected["common"]["forbidden"] if f.lower() not in text]
    # Secrets are spread across fixtures; identities must appear in every one.
    identity_like = {"john doe", "john.doe", "jane smith", "王小明", "wang.xiaoming", "acme-org"}
    assert not [m for m in missing if m.lower() in identity_like], missing


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_preserve_snippets_and_leftovers_exist_in_raw_fixture(built, name):
    root = built[name]
    for prefix, sec in _sections(name, load_expected()):
        for rel, snippets in sec.get("must_preserve", {}).items():
            text = (root / prefix / rel).read_bytes().decode("utf-8")
            for s in snippets:
                assert s in text, (prefix + rel, s)
        skip = {"FSMONITOR_TRIGGERED", "__MACOSX"} | ({".git"} if prefix == "backend/" else set())
        leftovers = [r for r in sec.get("absent_paths", []) if r not in skip]
        for rel in leftovers:
            assert (root / prefix / rel).exists(), prefix + rel


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_raw_fixture_fails_oracle(built, name):
    kinds = {v.kind for v in check_deliverable(built[name], name)}
    assert {"forbidden", "org-term", "leftover"} <= kinds


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_raw_fixture_keeps_encodings(built, name):
    assert not [v for v in check_deliverable(built[name], name) if v.kind == "encoding"]


def test_git_history_has_three_authors(built):
    out = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(built["fastapi-app"]), "log", "--all", "--format=%ae"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert {"john.doe@corp.acme.com", "12345678+jsmith-acme@users.noreply.github.com",
            "wang.xiaoming@corp.acme.com"} <= set(out)


def test_fsmonitor_trap_is_armed_but_not_fired(built, tmp_path):
    for name in ("fastapi-app", "fullstack"):
        assert not (built[name] / "FSMONITOR_TRIGGERED").exists()
        config = (built[name] / ".git" / "config").read_text(encoding="utf-8")
        assert "fsmonitor" in config


def test_zip_contains_git_and_macos_junk(built):
    import zipfile

    names = zipfile.ZipFile(built["fullstack"].parent / "fullstack.zip").namelist()
    assert any(n.startswith("fullstack/.git/") for n in names)
    assert any(n.startswith("fullstack/frontend/.git/") for n in names)
    assert any(n.startswith("__MACOSX/") for n in names)


# --- baselines (slow: install dependencies from the package mirrors) ---------------------------

def _uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not installed")
    return uv


@pytest.mark.slow
def test_fastapi_baseline_tests_pass(built, tmp_path):
    root = built["fastapi-app"]
    uv = _uv()
    venv = tmp_path / "venv"
    subprocess.run([uv, "venv", "-q", "-p", "3.12", str(venv)], check=True)
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([uv, "pip", "install", "-q", "-p", str(py), "-r", "requirements.txt"], cwd=root, check=True)
    r = subprocess.run([str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow
def test_angular_baseline_build_passes(built):
    root = built["angular-app"]
    npm = shutil.which("npm")
    if not npm:
        pytest.skip("npm not installed")
    shutil.rmtree(root / "node_modules")  # leftover stub from the fixture
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=root, check=True, capture_output=True)
    npx = shutil.which("npx")
    r = subprocess.run([npx, "ng", "build"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
