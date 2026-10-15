"""Scan a snapshot and build the change plan (nothing is modified here).

Each finding says *where* (file, line, syntax context), *what* (identity,
organisation term, secret, leftover file) and *what will happen*:

    remove     leftover / VCS file, dropped from the output
    replace    text replaced inside a comment, string, text node or config file
    redact     secret value replaced by a REDACTED_SECRET_nnn placeholder
    keep       match left as is on purpose (allow-list, code symbol)
    review     cannot be handled automatically (binary file, decode error)
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .secrets import find_secrets
from .markers import find_markers
from .syntax import code_tokens, enclosing_token, language_for, regions_for
from .terms import EMAIL, HOME_PATH_RE, PERSON, Matcher, Policy, case_like, glob_match

RESIDUE_DEFAULTS = [
    # version control
    ".git", ".svn", ".hg", ".github", "CODEOWNERS", ".mailmap", "*.orig", "*.rej",
    # editors / IDE
    ".idea", ".vscode", ".history", "*.swp", "*.swo", "*~", ".project", ".settings",
    # operating system
    ".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX",
    # logs
    "*.log",
    # tests / coverage
    ".pytest_cache", ".coverage", "coverage", "htmlcov", ".nyc_output",
    # Python build output
    "__pycache__", "*.pyc", "*.pyo", ".venv", "venv", "*.egg-info", ".mypy_cache", ".ruff_cache", ".tox",
    # Node / Angular build output
    "node_modules", "dist", ".angular", "*.map",
    # local environment files (an .env.example with keys only is generated)
    ".env", ".env.local", ".env.*.local",
]
EMAIL_RE = re.compile(rb"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PERSON_KINDS = {"person", "account", "email"}
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
             "Pipfile.lock"}
KNOWN_SAFE_EMAIL_SUFFIXES = (b"@example.invalid", b"@example.com", b"@example.org", b"@localhost")
ENV_FILES = [".env", ".env.local", ".env.*.local"]


@dataclass
class Finding:
    path: str
    line: int
    category: str  # leftover | person | email | account | company | org | repo | domain | system | home-path | secret | binary
    context: str
    action: str
    detail: str = ""


@dataclass
class Edit:
    start: int
    end: int
    replacement: bytes
    kind: str = ""


@dataclass
class Plan:
    findings: list[Finding] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)
    edits: dict[str, list[Edit]] = field(default_factory=dict)
    generated: dict[str, bytes] = field(default_factory=dict)
    secrets: list[dict] = field(default_factory=list)  # id, path, line, kind (never the value)

    def summary(self) -> dict:
        by = Counter((f.category, f.action) for f in self.findings)
        return {f"{c}/{a}": n for (c, a), n in sorted(by.items())}

    def to_json(self) -> dict:
        return {
            "summary": self.summary(),
            "removals": self.removals,
            "generated": sorted(self.generated),
            "edit_counts": {p: len(e) for p, e in sorted(self.edits.items())},
            "secrets": self.secrets,
            "findings": [asdict(f) for f in self.findings],
        }


def _line(data: bytes, pos: int) -> int:
    return data.count(b"\n", 0, pos) + 1


def _iter_files(root: Path, residue: list[str], plan: Plan) -> list[str]:
    keep = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        rel_dir = d.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        for name in sorted(dirnames):
            rel = rel_dir + name
            if glob_match(rel, residue):
                plan.removals.append(rel + "/")
                plan.findings.append(Finding(rel + "/", 0, "leftover", "folder", "remove"))
                dirnames.remove(name)
        dirnames.sort()
        for fn in sorted(filenames):
            rel = rel_dir + fn
            if glob_match(rel, residue):
                plan.removals.append(rel)
                plan.findings.append(Finding(rel, 0, "leftover", "file", "remove"))
            else:
                keep.append(rel)
    return keep


def _env_example(data: bytes) -> bytes:
    out = []
    for line in data.splitlines(keepends=True):
        m = re.match(rb"^(\s*[A-Za-z_][A-Za-z0-9_.-]*\s*=)", line)
        if m:
            nl = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            out.append(m.group(1) + nl)
    return b"".join(out)


def _decode_ok(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def build_plan(root: Path, policy: Policy) -> Plan:
    plan = Plan()
    residue = [r for r in RESIDUE_DEFAULTS + policy.residue_extra if r not in policy.residue_keep]
    # Original Git objects, signatures, configuration and hooks must never be exported.
    if ".git" not in residue:
        residue.append(".git")
    files = _iter_files(root, residue, plan)

    # .env files are removed; keep their keys in an .env.example (unless one exists).
    for rel in list(plan.removals):
        if not rel.endswith("/") and glob_match(rel, ENV_FILES):
            example = rel.rsplit("/", 1)[0] + "/.env.example" if "/" in rel else ".env.example"
            if example not in files and example not in plan.generated:
                plan.generated[example] = _env_example((root / rel).read_bytes())
            for h in find_secrets((root / rel).read_bytes(), is_env_file=True):
                plan.secrets.append({"id": None, "path": rel, "line": _line((root / rel).read_bytes(), h.start),
                                     "kind": h.kind, "handled_by": "file removed; keys kept in " + example})

    contents: dict[str, bytes] = {}
    regions = {}
    tokens: set[bytes] = set()
    for rel in files:
        data = (root / rel).read_bytes()
        contents[rel] = data
        if not _decode_ok(data):
            continue
        r = regions_for(rel, data)
        regions[rel] = r
        if language_for(rel):
            tokens |= code_tokens(data, r)

    matcher = Matcher(policy.terms)
    secret_no = 0
    for rel in files:
        data = contents[rel]
        if rel not in regions:
            hits = matcher.find(data)
            if hits:
                plan.findings.append(Finding(rel, 0, "binary", "binary", "review",
                                             f"{len(hits)} sensitive term(s) in a binary/undecodable file"))
            continue
        reg = regions[rel]
        edits: list[Edit] = []

        def taken(s: int, e: int) -> bool:
            return any(x.start < e and s < x.end for x in edits)

        for h in find_secrets(data):
            secret_no += 1
            sid = f"REDACTED_SECRET_{secret_no:03d}"
            edits.append(Edit(h.start, h.end, sid.encode()))
            plan.secrets.append({"id": sid, "path": rel, "line": _line(data, h.start), "kind": h.kind})
            plan.findings.append(Finding(rel, _line(data, h.start), "secret", reg.context_at(h.start, h.end),
                                         "redact", f"{h.kind} -> {sid}"))

        for m in HOME_PATH_RE.finditer(data):
            s, e = m.span("user")
            if taken(s, e) or m.group("user").lower() == PERSON.encode():
                continue
            ctx = reg.context_at(s, e)
            action = "replace" if ctx in {"comment", "string", "text", "config"} else "keep"
            if action == "replace":
                edits.append(Edit(s, e, PERSON.encode()))
            plan.findings.append(Finding(rel, _line(data, s), "home-path", ctx, action,
                                         m.group(0).decode("utf-8", "replace")))

        for hit in matcher.find(data):
            s, e = hit.start, hit.end
            if taken(s, e):
                continue
            ctx = reg.context_at(s, e)
            term = hit.term
            original = data[s:e]
            detail = f"{original.decode('utf-8', 'replace')} -> {term.replacement}"
            allowed = policy.allowed(data, s, e)
            if allowed:
                action, detail = "keep", f"allow-list: {allowed}"
            elif ctx in {"code", "protected"}:
                action, detail = "keep", f"code symbol: {enclosing_token(data, s, e).decode('utf-8', 'replace')}"
            elif enclosing_token(data, s, e) in tokens:
                action = "keep"
                detail = f"same as code symbol: {enclosing_token(data, s, e).decode('utf-8', 'replace')}"
            else:
                action = "replace"
                edits.append(Edit(s, e, case_like(original, term.replacement), term.category))
            plan.findings.append(Finding(rel, _line(data, s), term.category, ctx, action, detail))

        for s, e, replacement, category in find_markers(data, policy):
            if taken(s, e):
                continue
            ctx = reg.context_at(s, e)
            allowed = policy.allowed(data, s, e)
            if allowed:
                action, detail = "keep", f"allow-list: {allowed}"
            elif ctx in {"code", "protected"}:
                action, detail = "keep", "code symbol"
            else:
                action, detail = "replace", replacement.decode()
                edits.append(Edit(s, e, replacement, category))
            plan.findings.append(Finding(rel, _line(data, s), category, ctx, action, detail))

        # E-mail addresses that are not covered by any known term: possibly an unknown person.
        for m in EMAIL_RE.finditer(data) if rel.rsplit("/", 1)[-1] not in LOCKFILES else ():
            s, e = m.span()
            if m.group(0).lower().endswith(KNOWN_SAFE_EMAIL_SUFFIXES) or policy.allowed(data, s, e):
                continue
            inside = [x for x in edits if s <= x.start and x.end <= e]
            if any(x.start == s and x.kind in PERSON_KINDS for x in inside):
                # A person's account is the local part: replace the whole address uniformly.
                edits[:] = [x for x in edits if x not in inside] + [Edit(s, e, EMAIL.encode(), "email")]
                continue
            if any(x.start == s for x in inside):
                continue
            plan.findings.append(Finding(rel, _line(data, s), "unknown-email", reg.context_at(s, e), "review",
                                         m.group(0).decode("utf-8", "replace")))

        if edits:
            plan.edits[rel] = sorted(edits, key=lambda x: x.start)
    return plan


def apply_edits(data: bytes, edits: list[Edit]) -> bytes:
    out, pos = [], 0
    for ed in sorted(edits, key=lambda x: x.start):
        out.append(data[pos:ed.start])
        out.append(ed.replacement)
        pos = ed.end
    out.append(data[pos:])
    return b"".join(out)
