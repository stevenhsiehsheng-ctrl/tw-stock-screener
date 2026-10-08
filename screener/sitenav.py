"""全站共用導覽列：每一頁頂端同一排按鈕，目前所在頁反白。

各頁產生時先在導覽位置放 <!--SITENAV:key--> 或 <!--SITENAV:key:../-->（子資料夾），
report.write_site 全部頁面寫完後再一次換成真的導覽列——這時才知道哪些頁有成功產生，壞掉的頁不出現在導覽列。
"""
from __future__ import annotations

import re
from pathlib import Path

# (key, 檔名, 文字)；None＝分組間隔
ITEMS = [("live", "live.html", "⚡ 盤中即時"), ("daily", "index.html", "📊 每日篩選"),
         ("stock", "stock.html", "🔎 個股查詢"), ("watch", "watch.html", "⭐ 我的自選"), ("screen", "screen.html", "🧮 自訂選股"), ("map", "map.html", "🗺 市場地圖"), ("compare", "compare.html", "⚖ 個股比較"),
         ("claude", None, "🔒 Claude 研判"), None,
         ("weekly", "weekly/index.html", "📅 市場週報"), ("macro", "macro.html", "🌏 大環境"), ("chips", "chips.html", "🏦 法人籌碼"), ("exdiv", "exdiv.html", "💰 除權息"), ("etf", "etf.html", "🧺 ETF 專區"),
         ("usx", "usx.html", "🇺🇸 美股篩選"), ("us", "us.html", "🌙 美股隔夜"), ("revdrift", "revdrift.html", "📈 營收漂移"),
         ("longterm", "longterm.html", "🌱 長期 Top 20"), None,
         ("gifts", "gifts.html", "🎁 股東紀念品"), ("archive", "archive.html", "🗂 歷史報表")]

CSS = ("<style>.sitenav{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0 16px;padding:0 0 12px;"
       "border-bottom:1px solid rgba(128,128,128,.25);font-size:13px;line-height:1.4}"
       ".sitenav .links{display:flex;flex-wrap:wrap;gap:6px;align-items:center;flex:1 1 0;min-width:0}"
       ".sitenav a{padding:4px 11px;border-radius:999px;text-decoration:none;color:inherit;"
       "background:rgba(128,128,128,.13);white-space:nowrap;cursor:pointer}"
       ".sitenav a:hover{background:rgba(128,128,128,.24)}"
       ".sitenav a.on{background:#2f5bd3;color:#fff;font-weight:600}"
       ".sitenav i{width:1px;height:18px;background:rgba(128,128,128,.35);margin:0 4px;flex:none}"
       ".sitenav form{margin:0 0 0 auto}.sitenav input{font:inherit;padding:4px 11px;border-radius:999px;width:150px;"
       "border:1px solid rgba(128,128,128,.35);background:transparent;color:inherit}"
       # 手機：連結變成一列可左右滑（目前頁捲到中間），搜尋框在下面整行
       "@media(max-width:860px){.sitenav .links{flex:1 1 100%;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch;"
       "padding-bottom:2px;mask-image:linear-gradient(90deg,#000 92%,transparent)}.sitenav .links::-webkit-scrollbar{display:none}"
       ".sitenav form{flex:1 1 100%;margin:2px 0 0}.sitenav input{width:100%;box-sizing:border-box}}</style>")
# 目前頁捲到看得到的位置（只動 nav 自己的橫向捲動，不動整頁）
CENTER = ("<script>(function(){var l=document.currentScript.previousElementSibling;l=l&&l.querySelector('.links');"
          "var a=l&&l.querySelector('a.on');if(a&&l.scrollWidth>l.clientWidth)l.scrollLeft=a.offsetLeft-l.offsetLeft-(l.clientWidth-a.offsetWidth)/2})()</script>")

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
    # 每頁都能直接查個股（代號或名稱，個股頁自己會解析名稱）；個股頁本身已有搜尋框就不放
    form = ""
    if current != "stock" and "stock.html" in available:
        form = (f"<form action='{prefix}stock.html' method='get' role='search'>"
                "<input name='code' placeholder='🔎 代號或名稱' aria-label='查個股' autocomplete='off'></form>")
    js = f"<script src='{prefix}claude_link.js'></script>" if need_js else ""
    return CSS + "<nav class='sitenav'><div class='links'>" + "".join(out) + "</div>" + form + "</nav>" + CENTER + js


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
