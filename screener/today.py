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


def build() -> dict:
    out: dict = {}
    try:
        r = json.loads((DATA / "macro" / "regime.json").read_text("utf-8"))
        out["regime"] = {k: r.get(k) for k in ("date", "light", "light_since", "r2", "r3", "dd52", "stale_core")}
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
        done = pos[pos.status == "closed"]   # 含出場訊號已出、明天開盤才賣的（報酬先用收盤估）
        r = pd.to_numeric(done.est_return_pct, errors="coerce").dropna() - 0.38
        if len(r):
            out["closed"] = {"n": int(len(r)), "avg": round(float(r.mean()), 2), "med": round(float(r.median()), 2),
                             "win": int(round(float((r > 0).mean()) * 100))}
    out.update({"hold": hold, "exits": exits, "today_sig": today_sig})
    # Cowork 虛擬帳戶（協作板 ledger_daily → 本尊 16:25 commit 成 data/ledger/YYYY-MM-DD.json，Cowork 0027 格式）
    lf = sorted((DATA / "ledger").glob("20??-??-??.json")) if (DATA / "ledger").exists() else []
    if lf:
        try:
            lg = json.loads(lf[-1].read_text("utf-8"))
            out["ledger"] = {k: lg.get(k) for k in ("date", "asof", "nav", "ret_cum", "ret_today", "bench_tr_cum", "bench_tr_today",
                                                    "pending", "exits_tomorrow")}
            out["ledger"]["n_pos"] = len(lg.get("positions") or [])
            out["ledger"]["names"] = {x.get("code"): x.get("name") for x in lg.get("positions") or []}
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
.today a{color:inherit;text-decoration:underline;text-decoration-color:rgba(128,128,128,.5);text-underline-offset:2px}"""
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
function regime(){const r=T.regime;if(!r)return;add('🚦',`大盤燈號 <b>${L[r.light]||E(r.light)}</b>（${E(r.light_since||'')} 起）${r.stale_core&&r.stale_core.length?'・<b>資料落後</b>':''} <a href="macro.html">大環境 →</a>`,r.date)}
function overnight(){const s=T.sox,t=T.tsm;if(!s&&!t)return;add('🌙',`昨夜費半 <b class="num">${P(s&&s.chg)}</b>${t?`・台積電 ADR <b class="num">${P(t.chg)}</b>${t.tw2330?`（換算台積電約 <b class="num">${t.tw2330}</b>）`:''}`:''} <a href="us.html">美股隔夜 →</a>`,(s||t).date)}
function ledgerLine(kind){const G=T.ledger;if(!G)return false;const N=G.names||{};
  if(kind==='pre'){const Q=G.pending||[];add('🧾',Q.length?`虛擬帳戶今天要執行 <b>${Q.length}</b> 筆：${Q.slice(0,4).map(q=>`${q.side==='buy'?'買':'賣'} <a href="${S(q.code)}">${E(N[q.code]||q.code)}</a>`).join('、')}`:'虛擬帳戶今天沒有要執行的單',G.date);return true}
  add('🧾',`虛擬帳戶今天 <b class="num">${P(G.ret_today)}</b>（0050 含息 ${P(G.bench_tr_today)}）・累計 <b class="num">${P(G.ret_cum)}</b> vs 0050 ${P(G.bench_tr_cum)}・持倉 ${G.n_pos} 檔`+((G.exits_tomorrow||[]).length?`・明天要出場 ${G.exits_tomorrow.map(c=>`<a href="${S(c)}">${E(N[c]||c)}</a>`).join('、')}`:''),G.date);return true}
function holdLine(H,ts,stale){if(!H||!H.length){add('💼','系統訊號持倉：<b>0</b> 檔',ts,stale);return}
  const r=H.filter(x=>x.ret!=null),avg=r.length?r.reduce((a,x)=>a+x.ret,0)/r.length:null,worst=r.slice().sort((a,b)=>a.ret-b.ret)[0];
  const C=T.closed;
  add('💼',`系統訊號持倉（非虛擬帳戶）<b class="num">${H.length}</b> 檔・浮動平均 <b class="num">${P(avg)}</b>${worst?`・最差 <a href="${S(worst.code)}">${E(worst.name)}</a> <b class="num">${P(worst.ret)}</b>`:''}`+
   (C&&C.n?`<br><span style="font-size:13px;opacity:.8">已出場 <b class="num">${C.n}</b> 筆（含明天開盤要賣的）：平均 <b class="num">${P(C.avg)}</b>、中位 ${P(C.med)}、賺錢 ${C.win}%（扣 0.38% 成本）。持倉中的是浮動，輸的會被量縮規則先賣掉，所以持倉平均會偏好看。</span>`:''),ts,stale)}
function exitsLine(when){const X=T.exits||[];if(!X.length)return;add('🚪',`${when}開盤要賣 <b>${X.length}</b> 檔：${X.slice(0,4).map(x=>`<a href="${S(x.code)}">${E(x.name)}</a>`).join('、')}${X.length>4?' 等':''}`,T.tw_date)}
function eventsLine(){const V=T.events||[];if(!V.length)return;add('📌',V.slice(0,4).map(v=>`${v.date?E(v.date.slice(5))+' ':''}<a href="${S(v.code)}">${E(v.name)}</a> ${E(v.kind)}${v.note?'（'+E(v.note)+'）':''}`).join('、')+(V.length>4?` 等 ${V.length} 件`:'')+'（只列持倉與長期 Top 20）',T.tw_date)}
function sigLine(list,ts,stale){if(!list||!list.length)return;add('🎯',`13:12 訊號（<b>觀察</b>，5 年回測買得到的那 4 成平均 −0.4%）<b>${list.length}</b> 檔：${list.slice(0,4).map(x=>`<a href="${S(x.code)}">${E(x.name)}</a>`).join('、')}${list.length>4?' 等':''} <a href="live.html">盤中 →</a>`,ts,stale)}
function draw(title){el.innerHTML=`<h2>📍 今天重點<span>${title}</span></h2>`+(rows.length?rows.slice(0,6).join(''):'<div class="row"><span class="ic">✅</span><span class="tx">今天沒事</span></div>')+'<div id="wlrow"></div>';el.hidden=false;watch()}
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
