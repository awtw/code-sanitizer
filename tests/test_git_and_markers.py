"""Git metadata removal and internal marker regression coverage."""

from sanitizer.scan import apply_edits, build_plan
from sanitizer.terms import Policy, load_policy


def test_git_removal_cannot_be_overridden(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /private/source")
    nested = tmp_path / "nested/.git"
    nested.mkdir(parents=True)
    (nested / "config").write_text("private config")
    plan = build_plan(tmp_path, Policy(residue_keep=[".git"]))
    assert set(plan.removals) == {".git", "nested/.git/"}


def test_markers_respect_context_policy_and_are_idempotent(tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("internal_markers:\n  domains: [jira.secret.example, git.secret.example]\n"
                           "  jira_projects: [PAY]\n  gitlab_projects: [team/service]\n"
                           "allow:\n  other: ['10.2.3.4', 'npm.company.local']\n")
    policy = load_policy({}, policy_file)
    policy_file.unlink()
    source = ("# TODO (@unknown.user): PAY-123 team/service#72\r\n"
              "# http://jira.secret.example/browse/PAY-456\r\n"
              "# https://git.secret.example/team/service/-/issues/789\r\n"
              "# https://git.company.local/team/service/-/issues/321\r\n"
              "# 10.9.8.7 172.16.0.1 192.168.1.1 fd12:3456::1\r\n"
              "# 8.8.8.8 127.0.0.1 999.1.2.3 10.2.3.4 npm.company.local PUBLIC-123\r\n"
              "PAY_123 = 1\r\n")
    path = tmp_path / "app.py"
    path.write_bytes(source.encode())
    plan = build_plan(tmp_path, policy)
    result = apply_edits(path.read_bytes(), plan.edits["app.py"])
    for old in (b"unknown.user", b"PAY-123", b"team/service#72", b"PAY-456", b"789", b"321",
                b"10.9.8.7", b"172.16.0.1", b"192.168.1.1", b"fd12:3456::1", b"company.local/team"):
        assert old not in result
    for kept in (b"8.8.8.8", b"127.0.0.1", b"999.1.2.3", b"10.2.3.4", b"npm.company.local", b"PUBLIC-123", b"PAY_123"):
        assert kept in result
    assert result.count(b"\r\n") == source.count("\r\n")
    path.write_bytes(result)
    assert build_plan(tmp_path, policy).edits == {}


def test_internal_issue_in_protected_import_is_kept(tmp_path):
    path = tmp_path / "app.ts"
    path.write_text('import thing from "PAY-123"; // TODO (@outsider)\n')
    plan = build_plan(tmp_path, Policy(jira_projects=["PAY"]))
    result = apply_edits(path.read_bytes(), plan.edits["app.ts"])
    assert b'from "PAY-123"' in result
    assert b"@developer" in result
    assert any(f.category == "internal-issue" and f.action == "keep" for f in plan.findings)
