"""Classify byte ranges of a source file by syntax context with tree-sitter.

Only the *location* of comments, string contents and template text is needed:
edits are made inside those ranges, never by re-printing the file, so
formatting, encoding and line endings stay as they are.

Context values:
    comment   comments (incl. HTML/SCSS comments)
    string    string literal contents (incl. docstrings, template text)
    text      HTML text nodes and plain attribute values
    code      everything else (identifiers, keywords, tags, selectors ...)
    protected string-like ranges that are really code references
              (import specifiers, Angular selectors, bound attribute values,
              template interpolations)
    config    whole-file context for files without a parser (JSON, YAML, md ...)
"""

from __future__ import annotations

import bisect
import re
import warnings
from dataclasses import dataclass, field
from functools import cache
from pathlib import PurePosixPath

import tree_sitter as ts

LANG_BY_SUFFIX = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "typescript", ".mjs": "typescript", ".cjs": "typescript",
    ".tsx": "tsx", ".jsx": "tsx",
    ".html": "html", ".htm": "html",
    ".scss": "scss", ".css": "css",
}

# Identifier-like token used to decide whether a string is really a code reference.
TOKEN_CHARS = rb"[A-Za-z0-9_$@./-]"
TOKEN_RE = re.compile(rb"[A-Za-z_$@.][A-Za-z0-9_$@./-]*")
INTERPOLATION_RE = re.compile(rb"\{\{.*?\}\}", re.DOTALL)
BOUND_ATTR_RE = re.compile(rb"^(\[|\(|\*|#|bind-|on-|let-)")


@cache
def _parser(lang: str) -> ts.Parser:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if lang == "python":
            import tree_sitter_python as m
            ptr = m.language()
        elif lang == "typescript":
            import tree_sitter_typescript as m
            ptr = m.language_typescript()
        elif lang == "tsx":
            import tree_sitter_typescript as m
            ptr = m.language_tsx()
        elif lang == "html":
            import tree_sitter_html as m
            ptr = m.language()
        elif lang == "scss":
            import tree_sitter_scss as m
            ptr = m.language()
        elif lang == "css":
            import tree_sitter_css as m
            ptr = m.language()
        else:
            raise ValueError(lang)
        return ts.Parser(ts.Language(ptr))


def language_for(path: str) -> str | None:
    return LANG_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


@dataclass
class Regions:
    """Sorted, non-overlapping (start, end, context) ranges; gaps are ``default``."""

    spans: list[tuple[int, int, str]] = field(default_factory=list)
    default: str = "code"
    parse_errors: bool = False

    def __post_init__(self) -> None:
        self.spans.sort()
        self._starts = [s for s, _, _ in self.spans]

    def context_at(self, start: int, end: int) -> str:
        i = bisect.bisect_right(self._starts, start) - 1
        if i >= 0:
            s, e, ctx = self.spans[i]
            if s <= start and end <= e:
                return ctx
            if start < e:
                return "code"  # straddles a boundary: be conservative
        return self.default

    def code_ranges(self, length: int) -> list[tuple[int, int]]:
        out, pos = [], 0
        for s, e, ctx in self.spans:
            if pos < s:
                out.append((pos, s))
            if ctx == "protected":
                out.append((s, e))
            pos = max(pos, e)
        if pos < length:
            out.append((pos, length))
        return out if self.default == "code" else [(s, e) for s, e, c in self.spans if c == "protected"]


def _add(spans: list, start: int, end: int, ctx: str) -> None:
    if end > start:
        spans.append((start, end, ctx))


def _split_out(spans: list, start: int, end: int, ctx: str, holes: list[tuple[int, int]]) -> None:
    """Add [start, end) as ``ctx`` except the holes, which become protected."""
    pos = start
    for hs, he in sorted(holes):
        _add(spans, pos, hs, ctx)
        _add(spans, hs, he, "protected")
        pos = he
    _add(spans, pos, end, ctx)


def _python(tree: ts.Tree, src: bytes) -> list:
    spans: list = []

    def walk(n: ts.Node) -> None:
        if n.type == "comment":
            _add(spans, n.start_byte, n.end_byte, "comment")
            return
        if n.type == "string_content":
            _add(spans, n.start_byte, n.end_byte, "string")
            return
        if n.type in {"import_statement", "import_from_statement"}:
            return  # module names are code
        for c in n.children:
            walk(c)

    walk(tree.root_node)
    return spans


def _is_import_specifier(n: ts.Node) -> bool:
    p = n.parent
    if p is None:
        return False
    if p.type in {"import_statement", "export_statement", "import_require_clause"}:
        return True
    if p.type == "arguments" and p.parent is not None and p.parent.type == "call_expression":
        fn = p.parent.child_by_field_name("function")
        if fn is not None and fn.text in {b"require", b"import"}:
            return True
    return False


def _angular_protected_key(n: ts.Node) -> bool:
    p = n.parent
    if p is None or p.type != "pair":
        return False
    key = p.child_by_field_name("key")
    return key is not None and key.text in {b"selector", b"exportAs"}


def _typescript(tree: ts.Tree, src: bytes) -> list:
    spans: list = []

    def walk(n: ts.Node) -> None:
        if n.type == "comment":
            _add(spans, n.start_byte, n.end_byte, "comment")
            return
        if n.type == "string":
            ctx = "protected" if _is_import_specifier(n) or _angular_protected_key(n) else "string"
            for c in n.children:
                if c.type in {"string_fragment", "escape_sequence"}:
                    _add(spans, c.start_byte, c.end_byte, ctx)
            return
        if n.type == "template_string":
            for c in n.children:
                if c.type == "string_fragment":
                    holes = [(m.start() + c.start_byte, m.end() + c.start_byte)
                             for m in INTERPOLATION_RE.finditer(c.text)]
                    _split_out(spans, c.start_byte, c.end_byte, "string", holes)
                elif c.type == "template_substitution":
                    walk(c)
            return
        for c in n.children:
            walk(c)

    walk(tree.root_node)
    return spans


def _html(tree: ts.Tree, src: bytes) -> list:
    spans: list = []

    def walk(n: ts.Node) -> None:
        if n.type == "comment":
            _add(spans, n.start_byte, n.end_byte, "comment")
            return
        if n.type == "text":
            holes = [(m.start() + n.start_byte, m.end() + n.start_byte) for m in INTERPOLATION_RE.finditer(n.text)]
            _split_out(spans, n.start_byte, n.end_byte, "text", holes)
            return
        if n.type == "attribute":
            name = n.child(0)
            bound = name is not None and BOUND_ATTR_RE.match(name.text or b"") is not None
            for c in n.children:
                if c.type in {"quoted_attribute_value", "attribute_value"}:
                    inner = [x for x in c.children if x.type == "attribute_value"] or [c]
                    for v in inner:
                        if bound:
                            _add(spans, v.start_byte, v.end_byte, "protected")
                        else:
                            holes = [(m.start() + v.start_byte, m.end() + v.start_byte)
                                     for m in INTERPOLATION_RE.finditer(v.text)]
                            _split_out(spans, v.start_byte, v.end_byte, "text", holes)
            return
        if n.type == "raw_text":
            parent_tag = n.parent.child(0) if n.parent is not None else None
            tag = parent_tag.child(1).text if parent_tag is not None and parent_tag.child_count > 1 else b""
            lang = "typescript" if tag == b"script" else "css" if tag == b"style" else None
            if lang:
                sub = _parser(lang).parse(n.text)
                inner = _typescript(sub, n.text) if lang == "typescript" else _css(sub, n.text)
                for s, e, ctx in inner:
                    spans.append((s + n.start_byte, e + n.start_byte, ctx))
            return
        for c in n.children:
            walk(c)

    walk(tree.root_node)
    return spans


def _css(tree: ts.Tree, src: bytes) -> list:
    spans: list = []

    def walk(n: ts.Node) -> None:
        if n.type in {"comment", "js_comment", "single_line_comment"}:
            _add(spans, n.start_byte, n.end_byte, "comment")
            return
        if n.type == "string_value":
            text = n.text or b""
            q = 1 if text[:1] in {b"'", b'"'} else 0
            _add(spans, n.start_byte + q, n.end_byte - q, "string")
            return
        for c in n.children:
            walk(c)

    walk(tree.root_node)
    return spans


HANDLERS = {"python": _python, "typescript": _typescript, "tsx": _typescript, "html": _html,
            "scss": _css, "css": _css}


def regions_for(path: str, src: bytes) -> Regions:
    lang = language_for(path)
    if lang is None:
        return Regions([], default="config")
    tree = _parser(lang).parse(src)
    return Regions(HANDLERS[lang](tree, src), default="code", parse_errors=tree.root_node.has_error)


def code_tokens(src: bytes, regions: Regions) -> set[bytes]:
    """Identifier-like tokens that appear in code (or protected) positions."""
    out: set[bytes] = set()
    for s, e in regions.code_ranges(len(src)):
        for m in TOKEN_RE.finditer(src, s, e):
            tok = m.group(0).strip(b"./")
            if not tok:
                continue
            out.add(tok)
            out.update(p for p in re.split(rb"[./@]", tok) if p)
    return out


def enclosing_token(src: bytes, start: int, end: int) -> bytes:
    s, e = start, end
    while s > 0 and re.match(TOKEN_CHARS, src[s - 1:s]):
        s -= 1
    while e < len(src) and re.match(TOKEN_CHARS, src[e:e + 1]):
        e += 1
    return src[s:e].strip(b"./")
