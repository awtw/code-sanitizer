"""Internal HTML report (findings, verification, diff). Contains original
identities and must stay in the job folder. All source text is escaped."""

from __future__ import annotations

import html
from collections import Counter
from pathlib import Path

CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e5e5ea; --ok:#1a7f37; --bad:#c62828; --warn:#9a6700;
        --add:#e6ffec; --del:#ffebe9; --code:#f6f8fa; }
@media (prefers-color-scheme: dark) { :root { --bg:#161618; --fg:#f2f2f7; --muted:#a1a1a6; --line:#2c2c2e;
        --ok:#3fb950; --bad:#f85149; --warn:#d29922; --add:#12261e; --del:#2d1214; --code:#1f1f22; } }
body { background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
       margin:0 auto; max-width:1100px; padding:24px 16px; }
h1 { font-size:22px; } h2 { font-size:17px; margin-top:32px; border-bottom:1px solid var(--line); padding-bottom:4px; }
table { border-collapse:collapse; width:100%; font-size:13px; } th, td { text-align:left; padding:4px 8px;
        border-bottom:1px solid var(--line); vertical-align:top; } th { color:var(--muted); font-weight:600; }
.badge { display:inline-block; padding:2px 10px; border-radius:10px; font-weight:600; }
.qualified, .pass, .ok, .exported { color:var(--ok); } .regression, .fail, .error, .blocked { color:var(--bad); }
.incomplete, .skip, .skipped, .baseline-failure, .review { color:var(--warn); }
pre { background:var(--code); padding:12px; overflow-x:auto; font-size:12px; line-height:1.4; }
.add { background:var(--add); display:block; } .del { background:var(--del); display:block; }
.muted { color:var(--muted); } .wrap { overflow-x:auto; }
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _table(headers: list[str], rows: list[list], cls_col: int | None = None) -> str:
    out = ["<div class='wrap'><table><tr>", *[f"<th>{_e(h)}</th>" for h in headers], "</tr>"]
    for r in rows:
        out.append("<tr>")
        for i, c in enumerate(r):
            cls = f" class='{_e(c)}'" if i == cls_col else ""
            out.append(f"<td{cls}>{_e(c)}</td>")
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def _diff_html(patch: str, limit: int = 400_000) -> str:
    lines = []
    for line in patch[:limit].splitlines():
        cls = "add" if line.startswith("+") and not line.startswith("+++") else \
            "del" if line.startswith("-") and not line.startswith("---") else ""
        lines.append(f"<span class='{cls}'>{_e(line)}</span>" if cls else _e(line))
    more = "\n… (truncated, see changes.patch)" if len(patch) > limit else ""
    return "<pre>" + "\n".join(lines) + _e(more) + "</pre>"


def write_html_report(job: Path, plan: dict, verify: dict, export: dict) -> Path:
    status = export.get("delivery_status", "not verified")
    blocked = bool(export.get("blocked"))
    parts = [
        "<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Sanitizer report</title><style>{CSS}</style></head><body>",
        "<h1>去識別報告（內部，勿外流）</h1>",
        f"<p>交付判定：<span class='badge {'blocked' if blocked else 'exported'}'>"
        f"{'blocked' if blocked else 'exportable'}</span> 驗證：<span class='badge {_e(status)}'>{_e(status)}</span></p>",
    ]
    if blocked:
        parts.append("<ul>" + "".join(f"<li class='blocked'>{_e(b)}</li>" for b in export["blocked"]) + "</ul>")

    parts.append("<h2>處理摘要</h2>")
    parts.append(_table(["類別 / 處置", "數量"], [[k, v] for k, v in plan.get("summary", {}).items()]))

    if verify:
        parts.append("<h2>驗證比較</h2>")
        parts.append(f"<p class='muted'>執行隔離：{_e(verify.get('isolation'))}；"
                     f"必要層級：{_e(', '.join(verify.get('required_levels') or []))}</p>")
        rows = [[c["project"] or ".", c["step"], c["level"], c["baseline"], c["sanitized"], c["verdict"],
                 "; ".join(c["detail"])[:300]] for c in [verify["syntax"], *verify["comparisons"]]]
        parts.append(_table(["專案", "步驟", "層級", "原始", "去識別後", "判定", "說明"], rows, cls_col=5))
        if verify.get("reasons"):
            parts.append("<ul>" + "".join(f"<li>{_e(r)}</li>" for r in verify["reasons"]) + "</ul>")

    parts.append("<h2>機密位置</h2>")
    parts.append(_table(["Placeholder", "檔案", "行", "類型"],
                        [[s.get("id") or "(檔案移除)", s["path"], s["line"], s["kind"]] for s in plan.get("secrets", [])]))

    review = [f for f in plan.get("findings", []) if f["action"] == "review"]
    if review:
        parts.append("<h2>待人工確認</h2>")
        parts.append(_table(["檔案", "行", "類別", "內容"], [[f["path"], f["line"], f["category"], f["detail"]]
                                                       for f in review]))

    kept = [f for f in plan.get("findings", []) if f["action"] == "keep"]
    if kept:
        parts.append("<h2>刻意保留（程式符號、允許清單）</h2>")
        counts = Counter((f["path"], f["line"], f["detail"]) for f in kept)
        parts.append(_table(["檔案", "行", "原因", "次數"], [[p, l, d, n] for (p, l, d), n in sorted(counts.items())]))

    parts.append("<h2>檔案移除</h2>")
    parts.append("<pre>" + _e("\n".join(plan.get("removals", []))) + "</pre>")

    patch_file = job / "changes.patch"
    if patch_file.exists():
        parts.append("<h2>差異</h2>")
        parts.append(_diff_html(patch_file.read_text(encoding="utf-8", errors="replace")))
    parts.append("</body></html>")
    path = job / "report.html"
    path.write_text("".join(parts), encoding="utf-8")
    return path
