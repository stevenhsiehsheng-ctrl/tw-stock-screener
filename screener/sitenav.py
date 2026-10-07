"""全站共用導覽列：每一頁頂端同一排按鈕，目前所在頁反白。

各頁產生時先在導覽位置放 <!--SITENAV:key--> 或 <!--SITENAV:key:../-->（子資料夾），
report.write_site 全部頁面寫完後再一次換成真的導覽列——這時才知道哪些頁有成功產生，壞掉的頁不出現在導覽列。
"""
from __future__ import annotations

import re
from pathlib import Path

# (key, 檔名, 文字)；None＝分組間隔
ITEMS = [("live", "live.html", "⚡ 盤中即時"), ("daily", "index.html", "📊 每日篩選"),
         ("stock", "stock.html", "🔎 個股查詢"),
         ("claude", None, "🔒 Claude 研判"), None,
         ("weekly", "weekly/index.html", "📅 市場週報"), ("macro", "macro.html", "🌏 大環境"), ("chips", "chips.html", "🏦 法人籌碼"), ("exdiv", "exdiv.html", "💰 除權息"),
         ("usx", "usx.html", "🇺🇸 美股篩選"), ("us", "us.html", "🌙 美股隔夜"), ("revdrift", "revdrift.html", "📈 營收漂移"),
         ("longterm", "longterm.html", "🌱 長期 Top 20"), None,
         ("gifts", "gifts.html", "🎁 股東紀念品"), ("archive", "archive.html", "🗂 歷史報表")]

CSS = ("<style>.sitenav{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0 16px;padding:0 0 12px;"
       "border-bottom:1px solid rgba(128,128,128,.25);font-size:13px;line-height:1.4}"
       ".sitenav a{padding:4px 11px;border-radius:999px;text-decoration:none;color:inherit;"
       "background:rgba(128,128,128,.13);white-space:nowrap;cursor:pointer}"
       ".sitenav a:hover{background:rgba(128,128,128,.24)}"
       ".sitenav a.on{background:#2f5bd3;color:#fff;font-weight:600}"
       ".sitenav i{width:1px;height:18px;background:rgba(128,128,128,.35);margin:0 4px}@media(max-width:560px){.sitenav i{display:none}}</style>")

MARK = re.compile(r"<!--SITENAV:(\w+)(?::([./]*))?-->")


def render(current: str, available: set[str], prefix: str = "", need_js: bool = True) -> str:
    out, gap = [], False
    for it in ITEMS:
        if it is None:
            gap = bool(out)
            continue
        key, href, label = it
        if href and href not in available:
            continue
        if gap:
            out.append("<i></i>")
            gap = False
        on = " class='on'" if key == current else ""
        if href is None:
            out.append(f"<a{on} href='#' onclick='return openClaude()'>{label}</a>")
        else:
            out.append(f"<a{on} href='{prefix}{href}'>{label}</a>")
    js = f"<script src='{prefix}claude_link.js'></script>" if need_js else ""
    return CSS + "<nav class='sitenav'>" + "".join(out) + "</nav>" + js


def apply(site_dir: Path) -> None:
    """把 site 底下所有頁面的導覽標記換成導覽列。"""
    available = {str(p.relative_to(site_dir)).replace("\\", "/") for p in site_dir.rglob("*.html")}
    for p in site_dir.rglob("*.html"):
        s = p.read_text("utf-8")
        if "<!--SITENAV:" not in s:
            continue
        need_js = "claude_link.js" not in MARK.sub("", s)
        s = MARK.sub(lambda m: render(m.group(1), available, m.group(2) or "", need_js), s)
        p.write_text(s, "utf-8")
