"""Build the test fixture projects (with Git history and leftover files).

The fixture sources under ``sources/`` are plain files. Git history, nested
repositories, leftover files (IDE, OS, logs, caches) and CRLF/BOM variants
cannot live in this repository as-is, so this script generates them into a
target directory.

Usage:
    python tests/fixtures/build_fixtures.py OUT_DIR [--zip]

All names, e-mails, domains and secrets in the fixtures are fictitious.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

SOURCES = Path(__file__).resolve().parent / "sources"

JOHN = ("John Doe", "john.doe@corp.acme.com")
JANE = ("Jane Smith", "12345678+jsmith-acme@users.noreply.github.com")
WANG = ("王小明", "wang.xiaoming@corp.acme.com")
GITHUB_WEB = ("GitHub", "noreply@github.com")

FIXTURE_NAMES = ("fastapi-app", "angular-app", "fullstack")


@dataclass
class Commit:
    author: tuple[str, str]
    message: str
    prefixes: tuple[str, ...]  # files whose relative path starts with any of these; "" = all remaining
    committer: tuple[str, str] | None = None


@dataclass
class RepoPlan:
    commits: list[Commit]
    remotes: dict[str, str]
    branches: dict[str, Commit] = field(default_factory=dict)  # branch -> extra commit on top of HEAD~0
    tags: dict[str, tuple[str, str]] = field(default_factory=dict)  # tag -> tagger


FASTAPI_PLAN = RepoPlan(
    commits=[
        Commit(JOHN, "PAYGW-100 scaffold project (John Doe)",
               ("pyproject.toml", "requirements.txt", "README.md", ".gitignore", "app/__init__.py",
                "app/main.py", "app/config.py")),
        Commit(JANE, "PAYGW-214 payments endpoint\n\nReviewed-by: 王小明 <wang.xiaoming@corp.acme.com>",
               ("app/routers", "app/clients", "packages", "tests"), committer=GITHUB_WEB),
        Commit(WANG, "add legacy helpers and ops files", ("",)),
    ],
    remotes={
        "origin": "https://github.com/acme-org/paygw-api.git",
        "upstream": "git@github.corp.acme.com:platform/paygw-api.git",
    },
    branches={"feature/jdoe-login": Commit(JOHN, "WIP login (jdoe)", ())},
    tags={"v1.4.2": JANE},
)

ANGULAR_PLAN = RepoPlan(
    commits=[
        Commit(JANE, "PAYGW-300 init portal", ("package.json", "package-lock.json", "angular.json", "tsconfig",
                                               ".gitignore", ".npmrc", "src/main.ts", "src/index.html")),
        Commit(WANG, "PAYGW-311 header and layout", ("src/app", "packages")),
        Commit(JOHN, "env, i18n, proxy", ("",)),
    ],
    remotes={"origin": "git@github.com:acme-org/paygw-portal.git"},
    branches={"hotfix/jsmith-acme-cors": Commit(JANE, "fix CORS for portal.corp.acme.com", ())},
)

FULLSTACK_PLAN = RepoPlan(
    commits=[
        Commit(JOHN, "import backend from paygw-api", ("backend",)),
        Commit(JANE, "import frontend from paygw-portal", ("frontend",)),
        Commit(WANG, "compose, CI, owners", ("",)),
    ],
    remotes={"origin": "https://jdoe:fake-git-credential-41b7@github.com/acme-org/paygw.git"},
    tags={"release-2024.03": WANG},
)

FIXED_DATE = 1709258400  # 2024-03-01T10:00:00+08:00


def _git_env(author: tuple[str, str], committer: tuple[str, str], step: int) -> dict[str, str]:
    env = dict(os.environ)
    date = f"@{FIXED_DATE + step * 3600} +0800"
    env.update(
        GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1], GIT_AUTHOR_DATE=date,
        GIT_COMMITTER_NAME=committer[0], GIT_COMMITTER_EMAIL=committer[1], GIT_COMMITTER_DATE=date,
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
    )
    return env


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env or _git_env(JOHN, JOHN, 0),
                   stdout=subprocess.DEVNULL)


def _files(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(".git/") and "/.git/" not in rel:
            out.append(rel)
    return out


def _make_repo(root: Path, plan: RepoPlan, step0: int = 0) -> None:
    _git(root, "init", "-q", "-b", "main")
    remaining = _files(root)
    step = step0
    for c in plan.commits:
        chosen = [f for f in remaining if any(f.startswith(p) for p in c.prefixes)]
        remaining = [f for f in remaining if f not in chosen]
        if chosen:
            _git(root, "add", "--", *chosen)
        step += 1
        _git(root, "commit", "-q", "--allow-empty", "-m", c.message,
             env=_git_env(c.author, c.committer or c.author, step))
    for name, url in plan.remotes.items():
        _git(root, "remote", "add", name, url)
    for branch, c in plan.branches.items():
        step += 1
        _git(root, "checkout", "-q", "-b", branch)
        _git(root, "commit", "-q", "--allow-empty", "-m", c.message, env=_git_env(c.author, c.author, step))
        _git(root, "checkout", "-q", "main")
    for tag, tagger in plan.tags.items():
        step += 1
        _git(root, "tag", "-a", tag, "-m", f"release {tag} by {tagger[0]}", env=_git_env(tagger, tagger, step))


def _arm_fsmonitor(root: Path) -> None:
    """Configure a command that runs if anyone calls `git status` in the repo.

    The sanitizer must read Git data without executing it. The command only
    creates a marker file so tests can detect it.
    """
    marker = "FSMONITOR_TRIGGERED"
    cmd = f"{Path(sys.executable).as_posix()} -c \"open('{marker}','w').close()\""
    _git(root, "config", "core.fsmonitor", cmd)


def _to_crlf_bom(path: Path) -> None:
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(b"\xef\xbb\xbf" + data)


def _write(root: Path, rel: str, data: str | bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8", newline="")
    else:
        p.write_bytes(data)


def _python_residue(root: Path) -> None:
    _write(root, ".DS_Store", b"\x00\x00\x00\x01Bud1" + "jdoe paygw".encode("utf-16-be"))
    _write(root, "Thumbs.db", b"\xd0\xcf\x11\xe0fake-thumbs")
    _write(root, ".idea/workspace.xml",
           '<?xml version="1.0"?>\n<project version="4">\n'
           '  <component name="PropertiesComponent">\n'
           '    <property name="last_opened_file_path" value="/Users/jdoe/projects/paygw" />\n'
           '    <property name="git.user" value="john.doe@corp.acme.com" />\n'
           '  </component>\n</project>\n')
    _write(root, ".vscode/settings.json",
           '{\n  "python.defaultInterpreterPath": "/Users/jdoe/.venvs/paygw/bin/python"\n}\n')
    _write(root, "app/__pycache__/main.cpython-312.pyc",
           b"\xcb\r\r\n\x00\x00\x00\x00/Users/jdoe/projects/paygw/app/main.py\x00")
    _write(root, "app.log", "2024-03-01 10:00:00 started by jdoe on JDOE-MBP.corp.acme.com\n")
    _write(root, ".coverage", b"SQLite format 3\x00/Users/jdoe/projects/paygw/app/config.py")
    _write(root, ".pytest_cache/v/cache/nodeids", '["tests/test_api.py::test_health"]\n')
    _write(root, ".env",
           "DATABASE_URL=postgresql://paygw_app:Pr0d-Pa55w0rd!@db01.corp.acme.com:5432/paygw\n"
           "JWT_SECRET=9f8c2d7e6b5a4f3e2d1c0b9a8f7e6d5c\n")
    _write(root, "app/routers/payments.py.orig", "# merge leftover by jsmith-acme\n")


def _angular_residue(root: Path) -> None:
    _write(root, ".DS_Store", b"\x00\x00\x00\x01Bud1" + "jsmith-acme".encode("utf-16-be"))
    _write(root, ".angular/cache/19.2.0/paygw-portal/manifest.json",
           '{"root": "/Users/jsmith/work/paygw-portal"}\n')
    _write(root, "npm-debug.log", "0 verbose cli /Users/jsmith/.nvm/versions/node/v22/bin/node\n")
    _write(root, "dist/paygw-portal/browser/main.js.map",
           '{"version":3,"sources":["/Users/jsmith/work/paygw-portal/src/main.ts"],'
           '"sourcesContent":["// by john.doe@corp.acme.com"]}\n')
    _write(root, "node_modules/.package-lock.json", '{"name": "paygw-portal"}\n')
    _write(root, ".vscode/launch.json", '{ "configurations": [{ "url": "https://portal.corp.acme.com" }] }\n')
    _write(root, ".env.local", "NG_APP_SENTRY_DSN=https://3f9a1c2b4d5e6f708192a3b4c5d6e7f8@sentry.corp.acme.com/12\n")
    _write(root, "src/app/.app.component.ts.swp", b"b0VIM 8.2\x00jdoe\x00JDOE-MBP")


def build_fastapi(dest: Path) -> Path:
    root = dest / "fastapi-app"
    shutil.copytree(SOURCES / "fastapi-app", root)
    _to_crlf_bom(root / "app" / "legacy.py")
    _make_repo(root, FASTAPI_PLAN)
    _python_residue(root)
    _arm_fsmonitor(root)
    return root


def build_angular(dest: Path) -> Path:
    root = dest / "angular-app"
    shutil.copytree(SOURCES / "angular-app", root)
    _make_repo(root, ANGULAR_PLAN)
    _angular_residue(root)
    return root


def build_fullstack(dest: Path) -> Path:
    root = dest / "fullstack"
    shutil.copytree(SOURCES / "fastapi-app", root / "backend")
    shutil.copytree(SOURCES / "angular-app", root / "frontend")
    shutil.copytree(SOURCES / "fullstack-extra", root, dirs_exist_ok=True)
    _to_crlf_bom(root / "backend" / "app" / "legacy.py")
    # The frontend keeps its own repository (nested repo), committed before the outer one.
    _make_repo(root / "frontend", ANGULAR_PLAN)
    parked = dest / ".fullstack-frontend-git"
    (root / "frontend" / ".git").rename(parked)
    _make_repo(root, FULLSTACK_PLAN, step0=10)
    parked.rename(root / "frontend" / ".git")
    # A linked-worktree pointer file left behind in a subfolder.
    _write(root, "tools/.git", "gitdir: /Users/jdoe/src/paygw/.git/worktrees/tools\n")
    _python_residue(root / "backend")
    _angular_residue(root / "frontend")
    _write(root, ".DS_Store", b"\x00\x00\x00\x01Bud1" + "wang.xiaoming".encode("utf-16-be"))
    _arm_fsmonitor(root)
    return root


BUILDERS = {"fastapi-app": build_fastapi, "angular-app": build_angular, "fullstack": build_fullstack}


def zip_fixture(root: Path, zip_path: Path) -> Path:
    """Zip a fixture the way a developer would: whole folder, plus macOS junk entries."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                arc = f"{root.name}/{p.relative_to(root).as_posix()}"
                zf.write(p, arc)
        zf.writestr(f"__MACOSX/{root.name}/._README.md", b"\x00\x05\x16\x07jdoe")
    return zip_path


def build_all(dest: Path, names: tuple[str, ...] = FIXTURE_NAMES, make_zip: bool = False) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in names:
        target = dest / name
        if target.exists():
            raise FileExistsError(target)
        out[name] = BUILDERS[name](dest)
        if make_zip:
            zip_fixture(out[name], dest / f"{name}.zip")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--zip", action="store_true", help="also write <name>.zip next to each fixture")
    ap.add_argument("--only", choices=FIXTURE_NAMES, action="append")
    args = ap.parse_args()
    built = build_all(args.out_dir, tuple(args.only or FIXTURE_NAMES), args.zip)
    for name, path in built.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
