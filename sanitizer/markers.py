"""Conservative internal marker candidates; syntax and allow rules apply later."""

import ipaddress
import re

from .terms import Policy

TODO = re.compile(rb"\b(?:TODO|FIXME|XXX)\s*\(\s*@(?P<value>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s*\)", re.I)
IP = re.compile(rb"(?<![\w.:])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.]|:[0-9a-f]*:)|(?<![\w:])[fF][cCdD][0-9a-fA-F]{2}:[0-9a-fA-F:]+(?![\w:])")
NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
DOMAIN = re.compile(rb"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:internal|local|corp)(?![\w.-])", re.I)
ISSUE = re.compile(rb"(?P<host>(?:[a-z0-9-]+\.)+[a-z0-9-]+)(?::\d+)?/[^\s\"'<>]*?(?:/-/issues/|/issues/)(?P<value>\d+)(?!\d)", re.I)


def find_markers(data: bytes, policy: Policy):
    for m in TODO.finditer(data):
        if m["value"].lower() != b"developer":
            yield *m.span("value"), b"developer", "todo-account"
    for m in IP.finditer(data):
        try:
            addr = ipaddress.ip_address(m[0].decode())
        except ValueError:
            continue
        if any(addr.version == n.version and addr in n for n in NETWORKS):
            yield *m.span(), b"192.0.2.1" if addr.version == 4 else b"2001:db8::1", "internal-ip"
    for m in DOMAIN.finditer(data):
        if m[0].lower() != b"example.internal" and not m[0].lower().endswith(b".example.internal"):
            yield *m.span(), b"example.internal", "internal-domain"
    for projects, pattern in ((policy.jira_projects, rb"(?<![\w-])(?:%s)-\d+(?![\w-])"),
                              (policy.gitlab_projects, rb"(?<![\w/.-])(?:%s)\#\d+(?!\w)")):
        if projects:
            rx = re.compile(pattern % b"|".join(re.escape(p.encode()) for p in projects), re.I)
            for m in rx.finditer(data):
                yield *m.span(), b"REDACTED_ISSUE", "internal-issue"
    domains = [t.value.lower().encode() for t in policy.terms if t.category == "domain"]
    for m in ISSUE.finditer(data):
        host = m["host"].lower()
        try:
            addr = ipaddress.ip_address(host.decode())
            internal_ip = any(addr.version == n.version and addr in n for n in NETWORKS)
        except ValueError:
            internal_ip = False
        if m["value"] != b"0" and (internal_ip or DOMAIN.fullmatch(host) or any(host == d or host.endswith(b"." + d) for d in domains)):
            yield *m.span("value"), b"0", "internal-issue"
