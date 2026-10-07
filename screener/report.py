"""產生網頁報表（單一 HTML 檔，免外部資源）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

TEMPLATE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ __DATE__</title>
<style>
:root{
  --bg:#f7f7f5;--surface:#ffffff;--ink:#1d1d1b;--ink2:#5c5c57;--muted:#8a8a84;
  --line:#e4e4df;--chip:#efefea;--accent:#2f5bd3;--up:#d0312d;--down:#18864b;
  --spark:#1d1d1b;--ma:#2f5bd3;
}
@media (prefers-color-scheme: dark){
  :root{--bg:#141413;--surface:#1e1e1c;--ink:#ecebe6;--ink2:#b5b4ad;--muted:#85847e;
  --line:#33332f;--chip:#2a2a27;--accent:#7d9cf0;--up:#f0625d;--down:#3fbf7a;
  --spark:#ecebe6;--ma:#7d9cf0;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,"PingFang TC","Microsoft JhengHei","Noto Sans TC",sans-serif}
.wrap{max-width:1680px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--ink2);margin:0 0 20px}
.sub a{color:var(--accent)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:18px}
.tile{cursor:pointer;text-align:left;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  cursor:pointer;text-align:left;color:inherit;font:inherit}
.tile.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.tile .n{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .t{font-weight:600}
.tile .d{color:var(--muted);font-size:12px}
.tile .bt{font-size:11.5px;margin-top:4px;padding-top:4px;border-top:1px dashed var(--line);color:var(--muted);font-variant-numeric:tabular-nums}
.tile .bt b{color:var(--text,inherit);font-weight:600}
.btnote{color:var(--muted);font-size:12.5px;margin:4px 0 10px;line-height:1.6}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;margin-bottom:4px}
.badge.ok{background:#0ca30c;color:#fff}.badge.no{background:rgba(128,128,128,.2);color:var(--muted)}
.tile .bt3{display:flex;gap:10px;font-size:12px;margin-top:4px;padding-top:4px;border-top:1px dashed var(--line);color:var(--muted);font-variant-numeric:tabular-nums}
.tile .bt3 b{display:block;font-size:14px;color:var(--text,inherit)}
@media(max-width:560px){.tiles{grid-template-columns:1fr 1fr;gap:8px}.tile{padding:10px 11px}.tile .n{font-size:22px}}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.bar input{flex:1;min-width:180px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);
  background:var(--surface);color:var(--ink);font:inherit}
.bar .cnt{color:var(--ink2)}
/* 表格上方的橫向捲軸：不用滑到表格最下面就能往右拉 */
.topscroll{overflow-x:auto;overflow-y:hidden;height:16px;margin-bottom:4px}
.topscroll>div{height:1px}
.topscroll{scrollbar-width:thin;scrollbar-color:var(--ink2) transparent}
.topscroll::-webkit-scrollbar{height:10px}.topscroll::-webkit-scrollbar-thumb{background:var(--ink2);border-radius:5px;opacity:.6}
.topscroll[hidden]{display:none}
.tbl{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:auto;
  max-height:calc(100vh - 150px);max-height:calc(100dvh - 150px);overscroll-behavior:contain}
table{border-collapse:collapse;width:100%;min-width:920px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}
th{position:sticky;top:0;z-index:2;background:var(--surface);color:var(--ink2);font-weight:600;
  font-size:12px;cursor:pointer;user-select:none}
/* 代號、名稱固定在左邊，往右滑也看得到是哪一檔 */
th:nth-child(1),td:nth-child(1){position:sticky;left:0;z-index:1;background:var(--surface);min-width:62px;width:62px}
th:nth-child(2),td:nth-child(2){position:sticky;left:62px;z-index:1;background:var(--surface);box-shadow:1px 0 0 var(--line)}
th:nth-child(1),th:nth-child(2){z-index:3}
th.l,td.l{text-align:left}
th .arr{color:var(--accent)}
tr:last-child td{border-bottom:0}
td{font-variant-numeric:tabular-nums}
.code a{color:var(--accent);font-weight:600;text-decoration:none}
.name{color:var(--ink)}
.meta{color:var(--muted);font-size:12px}
.up{color:var(--up)}.down{color:var(--down)}
.tagcol{white-space:normal;min-width:180px;max-width:240px;line-height:1.9}
.tag{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;
  font-size:12px;margin:1px 2px;color:var(--ink2)}
svg.sp{display:block}
.empty{padding:40px;text-align:center;color:var(--muted)}
.foot{color:var(--muted);font-size:12px;margin-top:16px}
.legend{display:flex;gap:14px;color:var(--ink2);font-size:12px;margin:0 0 8px}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:4px}
.senti{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:18px}
.senti .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.senti .lv{font-size:18px;font-weight:650}
.senti .adv{color:var(--ink2);margin:4px 0 10px}
.senti .kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
.senti .k{background:var(--chip);border-radius:8px;padding:6px 10px}
.senti .k b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
.senti .k span{font-size:12px;color:var(--muted)}
.flag{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;margin:1px 2px;background:var(--up);color:#fff}
.news{min-width:260px;max-width:320px;white-space:normal;font-size:12px;line-height:1.35;cursor:pointer}
.news .clip{max-height:4.1em;overflow:hidden}
.news.open .clip{max-height:none}
.news a{color:var(--ink2);text-decoration:none}.news a:hover{text-decoration:underline}
.news .more{color:var(--muted)}
/* 族群強弱 */
.groups{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 16px}
.groups .hd{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;margin-bottom:8px}
.groups .hd b{font-size:16px}.groups .hd span{color:var(--muted);font-size:12px}
.groups .gt{overflow-x:auto}
.groups table{min-width:640px;width:auto}
.groups td,.groups th{padding:7px 14px}
.groups th{position:static;cursor:default}
.groups tbody tr{cursor:pointer}.groups tbody tr:hover td{background:var(--chip)}
.groups td:first-child,.groups th:first-child{position:static;width:auto;min-width:0}
.groups td:nth-child(2),.groups th:nth-child(2){position:static;box-shadow:none}
.groups .bar2{display:inline-block;height:6px;border-radius:3px;background:var(--accent);opacity:.7;vertical-align:middle;margin-right:6px}
.groups .more-btn{background:none;border:none;color:var(--accent);cursor:pointer;font:inherit;padding:6px 0 0}
/* 點名稱跳出 K 線 */
.nm{cursor:pointer;border-bottom:1px dashed var(--muted)}.nm:hover{color:var(--accent)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px}
.modal[hidden]{display:none}
.mbox{background:var(--surface);color:var(--ink);border-radius:12px;width:min(960px,100%);max-height:100%;overflow:auto;padding:16px 18px}
.mhd{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.mhd h3{margin:0;font-size:18px}.mhd .x{background:none;border:none;font-size:22px;line-height:1;cursor:pointer;color:var(--ink2)}
.minfo{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--ink2);font-size:13px;margin:6px 0 10px}
.minfo b{color:var(--ink)}.minfo a{color:var(--accent)}
#kc{height:380px;width:100%}
/* 圖表套件內部用 table 排版，不能套用上面報表表格的樣式 */
#kc table{min-width:0;width:auto;border-collapse:separate;border-spacing:0}
#kc td,#kc th,#kc tr{padding:0;border:0;position:static;left:auto;top:auto;min-width:0;width:auto;background:none;box-shadow:none;white-space:normal;text-align:left;z-index:auto}
.mleg{font-size:12px;color:var(--muted);margin-top:6px}
/* 浮動橫向捲軸：表格在畫面上、但它自己的捲軸不在畫面內時，固定在螢幕底部 */
.hbar{position:fixed;bottom:0;z-index:40;display:flex;align-items:center;gap:6px;background:var(--surface);
  border:1px solid var(--line);border-bottom:0;border-radius:10px 10px 0 0;padding:5px 8px;box-shadow:0 -2px 10px rgba(0,0,0,.12)}
.hbar[hidden]{display:none}
.hb-track{flex:1;overflow-x:auto;overflow-y:hidden;height:18px;scrollbar-width:auto;scrollbar-color:var(--ink2) transparent}
.hb-track::-webkit-scrollbar{height:12px}.hb-track::-webkit-scrollbar-thumb{background:var(--ink2);border-radius:6px}
.hb-track>div{height:1px}
.hbar button{border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:6px;min-width:36px;height:28px;cursor:pointer;font-size:13px;line-height:1}
.hbar button:hover{border-color:var(--accent)}
/* 大盤寬度走勢 */
.breadth{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 16px}
.breadth .hd{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;margin-bottom:6px}
.breadth .hd b{font-size:16px}.breadth .hd span{color:var(--muted);font-size:12px}
.bgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.bchart .t{font-size:13px;color:var(--ink2);margin-bottom:2px}.bchart .t b{color:var(--ink)}
.bchart svg{display:block;width:100%;height:120px}
.warnline{stroke:var(--up);stroke-dasharray:4 3;stroke-width:1}
.midline{stroke:var(--line);stroke-width:1}
/* 摺疊區塊：大型股觀察表、預警準確度 */
details.box{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:0 0 16px}
details.box summary{cursor:pointer;font-weight:600}
details.box summary span{color:var(--muted);font-size:12px;font-weight:400;margin-left:8px}
details.box .gt{overflow-x:auto;margin-top:8px}
details.box table{min-width:720px;width:auto}
details.box td,details.box th{padding:6px 12px}
details.box th{position:static;cursor:default}
details.box td:first-child,details.box th:first-child,details.box td:nth-child(2),details.box th:nth-child(2){position:static;box-shadow:none;width:auto;min-width:0}
details.box .note{color:var(--muted);font-size:12px;margin:6px 0 0}
/*__TODAY_CSS__*/
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">資料日期 __DATE__（__QDAY__）・ 共掃描 __SCANNED__ 檔</p>
  __ARCHIVE__
  __TODAY_HTML__
  <div class="senti" id="senti" hidden></div>
  <div class="tiles" id="tiles"></div>
  <p class="btnote" id="btnote"></p>
  <details class="box" id="retired" hidden></details>
  <div class="breadth" id="breadth" hidden></div>
  <div class="groups" id="groups" hidden></div>
  <details class="box" id="large" hidden></details>
  <details class="box" id="astats" hidden></details>
  <div class="bar">
    <input id="q" placeholder="搜尋代號、名稱或產業…" autocomplete="off">
    <span class="cnt" id="cnt"></span>
    <label class="cnt" id="showhid" hidden><input type="checkbox" id="hidchk"> 含已撤的招</label>
  </div>
  <div class="legend"><span><i style="background:var(--spark)"></i>收盤價</span>
    <span><i style="background:var(--ma)"></i>60日均線（季線）</span></div>
  <div class="topscroll" id="topscroll" hidden><div></div></div>
  <div class="tbl" id="tbl"><table>
    <thead><tr id="hd"></tr></thead><tbody id="bd"></tbody>
  </table><div class="empty" id="empty" hidden>沒有符合條件的股票</div></div>
  <p class="foot">表格可以直接上下、左右滑動；消息欄點一下展開全文。投信/外資 = 當日買賣超張數；連續 = 連續買超或賣超的天數（從 10/1 起累積法人歷史，第一次會往前補抓約 20 天）。RS = 近 3、6 個月漲幅和全市場比較的分數（1~99）。趨勢樣板 = 收盤 > MA50 > MA150 > MA200、MA200 比 20 日前高、收盤 ≥ 52 週低點 × 1.3 且 ≥ 52 週高點 × 0.75。營收連增 = 月營收年增率 > 0 連續幾個月（負數 = 連續衰退）。融資 5 日% = 融資餘額 5 個交易日的變化率。券資比僅供參考。點股票名稱可看 K 線。月營收年增 = 最新公布月份與去年同月比較。本益比 10 倍以下的爆量突破，歷史表現較弱。「準備突破觀察」只是觀察名單，本身不是買進訊號。量比 = 當日成交量 ÷ 比較基準。乖離 = 收盤相對 60 日均線。價格未還原除權息。本報表僅供參考，不構成投資建議。</p>
  <div class="modal" id="modal" hidden><div class="mbox" role="dialog" aria-modal="true">
    <div class="mhd"><div><h3 id="mt"></h3><div class="minfo" id="mi"></div></div><button class="x" id="mx" aria-label="關閉">×</button></div>
    <div id="kc"></div>
    <div class="mleg">近 120 個交易日 K 線（紅漲綠跌）・ 橘線 = 20 日均線（月線）・ 藍線 = 60 日均線（季線）・ 下方 = 成交量（張）・ ▲ = 爆量突破 60 日新高、▼ = 之後第一次量縮到爆量日一半（漲停日不算）・ 價格未還原除權息</div>
  </div></div>
</div>
<script>
const DATA=__DATA__;
const GROUPS=__GROUPS__;
const TODAY=__TODAY__;
const STRATS=__STRATS__;
const HIDDEN=__HIDDEN__;
const SHOWN=STRATS.filter(s=>!HIDDEN.includes(s.name)),SHOWN_N=new Set(SHOWN.map(s=>s.name));
const SENTI=__SENTI__;
const BREADTH=__BREADTH__;
const LARGE=__LARGE__;
const ASTATS=__ASTATS__;
const BT5=__BT5__;
const stk=v=>v===0?'—':(v>0?'買 ':'賣 ')+Math.abs(v)+' 天';
const cols=[
 {k:'code',t:'代號',l:1},{k:'name',t:'名稱 / 產業',l:1},{k:'spark',t:'近__SPARK__日走勢',l:1,ns:1},
 {k:'close',t:'收盤',f:v=>v.toFixed(2)},
 {k:'change_pct',t:'漲跌%',f:v=>(v>0?'+':'')+v.toFixed(2),c:v=>v>0?'up':v<0?'down':''},
 {k:'volume_lots',t:'成交量(張)',f:v=>Math.round(v).toLocaleString()},
 {k:'turnover',t:'換手率%',f:v=>v.toFixed(v<10?2:1)},
 {k:'vol_x_prev',t:'量比前日',f:v=>v.toFixed(1)+'×'},
 {k:'vol_x_avg5',t:'量比5日均',f:v=>v.toFixed(1)+'×'},
 {k:'bias60',t:'季線乖離%',f:v=>(v>0?'+':'')+v.toFixed(1),c:v=>v>0?'up':v<0?'down':''},
 {k:'rs',t:'RS',f:v=>v.toFixed(0),c:v=>v>=80?'up':v<=30?'down':''},
 {k:'tpl',t:'趨勢樣板',f:v=>v?'✓':'✗',c:v=>v?'up':''},
 {k:'pe',t:'本益比',f:v=>v<=0?'虧損':v.toFixed(1)},
 {k:'trust',t:'投信(張)',f:v=>(v>0?'+':'')+Math.round(v).toLocaleString(),c:v=>v>0?'up':v<0?'down':''},
 {k:'foreign',t:'外資(張)',f:v=>(v>0?'+':'')+Math.round(v).toLocaleString(),c:v=>v>0?'up':v<0?'down':''},
 {k:'foreign_streak',t:'外資連續',f:stk,c:v=>v>0?'up':v<0?'down':''},
 {k:'trust_streak',t:'投信連續',f:stk,c:v=>v>0?'up':v<0?'down':''},
 {k:'inst_pct',t:'法人占量%',f:v=>(v>0?'+':'')+v.toFixed(1),c:v=>v>0?'up':v<0?'down':''},
 {k:'inst_pct_5d',t:'法人5日占量%',f:v=>(v>0?'+':'')+v.toFixed(1),c:v=>v>0?'up':v<0?'down':''},
 {k:'rev_yoy',t:'月營收年增%',f:v=>(v>0?'+':'')+v.toFixed(0),c:v=>v>0?'up':v<0?'down':''},
 {k:'rev_streak',t:'營收連增',f:v=>v>0?v+' 個月':v<0?'減 '+(-v)+' 個月':'—',c:v=>v>0?'up':v<0?'down':''},
 {k:'rev_yoy_3m',t:'近3月年增%',f:v=>(v>0?'+':'')+v.toFixed(0),c:v=>v>0?'up':v<0?'down':''},
 {k:'margin_chg',t:'融資增減(張)',f:v=>(v>0?'+':'')+Math.round(v).toLocaleString(),c:v=>v>0?'up':v<0?'down':''},
 {k:'margin_5d_pct',t:'融資5日%',f:v=>(v>0?'+':'')+v.toFixed(1),c:v=>v>0?'up':v<0?'down':''},
 {k:'short_ratio',t:'券資比%',f:v=>v.toFixed(1)},
 {k:'dt_ratio',t:'當沖%',f:v=>v.toFixed(0)},
 {k:'news',t:'消息 / 重大訊息',l:1,ns:1},
 {k:'tags',t:'符合策略',l:1,ns:1},
];
let sel=null,sortK='change_pct',sortD=-1;
const $=id=>document.getElementById(id);
const pc=v=>(v>0?'+':'')+v.toFixed(2)+'%';
function bt(name){return (BT5.strategies||[]).find(x=>x.name===name)}
// 大題 A 判準（Cowork 2356）：20 日平均 ≥ +0.5、中位 ≥ 0、正的年份 ≥ 4（隔天開盤買、扣 0.38%、對同時點等權）
function pass(b){const c=b&&b.next_open_20;return !!c&&c.ex>=0.5&&c.med>=0&&c.pos_years&&c.pos_years[0]>=4}
function btLine(name){const b=bt(name);if(!b)return'';const c=b.next_open_20,a=b.next_open_5;
  return `<div class="bt3"><span>20 日平均<b>${pc(c.ex)}</b></span><span>中位<b>${pc(c.med)}</b></span><span>正的年份<b>${c.pos_years?c.pos_years.join('/'):'—'}</b></span></div>`+
   `<details class="d" onclick="event.stopPropagation()"><summary>更多</summary>5 日平均 ${pc(a.ex)}・20 日勝率 ${c.win.toFixed(0)}%・t ${c.t.toFixed(1)}・N ${b.n.toLocaleString()}</details>`}
function badge(name){const b=bt(name);if(!b)return'';return pass(b)?'<span class="badge ok">5 年有贏</span>':'<span class="badge no">未通過驗證・僅供參考</span>'}
function btNote(){const L=BT5.strategies||[];if(!L.length)return;const P=BT5.period||[];const ok=SHOWN.filter(s=>pass(bt(s.name)));
  $('btnote').innerHTML=(ok.length?`✅ 5 年驗證過的：<b>${ok.map(s=>s.name).join('、')}</b>。`:`🔍 <b>觀察清單</b>：目前<b>沒有</b>一招通過 5 年驗證，名單只用來找值得看的股票，不是買進訊號；進場看盤中 13:12。`)+
   `<br><span>驗證口徑：5 年含下市股（${P[0]||''}～${P[1]||''}），名單收盤後出來、隔天開盤買，持有 20 天，扣來回 0.38%，跟同期全市場平均比；要平均 ≥ +0.5%、中位數 ≥ 0、5 年至少 4 年是正的才算過。</span>`;
  const R=STRATS.filter(s=>HIDDEN.includes(s.name));if(!R.length)return;const el=$('retired');el.hidden=false;$('showhid').hidden=false;
  el.innerHTML=`<summary>已撤 ${R.length} 招：5 年隔天開盤買沒贏全市場平均（${R.map(s=>s.name).join('、')}）<span>點開看成績</span></summary>`+
   `<div class="gt"><table><tr><th>策略</th><th>今天檔數</th><th>20 日平均</th><th>中位</th><th>正的年份</th><th>5 日平均</th><th>N</th></tr>`+
   R.map(s=>{const b=bt(s.name);if(!b)return`<tr><td>${s.name}</td><td>${s.codes.length}</td><td colspan="5">—</td></tr>`;const c=b.next_open_20;
     return `<tr><td>${s.name}</td><td>${s.codes.length}</td><td>${pc(c.ex)}</td><td>${pc(c.med)}</td><td>${(c.pos_years||[]).join('/')}</td><td>${pc(b.next_open_5.ex)}</td><td>${b.n.toLocaleString()}</td></tr>`}).join('')+
   `</table></div><p class="note">這些招還是每天算（例如「準備突破觀察」是盤中監控的觀察名單），只是不放在這裡；個股頁會照樣標出來。漲停那招當天收盤買才有數字，但鎖漲停買不到，不可用。</p>`}
function tiles(){
  $('tiles').innerHTML=SHOWN.map((s,i)=>`<div class="tile${sel===i?' on':''}" data-i="${i}" role="button" tabindex="0">${badge(s.name)}
   <div class="n">${s.codes.length}</div><div class="t">${s.name}</div><div class="d">${s.desc}</div>${btLine(s.name)}</div>`).join('');
  document.querySelectorAll('.tile').forEach(b=>b.onclick=()=>{const i=+b.dataset.i;sel=sel===i?null:i;tiles();render();});
}
function spark(a,m){
  const W=120,H=30,v=a.concat(m).filter(x=>x!=null);if(!v.length)return'';
  let lo=Math.min(...v),hi=Math.max(...v);if(hi===lo)hi=lo+1;
  const pts=s=>s.map((y,i)=>y==null?null:[(i/(a.length-1||1))*(W-4)+2,H-2-(y-lo)/(hi-lo)*(H-4)]);
  const path=s=>{let d='',pen=0;pts(s).forEach(p=>{if(!p){pen=0;return}d+=(pen?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);pen=1});return d};
  const last=pts(a).at(-1);
  return `<svg class="sp" width="${W}" height="${H}" role="img" aria-label="近期收盤走勢">
   <title>最低 ${lo.toFixed(2)} ・ 最高 ${hi.toFixed(2)}</title>
   <path d="${path(m)}" fill="none" stroke="var(--ma)" stroke-width="1.5" stroke-linejoin="round" opacity=".9"/>
   <path d="${path(a)}" fill="none" stroke="var(--spark)" stroke-width="1.5" stroke-linejoin="round"/>
   ${last?`<circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="var(--spark)"/>`:''}</svg>`;
}
function render(){
  $('hd').innerHTML=cols.map(c=>`<th class="${c.l?'l':''}" data-k="${c.ns?'':c.k}">${c.t}${sortK===c.k?`<span class="arr">${sortD>0?' ▲':' ▼'}</span>`:''}</th>`).join('');
  document.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(!k)return;
    if(sortK===k)sortD*=-1;else{sortK=k;sortD=(k==='code'||k==='name')?1:-1}render();});
  const q=$('q').value.trim().toLowerCase();
  const allHid=$('hidchk')&&$('hidchk').checked;
  let rows=DATA.filter(r=>(sel===null?(allHid||!HIDDEN.length||r.tags.some(t=>SHOWN_N.has(t))):r.tags.includes(SHOWN[sel].name))&&
    (!q||(r.code+r.name+r.industry).toLowerCase().includes(q)));
  rows.sort((a,b)=>{const x=a[sortK],y=b[sortK];if(x==null)return 1;if(y==null)return -1;
    return (x>y?1:x<y?-1:0)*sortD;});
  $('cnt').textContent=`顯示 ${rows.length} 檔`;
  $('empty').hidden=rows.length>0;
  $('bd').innerHTML=rows.map(r=>`<tr>
   <td class="l code"><a href="stock.html?code=${r.code}" title="個股頁">${r.code}</a></td>
   <td class="l"><div class="name"><span class="nm" data-code="${r.code}" title="看 K 線">${r.name}</span></div><div class="meta">${r.market==='TWSE'?'上市':'上櫃'}${r.industry?' ・ '+r.industry:''}</div></td>
   <td class="l">${spark(r.spark,r.spark_ma)}</td>
   ${cols.filter(c=>!c.l).map(c=>{const v=r[c.k];return `<td class="${v!=null&&c.c?c.c(v):''}">${v==null?'—':c.f(v)}</td>`}).join('')}
   <td class="l news">${newsCell(r)}</td>
   <td class="l tagcol">${r.flag?`<span class="flag">${r.flag}</span>`:''}${r.rev_high12?'<span class="tag">營收創 12 個月新高</span>':''}${r.tags.map(t=>`<span class="tag">${t}</span>`).join('')}</td></tr>`).join('');
}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function newsCell(r){
  const a=(r.ann||[]).map(t=>`<div>📢 ${esc(t)}</div>`);
  const n=(r.news||[]).slice(0,2).map(x=>`<div><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a> <span class="more">${esc(x.when)}</span></div>`);
  const all=a.concat(n);return all.length?`<div class="clip">${all.join('')}</div>`:'<span class="more">—</span>';
}
function senti(){
  if(!SENTI||!SENTI.level)return;const s=SENTI;const el=$('senti');el.hidden=false;
  const k=(v,l)=>`<div class="k"><b>${v}</b><span>${l}</span></div>`;
  el.innerHTML=`<div class="hd"><span class="lv">${s.icon} 市場情緒：${s.level}</span>
   <span class="more">站上月線 ${s.above_ma20.toFixed(0)}%${s.above_ma20_5d_ago!=null?`（5 天前 ${s.above_ma20_5d_ago.toFixed(0)}%）`:''}</span></div>
   <div class="adv">${s.advice}${s.weak_market?' ⚠️ 大盤近 20 日跌超過 3%，回測顯示此時爆量突破平均虧損。':''}</div>
   <div class="kpis">${k(s.up_pct.toFixed(0)+'%','上漲家數')}${k(s.above_ma60.toFixed(0)+'%','站上季線')}
   ${k(s.new_high+' / '+s.new_low,'創60日新高 / 新低')}${k(s.limit_up+' / '+s.limit_down,'漲停 / 跌停')}
   ${k(s.surge_pct.toFixed(1)+'%','爆量家數')}${k((s.mkt20>0?'+':'')+s.mkt20.toFixed(1)+'%','大盤近20日')}</div>`;
}
function lineSvg(pts,lo,hi,lines,color){
  const W=600,H=120,n=pts.length;if(!n)return'';
  const x=i=>(i/(n-1||1))*(W-40)+36,y=v=>H-14-(v-lo)/(hi-lo)*(H-24);
  let d='',pen=0;pts.forEach((v,i)=>{if(v==null){pen=0;return}d+=(pen?'L':'M')+x(i).toFixed(1)+' '+y(Math.max(lo,Math.min(hi,v))).toFixed(1);pen=1;});
  const ls=lines.map(([v,cls,t])=>`<line x1="36" x2="${W-4}" y1="${y(v)}" y2="${y(v)}" class="${cls}"/><text x="2" y="${y(v)+4}" font-size="11" fill="var(--muted)">${t}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">${ls}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>`;
}
function breadthBox(){
  if(!BREADTH||BREADTH.length<2)return;const el=$('breadth');el.hidden=false;
  const a=BREADTH.map(r=>r[1]),m=BREADTH.map(r=>r[2]),la=a.at(-1),lm=m.at(-1);
  const mlo=Math.min(-6,...m.filter(v=>v!=null)),mhi=Math.max(6,...m.filter(v=>v!=null));
  el.innerHTML=`<div class="hd"><b>大盤寬度</b><span>近 ${BREADTH.length} 個交易日（${BREADTH[0][0]} ~ ${BREADTH.at(-1)[0]}）・ 回測：寬度 < 30% 或近 20 日跌超過 3% 時，突破策略平均虧錢</span></div>
  <div class="bgrid"><div class="bchart"><div class="t">站上月線比例 <b class="${la<30?'down':''}">${la==null?'—':la.toFixed(1)+'%'}</b>（20 日均量 ≥ 100 張的股票）</div>${lineSvg(a,0,100,[[30,'warnline','30%'],[50,'midline','50%']],'var(--accent)')}</div>
  <div class="bchart"><div class="t">全市場等權近 20 日 <b class="${lm<-3?'down':lm>0?'up':''}">${lm==null?'—':(lm>0?'+':'')+lm.toFixed(2)+'%'}</b></div>${lineSvg(m,mlo,mhi,[[-3,'warnline','-3%'],[0,'midline','0%']],'var(--ink2)')}</div></div>`;
}
function largeBox(){
  if(!LARGE||!LARGE.length)return;const el=$('large');el.hidden=false;
  const p=v=>v==null?'—':`<span class="${v>0?'up':v<0?'down':''}">${v>0?'+':''}${(+v).toFixed(1)}%</span>`;
  el.innerHTML=`<summary>大型股觀察表<span>近 20 日平均成交值前 ${LARGE.length} 大・ 核心持股檢查用</span></summary>
  <div class="gt"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>收盤</th><th>漲跌</th><th>成交值(億)</th><th>本益比</th><th>月營收年增</th><th>近3月年增</th><th>外資20日(張)</th><th>離52週高</th><th>樣板</th><th>RS</th></tr></thead><tbody>
  ${LARGE.map(r=>`<tr><td class="l">${r.rank}</td><td class="l"><a href="stock.html?code=${r.code}">${r.code}</a> ${esc(r.name)}<div class="meta">${esc(r.industry)}</div></td><td>${r.close.toFixed(2)}</td><td>${p(r.chg)}</td>
   <td>${r.turnover.toFixed(1)}</td><td>${r.pe==null?'—':r.pe<=0?'虧損':(+r.pe).toFixed(1)}</td><td>${p(r.rev_yoy)}</td><td>${p(r.rev_yoy_3m)}</td>
   <td class="${r.foreign_20d>0?'up':r.foreign_20d<0?'down':''}">${r.foreign_20d==null?'—':(r.foreign_20d>0?'+':'')+Math.round(r.foreign_20d).toLocaleString()}</td>
   <td>${p(r.hi52_dist)}</td><td>${r.tpl==null?'—':r.tpl?'✓':'✗'}</td><td>${r.rs==null?'—':r.rs.toFixed(0)}</td></tr>`).join('')}</tbody></table></div>`;
}
function astatsBox(){
  if(!ASTATS||!ASTATS.all)return;const el=$('astats');el.hidden=false;
  const p=v=>v==null?'—':`<span class="${v>0?'up':v<0?'down':''}">${v>0?'+':''}${v.toFixed(2)}%</span>`;
  const tb=(title,rows)=>`<table><thead><tr><th class="l">${title}</th><th>筆數</th><th>收盤仍符合</th><th>3 日報酬</th><th>5 日報酬</th></tr></thead><tbody>
   ${rows.map(r=>`<tr><td class="l">${r.bucket||'全部'}</td><td>${r.n}</td><td>${r.close_hit.toFixed(0)}%</td><td>${p(r.ret3)} <span class="meta">(${r.ret3_n})</span></td><td>${p(r.ret5)} <span class="meta">(${r.ret5_n})</span></td></tr>`).join('')}</tbody></table>`;
  el.innerHTML=`<summary>早期預警準確度<span>${ASTATS.from} ~ ${ASTATS.to}・ ${ASTATS.days} 個交易日・ 共 ${ASTATS.all.n} 筆</span></summary>
  <div class="gt">${tb('全部',[ASTATS.all])}${tb('預警時段',ASTATS.by_time)}${tb('族群同步家數',ASTATS.by_peers)}</div>
  <p class="note">收盤仍符合 = 預警當天收盤仍是「爆量突破 60 日新高」；報酬 = 預警價到第 3、5 個交易日收盤，括號是有結果的筆數。樣本少時參考就好。</p>`;
}
let gAll=false;
function groupsBox(){
  if(!GROUPS||!GROUPS.length)return;const el=$('groups');el.hidden=false;
  const rows=gAll?GROUPS:GROUPS.slice(0,10);const mx=Math.max(...GROUPS.map(g=>Math.abs(g.r20)),1);
  const p=(v,d=1)=>`<span class="${v>0?'up':v<0?'down':''}">${v>0?'+':''}${v.toFixed(d)}%</span>`;
  el.innerHTML=`<div class="hd"><b>族群強弱</b><span>依 5 日與 20 日等權平均漲幅綜合排名（近 20 日均量 100 張以上、5 檔以上的產業）・ 點一列只看該族群</span></div>
  <div class="gt"><table><thead><tr><th class="l">#</th><th class="l">產業</th><th>檔數</th><th>今日</th><th>5 日</th><th class="l">20 日</th><th>站上月線</th><th>創 60 日新高</th><th>爆量</th></tr></thead><tbody>
  ${rows.map(g=>`<tr data-ind="${esc(g.ind)}"><td class="l">${g.rank}</td><td class="l">${esc(g.ind)}</td><td>${g.n}</td><td>${p(g.r1,2)}</td><td>${p(g.r5)}</td>
   <td class="l"><span class="bar2" style="width:${Math.round(Math.abs(g.r20)/mx*60)}px;background:${g.r20>=0?'var(--up)':'var(--down)'}"></span>${p(g.r20)}</td>
   <td>${g.above.toFixed(0)}%</td><td>${g.newhi}</td><td>${g.surge}</td></tr>`).join('')}</tbody></table></div>
  ${GROUPS.length>10?`<button class="more-btn" id="gmore">${gAll?'只看前 10 名':'顯示全部 '+GROUPS.length+' 個產業'}</button>`:''}`;
  el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>{$('q').value=tr.dataset.ind;sel=null;tiles();render();$('q').scrollIntoView({behavior:'smooth',block:'start'});});
  const b=$('gmore');if(b)b.onclick=()=>{gAll=!gAll;groupsBox();};
}
// ---- K 線（點名稱開啟；圖表程式與資料第一次點時才下載）
let LWC=null,OHLC=null,chart=null;
const loadJS=src=>new Promise((ok,no)=>{const s=document.createElement('script');s.src=src;s.onload=ok;s.onerror=no;document.head.appendChild(s);});
async function openK(code){
  const r=DATA.find(x=>x.code===code);if(!r)return;
  $('modal').hidden=false;document.body.style.overflow='hidden';
  $('mt').innerHTML=`${esc(r.name)} <span style="color:var(--muted);font-size:14px">${r.code} ・ ${r.market==='TWSE'?'上市':'上櫃'}${r.industry?' ・ '+esc(r.industry):''}</span> <a href="stock.html?code=${r.code}" style="font-size:14px">完整個股頁 →</a>`;
  const f=(v,fn)=>v==null?'—':fn(v);
  $('mi').innerHTML=[`收盤 <b>${f(r.close,v=>v.toFixed(2))}</b>`,`漲跌 <b class="${r.change_pct>0?'up':r.change_pct<0?'down':''}">${f(r.change_pct,v=>(v>0?'+':'')+v.toFixed(2)+'%')}</b>`,
   `本益比 <b>${f(r.pe,v=>v<=0?'虧損':v.toFixed(1))}</b>`,`月營收年增 <b>${f(r.rev_yoy,v=>(v>0?'+':'')+v.toFixed(0)+'%')}</b>`,
   `外資 <b>${f(r.foreign,v=>(v>0?'+':'')+Math.round(v).toLocaleString()+' 張')}</b>${r.foreign_streak?`（${stk(r.foreign_streak)}）`:''}`,
   `投信 <b>${f(r.trust,v=>(v>0?'+':'')+Math.round(v).toLocaleString()+' 張')}</b>${r.trust_streak?`（${stk(r.trust_streak)}）`:''}`,`RS <b>${f(r.rs,v=>v.toFixed(0))}</b>`,
   `趨勢樣板 <b>${r.tpl==null?'資料不足':r.tpl?'✓':'✗'}</b>`,`離 52 週高點 <b>${f(r.hi52_dist,v=>v.toFixed(1)+'%')}</b>`,
   `融資 <b>${f(r.margin_chg,v=>(v>0?'+':'')+Math.round(v).toLocaleString()+' 張')}</b>${r.margin_streak?`（${r.margin_streak>0?'連增':'連減'} ${Math.abs(r.margin_streak)} 天）`:''}`,
   `融資 5 日 <b>${f(r.margin_5d_pct,v=>(v>0?'+':'')+v.toFixed(1)+'%')}</b>`,`融資使用率 <b>${f(r.margin_util,v=>v.toFixed(1)+'%')}</b>`,
   `券資比 <b>${f(r.short_ratio,v=>v.toFixed(1)+'%')}</b>`,`當沖 <b>${f(r.dt_ratio,v=>v.toFixed(0)+'%')}</b>`,
   `三大法人占量 <b>${f(r.inst_pct,v=>(v>0?'+':'')+v.toFixed(1)+'%')}</b>（5 日 ${f(r.inst_pct_5d,v=>(v>0?'+':'')+v.toFixed(1)+'%')}）`,
   `營收 <b>${r.rev_streak?(r.rev_streak>0?'連增 '+r.rev_streak:'連減 '+(-r.rev_streak))+' 個月':'—'}</b>`,
   `近 3 月年增 <b>${f(r.rev_yoy_3m,v=>(v>0?'+':'')+v.toFixed(0)+'%')}</b>${r.rev_accel!=null?`（${r.rev_accel>0?'加速':'減速'} ${Math.abs(r.rev_accel).toFixed(0)} 個百分點）`:''}`,
   `<a href="${r.url}" target="_blank" rel="noopener">Yahoo 股市 ↗</a>`].join('');
  const box=$('kc');box.innerHTML='<p style="color:var(--muted)">載入中…</p>';
  try{
    if(!LWC){await loadJS('https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js').catch(()=>loadJS('https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js'));LWC=window.LightweightCharts;}
    if(!OHLC)OHLC=await fetch('ohlc.json',{cache:'no-cache'}).then(x=>x.json());
  }catch(e){box.innerHTML='<p style="color:var(--muted)">K 線載入失敗，請稍後再試。</p>';return;}
  const k=OHLC.k[code];if(!k){box.innerHTML='<p style="color:var(--muted)">這一檔沒有 K 線資料（舊報表或今天沒成交）。</p>';return;}
  box.innerHTML='';if(chart){chart.remove();chart=null;}
  const css=getComputedStyle(document.documentElement),cv=n=>css.getPropertyValue(n).trim();
  chart=LWC.createChart(box,{width:box.clientWidth,height:box.clientHeight||380,layout:{background:{color:cv('--surface')},textColor:cv('--ink2'),fontSize:12},
    grid:{vertLines:{color:cv('--line')},horzLines:{color:cv('--line')}},rightPriceScale:{borderColor:cv('--line')},
    timeScale:{borderColor:cv('--line')},localization:{locale:'zh-TW'}});
  const up=cv('--up'),dn=cv('--down');
  const cs=chart.addCandlestickSeries({upColor:up,downColor:dn,borderUpColor:up,borderDownColor:dn,wickUpColor:up,wickDownColor:dn});
  const bars=[],vols=[],closes=[];
  OHLC.dates.forEach((d,i)=>{const x=k[i];if(!x)return;bars.push({time:d,open:x[0],high:x[1],low:x[2],close:x[3]});
    vols.push({time:d,value:x[4],color:(x[3]>=x[0]?up:dn)+'88'});closes.push([d,x[3]]);});
  cs.setData(bars);
  const have=new Set(bars.map(b=>b.time));
  cs.setMarkers(((OHLC.sig||{})[code]||[]).filter(x=>have.has(x[0])).map(([d,t])=>t==='B'
    ?{time:d,position:'belowBar',color:up,shape:'arrowUp',text:'突破'}
    :{time:d,position:'aboveBar',color:dn,shape:'arrowDown',text:'量縮'}));
  const vs=chart.addHistogramSeries({priceScaleId:'',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
  chart.priceScale('').applyOptions({scaleMargins:{top:.8,bottom:0}});cs.priceScale().applyOptions({scaleMargins:{top:.05,bottom:.25}});
  vs.setData(vols);
  const ma=n=>closes.map((c,i)=>i<n-1?null:{time:c[0],value:closes.slice(i-n+1,i+1).reduce((a,b)=>a+b[1],0)/n}).filter(Boolean);
  chart.addLineSeries({color:'#e08a1e',lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false}).setData(ma(20));
  chart.addLineSeries({color:cv('--ma'),lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false}).setData(ma(60));
  chart.timeScale().fitContent();
}
window.addEventListener('resize',()=>{if(chart&&!$('modal').hidden)chart.applyOptions({width:$('kc').clientWidth});});
function closeK(){$('modal').hidden=true;document.body.style.overflow='';}
$('mx').onclick=closeK;$('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeK();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('modal').hidden)closeK();});
$('bd').addEventListener('click',e=>{const n=e.target.closest('.nm');if(n)openK(n.dataset.code);});
$('q').oninput=render;$('hidchk').onchange=render;senti();tiles();btNote();breadthBox();groupsBox();largeBox();astatsBox();render();
/*__TODAY_JS__*/
// 上方捲軸與表格同步
(function(){const top=$('topscroll'),tb=$('tbl');
 function size(){top.firstElementChild.style.width=tb.scrollWidth+'px';top.hidden=tb.scrollWidth<=tb.clientWidth+2;}
 top.addEventListener('scroll',()=>{if(Math.abs(tb.scrollLeft-top.scrollLeft)>1)tb.scrollLeft=top.scrollLeft;});
 tb.addEventListener('scroll',()=>{if(Math.abs(tb.scrollLeft-top.scrollLeft)>1)top.scrollLeft=tb.scrollLeft;});
 window.addEventListener('resize',size);new MutationObserver(size).observe($('bd'),{childList:true});setTimeout(size,0);})();
// 浮動橫向捲軸（不用滑到表格最底下就能左右捲）
function floatBar(el){
  const bar=document.createElement('div');bar.className='hbar';bar.hidden=true;
  bar.innerHTML='<button type="button" aria-label="往左捲">◀</button><div class="hb-track"><div></div></div><button type="button" aria-label="往右捲">▶</button>';
  document.body.appendChild(bar);
  const [bl,br]=bar.querySelectorAll('button'),tr=bar.querySelector('.hb-track'),inner=tr.firstElementChild;
  bl.onclick=()=>el.scrollBy({left:-el.clientWidth*.6,behavior:'smooth'});
  br.onclick=()=>el.scrollBy({left:el.clientWidth*.6,behavior:'smooth'});
  tr.addEventListener('scroll',()=>{if(Math.abs(el.scrollLeft-tr.scrollLeft)>1)el.scrollLeft=tr.scrollLeft;});
  function upd(){
    const r=el.getBoundingClientRect(),vh=window.innerHeight;
    const show=el.scrollWidth>el.clientWidth+2&&r.top<vh-60&&r.bottom>vh+2;
    bar.hidden=!show;if(!show)return;
    const left=Math.max(r.left,0),w=Math.min(r.right,window.innerWidth)-left;
    bar.style.left=left+'px';bar.style.width=w+'px';
    // 讓捲軸可捲距離和表格一樣，直接同步 scrollLeft
    inner.style.width=(el.scrollWidth-el.clientWidth+tr.clientWidth)+'px';
    if(Math.abs(tr.scrollLeft-el.scrollLeft)>1)tr.scrollLeft=el.scrollLeft;
  }
  el.addEventListener('scroll',upd);window.addEventListener('scroll',upd,{passive:true});window.addEventListener('resize',upd);
  new MutationObserver(upd).observe(el,{childList:true,subtree:true});setTimeout(upd,0);
}
floatBar($('tbl'));
$('bd').addEventListener('click',e=>{const td=e.target.closest('td.news');if(td&&!e.target.closest('a'))td.classList.toggle('open');});
</script>
<script src="claude_link.js"></script>
</body>
</html>
"""


def _clean(v):
    if hasattr(v, "item") and not isinstance(v, (list, dict, str)):
        v = v.item()
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def build_rows(stocks: pd.DataFrame, met: pd.DataFrame, hits: dict[str, list[str]],
               extras: pd.DataFrame | None = None, ann: dict | None = None, news: dict | None = None) -> list[dict]:
    tags: dict[str, list[str]] = {}
    for name, codes in hits.items():
        for c in codes:
            tags.setdefault(c, []).append(name)
    info = stocks.set_index("code")
    rows = []
    for code, m in met.iterrows():
        mk = info.at[code, "market"] if code in info.index else "TWSE"
        ind = info.at[code, "industry"] if code in info.index else ""
        rows.append(
            {
                "code": code,
                "name": info.at[code, "name"] if code in info.index else "",
                "market": mk,
                "industry": ind if isinstance(ind, str) else "",
                "url": f"https://tw.stock.yahoo.com/quote/{code}.{'TW' if mk == 'TWSE' else 'TWO'}",
                "tags": tags.get(code, []),
                **{k: _clean(v) for k, v in m.items()},
            }
        )
        r = rows[-1]
        ex = extras.loc[code] if extras is not None and code in extras.index else None
        for k in ["pe", "trust", "foreign", "rev_yoy", "foreign_streak", "trust_streak", "rev_streak", "rev_yoy_3m",
                  "rev_accel", "margin_chg", "margin_5d_pct", "margin_util", "margin_streak", "short_ratio", "dt_ratio",
                  "dealer", "inst_pct", "inst_pct_5d"]:
            r[k] = _clean(float(ex[k])) if ex is not None and k in ex and pd.notna(ex[k]) else None
        if r["pe"] is None and ex is not None and "pe" in ex:
            r["pe"] = -1 if "pb" in ex and pd.notna(ex.get("pb")) else None  # 有資料但沒本益比 = 虧損
        sh = ex.get("shares") if ex is not None else None
        r["turnover"] = _clean(r["volume_lots"] * 1000 / float(sh) * 100) if sh and pd.notna(sh) and r.get("volume_lots") else None
        r["rev_high12"] = bool(ex["rev_high12"]) if ex is not None and "rev_high12" in ex and pd.notna(ex["rev_high12"]) else None
        r["flag"] = ex["flag"] if ex is not None and "flag" in ex and isinstance(ex["flag"], str) else ""
        r["ann"] = (ann or {}).get(code, [])
        r["news"] = (news or {}).get(code, [])
    return rows


def _qday(date: str) -> str:
    try:
        from . import fetch
        from .qday import quarter_info
        h = fetch.load_history()
        return quarter_info(date, h.date.unique())["text"]
    except Exception:  # noqa: BLE001
        return ""


def _bt5() -> dict:
    """每日篩選各策略 5 年回測（tools/strat5y/run.py 產生）。"""
    f = Path(__file__).resolve().parent.parent / "data" / "extras" / "strat_5y.json"
    try:
        return json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _today() -> dict:
    try:
        from . import today
        return _clean(today.build())
    except Exception as e:  # noqa: BLE001
        logging.warning("今天重點失敗：%s", e)
        return {}


def _today_parts() -> tuple[str, str, str]:
    from . import today
    return today.TEMPLATE_HTML, today.CSS, today.JS


def _hidden() -> list:
    """每日篩選頁不顯示的策略（config.yaml report.hidden_strategies；照樣每天算，只是不放首頁）。"""
    try:
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text("utf-8")) or {}
        return list((cfg.get("report") or {}).get("hidden_strategies") or [])
    except Exception:  # noqa: BLE001
        return []


def render_html(title, date, scanned, rows, strat_info, spark_days, archive_link="", senti=None, groups=None,
                breadth=None, large=None, astats=None):
    js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__QDAY__", _qday(date))
        .replace("__DATE__", date)
        .replace("__SCANNED__", f"{scanned:,}")
        .replace("__ARCHIVE__", archive_link)
        .replace("__SPARK__", str(spark_days))
        .replace("__DATA__", js(rows))
        .replace("__STRATS__", js(strat_info))
        .replace("__SENTI__", js(_clean(senti or {})))
        .replace("__GROUPS__", js(_clean(groups or [])))
        .replace("__BREADTH__", js(_clean(breadth or [])))
        .replace("__LARGE__", js(_clean(large or [])))
        .replace("__ASTATS__", js(_clean(astats or {})))
        .replace("__BT5__", js(_bt5()))
        .replace("__HIDDEN__", js(_hidden()))
        .replace("__TODAY__", js(_today()))
        .replace("__TODAY_HTML__", _today_parts()[0])
        .replace("/*__TODAY_CSS__*/", _today_parts()[1])
        .replace("/*__TODAY_JS__*/", _today_parts()[2])
    )


# ------------------------------------------------------------ 只改版面時：用現有資料重新產生網頁
def save_inputs(path: Path, **kw) -> None:
    """把產生報表用的資料存起來（data/report_data.json），之後改版面可以直接重畫，不用重抓資料。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(kw), ensure_ascii=False, separators=(",", ":")), "utf-8")


def inputs_from_html(html: str) -> dict:
    """舊報表沒有存 report_data.json 時，從 index.html 裡嵌入的資料還原。"""
    import re

    def const(name, default):
        m = re.search(rf"^const {name}=(.*);$", html, re.M)
        return json.loads(m.group(1)) if m else default

    sub = re.search(r"資料日期 (\S+?)(?:（.*?）)?\s*・ 共掃描 ([\d,]+) 檔", html)  # 舊版報表沒有季別文字
    return {"title": re.search(r"<h1>(.*?)</h1>", html).group(1), "date": sub.group(1),
            "scanned": int(sub.group(2).replace(",", "")),
            "spark_days": int(re.search(r"近(\d+)日走勢", html).group(1)),
            "rows": const("DATA", []), "strat_info": const("STRATS", []), "senti": const("SENTI", {}),
            "groups": const("GROUPS", []), "breadth": const("BREADTH", []), "large": const("LARGE", []),
            "astats": const("ASTATS", {})}


def rebuild(site_dir: Path, data_path: Path) -> str:
    """用 report_data.json（沒有就從 index.html 還原）以目前的版面重新產生網頁，回傳資料日期。"""
    kw = json.loads(data_path.read_text("utf-8")) if data_path.exists() else \
        inputs_from_html((site_dir / "index.html").read_text("utf-8"))
    write_site(site_dir, kw["date"], lambda link: render_html(archive_link=link, **kw))
    return kw["date"]


def write_site(out_dir: Path, date: str, html_for) -> None:
    """寫出 site/：YYYY-MM-DD.html（當日）、index.html（最新）、archive.html（歷史清單）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    from . import gifts, longterm, macro, revdrift, sitenav, stockpage, us, usx, weekly
    weeks = weekly.write(out_dir)
    try:
        has_us = us.write(out_dir)
    except Exception as e:  # 美股頁壞掉不能拖垮每日報表
        logging.warning("美股頁產生失敗：%s", e)
        has_us = False
    try:
        has_rev = revdrift.write(out_dir)
    except Exception as e:  # 研究頁壞掉不能拖垮每日報表
        logging.warning("營收漂移成績頁產生失敗：%s", e)
        has_rev = False
    try:
        has_macro = macro.write(out_dir)
    except Exception as e:  # 大環境頁壞掉不能拖垮每日報表
        logging.warning("大環境頁產生失敗：%s", e)
        has_macro = False
    try:
        has_gifts = gifts.write(out_dir)
    except Exception as e:  # 紀念品頁壞掉不能拖垮每日報表
        logging.warning("股東紀念品頁產生失敗：%s", e)
        has_gifts = False
    try:
        longterm.write(out_dir)
    except Exception as e:  # 長期 Top 20 頁壞掉不能拖垮每日報表
        logging.warning("長期 Top 20 頁產生失敗：%s", e)
    try:
        usx.write(out_dir)
    except Exception as e:  # 美股篩選頁壞掉不能拖垮每日報表
        logging.warning("美股篩選頁產生失敗：%s", e)
    try:
        stockpage.write(out_dir)
    except Exception as e:  # 個股頁壞掉不能拖垮每日報表
        logging.warning("個股頁產生失敗：%s", e)
    links = "<!--SITENAV:daily-->"  # 導覽列最後由 sitenav.apply 換成真的（那時才知道哪些頁有產生）
    (out_dir / f"{date}.html").write_text(html_for(links), "utf-8")
    (out_dir / "index.html").write_text(html_for(links), "utf-8")
    js = Path(__file__).with_name("claude_link.js")
    if js.exists():
        (out_dir / "claude_link.js").write_text(js.read_text("utf-8"), "utf-8")
    live = Path(__file__).with_name("live_page.html")
    if live.exists():
        (out_dir / "live.html").write_text(live.read_text("utf-8"), "utf-8")
    days = sorted((p.stem for p in out_dir.glob("20??-??-??.html")), reverse=True)
    items = "".join(f"<li><a href='{d}.html'>{d}</a></li>" for d in days)
    (out_dir / "archive.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>歷史報表</title><style>body{font:15px/1.8 sans-serif;max-width:600px;margin:24px auto;padding:0 16px;"
        "background:#f7f7f5;color:#1d1d1b}@media(prefers-color-scheme:dark){body{background:#141413;color:#ecebe6}"
        "a{color:#7d9cf0}}</style><!--SITENAV:archive--><h2>歷史報表</h2>"
        f"<ul>{items}</ul>",
        "utf-8",
    )
    try:
        sitenav.apply(out_dir)
    except Exception as e:  # 導覽列壞掉不能拖垮每日報表
        logging.warning("導覽列產生失敗：%s", e)
