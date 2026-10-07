"""長期看好 Top 20（Dennis 10/7 要）：Code 量化篩選＋外資／投信／券商觀點＋Cowork 質化審查，網站頁 site/longterm.html。

這是判斷題、不是回測過的策略，所以頁面一定要附「前推成績」：名單凍結那天的收盤當起點，
每天拿等權報酬跟 0050 比，好壞都照實列（股價不含息，兩邊都一樣不含）。

資料：
- data/longterm/top20.json：目前這一版名單（asof＝起點日、picks＝20 檔，含論點／風險／破壞條件／外部觀點）
- data/longterm/views_*.csv：外部觀點清單（來源、日期、連結、提到哪些股票）
- data/longterm/screen_*.csv：量化篩選全表（tools/longterm/screen.py 產生）
- data/history.csv.gz、data/extras/etf.csv.gz：每天收盤，算前推成績
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("longterm")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "longterm"
TOP = DATA / "top20.json"


def tracking(top: dict) -> dict | None:
    """起點日收盤到最新收盤：每檔報酬、等權平均、0050。起點日還沒有收盤資料就回 None。"""
    asof = top["asof"]
    codes = [p["code"] for p in top["picks"]]
    h = pd.read_csv(ROOT / "data" / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    h = h[h.code.isin(codes) & (h.date >= asof)]
    e = pd.read_csv(ROOT / "data" / "extras" / "etf.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    e = e[(e.code == "0050") & (e.date >= asof)]
    if h.empty or e.empty or e.date.min() != asof:
        return None
    px = h.pivot(index="date", columns="code", values="close").sort_index()
    b = e.set_index("date").close.sort_index()
    last = min(px.index[-1], b.index[-1])
    if last == asof:
        return {"asof": asof, "last": last, "days": 0, "per": {}, "ew": 0.0, "bench": 0.0}
    base = px.loc[asof]
    per = ((px.loc[last] / base - 1) * 100).dropna()
    return {"asof": asof, "last": last, "days": int((px.index > asof).sum()), "per": per.round(2).to_dict(),
            "ew": round(float(per.mean()), 2), "bench": round(float((b.loc[last] / b.loc[asof] - 1) * 100), 2),
            "n": int(per.size)}


def write(site_dir: Path) -> bool:
    if not TOP.exists():
        return False
    from .weekly import _page
    top = json.loads(TOP.read_text("utf-8"))
    try:
        tr = tracking(top)
    except Exception as e:  # noqa: BLE001
        log.warning("長期 Top 20 前推成績算不出來：%s", e)
        tr = None
    esc = html.escape
    f = lambda v, nd=1: "—" if v is None or v != v else f"{v:+.{nd}f}%"
    views = {}
    vf = DATA / top.get("views", "")
    if top.get("views") and vf.exists():
        views = {r.list_id: r for r in pd.read_csv(vf).itertuples()}

    if tr is None:
        perf = (f"<p class='meta'>起點是 {esc(top['asof'])} 收盤，當天收盤資料進來（每天 15:20 後）才開始算。</p>")
    else:
        diff = tr["ew"] - tr["bench"]
        perf = (f"<div class='box'><b>前推成績</b>（{esc(tr['asof'])} 收盤 → {esc(tr['last'])} 收盤，{tr['days']} 個交易日）："
                f"20 檔等權 <b>{f(tr['ew'], 2)}</b>，0050 {f(tr['bench'], 2)}，差 <b>{f(diff, 2)}</b>"
                "<div class='meta'>股價報酬、不含息（兩邊都不含）。幾天、幾週的差距幾乎都是雜訊，至少看一年以上才有意義。</div></div>")
    per = (tr or {}).get("per", {})
    cards = []
    for i, p in enumerate(top["picks"], 1):
        ext = p.get("ext_lists", [])
        ext_txt = "、".join(esc(getattr(views[x], "short", "") or views[x].source) if x in views else esc(x) for x in ext)
        r = per.get(p["code"])
        cls = "" if r is None else (" up" if r > 0 else (" dn" if r < 0 else ""))
        cards.append(
            f"<div class='card'><div class='hd'><span class='no'>{i}</span><b>{esc(p['name'])}</b> <span class='meta'>{esc(p['code'])}"
            f"・{esc(p.get('group', ''))}</span><span class='ret{cls}'>{'' if r is None else f(r, 1)}</span></div>"
            f"<div>{esc(p.get('thesis', ''))}</div>"
            f"<div class='meta'><b>風險</b>　{esc(p.get('risk', ''))}</div>"
            f"<div class='meta'><b>破壞條件</b>　{esc(p.get('break_rule', ''))}</div>"
            f"<div class='meta'><b>外部清單</b>　{len(ext)} 份{('：' + ext_txt) if ext_txt else ''}"
            f"　<b>量化排名</b>　{p.get('qrank') or '—'}／294</div></div>")
    vrows = "".join(
        f"<li><a href='{esc(r.url)}' target='_blank' rel='noopener'>{esc(r.source)}</a>（{esc(str(r.date))}，{esc(r.kind)}）</li>"
        for r in views.values())
    css = ("<style>.meta{font-size:12.5px;color:var(--muted);margin-top:3px}"
           ".box,.card{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0;text-align:left}"
           ".hd{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}.hd .meta{margin:0}"
           ".no{display:inline-block;min-width:22px;color:var(--muted);font-weight:600}"
           ".ret{margin-left:auto;font-weight:600;white-space:nowrap}.ret.up{color:#d33}.ret.dn{color:#1a8a3a}</style>")
    body = (css + "<h1>長期看好 Top 20</h1>"
            "<blockquote><b>這份名單沒有經過回測驗證</b>，是判斷題：我們自己的量化篩選、外資／投信／券商公開的看法、"
            "再加上 Cowork 逐檔審查論點和風險，綜合出來的。我們 5 年資料只有一段完整的 3 年可驗，長期選股在這份資料上證明不了什麼，"
            "所以下面用「前推成績」從起點日開始跟 0050 比，時間會說話。不構成投資建議。</blockquote>"
            + perf +
            f"<p class='meta'>版本 {esc(str(top.get('version', 1)))}・起點 {esc(top['asof'])}・{esc(top.get('made_by', ''))}"
            "・右上角是起點至今的股價漲跌</p>" + "".join(cards) +
            "<h2>怎麼選的</h2>" + "".join(f"<p>{esc(x)}</p>" for x in top.get("method", []))
            + (f"<h2>參考的外部觀點</h2><ul>{vrows}</ul>" if vrows else "")
            + "<p class='meta'>外部清單數＝被幾份外資／投信／券商清單點名（散戶定期定額人氣、媒體評論只當參考，不算進去）。"
              "量化排名＝在市值 300 億以上、有流動性的 294 檔裡的名次（tools/longterm/screen.py）。</p>")
    (site_dir / "longterm.html").write_text(_page("長期看好 Top 20", body, "<!--SITENAV:longterm-->"), "utf-8")
    return True
