"""首頁「今天重點」（Cowork 大題 C，10/7 23:57）：最多 6 行，每行一個數字＋一句話＋連結；沒事就寫「今天沒事」。

建站時（每日資料、06:20 美股、pages 重畫）先算好靜態部分放進 index.html（const TODAY）；
盤中（09:00–13:30）網頁自己讀 live 分支的 live.json 補即時部分（持倉現價、13:12 正式訊號）。
持倉＝系統正式訊號（data/positions.csv）；Cowork 的虛擬帳戶帳本不在 repo，先不放。
硬規定：不放 Dennis 真實持股、不放沒驗證過的策略名單（每日篩選 8 招、盤中預警都沒過 5 年驗證）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("today")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EX = DATA / "extras"


def _csv(p: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(p, dtype={"code": str}) if p.exists() else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def ledger_mix(lg: dict) -> dict | None:
    """虛擬帳戶組成（本尊 1345-cc-ledger0050）：role=core＝題材、role=etf 或 0050＝0050、其他＝突破格；現金＝cash（元）。"""
    eq = lg.get("equity")
    pos = lg.get("positions") or []
    if not eq or not pos:
        return None
    grp = {"brk": [], "core": [], "etf": []}
    for x in pos:
        k = "core" if x.get("role") == "core" else "etf" if x.get("role") == "etf" or x.get("code") == "0050" else "brk"
        grp[k].append(float(x.get("mv") or 0))
    pct = lambda v: round(v / eq * 100, 1)
    return {"brk_n": len(grp["brk"]), "brk_pct": pct(sum(grp["brk"])), "core_n": len(grp["core"]),
            "core_pct": pct(sum(grp["core"])), "etf_pct": pct(sum(grp["etf"])), "cash_pct": pct(float(lg.get("cash") or 0))}


def _sys_stats(pos: pd.DataFrame) -> dict | None:
    """系統訊號合計（Cowork 0427、分身 0415-cc-ac）：主數字＝全部訊號（持有中按最新收盤估、已出場用實際出場），
    扣 0.38%；並列同期 0050 含息（訊號日收盤 → 出場訊號日收盤／最新收盤，近似）與超額；照訊號日收盤是否鎖漲停（close_lu）拆兩組。"""
    p = pos.copy()
    est = pd.to_numeric(p.est_return_pct, errors="coerce")
    est = est.where(est.notna() | (p.status != "open"), 0.0)   # 今天才出的訊號：進場價＝今天收盤，浮動 0
    p = p[est.notna()].copy()
    if p.empty:
        return None
    p["ret"] = est[p.index] - 0.38
    tr = pd.Series(dtype=float)
    try:
        b = pd.read_csv(EX / "bench.csv", dtype={"code": str})
        tr = b[b.code.isin(["0050", "50"]) & b.tr.notna()].set_index("date").tr.astype(float).sort_index()
    except Exception:  # noqa: BLE001
        pass
    if len(tr):
        idx = list(tr.index)

        def at(d):   # 當天或之前最近一個交易日的 tr（日期是字串，不能用 Series.asof）
            if not isinstance(d, str) or d < idx[0]:
                return np.nan
            return float(tr.iloc[np.searchsorted(idx, d, side="right") - 1])
        end = p.exit_signal_date.where(p.exit_signal_date.notna(), idx[-1])
        t0, t1 = p.signal_date.map(at), end.map(at)
        p["bench"] = (t1 / t0 - 1) * 100
    else:
        p["bench"] = np.nan
    lu = pd.to_numeric(p.get("close_lu"), errors="coerce")
    f = lambda x: None if x is None or x != x else round(float(x), 2)
    out = {"n": int(len(p)), "avg": f(p.ret.mean()), "med": f(p.ret.median()), "bench": f(p.bench.mean()),
           "ex": f((p.ret - p.bench).mean()), "n_open": int((p.status == "open").sum()),
           "n_lu": int((lu == 1).sum()), "n_nolu": int((lu == 0).sum()),
           "lu_avg": f(p.ret[lu == 1].mean()), "nolu_avg": f(p.ret[lu == 0].mean()),
           "since": str(p.signal_date.min())}
    out["win"] = int(round(float((p.ret > 0).mean()) * 100)) if len(p) >= 30 else None
    return out


def build() -> dict:
    out: dict = {}
    try:
        r = json.loads((DATA / "macro" / "regime.json").read_text("utf-8"))
        out["regime"] = {k: r.get(k) for k in ("date", "light", "light_since", "r2", "r3", "dd52", "stale_core")}
    except Exception:  # noqa: BLE001
        pass
    try:   # 盤勢主判：全市場等權指數 vs 200 日線（screener/ewindex.py）
        out["ew"] = json.loads((DATA / "extras" / "ew_state.json").read_text("utf-8"))
    except Exception:  # noqa: BLE001
        pass
    us = _csv(DATA / "us" / "history.csv.gz").rename(columns={"sym": "code"}) if (DATA / "us" / "history.csv.gz").exists() else pd.DataFrame()
    if len(us):
        for sym, key in (("^SOX", "sox"), ("TSM", "tsm")):
            g = us[us.code == sym].sort_values("date")
            if len(g) >= 2:
                out[key] = {"date": g.date.iloc[-1], "chg": round(float(g.close.iloc[-1] / g.close.iloc[-2] - 1) * 100, 2)}
    h = _csv(DATA / "history.csv.gz")
    last_close = {}
    if len(h):
        out["tw_date"] = h.date.max()
        last_close = h[h.date == h.date.max()].set_index("code").close.to_dict()
        if "tsm" in out and "2330" in last_close:
            out["tsm"]["tw2330"] = round(float(last_close["2330"]) * (1 + out["tsm"]["chg"] / 100), 1)
    pos = _csv(DATA / "positions.csv")
    hold, exits, today_sig = [], [], []
    if len(pos):
        op = pos[pos.status == "open"]
        for r in op.itertuples():
            row = {"code": r.code, "name": r.name, "signal_date": r.signal_date, "entry": float(r.entry_price),
                   "days": None if pd.isna(r.days_held) else int(float(r.days_held)),
                   "ret": None if pd.isna(r.est_return_pct) else float(r.est_return_pct)}
            hold.append(row)
            if r.signal_date == out.get("tw_date"):
                today_sig.append(row)
        # 出場訊號已出、還沒到隔天開盤賣（positions.py：status=closed、exit_open 空）
        for r in pos[(pos.status == "closed") & pos.exit_open.isna() & pos.exit_signal_date.notna()].itertuples():
            exits.append({"code": r.code, "name": r.name, "exit_reason": r.exit_reason if isinstance(r.exit_reason, str) else ""})
        # 已出場（隔天開盤賣掉、exit_open 有值）的成績，扣 0.38% 成本。持倉平均只看活著的，輸的被量縮規則先洗出去，
        # 單看持倉會偏好看（分身 1415-cc-ac：10/1～10/6 持倉 17 筆 +8.05%、已出場 8 筆 −2.04%）
        done = pos[(pos.status == "closed") & pos.exit_open.notna()]   # 真的賣掉的；明天開盤才賣的另一行（🚪）列
        r = pd.to_numeric(done.est_return_pct, errors="coerce").dropna() - 0.38
        if len(r):
            out["closed"] = {"n": int(len(r)), "avg": round(float(r.mean()), 2), "med": round(float(r.median()), 2),
                             "win": int(round(float((r > 0).mean()) * 100)) if len(r) >= 30 else None}
        out["sys"] = _sys_stats(pos)
        try:   # 雜訊帶（tools/ledger_band.py → sys_band.json）：k＝首筆訊號到最新交易日的交易日數
            if out["sys"] and len(h):
                bd = json.loads((EX / "sys_band.json").read_text("utf-8"))["band"]
                days = sorted(d for d in h.date.unique() if out["sys"]["since"] <= d <= out["tw_date"])
                k = len(days)
                ks = sorted(int(x) for x in bd)
                kk = min(max(k, ks[0]), ks[-1])
                lo_k = max(x for x in ks if x <= kk)
                hi_k = min(x for x in ks if x >= kk)
                w = 0 if hi_k == lo_k else (kk - lo_k) / (hi_k - lo_k)
                lerp = lambda f: round(bd[str(lo_k)][f] * (1 - w) + bd[str(hi_k)][f] * w, 1)
                out["sys"].update(k=k, band_lo=lerp("p5"), band_hi=lerp("p95"))
        except Exception as e:  # noqa: BLE001
            log.warning("雜訊帶讀不到：%s", e)
    out.update({"hold": hold, "exits": exits, "today_sig": today_sig})
    # Cowork 虛擬帳戶（協作板 ledger_daily → 本尊 16:25 commit 成 data/ledger/YYYY-MM-DD.json，Cowork 0027 格式）
    lf = sorted((DATA / "ledger").glob("20??-??-??.json")) if (DATA / "ledger").exists() else []
    if lf:
        try:
            lg = json.loads(lf[-1].read_text("utf-8"))
            out["ledger"] = {k: lg.get(k) for k in ("date", "asof", "nav", "ret_cum", "ret_today", "bench_tr_cum", "bench_tr_today",
                                                    "pending", "exits_tomorrow", "core_ret_cum", "bench_base_date")}
            out["ledger"]["n_pos"] = len(lg.get("positions") or [])
            out["ledger"]["names"] = {x.get("code"): x.get("name") for x in lg.get("positions") or []}
            out["ledger"]["mix"] = ledger_mix(lg)
        except Exception as e:  # noqa: BLE001
            log.warning("帳本讀不到：%s", e)
    watch = {x["code"] for x in hold}
    top = {}
    try:
        top = {p["code"]: p["name"] for p in json.loads((DATA / "longterm" / "top20.json").read_text("utf-8"))["picks"]}
    except Exception:  # noqa: BLE001
        pass
    names = {**top, **{x["code"]: x["name"] for x in hold}}
    ev = []
    exu = _csv(EX / "exdiv_upcoming.csv")
    if len(exu) and out.get("tw_date"):
        x = exu[exu.code.isin(watch | set(top)) & (exu.date > out["tw_date"])].sort_values("date")
        days = sorted(x.date.unique())[:2]
        for r in x[x.date.isin(days)].itertuples():
            ev.append({"date": r.date, "code": r.code, "name": names.get(r.code, r.code), "kind": f"除{r.kind}",
                       "note": f"現金 {r.cash_div:g} 元" if r.cash_div else ""})
    wn = _csv(EX / "warnings.csv")
    if len(wn):
        for r in wn[wn.code.isin(watch | set(top))].itertuples():
            ev.append({"date": "", "code": r.code, "name": names.get(r.code, r.code), "kind": str(r.flag), "note": ""})
    out["events"] = ev
    return out


TEMPLATE_HTML = """<section class="today" id="today" hidden></section>"""
CSS = """.today{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:6px 0 14px}
.today h2{font-size:15px;margin:0 0 6px;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.today h2 span{font-size:12px;color:var(--muted);font-weight:400}
.today .row{display:flex;gap:10px;align-items:baseline;padding:5px 0;border-top:1px dashed var(--line);font-size:14px;line-height:1.5}
.today .row:first-of-type{border-top:0}.today .row .ic{flex:none;width:20px;text-align:center}
.today .row .tx{flex:1;min-width:0}.today .row .ts{flex:none;font-size:11px;color:var(--muted)}
.today .row.stale{opacity:.45}.today b.num{font-variant-numeric:tabular-nums}
.today a{color:inherit;text-decoration:underline;text-decoration-color:rgba(128,128,128,.5);text-underline-offset:2px}
.today .tools{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid var(--line)}
.today .tools a{text-decoration:none;font-size:13px;padding:3px 10px;border-radius:999px;background:rgba(128,128,128,.12)}"""
JS = r"""
(function(){const T=TODAY||{};const el=document.getElementById('today');if(!el)return;
const repo=location.hostname.endsWith('github.io')?location.hostname.split('.')[0]+'/'+(location.pathname.split('/').filter(Boolean)[0]||''):'stevenhsiehsheng-ctrl/tw-stock-screener';
const now=new Date(),tw=new Date(now.toLocaleString('en-US',{timeZone:'Asia/Taipei'})),hm=tw.getHours()*60+tw.getMinutes(),wd=tw.getDay();
const iso=`${tw.getFullYear()}-${String(tw.getMonth()+1).padStart(2,'0')}-${String(tw.getDate()).padStart(2,'0')}`;
const wk=wd===0||wd===6,mode=wk?'week':hm<540?'pre':hm<810?'live':'post';
const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const P=v=>v==null?'—':(v>0?'+':'')+Number(v).toFixed(2)+'%',S=c=>`stock.html?code=${encodeURIComponent(c)}`;
const L={green:'🟢 綠燈',yellow:'🟡 黃燈',red:'🔴 紅燈'};
const rows=[];const add=(ic,html,ts,stale)=>rows.push(`<div class="row${stale?' stale':''}"><span class="ic">${ic}</span><span class="tx">${html}</span><span class="ts">${E(ts||'')}</span></div>`);
function regime(){const r=T.regime,w=T.ew;
  if(r)add('🚦',`大盤燈號 <b>${L[r.light]||E(r.light)}</b>（${E(r.light_since||'')} 起）${r.stale_core&&r.stale_core.length?'・<b>資料落後</b>':''}${w&&w.above?`・等權指數在 200 日線上 <b class="num">${P(w.dev)}</b>`:''} <a href="macro.html">大環境 →</a>`,r.date);
  if(w&&!w.above)add('🌧',`<b>盤勢線下</b>：全市場等權指數收在 200 日線下 <b class="num">${P(w.dev)}</b>（${E(String(w.since).slice(5))} 起 ${w.streak} 天）。線下沒有可靠的超額（月t 為負）、樣本不足，只提醒、部位不自動砍 <a href="macro.html#ew">看圖 →</a>`,w.judged_on)}
function overnight(){const s=T.sox,t=T.tsm;if(!s&&!t)return;add('🌙',`昨夜費半 <b class="num">${P(s&&s.chg)}</b>${t?`・台積電 ADR <b class="num">${P(t.chg)}</b>${t.tw2330?`（換算台積電約 <b class="num">${t.tw2330}</b>）`:''}`:''} <a href="us.html">美股隔夜 →</a>`,(s||t).date)}
function ledgerLine(kind){const G=T.ledger;if(!G)return false;const N=G.names||{};
  if(kind==='pre'){const Q=G.pending||[];add('🧾',Q.length?`虛擬帳戶今天要執行 <b>${Q.length}</b> 筆：${Q.slice(0,4).map(q=>`${q.side==='buy'?'買':'賣'} <a href="${S(q.code)}">${E(N[q.code]||q.code)}</a>`).join('、')}`:'虛擬帳戶今天沒有要執行的單',G.date);return true}
  add('🧾',`虛擬帳戶今天 <b class="num">${P(G.ret_today)}</b>（0050 含息 ${P(G.bench_tr_today)}）・累計 <b class="num">${P(G.ret_cum)}</b>${G.core_ret_cum!=null?`（核心選股 ${P(G.core_ret_cum)}）`:''} vs 0050 ${P(G.bench_tr_cum)}${G.bench_base_date?`（從 ${E(String(G.bench_base_date).slice(5))} 收盤起算）`:''}・持倉 ${G.n_pos} 檔`+(G.mix?`<br><span style="font-size:13px;opacity:.8">組成：突破 ${G.mix.brk_n}/10 格 ${G.mix.brk_pct}%｜題材 ${G.mix.core_n} 檔 ${G.mix.core_pct}%｜0050 ${G.mix.etf_pct}%｜現金 ${G.mix.cash_pct}%</span>`:'')+((G.exits_tomorrow||[]).length?`・明天要出場 ${G.exits_tomorrow.map(c=>`<a href="${S(c)}">${E(N[c]||c)}</a>`).join('、')}`:''),G.date);return true}
function holdLine(H,ts,stale){if(!H||!H.length){add('💼','系統訊號持倉：<b>0</b> 檔',ts,stale);return}
  const r=H.filter(x=>x.ret!=null),avg=r.length?r.reduce((a,x)=>a+x.ret,0)/r.length:null,worst=r.slice().sort((a,b)=>a.ret-b.ret)[0];
  const C=T.closed,Y=T.sys;
  const head=Y&&Y.n?`系統訊號（非虛擬帳戶，${E(String(Y.since).slice(5))} 起）合計 <b class="num">${Y.n}</b> 筆：平均 <b class="num">${P(Y.avg)}</b>、中位 ${P(Y.med)}${Y.win!=null?`、賺錢 ${Y.win}%`:''}；同期 0050 含息 ${P(Y.bench)}、超額 <b class="num">${P(Y.ex)}</b>`
    :`系統訊號持倉（非虛擬帳戶）<b class="num">${H.length}</b> 檔・浮動平均 <b class="num">${P(avg)}</b>`;
  const inBand=Y&&Y.ex!=null&&Y.ex>=Y.band_lo&&Y.ex<=Y.band_hi;
  const band=Y&&Y.k&&Y.band_lo!=null&&Y.ex!=null?`<br><span style="font-size:13px">第 ${Y.k} 個交易日：歷史同天數的超額 5～95% 帶 <b class="num">${P(Y.band_lo)}～${P(Y.band_hi)}</b>，`+
    (inBand?'<b>在帶內＝雜訊，不下結論</b>。':`<b>超出帶外</b>，但${Y.k<60?'未滿 60 個交易日一律不下結論':'要先用最新資料重驗'}。`)+
    (Y.k<60?'滿 60 個交易日（帶寬約 ±1.5pp）才第一次能看。':'')+'</span>':'';
  const sub=[`持有中 ${H.length} 檔${worst?`（最差 <a href="${S(worst.code)}">${E(worst.name)}</a> ${P(worst.ret)}）`:''}`,
    C&&C.n?`已出場 ${C.n} 筆 ${P(C.avg)}（早出場先天偏負：回測 1～3 天出場平均 −2.8%）`:'',
    Y&&(Y.n_lu||Y.n_nolu)?`收盤鎖漲停 ${Y.n_lu} 筆 ${P(Y.lu_avg)}（排隊多半排不到）、沒鎖 ${Y.n_nolu} 筆 ${P(Y.nolu_avg)}（回測超額 −0.3%）`:''].filter(Boolean);
  add('💼',head+band+`<br><span style="font-size:13px;opacity:.8">${sub.join('｜')}。持有中按收盤估、扣 0.38%。</span>`,ts,stale)}
function exitsLine(when){const X=T.exits||[];if(!X.length)return;const U=[];X.forEach(x=>{const u=U.find(y=>y.code===x.code);u?u.n++:U.push({code:x.code,name:x.name,n:1})});
  add('🚪',`${when}開盤要賣 <b>${X.length}</b> 筆：${U.slice(0,4).map(x=>`<a href="${S(x.code)}">${E(x.name)}</a>${x.n>1?'×'+x.n:''}`).join('、')}${U.length>4?' 等':''}`,T.tw_date)}
function eventsLine(){const V=T.events||[];if(!V.length)return;add('📌',V.slice(0,4).map(v=>`${v.date?E(v.date.slice(5))+' ':''}<a href="${S(v.code)}">${E(v.name)}</a> ${E(v.kind)}${v.note?'（'+E(v.note)+'）':''}`).join('、')+(V.length>4?` 等 ${V.length} 件`:'')+'（只列持倉與長期 Top 20）',T.tw_date)}
function sigLine(list,ts,stale){if(!list||!list.length)return;add('🎯',`13:12 訊號（<b>只記錄</b>：鎖漲停的排不到、沒鎖的歷史超額為負）<b>${list.length}</b> 檔：${list.slice(0,4).map(x=>`<a href="${S(x.code)}">${E(x.name)}</a>`).join('、')}${list.length>4?' 等':''} <a href="live.html">盤中 →</a>`,ts,stale)}
function draw(title){el.innerHTML=`<h2>📍 今天重點<span>${title}</span></h2>`+(rows.length?rows.slice(0,6).join(''):'<div class="row"><span class="ic">✅</span><span class="tx">今天沒事</span></div>')+'<div id="wlrow"></div>'+TOOLS;el.hidden=false;watch()}
const TOOLS='<div class="tools">'+[['screen.html','🧮 自訂選股'],['map.html','🗺 市場地圖'],['etf.html','🧺 ETF 專區'],['compare.html','⚖ 個股比較'],['watch.html','⭐ 我的自選']].map(([h,t])=>`<a href="${h}">${t}</a>`).join('')+'</div>';
// 自選股（存在這台瀏覽器；個股頁按 ☆ 加入）：最近收盤漲跌、今天上榜的策略、處置／除權息
async function watch(){let W=[];try{W=JSON.parse(localStorage.getItem('watchlist')||'[]')}catch(e){}if(!W.length)return;
  const got=await Promise.all(W.slice(0,20).map(([c])=>fetch(`stocks/${encodeURIComponent(c)}.json`).then(r=>r.ok?r.json():null).catch(()=>null)));
  const xs=got.filter(Boolean);if(!xs.length)return;xs.sort((a,b)=>Math.abs(b.chg||0)-Math.abs(a.chg||0));
  const tag=o=>[o.warn?`⚠️${E(o.warn)}`:'',(o.strats||[]).some(x=>x[0]===o.asof)?'📊上榜':'',o.exdiv_next&&o.exdiv_next.length?`💰${E(o.exdiv_next[0][0].slice(5))}除${E(o.exdiv_next[0][1])}`:''].filter(Boolean).join(' ');
  document.getElementById('wlrow').innerHTML=`<div class="row"><span class="ic">⭐</span><span class="tx">自選 <b>${xs.length}</b> 檔：${xs.slice(0,6).map(o=>`<a href="${S(o.code)}">${E(o.name)}</a> <b class="num">${P(o.chg)}</b>${tag(o)?' '+tag(o):''}`).join('、')}</span><span class="ts">${E(xs[0].last||'')}</span></div>`}
if(mode==='live'){fetch(`https://raw.githubusercontent.com/${repo}/live/live.json?t=${Date.now()}`,{cache:'no-store'}).then(r=>r.json()).then(J=>{
  const fresh=J.date===iso,age=fresh?(tw-new Date(J.updated.replace(' ','T')))/6e4:999,stale=!fresh||age>30;
  regime();const m=J.market||{};if(fresh)add('📈',`上漲家數 <b class="num">${m.up_pct!=null?m.up_pct.toFixed(0)+'%':'—'}</b>・漲停 ${m.limit_up??'—'} 家・${E(m.level||'')} <a href="live.html">盤中 →</a>`,J.updated.slice(11,16),stale);
  const H=(J.holdings||[]).map(h=>({code:h.code,name:h.name,ret:h.ret}));holdLine(fresh?H:T.hold,fresh?J.updated.slice(11,16):T.tw_date,stale);
  const near=(J.holdings||[]).filter(h=>h.drop_alert||/縮/.test(h.status||''));if(fresh&&near.length)add('⚠️',`要留意：${near.slice(0,3).map(h=>`<a href="${S(h.code)}">${E(h.name)}</a> ${E(h.status||'')} ${P(h.ret)}`).join('、')}`,J.updated.slice(11,16),stale);
  if(fresh&&J.official&&J.official.stocks)sigLine(J.official.stocks,J.official.time,stale);
  eventsLine();draw(`盤中・${fresh?J.updated.slice(11,16)+' 更新':'盤中監控還沒有今天的資料'}`)}).catch(()=>{regime();holdLine(T.hold,T.tw_date);eventsLine();draw('盤中・讀不到即時資料')});return}
regime();overnight();
if(mode==='pre'){ledgerLine('pre');exitsLine('今天');holdLine(T.hold,T.tw_date);eventsLine();draw('盤前')}
else if(mode==='post'){sigLine(T.today_sig,T.tw_date);ledgerLine('post');holdLine(T.hold,T.tw_date);exitsLine('明天');eventsLine();draw(T.tw_date===iso?'盤後':'盤後・今天的收盤資料還沒進來')}
else{ledgerLine('post');holdLine(T.hold,T.tw_date);eventsLine();draw('週末')}
})();
"""
