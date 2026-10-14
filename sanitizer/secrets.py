"""Find hard-coded secrets. Values are replaced by labelled placeholders so a
tester still sees *that* a secret was there, without getting the real value."""

from __future__ import annotations

import re
from dataclasses import dataclass

KEY_WORDS = rb"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|auth|dsn|credential)"


@dataclass(frozen=True)
class SecretHit:
    start: int
    end: int
    kind: str


PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    # scheme://user:password@host
    ("url-password", re.compile(rb"[a-z][a-z0-9+.-]*://[^\s:/@'\"`]+:(?P<v>[^\s@/'\"`]+)@[^\s'\"`]+", re.I)),
    # scheme://<long hex/base64 key>@host (Sentry DSN and similar)
    ("url-key", re.compile(rb"[a-z][a-z0-9+.-]*://(?P<v>[A-Za-z0-9]{24,})@", re.I)),
    ("aws-access-key", re.compile(rb"(?<![A-Z0-9])(?P<v>(?:AKIA|ASIA)[A-Z0-9]{16})(?![A-Z0-9])")),
    ("npm-auth-token", re.compile(rb"_authToken\s*=\s*(?P<v>[^\s]+)")),
    ("private-key", re.compile(rb"(?P<v>-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)", re.S)),
    # getenv("JWT_SECRET", "value") / config.get('api_key', 'value')
    ("default-value", re.compile(rb"[\"'][\w.-]*" + KEY_WORDS + rb"[\w.-]*[\"']\s*,\s*[\"'](?P<v>[^\"'\s]{8,})[\"']", re.I)),
    # KEY = "value" / key: 'value' / "key": "value"
    ("assignment", re.compile(rb"(?<![\w-])[\w.-]*" + KEY_WORDS + rb"[\w.-]*[\"']?\s*[:=]\s*[\"'](?P<v>[^\"'\s]{8,})[\"']", re.I)),
]

ENV_LINE_RE = re.compile(rb"^(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(?P<v>.*?)\s*$", re.M)
PLACEHOLDER_RE = re.compile(rb"^(?:REDACTED_SECRET_\d+|\$\{[^}]*\}|<[^>]*>|x+|\*+|changeme|example|placeholder|your[_-].*)$", re.I)


def find_secrets(data: bytes, is_env_file: bool = False) -> list[SecretHit]:
    hits: list[SecretHit] = []
    if is_env_file:
        for m in ENV_LINE_RE.finditer(data):
            if m.group("v") and not m.group("key").startswith(b"#"):
                hits.append(SecretHit(m.start("v"), m.end("v"), "env-value"))
        return hits
    for kind, pat in PATTERNS:
        for m in pat.finditer(data):
            s, e = m.span("v")
            if b"REDACTED_SECRET_" in data[s:e] or PLACEHOLDER_RE.match(data[s:e]):
                continue  # already redacted (keeps a second pass idempotent)
            if any(h.start <= s < h.end or s <= h.start < e for h in hits):
                continue
            hits.append(SecretHit(s, e, kind))
    return sorted(hits, key=lambda h: h.start)
