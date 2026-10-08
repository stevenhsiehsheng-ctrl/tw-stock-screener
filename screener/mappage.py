"""市場地圖 site/map.html：產業分組的方塊圖，方塊大小＝市值（或成交值），顏色＝漲跌（今天／5 日／20 日／一年）。
資料直接用自訂選股（screenpage.build）那份，不另外抓；點方塊進個股頁。版面是自己寫的 squarified treemap，不靠外部函式庫。
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)
MAX_N = 800

PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>市場地圖</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--accent:#2f5bd3}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1400px;margin:0 auto;padding:20px 16px 50px}h1{font-size:23px;margin:8px 0 4px}.meta{color:var(--muted);font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}
.bar button{font:inherit;font-size:13.5px;padding:4px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.bar button.on{background:var(--accent);border-color:var(--accent);color:#fff}.bar i{width:1px;height:18px;background:var(--line);margin:0 4px}
#map{position:relative;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.g{position:absolute;box-sizing:border-box;border:1.5px solid var(--bg);overflow:hidden}
.g>b{position:absolute;left:3px;top:0;font-size:11.5px;font-weight:650;white-space:nowrap;z-index:2;pointer-events:none;text-shadow:0 0 3px var(--card)}
.t{position:absolute;box-sizing:border-box;border:1px solid rgba(255,255,255,.35);display:flex;flex-direction:column;align-items:center;justify-content:center;
 text-decoration:none;color:inherit;overflow:hidden;text-align:center;line-height:1.15;font-size:11px}
.t:hover{outline:2px solid var(--fg);z-index:3}.t span{white-space:nowrap}.t .p{font-size:10.5px;opacity:.9}
.leg{display:flex;flex-wrap:wrap;align-items:center;gap:4px;font-size:12px;color:var(--muted);margin:8px 0}.leg span{display:inline-block;width:26px;height:12px;border-radius:2px}
#tip{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:9}
</style></head><body><main>
<!--SITENAV:map-->
<h1>🗺 市場地圖</h1>
<div class="meta">資料日 __DATE__・上市櫃普通股市值前 __N__ 大・按產業分組，方塊越大市值（或成交值）越大，紅漲綠跌・點方塊看個股</div>
<div class="bar" id="bar">
 <button data-p="d1" class="on">今天</button><button data-p="r5">5 日</button><button data-p="r20">20 日</button><button data-p="r250">一年</button><i></i>
 <button data-s="cap" class="on">大小＝市值</button><button data-s="val20">大小＝成交值</button><i></i>
 <button data-m="" class="on">上市＋上櫃</button><button data-m="上市">上市</button><button data-m="上櫃">上櫃</button>
</div>
<div class="leg" id="leg"></div>
<div id="map"></div><div id="tip"></div>
<p class="meta">報酬是價格報酬（減資已接、除息沒還原）。產業旁的 % 是該產業方塊的市值加權漲跌。不構成投資建議。</p>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const IX=Object.fromEntries(D.cols.map((k,i)=>[k,i])),SC={d1:3,r5:8,r20:15,r250:60},PN={d1:'今天',r5:'5 日',r20:'20 日',r250:'一年'};
let P='d1',SZ='cap',MK='';
function worst(row,s){let sum=0,mx=0,mn=Infinity;for(const o of row){sum+=o.a;if(o.a>mx)mx=o.a;if(o.a<mn)mn=o.a}return Math.max(s*s*mx/(sum*sum),sum*sum/(s*s*mn))}
function squarify(items,x,y,w,h){const out=[],tot=items.reduce((a,o)=>a+o.v,0);if(!tot||w<=0||h<=0)return out;
 let rest=items.map(o=>({...o,a:o.v*w*h/tot}));
 while(rest.length){const s=Math.min(w,h);let row=[rest[0]],i=1,wr=worst(row,s);
  while(i<rest.length){const nr=row.concat([rest[i]]),nw=worst(nr,s);if(nw>wr)break;row=nr;wr=nw;i++}
  const sum=row.reduce((a,o)=>a+o.a,0);
  if(w>=h){const cw=sum/h;let cy=y;for(const o of row){const ch=o.a/cw;out.push({...o,x,y:cy,w:cw,h:ch});cy+=ch}x+=cw;w-=cw}
  else{const rh=sum/w;let cx=x;for(const o of row){const cw=o.a/rh;out.push({...o,x:cx,y,w:cw,h:rh});cx+=cw}y+=rh;h-=rh}
  rest=rest.slice(i)}
 return out}
function color(v){if(v==null)return['rgba(128,128,128,.25)','inherit'];const t=Math.max(-1,Math.min(1,v/SC[P])),a=.12+.83*Math.abs(t);
 return[v>=0?`rgba(208,52,52,${a})`:`rgba(18,140,66,${a})`,a>.5?'#fff':'inherit']}
const pct=v=>v==null?'—':(v>0?'+':'')+v.toFixed(Math.abs(v)>=100?0:Math.abs(v)>=10?1:2)+'%';
function legend(){const s=SC[P],st=[-1,-.66,-.33,0,.33,.66,1];$('leg').innerHTML=`<span style="width:auto;background:none">${PN[P]}</span>`+st.map(t=>`<span style="background:${color(t*s)[0]}" title="${pct(t*s)}"></span>`).join('')+` <span style="width:auto;background:none">${pct(-s)} ～ ${pct(s)} 以上</span>`}
function draw(){const box=$('map'),W=box.clientWidth,H=Math.round(W<700?W*1.5:Math.min(760,Math.max(480,innerHeight*.72)));box.style.height=H+'px';
 const rows=D.rows.filter(r=>(!MK||r[IX.m]===MK)&&r[IX[SZ]]>0);const groups={};
 rows.forEach(r=>{(groups[r[IX.i]||'其他']??=[]).push(r)});
 const G=Object.entries(groups).map(([k,rs])=>({k,rs,v:rs.reduce((a,r)=>a+r[IX[SZ]],0)})).sort((a,b)=>b.v-a.v);
 let h='';for(const g of squarify(G,0,0,W,H)){
  const capw=g.rs.reduce((a,r)=>a+(r[IX[P]]!=null?r[IX.cap]:0),0),avg=capw?g.rs.reduce((a,r)=>a+(r[IX[P]]!=null?r[IX[P]]*r[IX.cap]:0),0)/capw:null;
  const lab=g.w>60&&g.h>34,top=lab?15:0;
  h+=`<div class="g" style="left:${g.x}px;top:${g.y}px;width:${g.w}px;height:${g.h}px">${lab?`<b>${esc(g.k)} ${pct(avg)}</b>`:''}`;
  const st=squarify(g.rs.map(r=>({r,v:r[IX[SZ]]})).sort((a,b)=>b.v-a.v),0,top,g.w-3,g.h-3-top);
  for(const t of st){const r=t.r,v=r[IX[P]],[bg,fg]=color(v),big=t.w>34&&t.h>16,two=t.w>40&&t.h>30;
   h+=`<a class="t" href="stock.html?code=${esc(r[IX.c])}" data-c="${esc(r[IX.c])}" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;background:${bg};color:${fg};font-size:${Math.max(9,Math.min(15,Math.sqrt(t.w*t.h)/6)).toFixed(0)}px">`+
    (big?`<span>${esc(t.w>58?r[IX.n]:r[IX.c])}</span>`:'')+(two?`<span class="p">${pct(v)}</span>`:'')+'</a>'}
  h+='</div>'}
 box.innerHTML=h;legend()}
const ROW=Object.fromEntries(D.rows.map(r=>[r[IX.c],r]));
$('map').addEventListener('mousemove',e=>{const a=e.target.closest('.t');const tp=$('tip');if(!a){tp.style.display='none';return}const r=ROW[a.dataset.c];
 tp.innerHTML=`<b>${esc(r[IX.n])}</b> ${esc(r[IX.c])}・${esc(r[IX.i])}<br>今天 ${pct(r[IX.d1])}・5 日 ${pct(r[IX.r5])}・20 日 ${pct(r[IX.r20])}・一年 ${pct(r[IX.r250])}<br>市值 ${r[IX.cap]==null?'—':Math.round(r[IX.cap]).toLocaleString()} 億・成交值 ${r[IX.val20]==null?'—':r[IX.val20].toFixed(2)} 億/天`;
 tp.style.display='block';const x=Math.min(e.clientX+14,innerWidth-tp.offsetWidth-8),y=Math.min(e.clientY+14,innerHeight-tp.offsetHeight-8);tp.style.left=x+'px';tp.style.top=y+'px'});
$('map').addEventListener('mouseleave',()=>$('tip').style.display='none');
$('bar').querySelectorAll('button').forEach(b=>b.onclick=()=>{const k=b.dataset.p!=null?'p':b.dataset.s!=null?'s':'m';
 $('bar').querySelectorAll(`button[data-${k}]`).forEach(x=>x.classList.toggle('on',x===b));
 if(k==='p')P=b.dataset.p;else if(k==='s')SZ=b.dataset.s;else MK=b.dataset.m;draw()});
let RT;addEventListener('resize',()=>{clearTimeout(RT);RT=setTimeout(draw,150)});draw();
</script></body></html>"""


def write(site_dir: Path, d: dict) -> bool:
    """d＝screenpage.build() 的結果；只留市值前 MAX_N 大、地圖用得到的欄位。"""
    ix = {k: i for i, k in enumerate(d["cols"])}
    keep = ["c", "n", "m", "i", "cap", "val20", "d1", "r5", "r20", "r250"]
    rows = [r for r in d["rows"] if r[ix["cap"]] is not None and r[ix["cap"]] > 0]
    rows = sorted(rows, key=lambda r: -r[ix["cap"]])[:MAX_N]
    data = {"cols": keep, "rows": [[r[ix[k]] for k in keep] for r in rows]}
    page = (PAGE.replace("__DATE__", html.escape(d["date"])).replace("__N__", str(len(rows)))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=float).replace("</", "<\\/")))
    (site_dir / "map.html").write_text(page, "utf-8")
    log.info("市場地圖：%d 檔", len(rows))
    return True
