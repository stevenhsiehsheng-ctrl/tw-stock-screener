"""法人籌碼頁 site/chips.html：外資／投信連買連賣排行、市場每日三大法人買賣超金額、產業流向。

只描述籌碼在誰手上，不當訊號：一年回測（Cowork 0225／0255／0254）外資連買、投信買超都沒有獨立超額，外資連賣也不是出場訊號。
資料：data/extras/inst_hist.csv.gz（個股三大法人，張）＋ data/history.csv.gz（收盤、成交量）＋ data/stock_list.csv。
"""
from __future__ import annotations

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

K = ("foreign", "trust", "dealer")


def _streak(a: np.ndarray) -> int:
    """最近連續同方向的天數：正＝連買，負＝連賣，0＝最近一天沒進出。"""
    if not len(a) or not np.isfinite(a[-1]) or a[-1] == 0:
        return 0
    s = 1 if a[-1] > 0 else -1
    n = 0
    for x in a[::-1]:
        if not np.isfinite(x) or x == 0 or (x > 0) != (s > 0):
            break
        n += 1
    return s * n


def _r(v, nd=1):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def build() -> dict | None:
    f = EX / "inst_hist.csv.gz"
    if not f.exists():
        return None
    inst = pd.read_csv(f, dtype={"code": str})
    inst = inst[inst.code.str.fullmatch(r"\d{4}")]
    h = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close", "volume"])
    h = h[h.code.str.fullmatch(r"\d{4}")]
    days = sorted(set(inst.date) & set(h.date))
    if len(days) < 25:
        return None
    days = days[-260:]
    last = days[-1]
    C = h.pivot(index="date", columns="code", values="close").reindex(days)
    V = h.pivot(index="date", columns="code", values="volume").reindex(days) / 1000      # 張
    P = {k: inst.pivot_table(index="date", columns="code", values=k, aggfunc="sum").reindex(index=days, columns=C.columns) for k in K}
    amt = {k: P[k] * C * 1000 / 1e8 for k in K}                                           # 億元
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code")

    # 市場：每天三大法人買賣超金額（近 120 個交易日）
    mk = pd.DataFrame({k: amt[k].sum(axis=1, min_count=1) for k in K}).tail(120)
    market = [[d, _r(r.foreign), _r(r.trust), _r(r.dealer)] for d, r in mk.iterrows()]
    # ETF（分身 0315：官方外資買賣超含 ETF）：etf.csv 只有部分 ETF 有整年收盤，缺的用最近一個收盤往回補，標『估』
    etf = {}
    try:
        ie = pd.read_csv(f, dtype={"code": str})
        ie = ie[ie.code.str.startswith("00") & ie.date.isin(days)]
        ep = pd.read_csv(EX / "etf.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
        EP = ep.pivot(index="date", columns="code", values="close").reindex(days).ffill().bfill()
        for k in ("foreign", "trust"):
            q = ie.pivot_table(index="date", columns="code", values=k, aggfunc="sum").reindex(index=days)
            a = (q * EP.reindex(columns=q.columns) * 1000 / 1e8).sum(axis=1, min_count=1)
            etf[k] = [_r(a.iloc[-1]), _r(a.tail(5).sum()), _r(a.tail(20).sum())]
    except Exception as e:  # noqa: BLE001
        log.warning("ETF 法人金額失敗：%s", e)

    # 個股
    v20 = V.tail(20).mean()
    chg = (C.iloc[-1] / C.iloc[-2] - 1) * 100
    r20 = (C.iloc[-1] / C.iloc[-21] - 1) * 100
    rows = []
    for c in C.columns:
        cl = C[c].iloc[-1]
        if not np.isfinite(cl) or c not in sl.index:
            continue
        fa, ta = P["foreign"][c].to_numpy(), P["trust"][c].to_numpy()
        if not np.isfinite(fa[-20:]).any():
            continue
        v5 = np.nansum(V[c].to_numpy()[-5:])
        f5, t5 = np.nansum(fa[-5:]), np.nansum(ta[-5:])
        r = sl.loc[c]
        rows.append({"c": c, "n": r["name"], "i": r.get("industry") if isinstance(r.get("industry"), str) else "",
                     "m": "上市" if r.get("market") == "TWSE" else "上櫃",
                     "p": _r(cl, 2), "ch": _r(chg[c], 2), "r20": _r(r20[c], 1), "v20": _r(v20[c], 0),
                     "fs": _streak(fa), "ts": _streak(ta),
                     "f1": _r(fa[-1], 0), "t1": _r(ta[-1], 0),
                     "fa1": _r(amt["foreign"][c].iloc[-1], 2), "ta1": _r(amt["trust"][c].iloc[-1], 2),
                     "f5": _r(f5, 0), "t5": _r(t5, 0),
                     "f20": _r(np.nansum(fa[-20:]), 0), "t20": _r(np.nansum(ta[-20:]), 0),
                     "fp5": _r(f5 / v5 * 100, 1) if v5 > 0 else None, "tp5": _r(t5 / v5 * 100, 1) if v5 > 0 else None,
                     "fa5": _r(np.nansum(amt["foreign"][c].to_numpy()[-5:]), 2), "ta5": _r(np.nansum(amt["trust"][c].to_numpy()[-5:]), 2)})

    # 產業：近 5 日外資、投信買賣超金額
    df = pd.DataFrame(rows)
    ind = (df[df.i != ""].groupby("i")[["fa5", "ta5"]].sum().assign(tot=lambda x: x.fa5 + x.ta5)
           .sort_values("tot"))
    industry = [[i, _r(r.fa5), _r(r.ta5)] for i, r in ind.iterrows()]
    return {"date": last, "days": len(days), "first": days[0], "market": market, "etf": etf, "rows": rows, "industry": industry}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>法人籌碼</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--up:#d03b3b;--down:#0b8a3a;--accent:#2f5bd3;--f:#2a78d6;--t:#c77d12;--d:#8a8a84}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b;--up:#f06b6b;--down:#3fbf6a;--f:#5c9cf0;--t:#e7a64a;--d:#9a9a92}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}a{color:var(--link)}
h1{font-size:23px;margin:8px 0 4px}h2{font-size:17px;margin:22px 0 6px}.meta{color:var(--muted);font-size:13px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:14px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.tabs button{font:inherit;font-size:13.5px;padding:5px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.tabs button.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;cursor:pointer;position:sticky;top:0;background:var(--card)}td.l,th.l{text-align:left}
.up{color:var(--up)}.down{color:var(--down)}.k{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:-1px}
input[type=search]{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:200px;max-width:100%}
label{font-size:13.5px;color:var(--muted);margin-left:10px;white-space:nowrap}
svg text{fill:var(--muted);font-size:11px}.ind{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:700px){.ind{grid-template-columns:1fr}}
.ind table td:first-child{text-align:left}
</style></head><body><main>
<!--SITENAV:chips-->
<h1>🏦 法人籌碼</h1>
<div class="meta">資料日 __DATE__・三大法人買賣超（張）來自證交所／櫃買中心・金額＝張數×當天收盤・圖和排行只含個股（不含 ETF）</div>
<div class="box">🔍 <b>這頁只看籌碼在誰手上，不是買賣訊號。</b>我們用一年資料驗過，以下說法都沒有證據：
① 外資連買 5 天以上：20 天後沒有比同產業、前 5 天漲一樣多的股票好（配對差平均 −0.02%、中位 −0.96%）；
② 投信買超、季底作帳：沒有獨立超額；
③ 外資連賣當成賣出訊號：中位雖然差 1.5%，但賣掉也會賣掉反彈最大的那幾檔，整體不划算。
5 年版資料補齊後會重驗，結果會更新在這裡。</div>

<h2>市場：每天三大法人買賣超（億元）</h2>
<div class="meta"><span class="k" style="background:var(--f)"></span>外資<span class="k" style="background:var(--t)"></span>投信<span class="k" style="background:var(--d)"></span>自營商　近 120 個交易日</div>
<div id="mkt"></div><div class="meta" id="mktsum"></div>

<h2>個股排行</h2>
<div class="tabs" id="tabs"></div>
<div style="margin:6px 0"><input type="search" id="q" placeholder="搜尋代號、名稱、產業"><label><input type="checkbox" id="liq" checked> 只看 20 日均量 ≥500 張</label></div>
<div class="meta" id="desc"></div>
<div class="tbl"><table><thead id="hd"></thead><tbody id="bd"></tbody></table></div>

<h2>產業：近 5 日法人買賣超（億元）</h2>
<div class="ind" id="ind"></div>
<p class="meta">連買／連賣天數＝最近連續同方向的交易日數。占量%＝近 5 日買賣超 ÷ 近 5 日成交量。不構成投資建議。</p>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cl=v=>v>0?'up':v<0?'down':'',sg=(v,d=0)=>v==null?'—':(v>0?'+':'')+v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const TABS=[
 {k:'fbuy',t:'外資連買',f:r=>r.fs>0,s:(a,b)=>b.fs-a.fs||b.fa5-a.fa5,d:'外資連續買超天數，最久的排前面（同天數比 5 日買超金額）。'},
 {k:'fsell',t:'外資連賣',f:r=>r.fs<0,s:(a,b)=>a.fs-b.fs||a.fa5-b.fa5,d:'外資連續賣超天數，最久的排前面。'},
 {k:'tbuy',t:'投信連買',f:r=>r.ts>0,s:(a,b)=>b.ts-a.ts||b.ta5-a.ta5,d:'投信連續買超天數，最久的排前面。'},
 {k:'tsell',t:'投信連賣',f:r=>r.ts<0,s:(a,b)=>a.ts-b.ts||a.ta5-b.ta5,d:'投信連續賣超天數，最久的排前面。'},
 {k:'famt',t:'外資今天買最多',f:r=>r.fa1>0,s:(a,b)=>b.fa1-a.fa1,d:'今天外資買超金額（張數×收盤）最多的。'},
 {k:'famtn',t:'外資今天賣最多',f:r=>r.fa1<0,s:(a,b)=>a.fa1-b.fa1,d:'今天外資賣超金額最多的。'},
 {k:'tamt',t:'投信今天買最多',f:r=>r.ta1>0,s:(a,b)=>b.ta1-a.ta1,d:'今天投信買超金額最多的。'},
 {k:'fp',t:'外資 5 日占量最高',f:r=>r.fp5!=null&&r.fp5>0,s:(a,b)=>b.fp5-a.fp5,d:'近 5 日外資買超占成交量的比例：外資在這檔有多「重」。'},
 {k:'tp',t:'投信 5 日占量最高',f:r=>r.tp5!=null&&r.tp5>0,s:(a,b)=>b.tp5-a.tp5,d:'近 5 日投信買超占成交量的比例。'}];
const COLS=[['c','代號',1],['n','名稱',1],['i','產業',1],['p','收盤'],['ch','漲跌%'],['r20','20 日%'],['fs','外資連'],['f1','外資今天(張)'],['fa5','外資 5 日(億)'],['fp5','外資占量%'],
 ['ts','投信連'],['t1','投信今天(張)'],['ta5','投信 5 日(億)'],['tp5','投信占量%'],['v20','均量(張)']];
let tab=0,sk=null,sd=-1;
function cell(r,k){const v=r[k];
 if(k==='c')return`<td class="l"><a href="stock.html?code=${esc(v)}">${esc(v)}</a></td>`;
 if(k==='n'||k==='i')return`<td class="l">${esc(v)}</td>`;
 if(k==='fs'||k==='ts')return`<td class="${cl(v)}">${v?(v>0?'買 ':'賣 ')+Math.abs(v)+' 天':'—'}</td>`;
 if(k==='p')return`<td>${v??'—'}</td>`;
 if(k==='v20')return`<td>${v==null?'—':v.toLocaleString()}</td>`;
 const d=(k==='fa5'||k==='ta5')?2:(k==='f1'||k==='t1')?0:1;return`<td class="${cl(v)}">${sg(v,d)}</td>`}
function draw(){const T=TABS[tab],q=$('q').value.trim().toLowerCase(),liq=$('liq').checked;
 $('tabs').innerHTML=TABS.map((x,i)=>`<button class="${i===tab?'on':''}" data-i="${i}">${x.t}</button>`).join('');
 document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{tab=+b.dataset.i;sk=null;draw()});
 $('desc').textContent=T.d;
 let R=D.rows.filter(r=>(!liq||(r.v20||0)>=500)&&(!q||(r.c+r.n+r.i).toLowerCase().includes(q))&&T.f(r));
 R.sort(sk?(a,b)=>((a[sk]??-1e18)>(b[sk]??-1e18)?1:-1)*sd:T.s);R=R.slice(0,60);
 $('hd').innerHTML='<tr>'+COLS.map(([k,t,l])=>`<th class="${l?'l':''}" data-k="${k}">${t}${sk===k?(sd>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
 document.querySelectorAll('#hd th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sd=sk===k?-sd:-1;sk=k;draw()});
 $('bd').innerHTML=R.length?R.map(r=>'<tr>'+COLS.map(([k])=>cell(r,k)).join('')+'</tr>').join(''):'<tr><td class="l" colspan="15">沒有符合的股票</td></tr>'}
function mkt(){const M=D.market,W=Math.max(320,Math.min(1060,$('mkt').clientWidth||900)),H=190,pl=40,pb=18;
 const v=M.flatMap(r=>[r[1],r[2],r[3]]).filter(x=>x!=null),mx=Math.max(1,...v.map(Math.abs));
 const y=x=>(H-pb)/2-(x/mx)*((H-pb)/2-6),bw=(W-pl)/M.length;
 let s=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="每天三大法人買賣超"><line x1="${pl}" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>`+
  `<text x="${pl-4}" y="${y(mx)+4}" text-anchor="end">+${Math.round(mx)}</text><text x="${pl-4}" y="${y(-mx)+4}" text-anchor="end">−${Math.round(mx)}</text>`;
 M.forEach((r,i)=>{const x=pl+i*bw;[[1,'--f'],[2,'--t'],[3,'--d']].forEach(([j,c],n)=>{const val=r[j];if(val==null)return;
  const a=y(Math.max(val,0)),b=y(Math.min(val,0));s+=`<rect x="${(x+n*bw/3).toFixed(1)}" y="${a.toFixed(1)}" width="${Math.max(bw/3-.3,.6).toFixed(1)}" height="${Math.max(b-a,.5).toFixed(1)}" fill="var(${c})"><title>${r[0]} ${['','外資','投信','自營商'][j]} ${sg(val,1)} 億</title></rect>`})});
 [[0,'start'],[Math.floor(M.length/2),'middle'],[M.length-1,'end']].forEach(([i,a])=>s+=`<text x="${pl+(i+.5)*bw}" y="${H-3}" text-anchor="${a}">${M[i][0].slice(5)}</text>`);
 $('mkt').innerHTML=s+'</svg>';
 const sum=(j,n)=>M.slice(-n).reduce((a,r)=>a+(r[j]||0),0),L=M[M.length-1];
 $('mktsum').innerHTML=`今天 外資 <b class="${cl(L[1])}">${sg(L[1],1)}</b> 億、投信 <b class="${cl(L[2])}">${sg(L[2],1)}</b> 億、自營商 <b class="${cl(L[3])}">${sg(L[3],1)}</b> 億｜近 5 日外資 <b class="${cl(sum(1,5))}">${sg(sum(1,5),0)}</b> 億、近 20 日 <b class="${cl(sum(1,20))}">${sg(sum(1,20),0)}</b> 億`+
  (()=>{const A=M.slice(-20).map(r=>r[1]||0),big=A.reduce((a,b)=>Math.abs(b)>Math.abs(a)?b:a,0),i=A.indexOf(big),tot=A.reduce((a,b)=>a+b,0),med=A.slice().sort((a,b)=>a-b)[Math.floor(A.length/2)];
    return `<br>⚠️ 20 日加總會被單一天綁架：窗內最大一天是 ${M.slice(-20)[i][0].slice(5)} 的 ${sg(big,0)} 億${Math.abs(big)>Math.abs(tot)/2?'（超過 20 日合計的一半）':''}，單日中位 ${sg(med,0)} 億。`})()+
  (D.etf&&D.etf.foreign?`<br>以上只算個股。ETF 另計（部分 ETF 用最近收盤估）：外資今天 <b class="${cl(D.etf.foreign[0])}">${sg(D.etf.foreign[0],1)}</b> 億、5 日 ${sg(D.etf.foreign[1],0)} 億、20 日 ${sg(D.etf.foreign[2],0)} 億；新聞的「外資買賣超」通常含 ETF。`:'')}
function ind(){const I=D.industry,t=(L,h)=>`<div><div class="meta">${h}</div><div class="tbl"><table><tr><th class="l">產業</th><th>外資</th><th>投信</th><th>合計</th></tr>`+
  L.map(r=>`<tr><td>${esc(r[0])}</td><td class="${cl(r[1])}">${sg(r[1],1)}</td><td class="${cl(r[2])}">${sg(r[2],1)}</td><td class="${cl(r[1]+r[2])}"><b>${sg(r[1]+r[2],1)}</b></td></tr>`).join('')+'</table></div></div>';
 $('ind').innerHTML=t(I.slice().reverse().slice(0,10),'買最多的 10 個產業')+t(I.slice(0,10),'賣最多的 10 個產業')}
$('q').oninput=draw;$('liq').onchange=draw;draw();mkt();ind();addEventListener('resize',mkt);
</script></body></html>"""


def write(site_dir: Path) -> bool:
    d = build()
    if d is None:
        return False
    page = PAGE.replace("__DATE__", html.escape(d["date"])).replace("__DATA__", json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    (site_dir / "chips.html").write_text(page, "utf-8")
    log.info("法人籌碼頁：%d 檔、%s", len(d["rows"]), d["date"])
    return True
