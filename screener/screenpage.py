"""自訂選股 site/screen.html：全部上市櫃普通股一張表，價格、報酬、市值、本益比、殖利率、營收、強弱、法人，
網頁上自己設條件篩、點欄位排序、匯出 CSV，網址會記住條件（可以分享）。

這頁只是「過濾」，不是回測過的策略；報酬是價格報酬（減資／變更面額已接起來，除息沒還原）。
資料：data/history.csv.gz、data/stock_list.csv、data/extras/{shares,pe,revenue,tech,inst_hist,warnings,longterm_screen}.csv。
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import corpact

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EX = DATA / "extras"
log = logging.getLogger(__name__)

# 欄位順序＝網頁 JS 的 COLS（改這裡要一起改 PAGE）
COLS = ["c", "n", "m", "i", "px", "d1", "r5", "r20", "r60", "r250", "v", "val20", "cap", "pe", "y", "pb",
        "ry", "rc", "rm", "rs", "h52", "tpl", "f5", "f5p", "t5", "roe3", "cagr3", "w", "pe_pct", "pb_pct", "y_pct"]


def _read(p: Path, **kw) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype={"code": str}, **kw) if p.exists() else None
    except Exception as e:  # noqa: BLE001
        log.warning("讀不到 %s：%s", p.name, e)
        return None


def _col(df: pd.DataFrame | None, col: str) -> pd.Series:
    if df is None or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df.drop_duplicates("code").set_index("code")[col], errors="coerce")


def build() -> dict | None:
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code").fillna("")
    h = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str})
    h = h[h.code.str.fullmatch(r"[1-9]\d{3}")]
    if h.empty:
        return None
    h = corpact.adjust(h)
    piv = lambda c: h.pivot(index="date", columns="code", values=c).sort_index()
    C, V = piv("close"), piv("volume")
    last = C.index[-1]
    traded = V.loc[last].fillna(0) > 0
    codes = [c for c in C.columns if traded.get(c, False)]
    px = C.loc[last]
    CF = C.ffill()

    def ret(k: int) -> pd.Series:
        if len(C) <= k:
            return pd.Series(dtype=float)
        return (px / CF.iloc[-1 - k] - 1) * 100

    rets = {k: ret(k) for k in (1, 5, 20, 60, 250)}
    val20 = (C * V).iloc[-20:].mean() / 1e8
    raw = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    raw_px = raw[raw.date == last].set_index("code").close          # 市值用當天官方收盤
    shares = _col(_read(EX / "shares.csv"), "shares")
    pe = _read(EX / "pe.csv")
    rev = _read(EX / "revenue.csv")
    tech = _read(EX / "tech.csv")
    lt = _read(EX / "longterm_screen.csv")
    warn = _read(EX / "warnings.csv")
    wflag = warn.drop_duplicates("code").set_index("code").flag if warn is not None and not warn.empty else pd.Series(dtype=str)
    tpl = tech.drop_duplicates("code").set_index("code").tpl if tech is not None and "tpl" in tech else pd.Series(dtype=object)
    # 法人近 5 個交易日買賣超（張），外資另算占同期成交量 %
    inst = _read(EX / "inst_hist.csv.gz", usecols=["date", "code", "foreign", "trust"])
    f5 = t5 = pd.Series(dtype=float)
    if inst is not None and not inst.empty:
        d5 = sorted(inst.date.unique())[-5:]
        g = inst[inst.date.isin(d5)].groupby("code")[["foreign", "trust"]].sum()
        f5, t5 = g.foreign, g.trust
        vol5 = V.loc[[d for d in V.index if d in set(d5)]].sum() / 1000
    else:
        vol5 = pd.Series(dtype=float)
    rmonth = None
    if rev is not None and "rev_month" in rev and rev.rev_month.notna().any():
        ym = str(int(pd.to_numeric(rev.rev_month, errors="coerce").max()))
        rmonth = f"{int(ym[:-2]) + 1911}-{ym[-2:]}"

    def num(s: pd.Series, c: str, nd: int = 2):
        v = s.get(c, np.nan)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if not np.isfinite(v) else round(v, nd)

    pe_pe, pe_y, pe_pb = _col(pe, "pe"), _col(pe, "yield"), _col(pe, "pb")
    rv_y, rv_c, rv_m = _col(rev, "rev_yoy"), _col(rev, "rev_cum_yoy"), _col(rev, "rev_mom")
    rs, h52 = _col(tech, "rs"), _col(tech, "hi52_dist")
    roe3, cagr3 = _col(lt, "roe3_avg"), _col(lt, "rev_cagr3")
    try:
        from . import valuation
        vt = valuation.table()
    except Exception as e:  # noqa: BLE001
        log.warning("估值位階失敗：%s", e)
        vt = pd.DataFrame()
    vcol = lambda k: vt[k] if k in vt.columns else pd.Series(dtype=float)
    pe_p, pb_p, y_p = vcol("pe_pct"), vcol("pb_pct"), vcol("y_pct")
    rows = []
    for c in codes:
        cap = raw_px.get(c, np.nan) * shares.get(c, np.nan) / 1e8
        f = num(f5, c, 0)
        v5 = vol5.get(c, np.nan)
        rows.append([
            c, sl.name.get(c, ""), {"TWSE": "上市", "TPEX": "上櫃"}.get(sl.market.get(c, ""), ""), sl.industry.get(c, "") or "",
            num(raw_px, c), num(rets[1], c), num(rets[5], c, 1), num(rets[20], c, 1), num(rets[60], c, 1), num(rets[250], c, 1),
            num(V.loc[last] / 1000, c, 0), num(val20, c), None if not np.isfinite(cap) else round(cap, 1),
            num(pe_pe, c), num(pe_y, c), num(pe_pb, c),
            num(rv_y, c, 1), num(rv_c, c, 1), num(rv_m, c, 1), num(rs, c, 0), num(h52, c, 1),
            1 if str(tpl.get(c, "")).lower() == "true" else 0,
            f, None if f is None or not (v5 > 0) else round(f / v5 * 100, 1), num(t5, c, 0),
            num(roe3, c, 1), num(cagr3, c, 1), wflag.get(c, "") or "",
            num(pe_p, c, 0), num(pb_p, c, 0), num(y_p, c, 0),
        ])
    inds = sorted({r[3] for r in rows if r[3]})
    return {"date": last, "rmonth": rmonth, "cols": COLS, "rows": rows, "inds": inds}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自訂選股</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--up:#d03b3b;--down:#0b8a3a;--accent:#2f5bd3}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b;--up:#f06b6b;--down:#3fbf6a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 60px}a{color:var(--link)}
h1{font-size:23px;margin:8px 0 4px}h2{font-size:17px;margin:20px 0 6px}.meta{color:var(--muted);font-size:13px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:14px}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.presets button,.bar button{font:inherit;font-size:13.5px;padding:5px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.presets button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-top:8px}
.f{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 10px;font-size:13px}
.f.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.f .k{color:var(--muted);display:flex;justify-content:space-between}.f .k span{font-size:11.5px}
.f input{font:inherit;width:44%;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--fg);box-sizing:border-box}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
.bar select,.bar input[type=search]{font:inherit;font-size:14px;padding:5px 9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);max-width:100%}
.bar label{font-size:13.5px;color:var(--muted);white-space:nowrap}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;max-height:75vh}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:5px 9px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);cursor:pointer;z-index:1}th.s{color:var(--accent)}td.l,th.l{text-align:left}
.up{color:var(--up)}.down{color:var(--down)}.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:999px;background:rgba(208,59,59,.15);color:var(--up)}
#n{font-weight:650}details summary{cursor:pointer;color:var(--muted);font-size:13.5px}#more summary{color:var(--fg);font-weight:600;padding:4px 0}
</style></head><body><main>
<!--SITENAV:screen-->
<h1>🧮 自訂選股</h1>
<div class="meta">資料日 __DATE__・全部上市櫃普通股（不含 ETF）・營收月份 __RMONTH__・網址會記住你設的條件，可以直接分享</div>
<div class="box">⚠️ 這頁只是<b>把條件過濾出來</b>，不是回測過的策略。條件越多越像「事後挑」，看到漂亮的名單先問：這組條件 5 年前就會這樣選嗎？
報酬是價格報酬（減資已接起來，除息沒還原）；本益比、殖利率、淨值比來自證交所／櫃買中心；外資 5 日占量＝近 5 天外資買賣超 ÷ 同期成交量。</div>
<div class="presets" id="pre"></div><div class="meta" id="pdesc"></div>
<details id="more"><summary>📋 條件（<span id="nf">0</span> 個在用）：價格、報酬、市值、本益比、殖利率、營收、強弱、法人</summary><div class="grid" id="flt"></div></details>
<div class="bar">
 <input type="search" id="q" placeholder="代號、名稱">
 <select id="mk"><option value="">上市＋上櫃</option><option>上市</option><option>上櫃</option></select>
 <select id="ind"><option value="">全部產業</option></select>
 <label><input type="checkbox" id="nw" checked> 排除注意／處置股</label>
 <label><input type="checkbox" id="tp"> 只看趨勢樣板 ✓</label>
 <button id="clr">清除條件</button><button id="csv">⬇ 匯出 CSV</button>
</div>
<div class="meta">符合 <span id="n">0</span> 檔<span id="cut"></span>・點欄位排序</div>
<div class="tbl"><table><thead><tr id="hd"></tr></thead><tbody id="bd"></tbody></table></div>
<details style="margin-top:10px"><summary>欄位說明</summary><div class="meta" style="margin-top:6px">
RS＝過去一年漲幅在全市場的百分位（99 最強）；距一年高＝現價離 52 週最高價差幾 %；趨勢樣板＝收盤 > 50 日線 > 150 日線 > 200 日線且 200 日線上彎等條件（Minervini）。
ROE 3 年、營收 3 年 CAGR 只有長期篩選有算到的公司才有。5 年位階＝現在的值在這檔自己過去 5 年每月快照的第幾百分位（本益比 10＝比過去 9 成時間都便宜；殖利率 90＝比過去 9 成時間都高），至少 24 個月才算。市值＝當天收盤 × 已發行股數。20 日成交值＝近 20 天平均每天成交金額。不構成投資建議。</div></details>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const IX=Object.fromEntries(D.cols.map((k,i)=>[k,i])),R=D.rows;
/* 可設範圍的欄位：[key, 名稱, 單位] */
const F=[['px','股價','元'],['d1','今天漲跌','%'],['r5','5 日漲跌','%'],['r20','20 日漲跌','%'],['r60','60 日漲跌','%'],['r250','一年漲跌','%'],
 ['cap','市值','億'],['val20','20 日成交值','億/天'],['pe','本益比','倍'],['y','殖利率','%'],['pb','股價淨值比','倍'],
 ['ry','營收年增','%'],['rc','累計營收年增','%'],['rm','營收月增','%'],['rs','RS 強弱','1-99'],['h52','距一年高','%'],
 ['f5p','外資 5 日占量','%'],['t5','投信 5 日','張'],['roe3','ROE 3 年平均','%'],['cagr3','營收 3 年 CAGR','%'],
 ['pe_pct','本益比 5 年位階','0-100'],['pb_pct','淨值比 5 年位階','0-100'],['y_pct','殖利率 5 年位階','0-100']];
/* 表格欄：[key, 標題, 小數, 上色] */
const T=[['c','代號'],['n','名稱'],['i','產業'],['px','股價',2],['d1','今天',2,1],['r20','20 日',1,1],['r250','一年',1,1],['cap','市值(億)',0],
 ['val20','成交值(億)',2],['pe','本益比',1],['pe_pct','PE 位階',0],['y','殖利率',2],['pb','淨值比',2],['ry','營收年增',1,1],['rc','累計年增',1,1],['rs','RS',0],['h52','距高',1,1],
 ['f5p','外資占量',1,1],['t5','投信5日',0,1],['roe3','ROE3',1],['w','']];
const PRE=[
 ['💎 低本益比高殖利率',{pe:[0,12],y:[5,''],cap:[50,'']},'本益比 12 倍以下、殖利率 5% 以上、市值 50 億以上'],
 ['🚀 營收爆發',{ry:[30,''],rc:[20,''],val20:[0.5,'']},'單月營收年增 ≥30%、累計年增 ≥20%、每天成交 5 千萬以上'],
 ['📈 強勢股',{rs:[80,''],h52:[-5,''],val20:[1,'']},'RS ≥80、離一年高不到 5%、每天成交 1 億以上'],
 ['🏦 外資在買',{f5p:[10,''],val20:[1,'']},'近 5 天外資買超占成交量 ≥10%'],
 ['🛡 大型穩健',{cap:[1000,''],roe3:[15,'']},'市值 1,000 億以上、3 年平均 ROE ≥15%'],
 ['📉 跌深',{r60:['',-20],val20:[1,'']},'60 天跌超過 20%、每天成交 1 億以上（跌深不等於便宜）'],
 ['🏷 比自己過去便宜',{pe_pct:['',20],y_pct:[60,''],val20:[0.3,'']},'本益比在自己 5 年的最低 2 成、殖利率在自己 5 年的前 4 成。⚠️ 回測沒贏：2023-09～2026-07 共 35 批，本益比位階最低 2 成的之後 60 日超額比最高 2 成的平均還差 1.8pp（中位差 0.7pp），便宜通常有原因']];
let S={f:{},sort:'cap',dir:-1,q:'',mk:'',ind:'',nw:1,tp:0};
const num=v=>v===''||v==null||isNaN(+v)?null:+v;
function fmt(v,d){return v==null?'—':(+v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d})}
function drawF(){$('flt').innerHTML=F.map(([k,n,u])=>{const r=S.f[k]||['',''];const on=r[0]!==''||r[1]!=='';
 return `<div class="f${on?' on':''}"><div class="k">${n}<span>${u}</span></div><input data-k="${k}" data-j="0" inputmode="decimal" placeholder="最低" value="${esc(r[0])}"> ～ <input data-k="${k}" data-j="1" inputmode="decimal" placeholder="最高" value="${esc(r[1])}"></div>`}).join('');
 $('flt').querySelectorAll('input').forEach(e=>e.oninput=()=>{const k=e.dataset.k,j=+e.dataset.j;const r=S.f[k]||['',''];r[j]=e.value.trim();
  if(r[0]===''&&r[1]==='')delete S.f[k];else S.f[k]=r;e.parentNode.classList.toggle('on',!!S.f[k]);markPre();run()})}
function cnt(){$('nf').textContent=Object.keys(S.f).length}
function markPre(){cnt();const cur=JSON.stringify(S.f);$('pre').querySelectorAll('button').forEach((b,i)=>b.classList.toggle('on',JSON.stringify(norm(PRE[i][1]))===cur))}
const norm=o=>Object.fromEntries(Object.entries(o).map(([k,[a,b]])=>[k,[String(a),String(b)]]));
function rows(){const q=S.q.toLowerCase(),fs=Object.entries(S.f).map(([k,[a,b]])=>[IX[k],num(a),num(b)]);
 return R.filter(r=>{if(q&&!(r[IX.c]+r[IX.n]).toLowerCase().includes(q))return false;if(S.mk&&r[IX.m]!==S.mk)return false;
  if(S.ind&&r[IX.i]!==S.ind)return false;if(S.nw&&r[IX.w])return false;if(S.tp&&!r[IX.tpl])return false;
  for(const[i,a,b]of fs){const v=r[i];if(v==null)return false;if(a!=null&&v<a)return false;if(b!=null&&v>b)return false}return true})}
function run(){const out=rows(),i=IX[S.sort];out.sort((a,b)=>{const x=a[i],y=b[i];if(x==null)return 1;if(y==null)return -1;return(x<y?-1:x>y?1:0)*S.dir});
 $('n').textContent=out.length;$('cut').textContent=out.length>400?'（只列前 400 檔，匯出 CSV 有全部）':'';
 $('hd').innerHTML=T.map(([k,t])=>`<th class="${['c','n','i','w'].includes(k)?'l ':''}${k===S.sort?'s':''}" data-k="${k}">${t}${k===S.sort?(S.dir<0?' ▼':' ▲'):''}</th>`).join('');
 $('hd').querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(k==='w')return;S.dir=S.sort===k?-S.dir:(['c','n','i'].includes(k)?1:-1);S.sort=k;run()});
 $('bd').innerHTML=out.slice(0,400).map(r=>'<tr>'+T.map(([k,t,d,c])=>{const v=r[IX[k]];
  if(k==='c')return `<td class="l"><a href="stock.html?code=${esc(v)}">${esc(v)}</a></td>`;if(k==='n'||k==='i')return `<td class="l">${esc(v)}</td>`;
  if(k==='w')return `<td class="l">${v?`<span class="tag">${esc(v)}</span>`:''}</td>`;
  return `<td${c&&v?` class="${v>0?'up':'down'}"`:''}>${c&&v>0?'+':''}${fmt(v,d)}</td>`}).join('')+'</tr>').join('')||`<tr><td class="l" colspan="${T.length}">沒有符合的，放寬條件試試</td></tr>`;
 save();window._out=out}
function save(){const o={};if(Object.keys(S.f).length)o.f=JSON.stringify(S.f);for(const k of['q','mk','ind'])if(S[k])o[k]=S[k];if(!S.nw)o.nw='0';if(S.tp)o.tp='1';
 if(S.sort!=='cap'||S.dir!==-1)o.s=S.sort+(S.dir<0?'-':'+');const h=new URLSearchParams(o).toString();history.replaceState(null,'',h?'#'+h:location.pathname+location.search)}
function load(){try{const p=new URLSearchParams(location.hash.slice(1));if(p.get('f'))S.f=JSON.parse(p.get('f'));for(const k of['q','mk','ind'])S[k]=p.get(k)||'';
 S.nw=p.get('nw')==='0'?0:1;S.tp=p.get('tp')==='1'?1:0;const s=p.get('s');if(s&&IX[s.slice(0,-1)]!=null){S.sort=s.slice(0,-1);S.dir=s.endsWith('-')?-1:1}}catch(e){S.f={}}}
load();
$('more').open=matchMedia('(min-width:700px)').matches||Object.keys(S.f).length>0;
$('ind').innerHTML+=D.inds.map(x=>`<option>${esc(x)}</option>`).join('');
$('pre').innerHTML=PRE.map(([t,o,d])=>`<button title="${esc(d)}">${t}</button>`).join('');
$('pre').querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{S.f=norm(PRE[i][1]);drawF();markPre();run();$('pdesc').textContent=PRE[i][2]});
$('q').value=S.q;$('mk').value=S.mk;$('ind').value=S.ind;$('nw').checked=!!S.nw;$('tp').checked=!!S.tp;
$('q').oninput=e=>{S.q=e.target.value.trim();run()};$('mk').onchange=e=>{S.mk=e.target.value;run()};$('ind').onchange=e=>{S.ind=e.target.value;run()};
$('nw').onchange=e=>{S.nw=e.target.checked?1:0;run()};$('tp').onchange=e=>{S.tp=e.target.checked?1:0;run()};
$('clr').onclick=()=>{S.f={};S.q='';S.mk='';S.ind='';S.tp=0;$('q').value='';$('mk').value='';$('ind').value='';$('tp').checked=false;drawF();markPre();run()};
$('csv').onclick=()=>{const H=['代號','名稱','市場','產業','股價','今天%','5日%','20日%','60日%','一年%','成交量(張)','20日成交值(億)','市值(億)','本益比','殖利率%','淨值比',
 '營收年增%','累計營收年增%','營收月增%','RS','距一年高%','趨勢樣板','外資5日(張)','外資5日占量%','投信5日(張)','ROE3年%','營收3年CAGR%','注意處置','本益比5年位階','淨值比5年位階','殖利率5年位階'];
 const q=v=>v==null?'':/[",\\n]/.test(String(v))?'"'+String(v).replace(/"/g,'""')+'"':v;
 const s='\\ufeff'+[H.join(','),...window._out.map(r=>r.map(q).join(','))].join('\\n');
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([s],{type:'text/csv'}));a.download=`選股_${D.date}.csv`;a.click()};
drawF();markPre();run();
</script></body></html>"""


def write(site_dir: Path) -> bool:
    d = build()
    if d is None:
        return False
    page = (PAGE.replace("__DATE__", html.escape(d["date"])).replace("__RMONTH__", html.escape(d["rmonth"] or "—"))
            .replace("__DATA__", json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    (site_dir / "screen.html").write_text(page, "utf-8")
    log.info("自訂選股：%d 檔", len(d["rows"]))
    try:
        from . import mappage
        mappage.write(site_dir, d)
    except Exception as e:  # noqa: BLE001  地圖壞掉不影響選股頁
        log.warning("市場地圖失敗：%s", e)
    return True
