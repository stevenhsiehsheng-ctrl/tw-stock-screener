"""除權息行事曆 site/exdiv.html：未來一個月要除權息的股票（現金股利、用現價算的殖利率、這檔上次填息花幾天）、
最近一個月除完的填息進度、過去一年全市場填息統計。

填息天數＝除息日起第一個收盤 ≥ 除息前一天收盤的交易日（第 1 天＝除息當天）。history.csv.gz 是未還原的官方收盤，所以只能算近一年。
資料：data/extras/exdiv_upcoming.csv（預告）、exdiv_5y.csv.gz（已除，含 prev_close）、data/history.csv.gz、data/stock_list.csv。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EX = DATA / "extras"
log = logging.getLogger(__name__)


def _r(v, nd=2):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _med(fill: pd.Series):
    """填息天數中位：還沒填的當成無限大（不能丟掉，不然只是『有填的那批』的中位）；中位落在沒填的那半就回 None。"""
    m = fill.fillna(np.inf).median()
    return None if not np.isfinite(m) else _r(m, 0)


def fill_days(done: pd.DataFrame, C: pd.DataFrame) -> pd.DataFrame:
    """每筆已除權息：填息天數（沒填完＝NaN）、到今天經過幾個交易日、現在離填息還差幾 %。"""
    days = list(C.index)
    pos = {d: i for i, d in enumerate(days)}
    out = []
    for r in done.itertuples():
        if r.date not in pos or r.code not in C.columns or not (r.prev_close > 0):
            continue
        s = C[r.code].to_numpy()[pos[r.date]:]
        ok = np.flatnonzero(s >= r.prev_close - 1e-9)
        last = s[np.isfinite(s)][-1] if np.isfinite(s).any() else np.nan
        out.append({"date": r.date, "code": r.code, "fill": int(ok[0]) + 1 if len(ok) else np.nan, "age": len(s),
                    "gap": (last / r.prev_close - 1) * 100 if np.isfinite(last) else np.nan})
    return pd.DataFrame(out)


def build() -> dict | None:
    up_f, done_f = EX / "exdiv_upcoming.csv", EX / "exdiv_5y.csv.gz"
    if not done_f.exists():
        return None
    h = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    C = h.pivot(index="date", columns="code", values="close").sort_index()
    last = C.index[-1]
    close = C.ffill().iloc[-1]
    try:   # ETF 現價（history 只有個股）
        e = pd.read_csv(EX / "etf.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"]).sort_values("date")
        close = pd.concat([e.groupby("code").close.last(), close])
        close = close[~close.index.duplicated(keep="last")]
    except Exception:  # noqa: BLE001
        pass
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code")
    names = sl.name.to_dict()
    try:   # ETF 不在 stock_list，名稱從 etf.csv 補
        e = pd.read_csv(EX / "etf.csv.gz", dtype={"code": str}, usecols=["code", "name"]).dropna().drop_duplicates("code", keep="last")
        names = {**dict(zip(e.code, e.name)), **names}
    except Exception:  # noqa: BLE001
        pass
    nm = lambda c: names.get(c, c)  # noqa: E731
    ind = lambda c: sl.industry.get(c, "") if isinstance(sl.industry.get(c, ""), str) else ""  # noqa: E731

    done = pd.read_csv(done_f, dtype={"code": str})
    done = done[done.code.str.fullmatch(r"\d{4}") & (done.date >= C.index[0])]
    fd = fill_days(done, C).merge(done[["date", "code", "kind", "prev_close", "value"]], on=["date", "code"], how="left")
    # 全市場過去一年：除權息日在 60 個交易日以前的才算（給足時間填）
    old = fd[fd.age >= 60]
    stats = {"n": int(len(old)), "since": old.date.min() if len(old) else None,
             "d1": _r((old.fill <= 1).mean() * 100, 0), "d5": _r((old.fill <= 5).mean() * 100, 0),
             "d20": _r((old.fill <= 20).mean() * 100, 0), "d60": _r((old.fill <= 60).mean() * 100, 0),
             "med": _med(old.fill)}
    # 殖利率分組（分身 0345：殖利率越高，要漲回去的幅度越大，越難填；不能只給全市場數字）
    yl = old.value / old.prev_close * 100
    stats["groups"] = []
    for lab, lo, hi in [("≤2%", -1, 2), ("2～4%", 2, 4), ("4～6%", 4, 6), (">6%", 6, 1e9)]:
        g = old[(yl > lo) & (yl <= hi)]
        if len(g):
            stats["groups"].append([lab, int(len(g)), _r((g.fill <= 20).mean() * 100, 0), _r((g.fill <= 60).mean() * 100, 0), _med(g.fill)])
    # 每檔最近一次的填息紀錄，給預告表參考
    prev = fd.sort_values("date").groupby("code").tail(1).set_index("code")   # tail 不會像 last() 跳過空值

    upcoming = []
    if up_f.exists():
        up = pd.read_csv(up_f, dtype={"code": str})
        up = up[(up.date > last) & up.code.str.fullmatch(r"\d{4}")].sort_values(["date", "code"])
        for r in up.itertuples():
            px = close.get(r.code, np.nan)
            cash = float(r.cash_div) if pd.notna(r.cash_div) else 0.0
            p = prev.loc[r.code] if r.code in prev.index else None
            upcoming.append({"d": r.date, "c": r.code, "n": nm(r.code), "i": ind(r.code), "k": r.kind,
                             "cash": _r(cash, 4), "stk": _r(float(r.stock_ratio) * 1000, 1) if pd.notna(r.stock_ratio) and r.stock_ratio else None,
                             "px": _r(px), "y": _r(cash / px * 100) if px and px > 0 and cash else None,
                             "pd": None if p is None else _r(p.fill, 0), "pdate": None if p is None else p["date"]})
    recent = []
    cut = C.index[max(0, len(C.index) - 22)]
    for r in fd[fd.date >= cut].sort_values("date", ascending=False).itertuples():
        recent.append({"d": r.date, "c": r.code, "n": nm(r.code), "k": r.kind, "v": _r(r.value, 4), "pc": _r(r.prev_close),
                       "px": _r(close.get(r.code, np.nan)), "fill": None if not np.isfinite(r.fill) else int(r.fill),
                       "age": int(r.age), "gap": _r(r.gap)})
    return {"date": last, "upcoming": upcoming, "recent": recent, "stats": stats}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>除權息行事曆</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--up:#d03b3b;--down:#0b8a3a;--accent:#2f5bd3}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b;--up:#f06b6b;--down:#3fbf6a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}a{color:var(--link)}
h1{font-size:23px;margin:8px 0 4px}h2{font-size:17px;margin:22px 0 6px}.meta{color:var(--muted);font-size:13px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:10px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px}.tile .k{color:var(--muted);font-size:12.5px}.tile .v{font-size:21px;font-weight:650}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);cursor:pointer}td.l,th.l{text-align:left}
tr.day td{background:rgba(128,128,128,.08);font-weight:600;text-align:left}
.up{color:var(--up)}.down{color:var(--down)}.tag{display:inline-block;font-size:11.5px;padding:0 7px;border-radius:999px;background:rgba(128,128,128,.14)}
input[type=search]{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:200px;max-width:100%}
label{font-size:13.5px;color:var(--muted);margin-left:10px;white-space:nowrap}
</style></head><body><main>
<!--SITENAV:exdiv-->
<h1>💰 除權息行事曆</h1>
<div class="meta">資料日 __DATE__・預告來自證交所／櫃買中心除權息預告表・殖利率＝這次現金股利 ÷ 最近收盤</div>
<div class="box">📌 <b>除息不是送錢</b>：除息當天股價會先扣掉股利，帳面總值不變；之後漲回除息前價格才叫「填息」。
領到的股利要併入綜合所得稅（或選 28% 分開計稅），單次領 2 萬元以上還要扣 2.11% 二代健保補充保費。
所以「為了領股利才買」通常不划算，除非你本來就想長期持有。
另外<b>殖利率越高越難填</b>（要漲回去的幅度越大）：過去一年殖利率 6% 以上的，60 天內填回的只有約 2 成。</div>

<h2>過去一年填息統計</h2>
<div class="tiles" id="stats"></div>
<div class="meta" id="statnote"></div>

<h2>未來要除權息的</h2>
<div style="margin:6px 0"><input type="search" id="q" placeholder="搜尋代號、名稱、產業"><label><input type="checkbox" id="hy"> 只看殖利率 ≥3%</label></div>
<div class="tbl"><table><thead><tr><th class="l">代號</th><th class="l">名稱</th><th class="l">產業</th><th>類別</th><th>現金股利</th><th>配股（每千股）</th><th>現價</th><th>這次殖利率</th><th>上次填息</th></tr></thead><tbody id="up"></tbody></table></div>

<h2>最近一個月除完的：填息進度</h2>
<div class="tbl"><table><thead><tr><th class="l">除權息日</th><th class="l">代號</th><th class="l">名稱</th><th>類別</th><th>權息值</th><th>除息前收盤</th><th>現價</th><th>填息</th><th>離填息</th></tr></thead><tbody id="rc"></tbody></table></div>
<p class="meta">填息天數＝除息當天算第 1 天，第一次收盤回到除息前一天收盤的那天。不構成投資建議。</p>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f=(v,d=2)=>v==null?'—':v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}),WD='日一二三四五六';
const S=D.stats;
$('stats').innerHTML=[['當天就填息',S.d1],['5 天內',S.d5],['20 天內',S.d20],['60 天內',S.d60]].map(([k,v])=>`<div class="tile"><div class="k">${k}</div><div class="v">${v==null?'—':v+'%'}</div></div>`).join('')+
 `<div class="tile"><div class="k">填息天數中位</div><div class="v">${S.med==null?'過半還沒填':S.med+' 天'}</div></div>`;
$('statnote').innerHTML=`${S.since||''} 起、已經過 60 個交易日以上的 ${S.n} 筆除權息。60 天內沒填的約 ${S.d60==null?'—':100-S.d60}%。中位數把還沒填的也算進去（當成無限久）。`+
 (S.groups&&S.groups.length?`<div class="tbl" style="margin-top:8px"><table><tr><th class="l">這次殖利率</th><th>筆數</th><th>20 天內填息</th><th>60 天內填息</th><th>填息天數中位</th></tr>`+
  S.groups.map(g=>`<tr><td class="l">${g[0]}</td><td>${g[1]}</td><td>${g[2]}%</td><td>${g[3]}%</td><td>${g[4]==null?'過半還沒填':g[4]+' 天'}</td></tr>`).join('')+'</table></div>':'');
function up(){const q=$('q').value.trim().toLowerCase(),hy=$('hy').checked;let last='',h='';
 D.upcoming.filter(r=>(!q||(r.c+r.n+r.i).toLowerCase().includes(q))&&(!hy||(r.y||0)>=3)).forEach(r=>{
  if(r.d!==last){last=r.d;const w=WD[new Date(r.d+'T00:00:00+08:00').getDay()];h+=`<tr class="day"><td colspan="9">${r.d.slice(5)}（${w}）</td></tr>`}
  h+=`<tr><td class="l"><a href="stock.html?code=${esc(r.c)}">${esc(r.c)}</a></td><td class="l">${esc(r.n)}</td><td class="l">${esc(r.i)}</td><td><span class="tag">${esc(r.k)}</span></td>`+
   `<td>${r.cash?f(r.cash,r.cash<1?3:2):'—'}</td><td>${r.stk?f(r.stk,1):'—'}</td><td>${f(r.px)}</td><td>${r.y==null?'—':'<b>'+f(r.y)+'%</b>'}</td>`+
   `<td>${r.pdate?(r.pd==null?'<span class="down">還沒填</span>':r.pd+' 天')+` <span class="meta">(${r.pdate.slice(2)})</span>`:'—'}</td></tr>`});
 $('up').innerHTML=h||'<tr><td class="l" colspan="9">沒有符合的</td></tr>'}
$('rc').innerHTML=D.recent.map(r=>`<tr><td class="l">${r.d.slice(5)}</td><td class="l"><a href="stock.html?code=${esc(r.c)}">${esc(r.c)}</a></td><td class="l">${esc(r.n)}</td><td><span class="tag">${esc(r.k)}</span></td>`+
 `<td>${f(r.v,r.v<1?3:2)}</td><td>${f(r.pc)}</td><td>${f(r.px)}</td><td>${r.fill!=null?`<span class="up">✅ ${r.fill} 天</span>`:`<span class="meta">第 ${r.age} 天</span>`}</td>`+
 `<td class="${r.gap>=0?'up':'down'}">${r.fill!=null?'—':(r.gap>0?'+':'')+f(r.gap,1)+'%'}</td></tr>`).join('')||'<tr><td class="l" colspan="9">最近沒有</td></tr>';
$('q').oninput=up;$('hy').onchange=up;up();
</script></body></html>"""


def write(site_dir: Path) -> bool:
    d = build()
    if d is None:
        return False
    page = PAGE.replace("__DATE__", html.escape(d["date"])).replace("__DATA__", json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    (site_dir / "exdiv.html").write_text(page, "utf-8")
    log.info("除權息行事曆：預告 %d 筆、最近 %d 筆", len(d["upcoming"]), len(d["recent"]))
    return True
