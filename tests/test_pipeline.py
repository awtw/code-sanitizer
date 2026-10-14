"""End-to-end tests: identities → scan → apply on the fixtures, checked against the oracle."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_fixtures import FIXTURE_NAMES, build_all  # noqa: E402
from oracle import check_deliverable, load_expected  # noqa: E402

from sanitizer.identity import harvest
from sanitizer.ingest import IngestError, ingest, tree_sha256
from sanitizer.pipeline import step_apply, step_identities, step_scan
from sanitizer.scan import build_plan
from sanitizer.terms import load_identities, load_policy

POLICY = Path(__file__).resolve().parent / "fixtures" / "policy.yaml"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("fixtures"), make_zip=True)


def _run(src: Path, work: Path) -> tuple[Path, Path]:
    job, out = work / "job", work / "out"
    step_identities(src, job)
    step_scan(job, POLICY)
    step_apply(job, out)
    return job, out


@pytest.fixture(scope="module")
def results(built, tmp_path_factory) -> dict[str, tuple[Path, Path]]:
    out = {}
    for name in FIXTURE_NAMES:
        out[name] = _run(built[name], tmp_path_factory.mktemp(name))
    out["fullstack.zip"] = _run(built["fullstack"].parent / "fullstack.zip", tmp_path_factory.mktemp("zip"))
    return out


@pytest.mark.parametrize("name", [*FIXTURE_NAMES, "fullstack.zip"])
def test_output_passes_oracle(results, name):
    _, out = results[name]
    violations = check_deliverable(out, name.removesuffix(".zip"))
    assert not violations, "\n".join(map(str, violations))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_harvest_matches_expected_identities(built, name):
    exp = load_expected()[name]["identities"]
    h = harvest(built[name])
    emails = {e for p in h.people for e in p.emails}
    for person in exp["people"]:
        assert set(person["emails"]) <= emails
    assert set(exp["accounts"]) <= h.accounts
    enabled = {t.value for t in h.org_terms if not t.needs_confirmation}
    assert set(exp["orgs"]) <= enabled
    assert set(exp["repos"]) - {"paygw"} <= enabled  # single generic words need confirmation
    hosts = {t.value for t in h.org_terms if t.category == "host"}
    assert set(exp["hosts"]) <= hosts
    assert len(h.credentials) == exp.get("remote_credentials", 0)
    assert all("fake-git-credential" not in c for c in h.credentials)
    for name_, email, _ in h.ignored:
        assert email in exp.get("ignored_people", [email])


def test_git_is_never_executed(built, results):
    for root in [*built.values(), *(p for pair in results.values() for p in pair)]:
        assert not list(root.rglob("FSMONITOR_TRIGGERED")), root


def test_input_is_not_modified(built, tmp_path):
    src = built["fastapi-app"]
    before = tree_sha256(src)
    _run(src, tmp_path)
    assert tree_sha256(src) == before


def test_output_is_deterministic(built, tmp_path):
    _, a = _run(built["angular-app"], tmp_path / "a")
    _, b = _run(built["angular-app"], tmp_path / "b")
    assert tree_sha256(a) == tree_sha256(b)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_second_pass_changes_nothing(results, name):
    job, out = results[name]
    policy = load_policy(load_identities(job / "identities.yaml"), POLICY)
    plan = build_plan(out, policy)
    assert plan.edits == {} and plan.removals == [] and plan.generated == {}


def test_secrets_are_labelled_not_deleted(results):
    _, out = results["fastapi-app"]
    config = (out / "app" / "config.py").read_text(encoding="utf-8")
    assert 'JWT_SECRET = os.getenv("JWT_SECRET", "REDACTED_SECRET_' in config
    assert ":REDACTED_SECRET_" in config  # password inside the DB URL
    env_example = (out / ".env.example").read_text(encoding="utf-8")
    assert env_example == "DATABASE_URL=\nJWT_SECRET=\n"


def test_patch_and_plan_are_written(results):
    job, _ = results["fastapi-app"]
    assert "+    title=\"Company SYSTEM01 API\"," in (job / "changes.patch").read_text(encoding="utf-8")
    assert (job / "plan.json").exists()


# --- ingest safety -------------------------------------------------------------------------------

def _zip(path: Path, entries: dict[str, bytes], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
    return path


@pytest.mark.parametrize("bad", ["../evil.py", "/abs/evil.py", "C:/evil.py"])
def test_zip_path_traversal_is_rejected(tmp_path, bad):
    z = _zip(tmp_path / "a.zip", {"ok.py": b"x = 1\n", bad: b"boom"})
    with pytest.raises(IngestError):
        ingest(z, tmp_path / "snap")
    assert not (tmp_path / "snap").exists()
    assert not (tmp_path / "evil.py").exists()


def test_zip_symlink_is_skipped(tmp_path):
    z = _zip(tmp_path / "a.zip", {"ok.py": b"x = 1\n"}, symlink="link")
    snap = ingest(z, tmp_path / "snap")
    assert not (snap.root / "link").exists()
    assert any("symlink" in s for s in snap.skipped)


def test_zip_case_collision_is_rejected(tmp_path):
    z = _zip(tmp_path / "a.zip", {"App.py": b"1", "app.py": b"2"})
    with pytest.raises(IngestError):
        ingest(z, tmp_path / "snap")


def test_zip_bomb_ratio_is_rejected(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"0" * (5 * 1024 * 1024))
    with pytest.raises(IngestError):
        ingest(z, tmp_path / "snap")


def test_folder_symlink_is_not_followed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (src / "link.txt").symlink_to(outside)
    snap = ingest(src, tmp_path / "snap")
    assert not (snap.root / "link.txt").exists()


# --- sanitized projects still work (slow) --------------------------------------------------------

@pytest.mark.slow
def test_sanitized_fastapi_tests_pass(results, tmp_path):
    _, out = results["fullstack"]
    uv = shutil.which("uv") or pytest.skip("uv not installed")
    venv = tmp_path / "venv"
    subprocess.run([uv, "venv", "-q", "-p", "3.12", str(venv)], check=True)
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    backend = out / "backend"
    subprocess.run([uv, "pip", "install", "-q", "-p", str(py), "-r", "requirements.txt"], cwd=backend, check=True)
    r = subprocess.run([str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=backend,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.slow
def test_sanitized_angular_builds(results):
    _, out = results["fullstack"]
    frontend = out / "frontend"
    npm, npx = shutil.which("npm"), shutil.which("npx")
    if not npm:
        pytest.skip("npm not installed")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, check=True, capture_output=True)
    r = subprocess.run([npx, "ng", "build"], cwd=frontend, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
