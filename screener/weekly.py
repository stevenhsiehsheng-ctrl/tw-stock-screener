"""市場週報：data/weekly/YYYY-Www.md（Cowork 草稿＋Claude Code 補資料）→ site/weekly/YYYY-Www.html 與週報列表。"""
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "weekly"

CSS = """:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.8 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 60px}
nav{font-size:14px;color:var(--muted)}a{color:var(--link)}
h1{font-size:24px;line-height:1.4;margin:16px 0 8px}h2{font-size:19px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--line);color:var(--muted);background:var(--card)}
.tbl{overflow-x:auto}table{border-collapse:collapse;margin:8px 0;font-size:14.5px;min-width:100%}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;white-space:nowrap}th{color:var(--muted);font-weight:600}
td:not(:first-child),th:not(:first-child){text-align:right}
ul.list li{margin:6px 0}hr{border:0;border-top:1px solid var(--line);margin:28px 0}"""


def _page(title: str, body: str, nav: str) -> str:
    return (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{nav if nav.startswith('<!--SITENAV') else f'<nav>{nav}</nav>'}{body}</main></body></html>")


def write(site_dir: Path) -> list[str]:
    """把所有週報轉成網頁，回傳週別清單（新到舊）。"""
    weeks = sorted((p.stem for p in SRC.glob("20??-W??.md")), reverse=True) if SRC.exists() else []
    if not weeks:
        return []
    out = site_dir / "weekly"
    out.mkdir(parents=True, exist_ok=True)
    nav = "<!--SITENAV:weekly:../--><nav><a href='index.html'>← 所有週報</a></nav>"
    titles = {}
    for w in weeks:
        md = (SRC / f"{w}.md").read_text("utf-8")
        m = re.search(r"^#\s+(.+)$", md, re.M)
        titles[w] = m.group(1).strip() if m else w
        body = markdown.markdown(md, extensions=["tables"])
        body = body.replace("<table>", "<div class='tbl'><table>").replace("</table>", "</table></div>")
        (out / f"{w}.html").write_text(_page(titles[w], body, nav), "utf-8")
    items = "".join(f"<li><a href='{w}.html'>{html.escape(titles[w])}</a></li>" for w in weeks)
    (out / "index.html").write_text(
        _page("市場週報", f"<h1>市場週報</h1><ul class='list'>{items}</ul>", "<!--SITENAV:weekly:../-->"), "utf-8")
    return weeks
