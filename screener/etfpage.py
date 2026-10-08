"""ETF 專區 site/etf.html：全部上市櫃 ETF 一張表——價格報酬、含息總報酬、近 12 個月配息與殖利率、配息頻率、
下次除息、上次填息天數、成交值。可搜尋、分類（股票／債券／槓桿反向／主動）、排序。

含息總報酬＝(現價＋期間內每單位配息) ÷ 起點價 − 1（不假設再投入）。殖利率＝近 12 個月配息 ÷ 現價。
資料：data/extras/etf.csv.gz（日 K）、exdiv_5y.csv.gz（已除息，value＝每單位現金）、exdiv_upcoming.csv（預告）。
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
EX = ROOT / "data" / "extras"
log = logging.getLogger(__name__)
COLS = ["c", "n", "t", "bad", "px", "d1", "r20", "r60", "r250", "tr250", "div12", "y12", "nd", "last", "lastd", "fill", "next", "nextd", "val20", "days"]


def _kind(code: str, name: str) -> str:
    if code.endswith("B"):
        return "債券"
    if code.endswith(("L", "R")) or "正2" in name or "反1" in name or "反向" in name or "槓桿" in name:
        return "槓桿反向"
    if code.endswith("A") or name.startswith("主動"):
        return "主動"
    if code.endswith(("U", "K")) or any(k in name for k in ("期", "原油", "黃金", "白銀", "美元")):
        return "商品期貨"
    return "股票"


def _r(v, nd=2):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else round(v, nd)


def build() -> dict | None:
    f = EX / "etf.csv.gz"
    if not f.exists():
        return None
    e = pd.read_csv(f, dtype={"code": str})
    if e.empty:
        return None
    last = e.date.max()
    names = e.dropna(subset=["name"]).drop_duplicates("code", keep="last").set_index("code").name
    C = e.pivot(index="date", columns="code", values="close").sort_index()
    V = e.pivot(index="date", columns="code", values="volume").reindex_like(C)
    codes = sorted(e.code[e.date == last].unique())
    days = list(C.index)
    xd = pd.read_csv(EX / "exdiv_5y.csv.gz", dtype={"code": str}) if (EX / "exdiv_5y.csv.gz").exists() else pd.DataFrame(columns=["date", "code", "kind", "prev_close", "value"])
    xd = xd[xd.code.isin(codes) & xd.kind.astype(str).str.contains("息")]
    up = pd.read_csv(EX / "exdiv_upcoming.csv", dtype={"code": str}) if (EX / "exdiv_upcoming.csv").exists() else pd.DataFrame(columns=["date", "code", "cash_div"])
    up = up[(up.date > last) & up.code.isin(codes)].sort_values("date").drop_duplicates("code")
    y1 = (dt.date.fromisoformat(last) - dt.timedelta(days=365)).isoformat()
    rows = []
    for c in codes:
        s = C[c].dropna()
        if s.empty or s.index[-1] != last:
            continue
        px = float(s.iloc[-1])
        jump = s.pct_change().abs()
        clean = lambda k: not (jump.iloc[-k:] > 0.5).any()   # 窗內單日 >50%：多半是分割／合併或 Yahoo 資料錯，報酬不給
        ret = lambda k: (px / s.iloc[-1 - k] - 1) * 100 if len(s) > k and clean(k) else None
        dv = xd[xd.code == c].sort_values("date")
        d12 = dv[dv.date > y1]
        div12 = float(d12.value.sum()) if len(d12) else 0.0
        tr = None
        if len(s) > 250 and clean(250):
            start = s.index[-251]
            tr = ((px + dv[dv.date > start].value.sum()) / s.iloc[-251] - 1) * 100
        fill = lastd = lastv = None
        if len(dv):
            ld = dv.iloc[-1]
            lastd, lastv = ld.date, float(ld.value)
            after = s[s.index >= ld.date]
            if len(after) and pd.notna(ld.prev_close) and s.index[0] <= ld.date:   # 資料要涵蓋除息日才算得出填息
                hit = np.flatnonzero(after.values >= float(ld.prev_close) - 1e-9)
                fill = int(hit[0]) + 1 if len(hit) else -len(after)    # 負數＝還沒填，已經過了幾天
        nx = up[up.code == c]
        val20 = (s * V[c].reindex(s.index)).iloc[-20:].mean() / 1e8
        nm = str(names.get(c, "") or "")
        rows.append([c, nm, _kind(c, nm), 0 if clean(min(250, len(s) - 1)) else 1, _r(px), _r(ret(1)), _r(ret(20), 1), _r(ret(60), 1), _r(ret(250), 1), _r(tr, 1),
                     _r(div12, 3), _r(div12 / px * 100) if div12 else 0, int(len(d12)), _r(lastv, 3), lastd, fill,
                     _r(nx.cash_div.iloc[0], 3) if len(nx) else None, nx.date.iloc[0] if len(nx) else None, _r(val20), int(len(s))])
    return {"date": last, "cols": COLS, "rows": rows, "n_days": len(days)}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF 專區</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--up:#d03b3b;--down:#0b8a3a;--accent:#2f5bd3}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b;--up:#f06b6b;--down:#3fbf6a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 60px}a{color:var(--link)}
h1{font-size:23px;margin:8px 0 4px}h2{font-size:17px;margin:20px 0 6px}.meta{color:var(--muted);font-size:13px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;margin:8px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:13.5px}.tile b{font-size:15px}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}
.bar button{font:inherit;font-size:13.5px;padding:4px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.bar button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.bar input,.bar select{font:inherit;font-size:14px;padding:5px 9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);max-width:100%}
.bar label{font-size:13.5px;color:var(--muted);white-space:nowrap}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;max-height:78vh}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:5px 9px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);cursor:pointer;z-index:1}th.s{color:var(--accent)}td.l,th.l{text-align:left}
.up{color:var(--up)}.down{color:var(--down)}.tag{display:inline-block;font-size:11px;padding:0 7px;border-radius:999px;background:rgba(128,128,128,.14)}
</style></head><body><main>
<!--SITENAV:etf-->
<h1>🧺 ETF 專區</h1>
<div class="meta">資料日 __DATE__・上市櫃全部 ETF・價格來自證交所／櫃買中心（較早的日子用 Yahoo 補）・配息來自除權息公告</div>
<div class="box">📌 <b>殖利率高不等於賺比較多</b>：配息可能來自本金或過去的資本利得，配完淨值就少那麼多。比較 ETF 請看<b>含息總報酬</b>（價格變化＋期間內領到的配息）。
另外月配、季配只是發錢的節奏，跟報酬高低無關；配息要課稅（股利所得、二代健保），在報酬上其實是扣分。</div>
<h2>成交最熱的 10 檔</h2>
<div class="tiles" id="hot"></div>
<div class="bar" id="kinds"></div>
<div class="bar">
 <input type="search" id="q" placeholder="代號或名稱，例如 0056、高息、美債">
 <select id="fq"><option value="">配息頻率：全部</option><option value="m">月配（近 12 月 ≥10 次）</option><option value="q">季配（4～6 次）</option><option value="h">半年／年配（1～3 次）</option><option value="0">近 12 月沒配</option></select>
 <label><input type="checkbox" id="liq" checked> 只看每天成交 ≥ 1,000 萬</label>
</div>
<div class="meta">共 <b id="n">0</b> 檔・點欄位排序</div>
<div class="tbl"><table><thead><tr id="hd"></tr></thead><tbody id="bd"></tbody></table></div>
<p class="meta">報酬都是價格報酬，含息總報酬另外一欄（不假設再投入）。一年＝250 個交易日；上市不滿一年的留空。填息＝上次除息後第幾個交易日收盤回到除息前價格，負數＝還沒填、已經過了幾天。不構成投資建議。</p>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const IX=Object.fromEntries(D.cols.map((k,i)=>[k,i])),R=D.rows;
const f=(v,d=2)=>v==null?'—':(+v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const sg=(v,d=1)=>v==null?'—':`<span class="${v>0?'up':v<0?'down':''}">${v>0?'+':''}${f(v,d)}%</span>`;
const T=[['c','代號'],['n','名稱'],['t','類型'],['px','價格'],['d1','今天'],['r20','20 日'],['r250','一年'],['tr250','一年含息'],['y12','近 12 月殖利率'],['nd','配息次數'],['last','上次配息'],['fill','填息'],['nextd','下次除息'],['val20','成交值(億/天)']];
const KD=['全部','股票','債券','主動','槓桿反向','商品期貨'];let S={k:'全部',sort:'val20',dir:-1};
function freq(n){return n>=10?'m':n>=4?'q':n>=1?'h':'0'}
function cell(r,k){const v=r[IX[k]];switch(k){
 case 'c':return `<td class="l"><a href="stock.html?code=${esc(v)}"><b>${esc(v)}</b></a></td>`;case 'n':return `<td class="l">${esc(v)}</td>`;case 't':return `<td class="l"><span class="tag">${esc(v)}</span>${r[IX.bad]?' <span class="tag" title="一年內有單日漲跌超過 50%（多半是分割、合併或資料錯），長期報酬先不算">⚠️ 資料可疑</span>':''}</td>`;
 case 'px':return `<td>${f(v)}</td>`;case 'd1':return `<td>${sg(v,2)}</td>`;case 'r20':case 'r250':case 'tr250':return `<td>${sg(v)}</td>`;
 case 'y12':return `<td>${v?'<b>'+f(v)+'%</b>':'—'}</td>`;case 'nd':return `<td>${v||'—'}</td>`;
 case 'last':return `<td>${v==null?'—':f(v,v<1?3:2)+` <span class="meta">${esc((r[IX.lastd]||'').slice(2))}</span>`}</td>`;
 case 'fill':return `<td>${v==null?'—':v>0?`<span class="up">${v} 天</span>`:`<span class="meta">還沒（${-v} 天）</span>`}</td>`;
 case 'nextd':return `<td>${v?esc(v.slice(5))+(r[IX.next]!=null?` <span class="meta">${f(r[IX.next],r[IX.next]<1?3:2)}</span>`:''):'—'}</td>`;
 case 'val20':return `<td>${f(v,2)}</td>`}return '<td></td>'}
function run(){const q=$('q').value.trim().toLowerCase(),fq=$('fq').value,liq=$('liq').checked;
 const out=R.filter(r=>(S.k==='全部'||r[IX.t]===S.k)&&(!q||(r[IX.c]+r[IX.n]).toLowerCase().includes(q))&&(!fq||freq(r[IX.nd])===fq)&&(!liq||(r[IX.val20]||0)>=0.1));
 const i=IX[S.sort];out.sort((a,b)=>{const x=a[i],y=b[i];if(x==null)return 1;if(y==null)return -1;return(x<y?-1:x>y?1:0)*S.dir});
 $('n').textContent=out.length;
 $('hd').innerHTML=T.map(([k,t])=>`<th class="${['c','n','t'].includes(k)?'l ':''}${k===S.sort?'s':''}" data-k="${k}">${t}${k===S.sort?(S.dir<0?' ▼':' ▲'):''}</th>`).join('');
 $('hd').querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;S.dir=S.sort===k?-S.dir:(['c','n','t','nextd'].includes(k)?1:-1);S.sort=k;run()});
 $('bd').innerHTML=out.map(r=>'<tr>'+T.map(([k])=>cell(r,k)).join('')+'</tr>').join('')||`<tr><td class="l" colspan="${T.length}">沒有符合的</td></tr>`}
$('kinds').innerHTML=KD.map(k=>`<button class="${k===S.k?'on':''}">${k}</button>`).join('');
$('kinds').querySelectorAll('button').forEach(b=>b.onclick=()=>{S.k=b.textContent;$('kinds').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));run()});
$('hot').innerHTML=R.slice().sort((a,b)=>(b[IX.val20]||0)-(a[IX.val20]||0)).slice(0,10).map(r=>`<div class="tile"><a href="stock.html?code=${esc(r[IX.c])}"><b>${esc(r[IX.c])}</b> ${esc(r[IX.n])}</a><br>${f(r[IX.px])} ${sg(r[IX.d1],2)}・一年含息 ${sg(r[IX.tr250])}<br><span class="meta">殖利率 ${r[IX.y12]?f(r[IX.y12])+'%':'—'}・成交 ${f(r[IX.val20],1)} 億/天</span></div>`).join('');
$('q').oninput=run;$('fq').onchange=run;$('liq').onchange=run;run();
</script></body></html>"""


def write(site_dir: Path) -> bool:
    d = build()
    if d is None or not d["rows"]:
        return False
    page = PAGE.replace("__DATE__", html.escape(d["date"])).replace(
        "__DATA__", json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    (site_dir / "etf.html").write_text(page, "utf-8")
    log.info("ETF 專區：%d 檔（etf.csv 有 %d 個交易日）", len(d["rows"]), d["n_days"])
    return True
