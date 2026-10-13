"""🧾 虛擬帳戶頁：site/ledger.html。

資料是 data/ledger/*.json（Cowork 每個交易日 15:47 收盤結算 → 協作板 ledger_daily → 本尊 16:25 存檔），只有虛擬帳戶。
- 資產曲線：帳戶淨值 vs 0050 含息（帳本的 bench_tr_cum），兩條都從起始 100 算
- 各桶績效：screener/ledger_nav.py（時間加權、扣費稅，各桶跟自己開張後的 0050 比）
- 目前持股、待執行的單、全部交易紀錄
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import ledger_nav

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "ledger"
BUCKET = {"core": "題材", "revdrift": "revdrift", "satellite": "衛星", "breakout": "突破", "etf": "0050 底倉"}
FILL = {"oddlot_after": "盤後零股", "close_fill": "收盤", "regular_open": "整股開盤", "regular_close": "整股收盤"}

CSS = """<style>
.viz{--s1:#2a78d6;--s2:#eb6834}
@media(prefers-color-scheme:dark){.viz{--s1:#3987e5;--s2:#d95926}}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat b{display:block;font-size:22px;line-height:1.3;font-variant-numeric:tabular-nums}.stat>span{font-size:13px;color:var(--muted)}
.legend{display:flex;gap:16px;font-size:14px;margin:4px 0}.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.chart{position:relative}.chart .plot{width:100%}.chart svg{display:block;overflow:visible}
.chart .tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px;line-height:1.5;white-space:nowrap;display:none;top:0;box-shadow:0 2px 8px rgba(0,0,0,.12)}.chart .tip i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.up{color:#c0392b}.dn{color:#1e8449}.meta{font-size:13px;color:var(--muted)}
td.wrap{white-space:normal;min-width:14em}
</style>"""


def _pct(v, d=2, sign=True):
    if v is None:
        return "—"
    s = f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"
    return f"<span class='{'up' if v > 0 else 'dn' if v < 0 else ''}'>{s}</span>"


def _money(v) -> str:
    return "" if v is None else f"{float(v):,.0f} 元"


def _chart(pts: list[dict]) -> str:
    """pts: [{date, acct, bench}]（起始 100）。單一 y 軸、兩條線、終點直接標示、滑過／手指點顯示當天數字。
    用 JS 照容器實際寬度畫（字是真的 12px，手機不會縮成看不到）；沒 JS 時下面的「看數字」表照樣看得到。"""
    data = json.dumps([{"d": p["date"], "a": round(p["acct"], 2), "b": round(p["bench"], 2)} for p in pts])
    js = """<script>(function(){var c=document.currentScript.parentNode,box=c.querySelector('.plot'),t=c.querySelector('.tip'),P=%s;
function f(v){return (v>=100?'+':'')+(v-100).toFixed(2)+'%%'}
function draw(){var W=box.clientWidth||600,H=Math.round(Math.min(300,Math.max(200,W*.5))),L=36,R=80,T=10,B=24;
var ys=[100];P.forEach(function(p){ys.push(p.a,p.b)});var lo=Math.min.apply(0,ys),hi=Math.max.apply(0,ys),pad=Math.max((hi-lo)*.12,.5);lo-=pad;hi+=pad;
var n=P.length,X=function(i){return L+(W-L-R)*(n>1?i/(n-1):0)},Y=function(v){return T+(H-T-B)*(hi-v)/(hi-lo)};
var st=[.5,1,2,2.5,5,10,20,50].filter(function(s){return (hi-lo)/s<=5})[0]||50,g=Math.ceil(lo/st)*st,o='';
for(;g<=hi;g+=st){o+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+Y(g)+'" y2="'+Y(g)+'" stroke="var(--line)"'+(Math.abs(g-100)<1e-9?' stroke-dasharray="4 3" stroke="var(--muted)"':'')+'/>'
+'<text x="'+(L-6)+'" y="'+(Y(g)+4)+'" text-anchor="end" font-size="12" fill="var(--muted)">'+(+g.toFixed(1))+'</text>'}
o+='<text x="'+X(0)+'" y="'+(H-6)+'" font-size="12" fill="var(--muted)">'+P[0].d.slice(5).replace('-','/')+'</text>';
if(n>1)o+='<text x="'+X(n-1)+'" y="'+(H-6)+'" text-anchor="end" font-size="12" fill="var(--muted)">'+P[n-1].d.slice(5).replace('-','/')+'</text>';
function ln(k,v){return '<path d="'+P.map(function(p,i){return (i?'L':'M')+X(i).toFixed(1)+','+Y(p[k]).toFixed(1)}).join(' ')+'" fill="none" stroke="var('+v+')" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'}
o+=ln('b','--s2')+ln('a','--s1');var e=P[n-1],ya=Y(e.a),yb=Y(e.b);
if(Math.abs(ya-yb)<16){var m=(ya+yb)/2;if(e.a>=e.b){ya=m-8;yb=m+8}else{ya=m+8;yb=m-8}}
o+='<circle cx="'+X(n-1)+'" cy="'+Y(e.a)+'" r="4" fill="var(--s1)" stroke="var(--bg)" stroke-width="2"/><circle cx="'+X(n-1)+'" cy="'+Y(e.b)+'" r="4" fill="var(--s2)" stroke="var(--bg)" stroke-width="2"/>'
+'<text x="'+(X(n-1)+8)+'" y="'+(ya+4)+'" font-size="12" fill="var(--fg)">帳戶 '+e.a.toFixed(1)+'</text><text x="'+(X(n-1)+8)+'" y="'+(yb+4)+'" font-size="12" fill="var(--fg)">0050 '+e.b.toFixed(1)+'</text>'
+'<line class="hair" y1="'+T+'" y2="'+(H-B)+'" stroke="var(--muted)" style="display:none"/><rect x="0" y="0" width="'+W+'" height="'+H+'" fill="transparent"/>';
box.innerHTML='<svg width="'+W+'" height="'+H+'" role="img" aria-label="虛擬帳戶淨值與 0050 含息，起始 100">'+o+'</svg>';
var s=box.firstChild,h=s.querySelector('.hair');
function show(ev){var r=s.getBoundingClientRect(),mx=(ev.touches?ev.touches[0].clientX:ev.clientX)-r.left,bi=0;
P.forEach(function(p,i){if(Math.abs(X(i)-mx)<Math.abs(X(bi)-mx))bi=i});var b=P[bi];h.setAttribute('x1',X(bi));h.setAttribute('x2',X(bi));h.style.display='';
t.innerHTML='<b>'+b.d+'</b><br><i style="background:var(--s1)"></i>帳戶 '+b.a.toFixed(2)+'（'+f(b.a)+'）<br><i style="background:var(--s2)"></i>0050 含息 '+b.b.toFixed(2)+'（'+f(b.b)+'）';
t.style.display='block';t.style.left=Math.min(Math.max(X(bi)-t.offsetWidth/2,0),W-t.offsetWidth)+'px'}
function hide(){t.style.display='none';h.style.display='none'}
s.addEventListener('mousemove',show);s.addEventListener('touchstart',show,{passive:true});s.addEventListener('touchmove',show,{passive:true});
s.addEventListener('mouseleave',hide);s.addEventListener('touchend',hide)}
draw();var rw;window.addEventListener('resize',function(){clearTimeout(rw);rw=setTimeout(draw,150)})})()</script>""" % data
    return ("<div class='viz'><div class='legend'><span><i style='background:var(--s1)'></i>虛擬帳戶</span>"
            "<span><i style='background:var(--s2)'></i>0050 含息</span></div>"
            f"<div class='chart'><div class='plot'></div><div class='tip'></div>{js}</div></div>")


def write(site_dir: Path) -> bool:
    files = sorted(LEDGER.glob("20??-??-??.json")) if LEDGER.exists() else []
    if not files:
        return False
    L = [json.loads(f.read_text("utf-8")) for f in files]
    lg = L[-1]
    names = {}
    for d in L:
        for p in d.get("positions") or []:
            names[str(p.get("code"))] = p.get("name")
        for t in d.get("trades") or []:
            names.setdefault(str(t.get("code")), t.get("name"))
    for q in lg.get("pending") or []:
        names.setdefault(str(q.get("code")), q.get("name"))

    def stock(c):
        c = str(c)
        return f"<a href='stock.html?code={html.escape(c)}'>{html.escape(c)}</a> {html.escape(str(names.get(c) or ''))}"

    pts = [{"date": d["date"], "acct": float(d["nav"]) * 100, "bench": (1 + float(d.get("bench_tr_cum") or 0) / 100) * 100}
           for d in L if d.get("nav") is not None]
    stats = (f"<div class='stats'><div class='stat'><span>總資產</span><b>{float(lg['equity']):,.0f}</b><span>起始 100,000</span></div>"
             f"<div class='stat'><span>累計</span><b>{_pct(lg.get('ret_cum'))}</b><span>同期 0050 含息 {_pct(lg.get('bench_tr_cum'))}</span></div>"
             f"<div class='stat'><span>{lg['date'][5:].replace('-', '/')} 當天</span><b>{_pct(lg.get('ret_today'))}</b><span>0050 含息 {_pct(lg.get('bench_tr_today'))}</span></div>"
             f"<div class='stat'><span>現金</span><b>{float(lg.get('cash') or 0):,.0f}</b><span>{len(lg.get('positions') or [])} 檔持股</span></div></div>")

    # 各桶
    nav = None
    try:
        nav = ledger_nav.compute(L)
    except Exception:  # noqa: BLE001
        nav = None
    if nav and nav["ok"]:
        rows = "".join(
            f"<tr><td>{html.escape(x['name'])}{'' if x['open'] else '<div class=meta>已結束</div>'}</td>"
            f"<td>{_pct(x['ret'])}</td><td>{_pct(x['bench'])}</td><td><b>{x['excess']:+.2f}</b></td>"
            f"<td>{x['start'][5:].replace('-', '/')}～{'' if x['open'] else x['end'][5:].replace('-', '/')}<div class='meta'>{x['days']} 天</div></td>"
            f"<td>{x['pnl']:+,.0f}</td><td>{x['value']:,.0f}</td></tr>"
            for b, x in nav["buckets"].items() if b != "cash")
        buckets = ("<h2>各桶 vs 同期 0050</h2><div class='tbl'><table><tr><th>桶</th><th>報酬</th><th>同期 0050</th><th>超額（點）</th>"
                   f"<th>期間</th><th>損益（元）</th><th>市值＋現金</th></tr>{rows}</table></div>"
                   "<p class='meta'>時間加權報酬、扣手續費和證交稅；桶跟桶之間撥款（例如賣 0050 撥給 revdrift）不算損益。"
                   "各桶的 0050 從自己開張那天收盤起算。0050 底倉那桶的超額＝零股滑價、費用、閒置現金的成本。"
                   "天數少的時候一天的漲跌就能翻盤，至少看滿 20 個交易日。</p>")
    else:
        buckets = "<h2>各桶 vs 同期 0050</h2><p class='meta'>帳本對帳沒過，這次先不列（screener/ledger_nav.py）。</p>"

    # 持股
    prow = []
    for p in sorted(lg.get("positions") or [], key=lambda p: (list(BUCKET).index(p.get("role")) if p.get("role") in BUCKET else 9, -float(p.get("mv") or 0))):
        cost, close = float(p.get("avg_cost") or 0), float(p.get("close") or 0)
        prow.append(f"<tr><td>{stock(p['code'])}<div class='meta'>{html.escape(BUCKET.get(p.get('role'), str(p.get('role'))))}・"
                    f"{str(p.get('opened') or '')[5:].replace('-', '/')} 買</div></td>"
                    f"<td>{_pct((close / cost - 1) * 100 if cost else None)}</td><td>{float(p.get('mv') or 0):,.0f}</td>"
                    f"<td>{cost:,.2f} → {close:,.2f}<div class='meta'>{p.get('shares'):,} 股</div></td>"
                    f"<td><div class='meta'>{html.escape(str(p.get('exit_by') or '—'))}</div></td></tr>")
    holds = ("<h2>目前持股</h2><div class='tbl'><table><tr><th>股票</th><th>漲跌</th><th>市值</th><th>成本 → 收盤</th><th>出場</th></tr>"
             f"{''.join(prow)}</table></div>"
             "<p class='meta'>漲跌＝收盤 ÷ 平均成本 − 1，還沒扣賣出的手續費和證交稅。</p>") if prow else ""

    pend = lg.get("pending") or []
    pending = ""
    if pend:
        pr = "".join(f"<tr><td>{'買' if q.get('side') == 'buy' else '賣'}</td><td>{stock(q.get('code'))}</td>"
                     f"<td>{html.escape(BUCKET.get(q.get('bucket'), str(q.get('bucket') or '')))}</td>"
                     f"<td>{_money(q.get('budget'))}</td>"
                     f"<td class='wrap'><div class='meta'>{html.escape(str(q.get('status') or q.get('reason') or ''))}</div></td></tr>" for q in pend)
        pending = ("<h2>待執行的單</h2><div class='tbl'><table><tr><th></th><th>股票</th><th>桶</th><th>預算</th><th>狀況</th></tr>"
                   f"{pr}</table></div>")

    trs = []
    for d in reversed(L):
        for t in d.get("trades") or []:
            pnl = t.get("pnl")
            trs.append(f"<tr><td>{d['date'][5:].replace('-', '/')}</td><td>{'買' if t.get('side') == 'buy' else '賣'}</td><td>{stock(t.get('code'))}</td>"
                       f"<td>{html.escape(BUCKET.get(t.get('bucket'), str(t.get('bucket') or '')))}</td>"
                       f"<td>{t.get('shares'):,}</td><td>{float(t.get('price') or 0):,.2f}<div class='meta'>{html.escape(FILL.get(t.get('fill_src'), str(t.get('fill_src') or '')))}</div></td>"
                       f"<td>{float(t.get('fee') or 0) + float(t.get('tax') or 0):,.0f}</td>"
                       f"<td>{'' if pnl is None else format(float(pnl), '+,.0f')}</td></tr>")
    trades = (f"<h2>交易紀錄（{len(trs)} 筆）</h2><div class='tbl'><table><tr><th>日期</th><th></th><th>股票</th><th>桶</th><th>股數</th>"
              f"<th>成交價</th><th>費稅</th><th>已實現損益</th></tr>{''.join(trs)}</table></div>") if trs else ""

    table = "".join(f"<tr><td>{p['date']}</td><td>{p['acct']:.2f}</td><td>{p['bench']:.2f}</td></tr>" for p in reversed(pts))
    body = (
        "<h1>🧾 虛擬帳戶</h1>"
        f"<p>10 萬元模擬帳戶，照系統規則買賣（題材選股、revdrift 營收漂移、0050 底倉；突破訊號 10/12 起暫停），"
        f"Cowork 每個交易日 15:47 用收盤後的成交價結算。<b>不是真的錢，也不含任何真實持股。</b>"
        f"資料到 {html.escape(lg['date'])}（{html.escape(str(lg.get('asof') or '')[11:16])} 結算）。</p>"
        f"{stats}<h2>資產曲線（起始 100）</h2>{_chart(pts) if len(pts) >= 2 else ''}"
        f"<details><summary>看數字</summary><div class='tbl'><table><tr><th>日期</th><th>帳戶</th><th>0050 含息</th></tr>{table}</table></div></details>"
        f"<p class='meta'>0050 含息＝同期買 0050 抱著、配息再投入（帳本 bench_tr_cum，從 {html.escape(str(lg.get('bench_base_date') or '')[:10])} 收盤起算）。"
        "帳戶累計包含 0050 底倉，選股有沒有用要看下面各桶自己的超額。</p>"
        f"{buckets}{holds}{pending}{trades}"
        "<p class='meta'>帳本：data/ledger/*.json（協作板 ledger_daily 每天收盤後存檔）。成交價：上市櫃股票用盤後零股成交價（加減滑價），0050 用盤後零股。</p>")
    from .weekly import _page
    (site_dir / "ledger.html").write_text(_page("虛擬帳戶", CSS + body, "<!--SITENAV:ledger-->"), "utf-8")
    return True
