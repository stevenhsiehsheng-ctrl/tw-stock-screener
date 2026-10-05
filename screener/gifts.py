"""股東會紀念品：抓全年紀念品清單 → data/gifts/{年}.csv，網站「股東紀念品」頁（site/gifts.html）。

來源：HiStock 嗨投資「股東會紀念品」全年清單（股價、最後買進日、股東會日期、紀念品、零股寄單、股代）。
估值：商品卡、禮物卡這類等同現金的，用面額；其他實體禮品沒有估值。
CP 值 = 估值 ÷ 1 股成本（股價＋手續費 1 元，國泰電子下單零股最低 1 元）。

用法：
  python -m screener.gifts            抓清單、存檔（GitHub Actions gifts.yml 每天跑）
  python -m screener.gifts --probe    只印出候選資料來源的格式
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "gifts"
TZ = ZoneInfo("Asia/Taipei")
HISTOCK = "https://histock.tw/stock/gift.aspx"
FEE = 1.0   # 零股電子下單手續費最低 1 元（國泰）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CANDIDATES = [
    ("HiStock 紀念品", HISTOCK),
    ("股代網", "https://www.gooddie.tw/stock/meeting"),
    ("元大股代", "https://www.yuanta.com.tw/eYuanta/agent/AgentAPI/ShareHolderMeeting"),
    ("中信股代", "https://ecorp.ctbcbank.com/cts/static/ag_gift.jsp"),
    ("中信股代（列印版）", "https://ecorp.ctbcbank.com/cts/static/ag_gift_printer.jsp"),
    ("宏遠股代", "https://srd.honsec.com.tw/stock/souvenir.aspx"),
    ("玩股網", "https://www.wantgoo.com/stock/calendar/shareholders-meeting-souvenirs"),
    ("換換零股", "https://www.stockbox.com.tw/meetings?year=2026"),
    ("永豐", "https://www.sinotrade.com.tw/richclub/tools/gifts"),
    ("口袋", "https://events.pocket.tw/pocketsmlist-34839"),
]

COLS = ["code", "name", "price", "last_buy", "meeting", "kind", "place", "gift",
        "odd_mail", "agent", "agent_tel", "value"]

# 等同現金的禮券（用面額估值）；折價券、抵用券、自家購物金、滿額才能用的有使用條件，不算
_CARD = re.compile(r"商品卡|禮物卡|禮券|禮卡|商品券|提貨券|儲值卡|現金|悠遊卡|一卡通|電子票券")
_COND = re.compile(r"抵用|折價|折抵|優惠|折扣|買一送一|買1送1|購物金|滿\s*\$?\d|結帳|取消")
_AMT = re.compile(r"(\d{2,5})\s*元")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    return s


def _text(r: requests.Response) -> str:
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


_CN = {"三十五": "35", "兩百": "200", "二百": "200", "三百": "300", "一百": "100", "五十": "50",
       "三十": "30", "二十": "20"}


def face_value(gift: str) -> float | None:
    """商品卡、禮物卡這類等同現金的紀念品回傳面額，其他回傳 None。"""
    g = unicodedata.normalize("NFKC", str(gift or ""))
    if _COND.search(g) or not _CARD.search(g):
        return None
    g = re.sub(r"7\s*-\s*11|7-?eleven|711", " 超商 ", g, flags=re.I)   # 「7-1135元」是 7-11 的 35 元
    for k, v in _CN.items():
        g = g.replace(k, v)
    m = _AMT.findall(g) or re.findall(r"\$\s*(\d{2,5})", g)
    return float(m[0]) if m else None


def _date(md: str, year: int) -> dt.date | None:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})", str(md or ""))
    if not m:
        return None
    try:
        return dt.date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def parse_histock(page: str) -> tuple[int, pd.DataFrame]:
    """HiStock 紀念品頁 → (年度, 清單)。日期只有月/日，年度從標題「2026年(115)」取。"""
    m = re.search(r"<title[^>]*>[^<]*?(20\d\d)年", page)
    year = int(m.group(1)) if m else dt.datetime.now(TZ).year
    tables = pd.read_html(io.StringIO(page))
    df = max((t for t in tables if {"代號", "股東會紀念品"} <= {str(c) for c in t.columns}),
             key=len, default=None)
    if df is None or len(df) == 0:
        raise ValueError("HiStock 頁面找不到紀念品表格")
    df = df[df["代號"].astype(str).str.fullmatch(r"\d{4,6}[A-Z]?(\.0)?")].copy()
    out = pd.DataFrame({
        "code": df["代號"].astype(str).str.replace(r"\.0$", "", regex=True),
        "name": df["名稱"].astype(str).str.strip(),
        "price": pd.to_numeric(df["股價"], errors="coerce"),
        "kind": df["性質"].astype(str).str.strip(),
        "place": df.get("開會地點", "").astype(str).str.strip(),
        "gift": [unicodedata.normalize("NFKC", str(g)).replace("參考圖", "").strip() for g in df["股東會紀念品"]],
        "odd_mail": df.get("零股寄單", "").astype(str).str.strip(),
        "agent": df.get("股代", "").astype(str).str.strip(),
        "agent_tel": df.get("股代電話", "").astype(str).str.strip(),
    })
    meet = [_date(x, year) for x in df["股東會日期"]]
    buy = [_date(x, year) for x in df["最後買進日"]]
    # 隔年年初的股東會，最後買進日在前一年年底
    buy = [b.replace(year=b.year - 1) if b and mt and b > mt else b for b, mt in zip(buy, meet)]
    out["meeting"] = [d.isoformat() if d else "" for d in meet]
    out["last_buy"] = [d.isoformat() if d else "" for d in buy]
    out["gift"] = out["gift"].replace({"nan": ""})
    out["value"] = [face_value(g) for g in out["gift"]]
    out = out.drop_duplicates(["code", "meeting", "gift"])
    return year, out[COLS].reset_index(drop=True)


def refresh() -> Path:
    """抓 HiStock 全年清單，存成 data/gifts/{年}.csv，回傳檔案路徑。"""
    r = _session().get(HISTOCK, timeout=60)
    r.raise_for_status()
    year, df = parse_histock(_text(r))
    if len(df) < 20:
        raise ValueError(f"HiStock 只抓到 {len(df)} 筆，可能改版了")
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{year}.csv"
    df.to_csv(path, index=False)
    meta = {"updated": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "source": HISTOCK,
            "year": year, "rows": len(df)}
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    logging.info("紀念品清單 %s 年 %d 筆 → %s", year, len(df), path)
    return path


# ---------- 網頁 ----------

GUIDE = """
<h2>怎麼領（先看這段）</h2>
<ol class="guide">
<li><b>買 1 股就是股東。</b>在「最後買進日」收盤前買進（盤中零股、盤後零股都可以），交割完成後就會在股東名冊上。
股東常會開會前 60 天停止過戶、臨時會前 30 天，再扣掉 T+2 交割，所以<b>常會大約要在開會前兩個月買</b>。表裡的最後買進日已經幫你算好。</li>
<li><b>零股能不能領，看公司規定</b>（開會通知、公開資訊觀測站的股東會公告裡寫）：
<ul><li>「不限股數」：零股照發。</li>
<li>「未滿 1,000 股須電子投票（或親自出席）才發」：最常見。用「集保e手掌握」App、股東e票通網站或券商 App 投票。</li>
<li>表中「零股寄單：否」是公司<b>不寄開會通知書</b>給零股股東，一樣可以電子投票，再帶身分證去領，或交給代領。</li></ul></li>
<li><b>三種領法：</b>
<ul><li>自己去：在「發放期間」帶身分證（有通知書就一起帶）到股務代理或公司指定地點。股代電話在表裡，不確定就打去問。</li>
<li>eGift（2026 年起）：有些公司改發電子禮券，電子投票、沒交委託書的股東，到投票平台的「紀念品」專區線上領，不用跑。</li>
<li>代領：交給代領業者（例如股代網、換換零股），要付代領費、照它的截止日委託。</li></ul></li>
<li><b>成本：</b>1 股的錢買了還是你的，真正花掉的是手續費（國泰電子下單零股最低 1 元）、賣出時的證交稅，還有股價漲跌。
所以 CP 值是「每 1 元本金換到多少紀念品」，不是報酬率。</li>
<li><b>注意：</b>紀念品數量有限、可能以等值商品替代；每年送的不一定一樣，去年的只能參考；
不要為了紀念品買體質很差的股票，股價跌一點就把禮物吃掉了。</li>
</ol>
<h2>一年的時間表</h2>
<ul class="guide">
<li><b>1～3 月</b>：公司陸續公告股東常會日期和紀念品。</li>
<li><b>3 月中～4 月中</b>：大部分常會的最後買進日（開會前約兩個月）。</li>
<li><b>4 月底～6 月</b>：電子投票、發放期間，股東常會集中在 5、6 月。</li>
<li><b>下半年</b>：零星的股東臨時會（停止過戶 30 天），紀念品通常比較少。</li>
</ul>
"""

CSS_EXTRA = """
.meta{color:var(--muted);font-size:14px}
.guide li{margin:6px 0}
td.gift{white-space:normal;min-width:150px;text-align:left!important}
td.n,th.n{text-align:right!important}
td:not(:first-child),th:not(:first-child){text-align:left}
.hi{color:#c0392b;font-weight:600}
input#q{width:100%;max-width:360px;padding:8px 10px;font-size:15px;border:1px solid var(--line);border-radius:6px;
  background:var(--card);color:var(--fg);margin:8px 0}
details summary{cursor:pointer;font-weight:600;margin:12px 0}
"""

SCRIPT = """<script>
(function(){
  var t=new Date(),p=function(n){return (n<10?'0':'')+n},
      today=t.getFullYear()+'-'+p(t.getMonth()+1)+'-'+p(t.getDate());
  document.querySelectorAll('tr[data-lb]').forEach(function(r){if(r.dataset.lb<today)r.remove();});
  var left=document.querySelectorAll('#soon tbody tr').length,s=document.getElementById('soon-n');
  if(s)s.textContent=left;
  var q=document.getElementById('q');
  if(q)q.addEventListener('input',function(){
    var v=q.value.trim().toLowerCase();
    document.querySelectorAll('#all tbody tr').forEach(function(r){
      r.style.display=!v||r.textContent.toLowerCase().indexOf(v)>=0?'':'none';});
  });
})();
</script>"""


def _fmt(x, nd=1) -> str:
    if x is None or pd.isna(x):
        return ""
    s = f"{x:,.{nd}f}"
    return s.rstrip("0").rstrip(".") if nd else s


def _rows(df: pd.DataFrame, cols: list[str], mark_lb: bool = False) -> str:
    out = []
    for r in df.itertuples(index=False):
        cost = r.price + FEE if pd.notna(r.price) else None
        cp = r.value / cost if pd.notna(r.value) and cost else None
        cell = {
            "last_buy": f"<td>{r.last_buy[5:].replace('-', '/') if r.last_buy else ''}</td>",
            "meeting": f"<td>{r.meeting[5:].replace('-', '/') if r.meeting else ''}</td>",
            "stock": f"<td>{html.escape(r.code)} {html.escape(r.name)}</td>",
            "kind": f"<td>{html.escape(r.kind)}</td>",
            "gift": f"<td class='gift'>{html.escape(r.gift)}</td>",
            "value": f"<td class='n'>{_fmt(r.value, 0)}</td>",
            "cost": f"<td class='n'>{_fmt(cost, 2)}</td>",
            "cp": f"<td class='n{' hi' if cp and cp >= 2 else ''}'>{_fmt(cp, 1)}</td>",
            "odd_mail": f"<td>{html.escape(r.odd_mail)}</td>",
            "agent": f"<td>{html.escape(r.agent)} {html.escape(r.agent_tel)}</td>",
        }
        lb = f" data-lb='{r.last_buy}'" if mark_lb and r.last_buy else ""
        out.append(f"<tr{lb}>" + "".join(cell[c] for c in cols) + "</tr>")
    return "".join(out)


HEAD = {"last_buy": "最後買進日", "meeting": "股東會", "stock": "股票", "kind": "性質", "gift": "紀念品",
        "value": "估值", "cost": "1 股成本", "cp": "CP", "odd_mail": "零股寄單", "agent": "股代"}


def _table(df: pd.DataFrame, cols: list[str], tid: str = "", mark_lb: bool = False) -> str:
    th = "".join(f"<th{' class=n' if c in ('value', 'cost', 'cp') else ''}>{HEAD[c]}</th>" for c in cols)
    idattr = f" id='{tid}'" if tid else ""
    return (f"<div class='tbl'><table{idattr}><thead><tr>{th}</tr></thead>"
            f"<tbody>{_rows(df, cols, mark_lb)}</tbody></table></div>")


def load(year: int | None = None) -> tuple[int, pd.DataFrame] | None:
    files = sorted(DATA.glob("20??.csv")) if DATA.exists() else []
    if year is not None:
        files = [f for f in files if f.stem == str(year)]
    if not files:
        return None
    f = files[-1]
    df = pd.read_csv(f, dtype={"code": str, "last_buy": str, "meeting": str, "odd_mail": str,
                               "agent": str, "agent_tel": str, "kind": str, "gift": str, "name": str})
    for c in ("gift", "odd_mail", "agent", "agent_tel", "kind", "last_buy", "meeting", "place"):
        if c in df:
            df[c] = df[c].fillna("")
    df["value"] = [face_value(g) for g in df["gift"]]   # 估值規則改了不用重抓
    return int(f.stem), df


def render(today: dt.date | None = None) -> str | None:
    """產生 gifts.html 的內容；還沒有資料就回傳 None。"""
    got = load()
    if got is None:
        return None
    year, df = got
    today = today or dt.datetime.now(TZ).date()
    meta = {}
    if (DATA / "meta.json").exists():
        meta = json.loads((DATA / "meta.json").read_text("utf-8"))
    has_gift = df[~df["gift"].str.fullmatch(r"\s*(無|不發放|不發|未決定|等待公告|未公告|-)?\s*")
                  & ~df["gift"].str.contains("取消")]
    soon = has_gift[has_gift["last_buy"] >= today.isoformat()].sort_values(["last_buy", "meeting"])
    cost = has_gift["price"] + FEE
    ranked = has_gift.assign(cp=has_gift["value"] / cost).dropna(subset=["cp"]).sort_values("cp", ascending=False)

    from .weekly import CSS
    nav = "<a href='index.html'>← 每日報表</a>"
    body = [f"<h1>🎁 股東紀念品（{year} 年）</h1>",
            f"<p class='meta'>資料更新：{html.escape(meta.get('updated', ''))}・來源："
            f"<a href='{HISTOCK}'>HiStock 嗨投資</a>（各公司公告整理）・"
            f"共 {len(df)} 場股東會，{len(has_gift)} 場有紀念品。實際以公司公告為準。</p>",
            "<p class='meta'>估值：只算商品卡、禮物卡這類等同現金的（用面額）；折價券、自家購物金、實體禮品不估。"
            "1 股成本＝股價＋手續費 1 元。CP＝估值 ÷ 1 股成本，≥2 標紅。</p>"]

    body.append(f"<h2>還來得及買（<span id='soon-n'>{len(soon)}</span> 場）</h2>")
    if len(soon):
        body.append(_table(soon, ["last_buy", "stock", "cp", "value", "cost", "gift", "meeting", "kind",
                                  "odd_mail", "agent"], "soon", mark_lb=True))
    else:
        body.append("<p>目前沒有還來得及的。股東常會的最後買進日集中在 3～4 月，公司 1～3 月公告後會出現在這裡。</p>")

    body.append(f"<h2>CP 值排行（{year} 年，明年可參考）</h2>")
    if len(ranked):
        body.append(_table(ranked.head(40), ["stock", "cp", "value", "cost", "gift", "meeting", "kind",
                                             "odd_mail", "agent"]))
    else:
        body.append("<p>還沒有可以估值的紀念品。</p>")

    body.append(GUIDE)
    body.append(f"<details><summary>全部 {len(has_gift)} 場有紀念品的股東會（可搜尋）</summary>"
                "<input id='q' placeholder='搜尋代號、名稱、紀念品，例如：全家、7-11、米'>"
                + _table(has_gift.sort_values("meeting", ascending=False),
                         ["meeting", "stock", "cp", "value", "cost", "gift", "kind", "last_buy",
                          "odd_mail", "agent"], "all")
                + "</details>")
    body.append("<p class='meta'>僅供參考，不構成投資建議。</p>")
    return (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>股東紀念品</title>"
            f"<style>{CSS}{CSS_EXTRA}</style></head><body><main><nav>{nav}</nav>{''.join(body)}</main>"
            f"{SCRIPT}</body></html>")


def write(site_dir: Path) -> bool:
    """寫出 site/gifts.html；沒有資料就不寫，回傳有沒有寫。"""
    page = render()
    if page is None:
        return False
    (site_dir / "gifts.html").write_text(page, "utf-8")
    return True


# ---------- 測試資料來源 ----------

def probe() -> None:
    """印出每個候選來源的狀態、標題、API 線索與前幾列表格。"""
    s = _session()
    for name, url in CANDIDATES:
        print(f"\n===== {name}  {url}")
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException as e:
            print("  連線失敗", e)
            continue
        t = _text(r)
        print("  HTTP", r.status_code, "長度", len(r.content), "type", r.headers.get("content-type"), "最後網址", r.url)
        m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
        print("  title", (m.group(1).strip() if m else None))
        hints = sorted(set(re.findall(r"""["']([^"'\s]*(?:api|ajax|json|ashx|Handler|GetData|query)[^"'\s]*)["']""", t, re.I)))
        print("  API 線索", hints[:20])
        try:
            tables = pd.read_html(io.StringIO(t))
        except (ValueError, ImportError) as e:
            tables = []
            print("  沒有表格", str(e)[:80])
        print("  表格數", len(tables))
        for i, df in enumerate(tables[:3]):
            print(f"  -- 表 {i} {df.shape}")
            print(df.head(8).to_string()[:3000])
        if not tables:
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
            print("  內文", body[:1500])


def raw(url: str, key: str, width: int = 6000) -> None:
    """印出網頁原始碼在關鍵字附近的一段（看 HTML 結構用）。"""
    r = _session().get(url, timeout=30)
    t = _text(r)
    i = t.find(key)
    print(f"===== {url}  HTTP {r.status_code}  長度 {len(t)}  「{key}」在 {i}")
    print(t[max(i - 1500, 0):i + width] if i >= 0 else t[:width])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只印出候選資料來源的格式")
    ap.add_argument("--raw", nargs=2, metavar=("URL", "KEY"), help="印出網頁原始碼在關鍵字附近的一段")
    a = ap.parse_args()
    if a.raw:
        raw(*a.raw)
        return 0
    if a.probe:
        probe()
        return 0
    refresh()
    return 0


if __name__ == "__main__":
    sys.exit(main())
