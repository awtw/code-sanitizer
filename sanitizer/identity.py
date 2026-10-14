"""Harvest developer identities and organisation hints from Git data.

Git metadata is read with dulwich (pure Python). The ``git`` executable is
never called: a repository's config can make ``git`` run arbitrary commands
(``core.fsmonitor``, ``core.sshCommand``, hooks ...). Pointers that lead
outside the project root (worktree ``.git`` files, ``commondir``,
``objects/info/alternates``, config includes) are recorded but not followed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dulwich.config import ConfigFile
from dulwich.objects import Commit, Tag
from dulwich.repo import Repo

PUBLIC_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "ssh.github.com"}
NOREPLY_RE = re.compile(r"^(?:\d+\+)?(?P<account>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$",
                        re.IGNORECASE)
IDENT_RE = re.compile(r"^\s*(?P<name>.*?)\s*<(?P<email>[^<>]*)>")
TRAILER_RE = re.compile(r"^(?:Reviewed-by|Signed-off-by|Co-authored-by|Acked-by|Tested-by|Reported-by):\s*(?P<ident>.+<[^<>]+>)\s*$",
                        re.IGNORECASE | re.MULTILINE)
MENTION_RE = re.compile(r"(?<![\w@])@(?P<handle>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?)")
SCP_URL_RE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")
SERVICE_IDENTITIES = {
    "noreply@github.com": "GitHub web-flow committer",
    "actions@github.com": "GitHub Actions",
    "41898282+github-actions[bot]@users.noreply.github.com": "GitHub Actions bot",
}


@dataclass
class Person:
    names: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    accounts: set[str] = field(default_factory=set)
    evidence: list[str] = field(default_factory=list)

    @property
    def derived(self) -> set[str]:
        """Usual corporate account patterns built from the name (jdoe, john.doe, doe.john ...)."""
        out: set[str] = set()
        for name in self.names:
            words = [w.lower() for w in re.findall(r"[A-Za-z]+", name)]
            if len(words) < 2:
                continue
            first, last = words[0], words[-1]
            out |= {f"{first}.{last}", f"{first}_{last}", f"{first[0]}{last}", f"{last}.{first}",
                    f"{first}{last}"}
        return {d for d in out if len(d) >= 4} - {a.lower() for a in self.accounts}


@dataclass
class OrgTerm:
    value: str
    category: str  # org | repo | host | email_domain
    evidence: list[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        """Single plain words (e.g. ``platform``) are too generic to replace without review."""
        return self.category in {"org", "repo"} and re.fullmatch(r"[a-z]+", self.value) is not None


@dataclass
class Harvest:
    people: list[Person] = field(default_factory=list)
    ignored: list[tuple[str, str, str]] = field(default_factory=list)  # (name, email, reason)
    org_terms: list[OrgTerm] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)  # where a credential was found (never the value)
    refs: list[str] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def accounts(self) -> set[str]:
        return set().union(*(p.accounts for p in self.people)) if self.people else set()


class _Collector:
    def __init__(self) -> None:
        self.raw: list[tuple[str, str, str]] = []  # (name, email, evidence)
        self.accounts: list[tuple[str, str]] = []  # (account, evidence)
        self.aliases: list[tuple[str, str, str]] = []  # (canonical email, alias email, evidence)
        self.org: dict[tuple[str, str], OrgTerm] = {}
        self.result = Harvest()

    def ident(self, raw: bytes | str, evidence: str) -> None:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        m = IDENT_RE.match(text)
        if m:
            self.raw.append((m["name"].strip(), m["email"].strip().lower(), evidence))

    def message(self, message: bytes, evidence: str) -> None:
        text = message.decode("utf-8", "replace")
        for m in TRAILER_RE.finditer(text):
            self.ident(m["ident"], f"{evidence} trailer")

    def add_org(self, value: str, category: str, evidence: str) -> None:
        if not value:
            return
        key = (value.lower(), category)
        term = self.org.setdefault(key, OrgTerm(value, category))
        if evidence not in term.evidence:
            term.evidence.append(evidence)

    def remote(self, url: str, evidence: str) -> None:
        user = password = None
        if "://" in url:
            parts = urlsplit(url)
            host, path = parts.hostname or "", parts.path
            user, password = parts.username, parts.password
        else:
            m = SCP_URL_RE.match(url)
            if not m:
                self.result.warnings.append(f"{evidence}: unrecognised remote URL format")
                return
            host, path, user = m["host"], m["path"], m["user"]
        if password:
            self.result.credentials.append(f"{evidence} (user {user!r}, password/token in URL)")
        if user and user not in {"git", "ssh"}:
            self.accounts.append((user, f"{evidence} URL user"))
        segments = [s for s in path.strip("/").removesuffix(".git").split("/") if s]
        if host and host.lower() not in PUBLIC_GIT_HOSTS:
            self.add_org(host.lower(), "host", evidence)
        if len(segments) >= 2:
            for org in segments[:-1]:
                self.add_org(org, "org", evidence)
            self.add_org(segments[-1], "repo", evidence)

    def finish(self) -> Harvest:
        # Union people that share an e-mail address or a name.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        kept = []
        for name, email, ev in self.raw:
            if email in SERVICE_IDENTITIES:
                entry = (name, email, SERVICE_IDENTITIES[email])
                if entry not in self.result.ignored:
                    self.result.ignored.append(entry)
                continue
            kept.append((name, email, ev))
            union(f"e:{email}", f"e:{email}")
            if name:
                union(f"n:{name.lower()}", f"e:{email}")
        for canonical, alias, _ in self.aliases:
            union(f"e:{alias}", f"e:{canonical}")

        groups: dict[str, Person] = {}

        def person_for(key: str) -> Person:
            return groups.setdefault(find(key), Person())

        for name, email, ev in kept:
            p = person_for(f"e:{email}")
            if name:
                p.names.add(name)
            p.emails.add(email)
            if ev not in p.evidence:
                p.evidence.append(ev)
        for canonical, alias, ev in self.aliases:
            p = person_for(f"e:{canonical}")
            p.emails.update({canonical, alias})
            p.evidence.append(ev)

        for p in groups.values():
            for email in p.emails:
                m = NOREPLY_RE.match(email)
                if m:
                    p.accounts.add(m["account"])

        # Accounts from URLs, CODEOWNERS, branch names: attach to the person whose
        # noreply account or e-mail local part matches, otherwise keep as a standalone person.
        def owner_of(account: str) -> Person | None:
            a = account.lower()
            for p in groups.values():
                if a in {x.lower() for x in p.accounts} or a in {e.split("@")[0].lower() for e in p.emails}:
                    return p
            return None

        for account, ev in self.accounts:
            p = owner_of(account)
            if p is None:
                p = groups.setdefault(f"a:{account.lower()}", Person())
            p.accounts.add(account)
            if ev not in p.evidence:
                p.evidence.append(ev)

        # Branch / tag names that contain a known account.
        known = {a.lower() for p in groups.values() for a in p.accounts}
        for ref in self.result.refs:
            for a in known:
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", ref, re.IGNORECASE):
                    p = owner_of(a)
                    if p is not None and f"ref {ref}" not in p.evidence:
                        p.evidence.append(f"ref {ref}")

        for p in groups.values():
            for e in p.emails:
                domain = e.split("@")[-1]
                if domain and not domain.endswith("noreply.github.com"):
                    self.add_org(domain, "email_domain", f"e-mail {e}")

        self.result.people = sorted(groups.values(), key=lambda p: (sorted(p.emails) or sorted(p.accounts)))
        self.result.org_terms = sorted(self.org.values(), key=lambda t: (t.category, t.value.lower()))
        return self.result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _find_git_entries(root: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if ".git" in dirnames:
            found.append(Path(dirpath) / ".git")
            dirnames.remove(".git")
        if ".git" in filenames:
            found.append(Path(dirpath) / ".git")
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
    return sorted(found)


def _cfg_get(cfg: ConfigFile, section: tuple[bytes, ...], key: bytes) -> str:
    try:
        return cfg.get(section, key).decode("utf-8", "replace")
    except KeyError:
        return ""


def _read_repo(gitdir: Path, rel: str, root: Path, c: _Collector) -> None:
    if (gitdir / "commondir").exists():
        c.result.warnings.append(f"{rel}: has commondir (linked worktree); not followed")
        return
    alternates = gitdir / "objects" / "info" / "alternates"
    if alternates.exists() and alternates.read_text(errors="replace").strip():
        c.result.warnings.append(f"{rel}: uses objects/info/alternates; history not read")
        return
    c.result.repositories.append(rel)

    config_path = gitdir / "config"
    if config_path.exists():
        cfg = ConfigFile.from_path(str(config_path), expand_includes=False)
        for section in cfg.sections():
            if section[0] == b"remote" and len(section) > 1:
                name = section[1].decode("utf-8", "replace")
                for key in (b"url", b"pushurl"):
                    url = _cfg_get(cfg, section, key)
                    if url:
                        c.remote(url, f"{rel}/config remote {name}")
            if section[0] == b"user":
                name = _cfg_get(cfg, section, b"name")
                email = _cfg_get(cfg, section, b"email")
                if email:
                    c.ident(f"{name} <{email}>", f"{rel}/config user")
            if section[0] == b"include" or section[0] == b"includeIf":
                c.result.warnings.append(f"{rel}/config: include section not followed")

    repo = Repo(str(gitdir.parent)) if gitdir.name == ".git" else Repo(str(gitdir))
    try:
        heads = []
        for ref, sha in repo.get_refs().items():
            name = ref.decode("utf-8", "replace")
            if name != "HEAD":
                short = name.removeprefix("refs/heads/").removeprefix("refs/tags/").removeprefix("refs/remotes/")
                c.result.refs.append(short)
            try:
                obj = repo[sha]
            except KeyError:
                continue
            while isinstance(obj, Tag):
                if obj.tagger:
                    c.ident(obj.tagger, f"{rel} tag {name} tagger")
                c.message(obj.message, f"{rel} tag {name}")
                obj = repo[obj.object[1]]
            if isinstance(obj, Commit):
                heads.append(obj.id)
        seen: set[bytes] = set()
        for entry in repo.get_walker(include=heads):
            commit = entry.commit
            if commit.id in seen:
                continue
            seen.add(commit.id)
            short = commit.id.decode()[:7]
            c.ident(commit.author, f"{rel} commit {short} author")
            c.ident(commit.committer, f"{rel} commit {short} committer")
            c.message(commit.message, f"{rel} commit {short} message")
    finally:
        repo.close()

    logs = gitdir / "logs"
    if logs.is_dir():
        for log in sorted(logs.rglob("*")):
            if not log.is_file():
                continue
            for i, line in enumerate(log.read_bytes().splitlines(), 1):
                head = line.split(b"\t", 1)[0]
                parts = head.split(b" ", 2)
                if len(parts) == 3:
                    c.ident(parts[2], f"{rel}/{log.relative_to(gitdir).as_posix()}:{i}")


def _read_worktree_pointer(path: Path, rel: str, root: Path, c: _Collector) -> None:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    target = text.removeprefix("gitdir:").strip()
    if target and not _inside(path.parent / target, root):
        c.result.warnings.append(f"{rel}: worktree pointer to a path outside the project; not followed")
    for m in re.finditer(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)(?P<user>[^/\\]+)", target):
        c.accounts.append((m["user"], f"{rel} home path"))


def _read_mailmap(path: Path, rel: str, c: _Collector) -> None:
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        idents = re.findall(r"([^<>]*)<([^<>]+)>", line)
        if not idents:
            continue
        name, canonical = idents[0][0].strip(), idents[0][1].strip().lower()
        c.raw.append((name, canonical, f"{rel}:{i}"))
        for alias_name, alias in idents[1:]:
            c.aliases.append((canonical, alias.strip().lower(), f"{rel}:{i}"))
            if alias_name.strip():
                c.raw.append((alias_name.strip(), alias.strip().lower(), f"{rel}:{i}"))


def _read_codeowners(path: Path, rel: str, c: _Collector) -> None:
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.split("#", 1)[0]
        for m in MENTION_RE.finditer(line):
            handle = m["handle"]
            if "/" in handle:
                org, team = handle.split("/", 1)
                c.add_org(org, "org", f"{rel}:{i}")
            else:
                c.accounts.append((handle, f"{rel}:{i}"))
        for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", line):
            c.ident(f"<{m.group(0)}>", f"{rel}:{i}")


def harvest(root: Path) -> Harvest:
    """Collect identities from every Git repository and ownership file under ``root``."""
    root = root.resolve()
    c = _Collector()
    for entry in _find_git_entries(root):
        rel = entry.relative_to(root).as_posix()
        if entry.is_dir():
            _read_repo(entry, rel, root, c)
        else:
            _read_worktree_pointer(entry, rel, root, c)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules"}]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if fn == ".mailmap":
                _read_mailmap(p, rel, c)
            elif fn == "CODEOWNERS":
                _read_codeowners(p, rel, c)
    return c.finish()


def to_document(h: Harvest) -> dict:
    """The reviewable ``identities.yaml`` content. Never contains credential values."""
    return {
        "version": 1,
        "replacements": {"person": "developer", "email": "developer@example.invalid"},
        "people": [
            {"names": sorted(p.names), "emails": sorted(p.emails), "accounts": sorted(p.accounts),
             "derived": sorted(p.derived), "evidence": p.evidence[:20]}
            for p in h.people
        ],
        "ignored": [{"name": n, "email": e, "reason": r} for n, e, r in h.ignored],
        "organization": [
            {"value": t.value, "category": t.category, "enabled": not t.needs_confirmation,
             **({"note": "generic word: enable only after review"} if t.needs_confirmation else {}),
             "evidence": t.evidence[:10]}
            for t in h.org_terms
        ],
        "credentials_found": h.credentials,
        "repositories": h.repositories,
        "refs": sorted(set(h.refs)),
        "warnings": h.warnings,
    }
