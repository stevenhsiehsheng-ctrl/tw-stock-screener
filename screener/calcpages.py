"""存股族的三個試算頁（純前端計算，不抓資料）：site/calc/tax.html 股利所得稅、nhi.html 二代健保補充保費、goal.html 存股目標。

給 Google 搜尋帶人進來，頁尾連到配息頁與付費試算表。只算數字，不給任何買賣建議。
稅率級距、補充保費規定每年要核對一次：改 RULES 和 CHECKED。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .divpages import CSS, DISCLAIMER, SITE_URL, _cfg, _cta

CHECKED = "2026-10"   # 規定最後核對的年月
# 綜所稅級距：(上限, 稅率, 累進差額)；所得年度 → 表（cpacorey.com/income-tax-level-2026、財政部公告）
RULES = {
    "brackets": {
        "2025": [[590000, 0.05, 0], [1330000, 0.12, 41300], [2660000, 0.20, 147700], [4980000, 0.30, 413700], [None, 0.40, 911700]],
        "2026": [[610000, 0.05, 0], [1380000, 0.12, 42700], [2770000, 0.20, 153100], [5190000, 0.30, 430100], [None, 0.40, 949100]],
    },
    "credit_rate": 0.085, "credit_cap": 80000, "separate_rate": 0.28,
    "nhi_rate": 0.0211, "nhi_min": 20000, "nhi_max": 10000000,
}

PAGES = {
    "tax": ("股利所得稅試算：合併計稅還是分開計稅比較划算？",
            "輸入股利和其他所得淨額，算出合併計稅（8.5% 可抵減，上限 8 萬）和分開計稅（28%）各要繳多少稅。",
            """<label>所得年度 <select id=yr><option value=2025>2025（115 年 5 月申報）</option><option value=2026>2026（116 年 5 月申報）</option></select></label>
<label>股利所得（全年現金股利合計，元）<input id=div type=number min=0 value=200000></label>
<label>不含股利的綜合所得淨額（已扣免稅額、扣除額，元）<input id=net type=number min=0 value=800000></label>
<div id=out class=kpi></div>
<h2>怎麼算</h2>
<p><b>合併計稅：</b>股利併入所得，用累進稅率算出稅額，再減掉股利 × 8.5% 的可抵減稅額（每戶上限 8 萬元，抵完還有剩可以退稅）。</p>
<p><b>分開計稅：</b>其他所得照累進稅率算，股利另外 × 28%。</p>
<p>大致上適用稅率 20% 以下選合併比較省，40% 選分開比較省，30% 要實際算。報稅時財政部系統也會自動幫你選有利的方式。</p>
<h2>常見問題</h2>
<p><b>ETF 配息都算股利所得嗎？</b>不一定。ETF 的配息會拆成股利、利息、收益平準金等項目，只有股利所得適用這裡的算法，以扣繳憑單為準。</p>
<p><b>8.5% 抵減上限是每人還是每戶？</b>每一申報戶 8 萬元。</p>""",
            """function tax(n,b){for(const[c,r,d]of b){if(c===null||n<=c)return Math.max(0,n*r-d)}}
function run(){const b=R.brackets[$('yr').value],d=+$('div').value||0,n=+$('net').value||0;
const comb=tax(n+d,b)-Math.min(d*R.credit_rate,R.credit_cap),sep=tax(n,b)+d*R.separate_rate,best=comb<=sep?'合併計稅':'分開計稅';
$('out').innerHTML=box('合併計稅應納（負數＝退稅）',f(comb))+box('分開計稅應納',f(sep))+box('比較省的是',best)+box('差多少',f(Math.abs(comb-sep)))}"""),
    "nhi": ("二代健保補充保費試算：領股息會被扣多少？",
            "單次領到 2 萬元以上的股利要扣 2.11% 補充保費。輸入持股和每次配息，算出每次和全年會被扣多少。",
            """<label>持有股數 <input id=sh type=number min=0 value=20000></label>
<label>每次每股配息（元）<input id=cash type=number min=0 step=0.001 value=1.2></label>
<label>一年配幾次 <select id=n><option>1</option><option>2</option><option selected>4</option><option>12</option></select></label>
<div id=out class=kpi></div>
<h2>規則</h2>
<p>單次給付的股利達 2 萬元，就按全額扣 2.11% 補充保費（單次上限 1,000 萬元）；未滿 2 萬元不扣。是看「單次」金額，不是全年合計，所以同樣的年股息，月配或季配每次金額較小，可能不用扣。</p>
<p>衛福部正在研議改成全年合併計算，若修法通過會改變結果，請以健保署公告為準。</p>
<h2>常見問題</h2>
<p><b>零股也算嗎？</b>算，看的是那一次發給你的股利總額。</p>
<p><b>有沒有不用扣的身分？</b>有，例如投保身分為第 1 類被保險人的雇主、低收入戶等，以健保署規定為準。</p>""",
            """function run(){const one=(+$('sh').value||0)*(+$('cash').value||0),n=+$('n').value,
fee=one>=R.nhi_min?Math.min(one,R.nhi_max)*R.nhi_rate:0;
$('out').innerHTML=box('每次領到',f(one))+box('每次扣補充保費',f(fee))+box('全年扣',f(fee*n))+
box('單次再少多少就不用扣',one>=R.nhi_min?f(one-R.nhi_min+1):'已不用扣')}"""),
    "goal": ("存股目標試算：每月領多少股息要存多少錢？",
             "輸入想要的每月股息和殖利率，算出需要的本金；也能用每月投入金額估算要存幾年。",
             """<label>目標：每月股息（元）<input id=m type=number min=0 value=20000></label>
<label>假設殖利率（%）<input id=y type=number min=0 step=0.1 value=5></label>
<label>每月投入（元，選填）<input id=save type=number min=0 value=20000></label>
<label>股息再投入 <select id=re><option value=1>是</option><option value=0>否</option></select></label>
<div id=out class=kpi></div>
<h2>怎麼算</h2>
<p>需要本金＝每月股息 × 12 ÷ 殖利率。存幾年是用每月投入加上（選擇再投入時）股息複利滾入估算，假設殖利率不變、股價不變，沒算稅和補充保費。</p>
<p>殖利率是你自己輸入的假設數字，過去的配息不代表未來，實際結果一定會不同。</p>
<h2>常見問題</h2>
<p><b>殖利率要填多少？</b>可以參考各檔 ETF 近 12 個月的實際殖利率，見 <a href='../div/index.html'>ETF 配息一覽</a>。</p>""",
             """function run(){const m=+$('m').value||0,y=(+$('y').value||0)/100,s=+$('save').value||0,re=$('re').value==='1';
const need=y>0?m*12/y:Infinity;let v=0,k=0;if(s>0&&y>0){while(v<need&&k<1200){v=v+s+(re?v*y/12:0);k++}}
$('out').innerHTML=box('需要本金',isFinite(need)?f(need):'—')+box('每月投入可達成',s>0&&y>0?(k>=1200?'超過 100 年':(k/12).toFixed(1)+' 年'):'—')+
box('達成時累計投入',s>0&&y>0&&k<1200?f(s*k):'—')}"""),
}

JS = ("<script>const R=__R__,$=i=>document.getElementById(i),f=v=>Math.round(v).toLocaleString('zh-TW')+' 元',"
      "box=(a,b)=>`<div><span>${a}</span><b>${b}</b></div>`;__F__;"
      "document.querySelectorAll('input,select').forEach(e=>e.addEventListener('input',run));run()</script>")
FORM = ("<style>label{display:block;margin:10px 0}input,select{font:inherit;padding:6px 8px;margin-top:4px;width:100%;"
        "max-width:320px;box-sizing:border-box;border:1px solid rgba(128,128,128,.4);border-radius:8px;background:transparent;color:inherit}"
        "@media(prefers-color-scheme:dark){select option{background:#141413}}</style>")
NAV = "<p><a href='tax.html'>股利所得稅</a> · <a href='nhi.html'>二代健保補充保費</a> · <a href='goal.html'>存股目標</a> · <a href='../div/index.html'>ETF 配息一覽</a> · <a href='../exdiv.html'>除權息行事曆</a></p>"


def page(key: str, cfg: dict) -> str:
    title, desc, body, fn = PAGES[key]
    return ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><meta name='description' content='{html.escape(desc)}'>"
            f"<link rel='canonical' href='{SITE_URL}calc/{key}.html'>{CSS}{FORM}</head><body><!--SITENAV:exdiv:../-->"
            f"<h1>{html.escape(title)}</h1><p>{html.escape(desc)}</p>{body}{_cta(cfg, '../')}{NAV}"
            f"<p class='note'>規定核對日期：{CHECKED}；來源：財政部、衛生福利部中央健康保險署公告。只是試算，以官方計算為準。{DISCLAIMER}</p>"
            + JS.replace("__R__", json.dumps(RULES)).replace("__F__", fn) + "</body></html>")


def write(site_dir: Path) -> list[str]:
    d = site_dir / "calc"
    d.mkdir(parents=True, exist_ok=True)
    cfg = _cfg()
    for k in PAGES:
        (d / f"{k}.html").write_text(page(k, cfg), "utf-8")
    return [f"calc/{k}.html" for k in PAGES]
