"""Unit tests for syntax contexts and term matching."""

from sanitizer.scan import apply_edits
from sanitizer.syntax import regions_for
from sanitizer.terms import Matcher, Term, case_like


def ctx(path: str, src: bytes, needle: bytes) -> str:
    i = src.index(needle)
    return regions_for(path, src).context_at(i, i + len(needle))


def test_python_hash_inside_string_is_not_a_comment():
    src = b'x = "a # jdoe"  # bob\n'
    assert ctx("a.py", src, b"jdoe") == "string"
    assert ctx("a.py", src, b"bob") == "comment"


def test_python_fstring_interpolation_is_code():
    src = b'x = f"by {jdoe} and jane"\n'
    assert ctx("a.py", src, b"jdoe") == "code"
    assert ctx("a.py", src, b"jane") == "string"


def test_python_import_is_code():
    src = b"from acme_common import mask\n"
    assert ctx("a.py", src, b"acme_common") == "code"


def test_ts_import_specifier_and_selector_are_protected():
    src = b"import { A } from '@acme/ui';\n@Component({ selector: 'acme-root', template: `<b>Acme</b>` })\nclass A {}\n"
    assert ctx("a.ts", src, b"@acme/ui") == "protected"
    assert ctx("a.ts", src, b"acme-root") == "protected"
    assert ctx("a.ts", src, b"Acme</b>"[:4]) == "string"


def test_ts_url_with_slashes_in_string_is_string():
    src = b"const u = 'https://x.acme.com/a'; // owner jdoe\n"
    assert ctx("a.ts", src, b"x.acme.com") == "string"
    assert ctx("a.ts", src, b"jdoe") == "comment"


def test_html_tag_bound_attr_and_interpolation_are_code():
    src = b'<acme-x [val]="acmeVal" label="Pay Acme">Hi {{ acmeT }} Acme</acme-x><!-- by jdoe -->'
    assert ctx("a.html", src, b"acme-x") == "code"
    assert ctx("a.html", src, b"acmeVal") == "protected"
    assert ctx("a.html", src, b"Pay Acme") == "text"
    assert ctx("a.html", src, b"acmeT") == "protected"
    assert ctx("a.html", src, b"jdoe") == "comment"


def test_scss_contexts():
    src = b"/* by jdoe */\n$acme-blue: #000; // jane\n.acme-x { font-family: 'Acme Sans'; }\n"
    assert ctx("a.scss", src, b"jdoe") == "comment"
    assert ctx("a.scss", src, b"jane") == "comment"
    assert ctx("a.scss", src, b"acme-x") == "code"
    assert ctx("a.scss", src, b"Acme Sans") == "string"


def test_unparsed_files_are_config():
    assert ctx("a.yaml", b"owner: jdoe\n", b"jdoe") == "config"


def test_matcher_boundaries():
    m = Matcher([Term("acme", "company", "company"), Term("jdoe", "developer", "account")])
    data = b"AcmeClient acme-core acme_x macme jdoe2 jdoe. fooAcme"
    found = [data[x.start:x.end] for x in m.find(data)]
    assert found == [b"Acme", b"acme", b"acme", b"jdoe", b"Acme"]


def test_case_like():
    assert case_like(b"ACME", "company") == b"COMPANY"
    assert case_like(b"Acme", "company") == b"Company"
    assert case_like(b"acme", "company") == b"company"
    assert case_like(b"\xe7\x8e\x8b", "developer") == b"developer"


def test_edits_keep_crlf_and_bom():
    from sanitizer.scan import Edit

    data = b"\xef\xbb\xbf# by jdoe\r\nx = 1\r\n"
    i = data.index(b"jdoe")
    assert apply_edits(data, [Edit(i, i + 4, b"developer")]) == b"\xef\xbb\xbf# by developer\r\nx = 1\r\n"
