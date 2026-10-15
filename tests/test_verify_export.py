"""Phase D: verification gate, leak scan and packaging."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from build_fixtures import build_all  # noqa: E402
from oracle import check_deliverable  # noqa: E402

from sanitizer.export import NOTES_NAME, step_export
from sanitizer.pipeline import JobError, step_apply, step_identities, step_scan
from sanitizer.verify import step_verify

FIXTURES = Path(__file__).resolve().parent / "fixtures"
POLICY = FIXTURES / "policy.yaml"
PROFILE = FIXTURES / "test-profile.yaml"


def _prepare(src: Path, work: Path, policy: Path | None = POLICY) -> Path:
    job = work / "job"
    step_identities(src, job)
    step_scan(job, policy)
    step_apply(job, work / "out")
    return job


def _small_project(root: Path, extra: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "notes.md").write_text(f"Ask ops@other-corp.example.net for access.\n{extra}", encoding="utf-8")
    (root / "util.py").write_text("# <script>alert(1)</script> by nobody\nx = 1\n", encoding="utf-8")
    return root


# --- fast ----------------------------------------------------------------------------------------

def test_unverified_export_is_blocked_then_labelled(tmp_path):
    job = _prepare(_small_project(tmp_path / "src"), tmp_path)
    res = step_export(job, tmp_path / "pkg.zip", accept_review=True)
    assert res["package"] is None
    assert any("verification status" in b for b in res["blocked"])
    res = step_export(job, tmp_path / "pkg.zip", accept_review=True, allow_unqualified=True)
    assert res["package"].endswith("pkg-UNQUALIFIED.zip")
    notes = zipfile.ZipFile(res["package"]).read(NOTES_NAME).decode()
    assert "**UNQUALIFIED**" in notes


def test_review_findings_block_until_accepted(tmp_path):
    job = _prepare(_small_project(tmp_path / "src"), tmp_path)
    res = step_export(job, tmp_path / "pkg.zip", allow_unqualified=True)
    assert any("need review" in b for b in res["blocked"])
    plan = json.loads((job / "plan.json").read_text())
    assert any(f["category"] == "unknown-email" and "ops@other-corp" in f["detail"] for f in plan["findings"])


def test_leak_scan_cannot_be_bypassed(tmp_path):
    job = _prepare(_small_project(tmp_path / "src", "Owner: Secret Squirrel\n"), tmp_path)
    # Someone adds a person after apply: the output still contains the name.
    doc = yaml.safe_load((job / "identities.yaml").read_text(encoding="utf-8"))
    doc["people"].append({"names": ["Secret Squirrel"], "emails": [], "accounts": []})
    (job / "identities.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    res = step_export(job, tmp_path / "pkg.zip", accept_review=True, allow_unqualified=True)
    assert res["package"] is None
    assert any("leak scan" in b and "notes.md" in b for b in res["blocked"])


def test_report_escapes_source_text(tmp_path):
    job = _prepare(_small_project(tmp_path / "src", "Owner: Secret Squirrel\n"), tmp_path)
    step_export(job, tmp_path / "pkg.zip", accept_review=True, allow_unqualified=True)
    html = (job / "report.html").read_text(encoding="utf-8")
    assert "<script>alert" not in html


def test_package_is_deterministic_and_internal_files_stay_out(tmp_path):
    shas = []
    for i in range(2):
        work = tmp_path / str(i)
        job = _prepare(_small_project(work / "src"), work)
        res = step_export(job, work / "pkg.zip", accept_review=True, allow_unqualified=True)
        shas.append(res["package_sha256"])
        names = zipfile.ZipFile(res["package"]).namelist()
        assert not {"plan.json", "identities.yaml", "changes.patch", "report.html", "manifest.json"} & set(names)
        manifest = json.loads((job / "manifest.json").read_text())
        assert manifest["files"]["util.py"] == hashlib.sha256(
            zipfile.ZipFile(res["package"]).read("util.py")).hexdigest()
    assert shas[0] == shas[1]


def test_changed_output_is_refused(tmp_path):
    job = _prepare(_small_project(tmp_path / "src"), tmp_path)
    (tmp_path / "out" / "util.py").write_text("x = 2\n")
    with pytest.raises(JobError):
        step_export(job, tmp_path / "pkg.zip", accept_review=True, allow_unqualified=True)
    with pytest.raises(JobError):
        step_verify(job, None)


def test_unknown_framework_is_incomplete_not_qualified(tmp_path):
    job = _prepare(_small_project(tmp_path / "src"), tmp_path)
    report = step_verify(job, None)
    assert report["delivery_status"] == "incomplete"
    assert report["syntax"]["verdict"] == "ok"
    assert any("no supported framework" in r for r in report["reasons"])


# --- slow: real installs, builds and tests -------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("fixtures"), names=("fastapi-app", "fullstack"))


@pytest.mark.slow
def test_fullstack_is_qualified_and_exported(built, tmp_path):
    job = _prepare(built["fullstack"], tmp_path)
    report = step_verify(job, PROFILE)
    assert report["delivery_status"] == "qualified", report["reasons"]
    verdicts = {(c["project"], c["step"]): c["verdict"] for c in report["comparisons"]}
    assert verdicts[("backend", "openapi")] == "ok"
    assert verdicts[("backend", "pytest")] == "ok"
    assert verdicts[("frontend", "build")] == "ok"
    res = step_export(job, tmp_path / "fullstack.zip")
    assert res["package"] and not res["blocked"]
    unpacked = tmp_path / "unpacked"
    zipfile.ZipFile(res["package"]).extractall(unpacked)
    (unpacked / NOTES_NAME).unlink()
    assert not check_deliverable(unpacked, "fullstack")


@pytest.mark.slow
def test_broken_private_package_reference_is_a_regression(built, tmp_path):
    # Without the allow-list, "./packages/acme-common" in requirements.txt is rewritten and install fails.
    policy = tmp_path / "policy.yaml"
    policy.write_text("organization:\n  - {value: Acme, category: company}\n", encoding="utf-8")
    job = _prepare(built["fastapi-app"], tmp_path, policy)
    report = step_verify(job, PROFILE)
    assert report["delivery_status"] == "regression"
    install = next(c for c in report["comparisons"] if c["step"] == "install")
    assert (install["baseline"], install["sanitized"]) == ("pass", "fail")


@pytest.mark.slow
def test_api_contract_change_is_a_regression(built, tmp_path):
    # The API version string is part of the OpenAPI contract but no test checks it:
    # pytest still passes, so only the contract comparison can catch the change.
    policy = tmp_path / "policy.yaml"
    base = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    base["terms"] = [{"value": "1.4.2", "replace_with": "9.9.9"}]
    policy.write_text(yaml.safe_dump(base), encoding="utf-8")
    job = _prepare(built["fastapi-app"], tmp_path, policy)
    report = step_verify(job, PROFILE)
    verdicts = {c["step"]: c for c in report["comparisons"]}
    assert verdicts["pytest"]["verdict"] == "ok"
    assert verdicts["openapi"]["verdict"] == "regression"
    assert verdicts["openapi"]["detail"] == ["$.info.version: '1.4.2' -> '9.9.9'"]
    assert report["delivery_status"] == "regression"


def test_route_word_that_is_also_a_module_is_protected(built, tmp_path):
    # "/payments" is kept because "payments" is a code symbol (module name): no contract break.
    policy = tmp_path / "policy.yaml"
    base = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    base["terms"] = [{"value": "payments", "replace_with": "orders"}]
    policy.write_text(yaml.safe_dump(base), encoding="utf-8")
    _prepare(built["fastapi-app"], tmp_path, policy)
    router = (tmp_path / "out" / "app" / "routers" / "payments.py").read_text(encoding="utf-8")
    assert 'APIRouter(prefix="/payments"' in router
