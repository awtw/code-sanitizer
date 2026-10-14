"""Check a deliverable directory against tests/fixtures/expected.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

EXPECTED_FILE = Path(__file__).resolve().parent / "fixtures" / "expected.yaml"


def load_expected() -> dict:
    return yaml.safe_load(EXPECTED_FILE.read_text(encoding="utf-8"))


@dataclass
class Violation:
    kind: str
    path: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.path}: {self.detail}"


def _sections(name: str, expected: dict) -> list[tuple[str, dict]]:
    """(prefix, section) pairs; composite fixtures pull in their parts with a path prefix."""
    sec = expected[name]
    out = [("", sec)]
    for prefix, part in sec.get("compose", {}).items():
        out.append((prefix + "/", expected[part]))
    return out


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _covered(text: str, start: int, end: int, allowed: list[str]) -> bool:
    lower = text.lower()
    for token in allowed:
        t = token.lower()
        i = lower.find(t, max(0, start - len(t)))
        while i != -1 and i <= start:
            if i + len(t) >= end:
                return True
            i = lower.find(t, i + 1)
    return False


def check_deliverable(root: Path, name: str, expected: dict | None = None) -> list[Violation]:
    expected = expected or load_expected()
    common = expected["common"]
    violations: list[Violation] = []
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]

    forbidden = [f.lower() for f in common["forbidden"]]
    org_patterns = [re.compile(re.escape(t), re.IGNORECASE) for t in common["org_terms"]]
    for p in files:
        rel = p.relative_to(root).as_posix()
        for f in forbidden:
            if f in rel.lower():
                violations.append(Violation("forbidden-path", rel, f))
        text = _text(p)
        lower = text.lower()
        for f in forbidden:
            if f in lower:
                line = lower[: lower.index(f)].count("\n") + 1
                violations.append(Violation("forbidden", f"{rel}:{line}", f))
        for pat in org_patterns:
            for m in pat.finditer(text):
                if not _covered(text, m.start(), m.end(), common["allowed_org_tokens"]):
                    line = text[: m.start()].count("\n") + 1
                    snippet = text[max(0, m.start() - 20): m.end() + 20].replace("\n", "\\n")
                    violations.append(Violation("org-term", f"{rel}:{line}", snippet))

    for prefix, sec in _sections(name, expected):
        for rel in sec.get("absent_paths", []):
            if (root / prefix / rel).exists():
                violations.append(Violation("leftover", prefix + rel, "must be removed"))
        for rel, snippets in sec.get("must_preserve", {}).items():
            p = root / prefix / rel
            if not p.exists():
                violations.append(Violation("missing", prefix + rel, "file must exist"))
                continue
            text = _text(p)
            for s in snippets:
                if s not in text:
                    violations.append(Violation("not-preserved", prefix + rel, repr(s)))
        for rel, snippets in sec.get("must_exist", {}).items():
            p = root / prefix / rel
            if not p.exists():
                violations.append(Violation("missing", prefix + rel, "file must exist"))
                continue
            text = _text(p)
            for s in snippets:
                if s not in text:
                    violations.append(Violation("missing-content", prefix + rel, repr(s)))
        for rel, enc in sec.get("encodings", {}).items():
            p = root / prefix / rel
            if not p.exists():
                continue
            data = p.read_bytes()
            if enc.get("bom") and not data.startswith(b"\xef\xbb\xbf"):
                violations.append(Violation("encoding", prefix + rel, "UTF-8 BOM lost"))
            if enc.get("newline") == "crlf" and (b"\r\n" not in data or data.replace(b"\r\n", b"").count(b"\n")):
                violations.append(Violation("encoding", prefix + rel, "CRLF line endings changed"))
    return violations
