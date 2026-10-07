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


ELEC = {"半導體業", "電腦及週邊設備業", "電子零組件業", "通信網路業", "其他電子業", "光電業", "電子通路業", "資訊服務業"}


def tracking(top: dict) -> dict | None:
    """起點日收盤到最新收盤：每檔報酬、20 檔等權，跟三條基準比（Cowork 1258）：
    ① 0050；② 同池等權（量化篩選那 294 檔）；③ 同產業配置的同池等權——名單裡電子股占幾成，同池電子股等權就占幾成、
    其餘用同池非電子股等權。只有 ③ 算「選股」，①② 混了產業配置。起點日還沒有收盤資料就回 None。"""
    asof = top["asof"]
    codes = [p["code"] for p in top["picks"]]
    pool = pd.read_csv(DATA / top["screen"], dtype={"code": str}, usecols=["code", "industry", "cap_e8"]) if top.get("screen") else None
    want = set(codes) | (set(pool.code) if pool is not None else set())
    h = pd.read_csv(ROOT / "data" / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    h = h[h.code.isin(want) & (h.date >= asof)]
    e = pd.read_csv(ROOT / "data" / "extras" / "etf.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    e = e[(e.code == "0050") & (e.date >= asof)]
    if h.empty or e.empty or e.date.min() != asof:
        return None
    px = h.pivot(index="date", columns="code", values="close").sort_index().ffill()   # 下市／停牌：最後價轉現金
    b = e.set_index("date").close.sort_index()
    last = min(px.index[-1], b.index[-1])
    out = {"asof": asof, "last": last, "days": int((px.index[px.index <= last] > asof).sum())}
    allr = ((px.loc[last] / px.loc[asof] - 1) * 100).dropna()
    per = allr.reindex(codes).dropna()
    out.update({"per": per.round(2).to_dict(), "n": int(per.size), "ew": round(float(per.mean()), 2),
                "bench": round(float((b.loc[last] / b.loc[asof] - 1) * 100), 2)})
    if pool is not None:
        picks = set(codes)
        ind = pool.set_index("code").industry
        pr = allr.reindex(pool.code).dropna()
        lo = pr[~pr.index.isin(picks)]                       # 基準池一律排除名單本身（leave-out）
        out.update({"pool": round(float(pr.mean()), 2), "pool_n": int(pr.size)})
        el = lo.index.map(lambda c: ind.get(c, "") in ELEC)
        pe, pn = lo[el], lo[~el]
        w = float(pd.Series([p.get("industry", "") in ELEC for p in top["picks"]]).mean())
        out.update({"elec_w": round(w, 2), "matched": round(float(w * pe.mean() + (1 - w) * pn.mean()), 2)})
        # 第三條：證交所產業別，照名單檔數配權、類內同池等權；排除名單後 <5 檔的類退回電子／非電子
        parts, fb = [], []
        for p in top["picks"]:
            g = lo[lo.index.map(lambda c: ind.get(c, "") == p.get("industry", ""))]
            if len(g) < 5:
                fb.append(p["name"])
                g = pe if p.get("industry", "") in ELEC else pn
            parts.append(float(g.mean()))
        out.update({"ind": round(sum(parts) / len(parts), 2), "ind_fallback": fb})
    pf = DATA / top["peers"] if top.get("peers") else None
    if pf is not None and pf.exists():
        peers = json.loads(pf.read_text("utf-8"))["peers"]
        def ex(c):   # 每檔對自己 10 檔同類等權的超額（同類下市：最後價轉現金，報酬停在那天）
            pp = [x for x in peers.get(c, {}).get("peers", [])]
            pr_ = allr.reindex(pp).dropna()
            return (allr[c] - pr_.mean(), allr[c] > pr_.median()) if c in allr.index and len(pr_) else (None, None)
        res = {c: ex(c) for c in peers}
        res = {c: v for c, v in res.items() if v[0] is not None}
        mine = [res[c] for c in codes if c in res]
        if mine:
            import numpy as np
            exs = np.clip(np.array([m[0] for m in mine]), -50, 50)
            n = len(exs)
            sd = float(exs.std(ddof=1)) if n > 1 else float("nan")
            beat = int(sum(bool(m[1]) for m in mine))
            allx = np.clip(np.array([v[0] for v in res.values()]), -50, 50)
            allb = np.array([bool(v[1]) for v in res.values()])
            rng = np.random.default_rng(int(asof.replace("-", "")))
            keys = list(res)
            idx = np.array([rng.choice(len(allx), n, replace=False) for _ in range(10000)])
            rm, rb = allx[idx].mean(axis=1), allb[idx].sum(axis=1)
            # 配對版（Cowork 1555）：起點市值三分位 × 電子／非電子，每層抽的檔數跟名單一樣
            capi = pool.set_index("code").cap_e8 if "cap_e8" in pool else None
            m_pct = mb_pct = None
            if capi is not None:
                cap = capi.reindex(keys)
                ter = pd.qcut(cap.rank(method="first"), 3, labels=False)
                layer = pd.Series([f"{t}-{ind.get(c, '') in ELEC}" for c, t in zip(keys, ter)], index=keys)
                need = layer.reindex([c for c in codes if c in res]).value_counts()
                pos = {k: np.flatnonzero(layer.values == k) for k in need.index}
                if all(len(pos[k]) >= v for k, v in need.items()):
                    midx = np.array([np.concatenate([rng.choice(pos[k], v, replace=False) for k, v in need.items()]) for _ in range(10000)])
                    mm, mb = allx[midx].mean(axis=1), allb[midx].sum(axis=1)
                    m_pct, mb_pct = round(float((mm < exs.mean()).mean() * 100), 1), round(float((mb < beat).mean() * 100), 1)
            out.update({"peer_ex": round(float(exs.mean()), 2), "peer_t": round(float(exs.mean() / (sd / n ** 0.5)), 2) if sd == sd and sd > 0 else None,
                        "peer_beat": beat, "peer_n": n, "rand_pct": round(float((rm < exs.mean()).mean() * 100), 1),
                        "rand_beat_pct": round(float((rb < beat).mean() * 100), 1),
                        "rand_m_pct": m_pct, "rand_m_beat_pct": mb_pct,
                        "peer_per": {c: round(float(res[c][0]), 2) for c in codes if c in res}})
    return out


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

    if tr is None or tr.get("days", 0) == 0:
        perf = (f"<p class='meta'>起點是 {esc(top['asof'])} 收盤（價格已凍結），下一個交易日收盤後開始有成績。</p>")
    else:
        perf = (f"<div class='box'><b>前推成績</b>（{esc(tr['asof'])} 收盤 → {esc(tr['last'])} 收盤，{tr['days']} 個交易日）："
                f"20 檔等權 <b>{f(tr['ew'], 2)}</b>"
                f"<div>對 0050 {f(tr['ew'] - tr['bench'], 2)}（0050 {f(tr['bench'], 2)}）</div>"
                + (f"<div>對同池 {tr['pool_n']} 檔等權 {f(tr['ew'] - tr['pool'], 2)}</div>" if "pool" in tr else "")
                + (f"<div>對同產業別配置（排除名單本身）{f(tr['ew'] - tr['ind'], 2)}"
                   + (f"<span class='meta'>（{esc('、'.join(tr['ind_fallback']))} 那類不到 5 檔，改比電子／非電子）</span>" if tr.get("ind_fallback") else "")
                   + f"</div><div>對電子／非電子配置（電子 {tr['elec_w']:.0%}）{f(tr['ew'] - tr['matched'], 2)}</div>" if "ind" in tr else "")
                + (f"<div><b>對每檔自己的 10 檔同類（相關最高、名單外）：平均超額 {f(tr['peer_ex'], 2)}"
                   f"（截在 ±50 點，t＝{tr['peer_t'] if tr['peer_t'] is not None else '—'}），{tr['peer_beat']}/{tr['peer_n']} 檔贏同類中位數</b>"
                   f"<div class='meta'>← 只有這條算選股。成績單：跟同一池子隨機抽 1 萬組 20 檔比，平均超額贏過 {tr['rand_pct']:.0f}% 的隨機組、"
                   f"贏同類的檔數贏過 {tr['rand_beat_pct']:.0f}% 的隨機組"
                   + (f"；照名單的市值大小、電子／非電子配對抽：{tr['rand_m_pct']:.0f}%／{tr['rand_m_beat_pct']:.0f}%" if tr.get("rand_m_pct") is not None else "")
                   + "。</div></div>" if "peer_ex" in tr else "")
                + "<div class='meta'>股價報酬、不含息（兩邊都不含）。0050 約六成是台積電，比它、比同池都混了產業配置，不是選股。"
                  "幾天、幾週的差距幾乎都是雜訊。</div></div>")
    if top.get("criterion"):
        perf += f"<p class='meta'><b>事前寫死的判準</b>：{esc(top['criterion'])}</p>"
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
            "所以下面用「前推成績」從起點日開始跟 0050、同一批候選股比，時間會說話。不構成投資建議。</blockquote>"
            + perf +
            f"<p class='meta'>版本 {esc(str(top.get('version', 1)))}・起點 {esc(top['asof'])}・{esc(top.get('made_by', ''))}"
            "・右上角是起點至今的股價漲跌</p>" + "".join(cards) +
            "<h2>怎麼選的</h2>" + "".join(f"<p>{esc(x)}</p>" for x in top.get("method", []))
            + (f"<h2>參考的外部觀點</h2><ul>{vrows}</ul>" if vrows else "")
            + "<p class='meta'>外部清單數＝被幾份外資／投信／券商清單點名（散戶定期定額人氣、媒體評論只當參考，不算進去）。"
              "量化排名＝在市值 300 億以上、有流動性的 294 檔裡的名次（tools/longterm/screen.py）。</p>")
    (site_dir / "longterm.html").write_text(_page("長期看好 Top 20", body, "<!--SITENAV:longterm-->"), "utf-8")
    return True
