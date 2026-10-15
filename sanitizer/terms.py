"""Sensitive terms: build them from the identity harvest and the policy file,
match them in bytes, and produce case-preserving replacements."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


PERSON = "developer"
EMAIL = "developer@example.invalid"
REPLACEMENT_DOMAIN = "example.internal"

HOME_PATH_RE = re.compile(
    rb"(?P<prefix>/Users/|/home/|[A-Za-z]:(?:\\\\|\\)Users(?:\\\\|\\))(?P<user>[^/\\\s'\"`<>]+)")


@dataclass(frozen=True)
class Term:
    value: str
    replacement: str
    category: str  # person | email | account | company | org | repo | host | domain | system | custom
    source: str = ""


@dataclass
class Policy:
    terms: list[Term] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)  # substrings that are kept as is (private packages, registries)
    residue_extra: list[str] = field(default_factory=list)
    residue_keep: list[str] = field(default_factory=list)
    jira_projects: list[str] = field(default_factory=list)
    gitlab_projects: list[str] = field(default_factory=list)

    def allowed(self, text: bytes, start: int, end: int) -> str | None:
        lower = text.lower()
        for token in self.allow:
            t = token.lower().encode()
            i = lower.find(t, max(0, start - len(t)))
            while i != -1 and i <= start:
                if i + len(t) >= end:
                    return token
                i = lower.find(t, i + 1)
        return None


def registrable_domain(host: str) -> str:
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and labels[-2] in {"com", "net", "org", "gov", "edu", "co", "ac"} and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _identity_terms(doc: dict) -> list[Term]:
    terms: list[Term] = []
    person = doc.get("replacements", {}).get("person", PERSON)
    email = doc.get("replacements", {}).get("email", EMAIL)
    for i, p in enumerate(doc.get("people", []), 1):
        src = f"person #{i}"
        for e in p.get("emails", []):
            terms.append(Term(e, email, "email", src))
            local = e.split("@")[0]
            if "+" in local:  # GitHub noreply: 12345678+account
                local = local.split("+", 1)[1]
            if len(local) >= 4:
                terms.append(Term(local, person, "account", src))
        for n in p.get("names", []):
            terms.append(Term(n, person, "person", src))
        for a in p.get("accounts", []):
            terms.append(Term(a, person, "account", src))
        for a in p.get("derived", []):
            terms.append(Term(a, person, "account", f"{src} (derived from name)"))
    counters: dict[str, int] = {}
    for o in doc.get("organization", []):
        if not o.get("enabled", True):
            continue
        cat = o["category"]
        value = o["value"]
        repl = o.get("replace_with")
        if repl is None:
            if cat in {"host", "email_domain", "domain"}:
                reg = registrable_domain(value)
                terms.append(Term(reg, REPLACEMENT_DOMAIN, "domain", f"{cat} {value}"))
                continue
            counters[cat] = counters.get(cat, 0) + 1
            repl = {"company": "company", "org": "org"}.get(cat, f"{cat}{counters[cat]:02d}")
        terms.append(Term(value, repl, "domain" if cat in {"host", "email_domain"} else cat, cat))
    return terms


def load_identities(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_policy(identities: dict, policy_path: Path | None) -> Policy:
    doc = dict(identities)
    pol = yaml.safe_load(policy_path.read_text(encoding="utf-8")) if policy_path else {}
    pol = pol or {}
    doc["organization"] = list(doc.get("organization", [])) + list(pol.get("organization", []))
    terms = _identity_terms(doc)
    markers = pol.get("internal_markers", {})
    for domain in markers.get("domains", []):
        terms.append(Term(domain, REPLACEMENT_DOMAIN, "domain", "internal_markers"))
    for t in pol.get("terms", []):
        terms.append(Term(t["value"], t.get("replace_with", "redacted"), t.get("category", "custom"), "policy"))
    # Deduplicate (case-insensitive); keep the first replacement.
    seen: dict[str, Term] = {}
    for t in terms:
        seen.setdefault(t.value.lower(), t)
    allow = list(pol.get("allow", {}).get("private_packages", [])) + list(pol.get("allow", {}).get("registries", []))
    allow += list(pol.get("allow", {}).get("other", []))
    residue = pol.get("residue", {})
    return Policy(sorted(seen.values(), key=lambda t: -len(t.value)), allow,
                  list(residue.get("extra", [])), list(residue.get("keep", [])),
                  list(markers.get("jira_projects", [])), list(markers.get("gitlab_projects", [])))


def _is_word(b: int) -> bool:
    return (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122)


def _lower(b: int) -> bool:
    return 97 <= b <= 122


def _upper(b: int) -> bool:
    return 65 <= b <= 90


@dataclass
class Match:
    start: int
    end: int
    term: Term


class Matcher:
    def __init__(self, terms: list[Term]) -> None:
        self.terms = terms
        self._by_lower = {t.value.lower().encode(): t for t in terms}
        alts = sorted(self._by_lower, key=len, reverse=True)
        self._re = re.compile(b"|".join(re.escape(a) for a in alts), re.IGNORECASE) if alts else None

    def find(self, data: bytes) -> list[Match]:
        if self._re is None:
            return []
        out = []
        pos = 0
        while True:
            m = self._re.search(data, pos)
            if not m:
                break
            s, e = m.span()
            if self._boundary_ok(data, s, e):
                out.append(Match(s, e, self._by_lower[m.group(0).lower()]))
                pos = e
            else:
                pos = s + 1
        return out

    @staticmethod
    def _boundary_ok(data: bytes, s: int, e: int) -> bool:
        first, last = data[s], data[e - 1]
        if s > 0 and _is_word(data[s - 1]) and _is_word(first):
            # allow camelCase start: "fooAcme" (lower → Upper)
            if not (_lower(data[s - 1]) and _upper(first)):
                return False
        if e < len(data) and _is_word(data[e]) and _is_word(last):
            # allow camelCase end: "AcmeClient" (lower → Upper)
            if not (_lower(last) and _upper(data[e])):
                return False
        return True


def case_like(original: bytes, replacement: str) -> bytes:
    """Apply the letter case style of ``original`` to ``replacement``."""
    letters = [c for c in original.decode("utf-8", "replace") if c.isascii() and c.isalpha()]
    if "@" in replacement or not letters:
        return replacement.encode()
    if all(c.isupper() for c in letters) and len(letters) > 1:
        return replacement.upper().encode()
    if letters[0].isupper():
        return (replacement[:1].upper() + replacement[1:]).encode()
    return replacement.encode()


def glob_match(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        pat = pat.rstrip("/")
        if "/" in pat:
            if fnmatch.fnmatchcase(rel, pat) or rel.startswith(pat + "/"):
                return True
        elif any(fnmatch.fnmatchcase(p, pat) for p in parts):
            return True
    return False
