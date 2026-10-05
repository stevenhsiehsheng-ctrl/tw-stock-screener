"""股東會紀念品：每天抓全年紀念品清單 → data/gifts/{年}.csv，網站「股東紀念品」頁（site/gifts.html）。

資料來源（合併，以「代號＋股東會日期」對起來）：
- HiStock 嗨投資「股東會紀念品」：股價、最後買進日、股東會日期、性質、開會城市、紀念品、零股寄單、股代
- 股代網：能不能代領、代領截止日、要不要電子投票與投票期間、收購價（代領商市場上有人願意付的現金）、
  還沒公告紀念品的臨時會
- 證交所／櫃買中心公司基本資料：官方登記的股票過戶機構（股代）名稱、電話、地址 → 領取地點的縣市區
- data/history.csv.gz：近 60 天單日漲跌的標準差 → 「最後買進日買、隔天賣」那一天的價格風險

估值：商品卡、禮物卡、禮券用面額；其他用股代網目前最高的收購出價；兩者都沒有就不估（不當 0）。
費用（網頁上可以切換領法、即時重算）：買進手續費 1 元；代領費 票券 12 元／其他 15 元＋運費每箱 53 元攤提；
隔天就賣的話再加賣出手續費 1 元＋證交稅 0.3%。實拿 = 估值 − 費用；CP = 實拿 ÷（股價＋1 元）。

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
import time
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "gifts"
HISTORY = ROOT / "data" / "history.csv.gz"
TZ = ZoneInfo("Asia/Taipei")
HISTOCK = "https://histock.tw/stock/gift.aspx"
GOODDIE = "https://www.gooddie.tw/stock/meeting"
TWSE_CO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_CO = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CANDIDATES = [
    ("HiStock 紀念品", HISTOCK),
    ("股代網", GOODDIE),
    ("元大股代", "https://www.yuanta.com.tw/eYuanta/agent/AgentAPI/ShareHolderMeeting"),
    ("中信股代", "https://ecorp.ctbcbank.com/cts/static/ag_gift.jsp"),
    ("中信股代（列印版）", "https://ecorp.ctbcbank.com/cts/static/ag_gift_printer.jsp"),
    ("宏遠股代", "https://srd.honsec.com.tw/stock/souvenir.aspx"),
    ("玩股網", "https://www.wantgoo.com/stock/calendar/shareholders-meeting-souvenirs"),
    ("換換零股", "https://www.stockbox.com.tw/meetings?year=2026"),
    ("永豐", "https://www.sinotrade.com.tw/richclub/tools/gifts"),
    ("口袋", "https://events.pocket.tw/pocketsmlist-34839"),
]

COLS = ["code", "name", "market", "price", "last_buy", "meeting", "kind", "place", "gift", "gift_type",
        "odd_mail", "agent", "agent_tel", "agent_addr", "region", "buy_min", "buy_max", "proxy_deadline",
        "proxy", "evote", "evote_period", "hist_id", "sd", "src"]

# 等同現金的禮券（用面額估值）；折價券、抵用券、自家購物金、滿額才能用的有使用條件，不算
_CARD = re.compile(r"商品卡|禮物卡|禮券|禮卡|商品券|提貨券|儲值卡|現金|悠遊卡|一卡通|電子票券|即享卡|即享券")
_COND = re.compile(r"抵用|折價|折抵|優惠|折扣|買一送一|買1送1|購物金|滿\s*\$?\d|結帳|取消")
_AMT = re.compile(r"(\d{2,5})\s*元")
_CN = {"三十五": "35", "兩百": "200", "二百": "200", "三百": "300", "一百": "100", "五十": "50",
       "三十": "30", "二十": "20"}
_CITY = ("台北市|新北市|桃園市|台中市|台南市|高雄市|基隆市|新竹市|新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|"
         "嘉義市|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣|澎湖縣|金門縣|連江縣")
NO_GIFT = r"\s*(無|不發放|不發|無發放|未決定|等待公告|未公告|-|--)?\s*"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    return s


def _text(r: requests.Response) -> str:
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def _nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def face_value(gift: str) -> float | None:
    """商品卡、禮物卡這類等同現金的紀念品回傳面額，其他回傳 None。"""
    g = _nfkc(gift)
    if _COND.search(g) or not _CARD.search(g):
        return None
    g = re.sub(r"7\s*-\s*11|7-?eleven|711", " 超商 ", g, flags=re.I)   # 「7-1135元」是 7-11 的 35 元
    for k, v in _CN.items():
        g = g.replace(k, v)
    m = _AMT.findall(g) or re.findall(r"\$\s*(\d{2,5})", g)
    if not m:
        return None
    n = re.search(r"([2-5二兩三四五])\s*張|[x×*]\s*([2-5])(?!\d)", g)   # 「50元禮物卡二張」「50元商品卡x2」
    k = (n.group(1) or n.group(2)) if n else "1"
    return float(m[0]) * ({"二": 2, "兩": 2, "三": 3, "四": 4, "五": 5}.get(k) or int(k))


def region(addr: str) -> str:
    """地址 → 縣市＋區（例如「台北市大安區」），找不到回傳空字串。"""
    a = _nfkc(addr).replace("臺", "台").replace("巿", "市")
    a = re.sub(r"^[\s(（]*\d{3,6}[)）]?\s*", "", a)
    m = re.search(rf"({_CITY})\s*([^\d\s()（）]{{1,3}}?區|[^\d\s()（）]{{1,3}}?[鄉鎮市])?", a)
    return (m.group(1) + (m.group(2) or "")) if m else ""


def _date(md: str, year: int) -> dt.date | None:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})", str(md or ""))
    if not m:
        return None
    try:
        return dt.date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _before(md: str, meet: dt.date | None, year: int) -> str:
    """股東會之前的月/日（最後買進日、代領截止日）→ ISO 日期；比會議日晚就是前一年。"""
    d = _date(md, year)
    if d and meet and d > meet + dt.timedelta(days=31):
        d = d.replace(year=d.year - 1)
    return d.isoformat() if d else ""


def _year(page: str) -> int:
    m = re.search(r"<title[^>]*>[^<]*?(20\d\d)年", page)
    return int(m.group(1)) if m else dt.datetime.now(TZ).year


# ---------- 資料來源 ----------

def parse_histock(page: str) -> tuple[int, pd.DataFrame]:
    """HiStock 紀念品頁 → (年度, 清單)。日期只有月/日，年度從標題「2026年(115)」取。"""
    year = _year(page)
    tables = pd.read_html(io.StringIO(page))
    df = max((t for t in tables if {"代號", "股東會紀念品"} <= {str(c) for c in t.columns}),
             key=len, default=None)
    if df is None or len(df) == 0:
        raise ValueError("HiStock 頁面找不到紀念品表格")
    df = df[df["代號"].astype(str).str.fullmatch(r"\d{4,6}[A-Z]?(\.0)?")].copy()
    meet = [_date(x, year) for x in df["股東會日期"]]
    out = pd.DataFrame({
        "code": df["代號"].astype(str).str.replace(r"\.0$", "", regex=True).values,
        "name": df["名稱"].astype(str).str.strip().values,
        "price": pd.to_numeric(df["股價"], errors="coerce").values,
        "kind": [("臨時" if "臨" in str(k) else "常會") for k in df["性質"]],
        "place": df.get("開會地點", pd.Series("", index=df.index)).astype(str).str.strip().values,
        "gift": [_nfkc(g).replace("參考圖", "").strip() for g in df["股東會紀念品"]],
        "odd_mail": df.get("零股寄單", pd.Series("", index=df.index)).astype(str).str.strip().values,
        "agent": df.get("股代", pd.Series("", index=df.index)).astype(str).str.strip().values,
        "agent_tel": df.get("股代電話", pd.Series("", index=df.index)).astype(str).str.strip().values,
        "meeting": [d.isoformat() if d else "" for d in meet],
        "last_buy": [_before(x, mt, year) for x, mt in zip(df["最後買進日"], meet)],
    })
    out = out.replace({"nan": ""}).drop_duplicates(["code", "meeting", "gift"])
    return year, out.reset_index(drop=True)


def _field(blk: str, title: str) -> str:
    """股代網卡片裡某個標題（股價、收購、最後買進日…）後面那一格的 HTML。"""
    m = re.search(rf'<div class="title"[^>]*>{title}</div>\s*</div>\s*<div class="col[^"]*">(.*?)</div>', blk, re.S)
    return m.group(1) if m else ""


def _plain(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def parse_gooddie(page: str) -> pd.DataFrame:
    """股代網股東會清單（每場一張卡片）→ 代領、電投、收購價、股代。"""
    year = _year(page)
    rows = []
    for blk in page.split('<div class="card">')[1:]:
        m = re.search(r'data-target="#collapse\d+"[^>]*>\s*([0-9]{4,6}[A-Z]?)\s+(\S+)\s+(\d{1,2}/\d{1,2})\s+(臨時|常會)', blk)
        if not m:
            continue
        code, name, md, kind = m.groups()
        meet = _date(md, year)
        gift = re.search(r'<div class="text-truncate" title="([^"]*)"', blk)
        gtype = re.search(r'<div class="type">([^<]+)</div>', blk)
        buy = [float(x) for x in re.findall(r'class="price(?: min| max)?">([\d.]+)<', _field(blk, "收購"))]
        price = re.search(r'class="price">([\d.]+)<', _field(blk, "股價"))
        lb = re.match(r"\s*(\d{1,2}/\d{1,2})", _plain(_field(blk, "最後買進日")))
        px = _plain(_field(blk, "代領截止日"))
        pm = re.match(r"(\d{1,2}/\d{1,2})?\s*(.*)", px)
        ev = re.search(r'<div class="title">電投</div>\s*</div>\s*<div class="col[^"]*">(.*?)</div>', blk, re.S)
        evs = re.findall(r"<span[^>]*>([^<]+)</span>", ev.group(1)) if ev else []
        ag = re.search(r'<div class="title">股務代理</div>.*?<div class="d-inline-block pr-1">([^<]+)</div>'
                       r'(?:.*?href="tel:([^"]+)")?', blk, re.S)
        mk = re.search(r'<div class="title">市場別</div>\s*</div>\s*<div class="col">\s*([^<\s]+)', blk)
        hist = re.search(r"/stock/meeting/history/(\d+)", blk)
        rows.append({
            "code": code, "name": name, "kind": kind,
            "meeting": meet.isoformat() if meet else "",
            "gift": _nfkc(html.unescape(gift.group(1))) if gift else "",
            "gift_type": gtype.group(1).strip() if gtype else "",
            "price": float(price.group(1)) if price else np.nan,
            "buy_min": min(buy) if buy else np.nan, "buy_max": max(buy) if buy else np.nan,
            "last_buy": _before(lb.group(1), meet, year) if lb else "",
            "proxy_deadline": _before(pm.group(1), meet, year) if pm and pm.group(1) else "",
            "proxy": (pm.group(2).strip() if pm else "") or ("" if not px else px),
            "evote": next((e.strip() for e in evs if "電投" in e), ""),
            "evote_period": next((e.strip() for e in evs if re.search(r"\d/\d+\s*~", e)), ""),
            "agent_g": _nfkc(ag.group(1)) if ag else "", "agent_tel_g": (ag.group(2) or "") if ag else "",
            "market": mk.group(1) if mk else "",
            "hist_id": hist.group(1) if hist else "",
        })
    return pd.DataFrame(rows)


def fetch_gooddie(s: requests.Session, max_pages: int = 80) -> pd.DataFrame:
    """股代網一頁 20 場，照分頁（?page=N）全部翻完。"""
    first = _text(s.get(GOODDIE, timeout=60))
    last = max((int(n) for n in re.findall(r'href="/stock/meeting\?page=(\d+)"', first)), default=1)
    frames = [parse_gooddie(first)]
    for p in range(2, min(last, max_pages) + 1):
        time.sleep(1)   # 別打太快
        try:
            frames.append(parse_gooddie(_text(s.get(GOODDIE, params={"page": p}, timeout=60))))
        except requests.RequestException as e:
            logging.warning("股代網第 %d 頁抓取失敗：%s", p, e)
    df = pd.concat(frames, ignore_index=True)
    logging.info("股代網 %d 頁 %d 場", min(last, max_pages), len(df))
    return df.drop_duplicates(["code", "meeting"]) if len(df) else df


def fetch_agents(s: requests.Session) -> pd.DataFrame:
    """證交所、櫃買中心公司基本資料 → 官方登記的股票過戶機構（股代）名稱、電話、地址。"""
    out = []
    for url, code, agent, tel, addr in (
            (TWSE_CO, "公司代號", "股票過戶機構", "過戶電話", "過戶地址"),
            (TPEX_CO, "SecuritiesCompanyCode", "StockTransferAgent", "StockTransferAgentTelephone",
             "StockTransferAgentAddress")):
        try:
            r = s.get(url, timeout=60)
            r.raise_for_status()
            for d in r.json():
                out.append({"code": str(d.get(code, "")).strip(), "agent_o": _nfkc(d.get(agent)),
                            "agent_tel_o": _nfkc(d.get(tel)), "agent_addr": _nfkc(d.get(addr))})
        except (requests.RequestException, ValueError) as e:
            logging.warning("股代資料抓取失敗 %s：%s", url, e)
    df = pd.DataFrame(out, columns=["code", "agent_o", "agent_tel_o", "agent_addr"])
    return df[df["code"] != ""].drop_duplicates("code")


def daily_sd(codes: set[str]) -> dict[str, float]:
    """近 60 天單日漲跌的標準差（算隔天賣的價格風險）。"""
    if not HISTORY.exists():
        return {}
    h = pd.read_csv(HISTORY, dtype={"code": str}, usecols=["date", "code", "close"])
    h = h[h["code"].isin(codes)].sort_values(["code", "date"])
    r = h.groupby("code")["close"].pct_change()
    h = h.assign(r=r).dropna(subset=["r"])
    return h.groupby("code").tail(60).groupby("code")["r"].std().dropna().round(4).to_dict()


def merge(hi: pd.DataFrame, go: pd.DataFrame, ag: pd.DataFrame, sd: dict[str, float]) -> pd.DataFrame:
    """HiStock 為主、股代網補代領與還沒進 HiStock 的場次，再接官方股代地址與價格風險。"""
    hi = hi.assign(src="H")
    if len(go):
        go = go.assign(src="G")
        df = hi.merge(go, on=["code", "meeting"], how="outer", suffixes=("", "_go"))
        for c in ("name", "price", "kind", "gift", "last_buy"):
            alt = df.get(f"{c}_go")
            if alt is not None:
                blank = df[c].isna() | (df[c].astype(str).str.strip() == "")
                if c == "gift":   # 股代網寫「未決定」、HiStock 已經有品項時用 HiStock
                    blank = blank | df[c].astype(str).str.fullmatch(NO_GIFT)
                df[c] = df[c].where(~blank, alt)
        df["src"] = np.where(df["src"].notna() & df["src_go"].notna(), "HG",
                             df["src"].fillna(df["src_go"]))
        df["agent"] = df["agent"].where(df["agent"].fillna("").astype(str).str.strip() != "", df["agent_g"])
        df["agent_tel"] = df["agent_tel"].where(df["agent_tel"].fillna("").astype(str).str.strip() != "",
                                                df["agent_tel_g"])
    else:
        df = hi.copy()
    if len(ag):
        df = df.merge(ag, on="code", how="left")
        df["agent"] = df["agent_o"].where(df["agent_o"].fillna("") != "", df["agent"])
        df["agent_tel"] = df["agent_tel_o"].where(df["agent_tel_o"].fillna("") != "", df["agent_tel"])
    df["region"] = [region(a) for a in df.get("agent_addr", pd.Series("", index=df.index)).fillna("")]
    df["sd"] = df["code"].map(sd)
    for c in COLS:
        if c not in df:
            df[c] = np.nan if c in ("price", "buy_min", "buy_max", "sd") else ""
    df = df[COLS].copy()
    txt = [c for c in COLS if c not in ("price", "buy_min", "buy_max", "sd")]
    df[txt] = df[txt].fillna("").astype(str).replace({"nan": ""})
    return df.sort_values(["meeting", "code"]).reset_index(drop=True)


def refresh() -> Path:
    """抓三個來源、合併，存成 data/gifts/{年}.csv，回傳檔案路徑。"""
    s = _session()
    r = s.get(HISTOCK, timeout=60)
    r.raise_for_status()
    year, hi = parse_histock(_text(r))
    if len(hi) < 20:
        raise ValueError(f"HiStock 只抓到 {len(hi)} 筆，可能改版了")
    try:
        go = fetch_gooddie(s)
    except (requests.RequestException, ValueError) as e:
        logging.warning("股代網抓取失敗：%s", e)
        go = pd.DataFrame()
    ag = fetch_agents(s)
    df = merge(hi, go, ag, daily_sd(set(hi["code"]) | set(go.get("code", []))))
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{year}.csv"
    df.to_csv(path, index=False)
    meta = {"updated": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "year": year, "rows": len(df),
            "histock": len(hi), "gooddie": len(go), "agents": len(ag),
            "with_proxy": int((df["proxy_deadline"] != "").sum()), "with_region": int((df["region"] != "").sum())}
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    logging.info("紀念品 %s", meta)
    return path


# ---------- 網頁 ----------

def load(year: int | None = None) -> tuple[int, pd.DataFrame] | None:
    files = sorted(DATA.glob("20??.csv")) if DATA.exists() else []
    if year is not None:
        files = [f for f in files if f.stem == str(year)]
    if not files:
        return None
    f = files[-1]
    df = pd.read_csv(f, dtype=str, keep_default_na=False)
    for c in ("price", "buy_min", "buy_max", "sd"):
        df[c] = pd.to_numeric(df.get(c, ""), errors="coerce")
    for c in COLS:
        if c not in df:
            df[c] = np.nan if c in ("price", "buy_min", "buy_max", "sd") else ""
    return int(f.stem), df


def _num(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def records(df: pd.DataFrame) -> list[dict]:
    """網頁用的精簡欄位（估值依據也在這裡決定）。"""
    out = []
    for r in df.itertuples(index=False):
        if re.fullmatch(NO_GIFT, r.gift or "") and not r.last_buy:
            continue
        if "取消" in (r.gift or "") or r.code == "000001":   # 000001 是證交所自己，買不到
            continue
        fv = face_value(r.gift)
        g = _nfkc(r.gift)
        no_amt = fv is None and bool(_CARD.search(g)) and not _COND.search(g)
        bmax = _num(r.buy_max, 0)
        v, vb = (fv, "面額") if fv is not None else ((bmax, "收購價") if bmax else (None, ""))
        out.append({
            "c": r.code, "n": r.name, "mk": r.market, "p": _num(r.price), "lb": r.last_buy, "m": r.meeting,
            "k": r.kind, "pl": r.place, "g": r.gift, "gt": r.gift_type, "om": r.odd_mail,
            "ag": r.agent, "at": r.agent_tel, "aa": r.agent_addr, "ar": r.region,
            "v": v, "vb": vb, "fv": fv, "b0": _num(r.buy_min, 0), "b1": bmax,
            "pd": r.proxy_deadline, "ps": r.proxy, "ev": r.evote, "ep": r.evote_period,
            "h": r.hist_id, "sd": _num(r.sd, 4), "src": r.src, "na": 1 if no_amt and v is None else 0,
        })
    # ④ 同一家、同樣禮物、會議日相差 3 天內（例如誠美材 6/29 臨時＋6/30 常會）多半是重複登錄，只留一列（常會優先）
    out.sort(key=lambda x: (x["c"], x["g"], x["k"] != "常會", x["m"]))
    keep, seen = [], {}
    for x in out:
        k = (x["c"], x["g"])
        if x["g"] and k in seen and abs((dt.date.fromisoformat(x["m"]) - dt.date.fromisoformat(seen[k])).days) <= 3:
            continue
        seen.setdefault(k, x["m"])
        keep.append(x)
    return keep


GUIDE = """
<h2>怎麼算（CP 值、實拿、排行）</h2>
<ol class="guide">
<li><b>估值</b>：商品卡、禮物卡、禮券用<b>面額</b>；其他紀念品用<b>股代網目前最高的收購出價</b>（代領商市場上有人願意付的現金）；
兩者都沒有就標「未估」，<b>不當 0、不排名</b>。折價券、抵用券、自家購物金有使用條件，不用面額估。</li>
<li><b>費用</b>：買進手續費 1 元（國泰電子下單零股最低 1 元）。<br>
「代領寄到家」再加代領費（票券 12 元／件，其他 15 元／件）＋運費（超商取貨每箱 38 元＋理貨 15 元＝53 元，除以一箱寄幾件）。
「自己去領」不加，但要在發放期間跑一趟股代或公司指定地點。<br>
勾「隔天就賣」再加賣出手續費 1 元＋證交稅 0.3%（1 股通常是 0 元）。<br>
另外一律扣<b>預期跌價＝股價 × 0.35%</b>：回測 2026 年上市櫃紀念品股，最後買進日收盤到隔天收盤，平均比同股價、同成交值的股票多跌約 0.35～0.4%
（同一批股票換到別的日子是 0）。樣本只有一年、集中在 3～4 月的 39 個交易日，t 值約 −1，方向可信、大小不準，明年 3～4 月用新資料重估。</li>
<li><b>實拿</b> ＝ 估值 − 費用。<b>CP</b> ＝ 實拿 ÷（股價＋1 元），也就是每拿出 1 元本金、最後淨賺多少紀念品價值。</li>
<li><b>排行</b>：只排「有估值、而且你選的領法做得到」的場次——選代領時，股代網沒有代領的（不能代領或不知道）不排，
標「不能代領／未知」。依 CP 由高到低，也可以改成依實拿金額。</li>
<li><b>價格風險</b>：最後買進日買進、<b>隔天就賣也能領</b>（股東名冊在最後過戶日就定了）。所以只要承擔 1 天的漲跌，
表上「1 天風險」＝股價 × 近 60 天單日漲跌的標準差（大約三分之二的日子漲跌在這個範圍內）；平均會跌的那部分已經在費用裡扣了，這裡只是上下抖的幅度。高價股買 1 股換 50 元，這個數字可能比紀念品還大。</li>
</ol>
<h2>怎麼領</h2>
<ol class="guide">
<li><b>買 1 股就是股東。</b>在「最後買進日」收盤前買進（盤中零股、盤後零股都可以）。股東常會開會前 60 天停止過戶、
臨時會前 30 天，再扣 T+2 交割，所以常會大約要在開會前兩個月買。表裡的最後買進日已經算好。</li>
<li><b>零股能不能領看公司規定</b>：「不限股數」照發；最常見的是「未滿 1,000 股須電子投票（或親自出席）才發」。
電子投票用「集保e手掌握」App、股東e票通網站或券商 App，免費。表中「電投」是股代網整理的投票規定與期間。</li>
<li><b>零股寄單</b>：「否」＝公司<b>不寄開會通知書</b>給零股股東（不是運費）。一樣可以電子投票，再帶身分證去領或找代領。</li>
<li><b>領取地點</b>：表裡的地點是證交所／櫃買中心登記的<b>股務代理（股代）地址</b>，零股股東多半在這裡、在發放期間領。
部分公司另有發放點（工廠、門市、股東會現場），以公司公告為準；不確定就打股代電話問。</li>
<li><b>能不能寄到家</b>：公司自己幾乎不寄。要寄到家就找代領業者（股代網、換換零股等），他們代領後用超商取貨或貨運寄給你，
費用就是上面的代領費＋運費；要在「代領截止日」前委託。有些公司 2026 年起改發 eGift 電子禮券，電子投票後在投票平台的「紀念品」專區線上領，不用跑也不用寄。</li>
<li><b>注意</b>：紀念品數量有限、可能以等值商品替代；每年送的不一定一樣，去年的只能參考；
不要為了紀念品買體質很差的股票。費用以業者公告為準：
<a href="https://www.gooddie.tw/">股代網</a>、<a href="https://www.stockbox.com.tw/pricing">換換零股（禮券 12 元、物品 16 元）</a>。</li>
</ol>
<h2>一年的時間表</h2>
<ul class="guide">
<li><b>1～3 月</b>：公司陸續公告股東常會日期和紀念品。</li>
<li><b>3 月中～4 月中</b>：大部分常會的最後買進日（開會前約兩個月）。</li>
<li><b>4 月底～6 月</b>：電子投票、代領、發放，股東常會集中在 5、6 月。</li>
<li><b>下半年</b>：零星的股東臨時會（停止過戶 30 天），紀念品通常比較少。</li>
</ul>
"""

CSS_EXTRA = """
.meta{color:var(--muted);font-size:14px}
.guide li{margin:6px 0}
.ctl{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:12px 0;padding:10px 12px;
  background:var(--card);border:1px solid var(--line);border-radius:8px;font-size:14.5px}
.ctl select,.ctl input{font-size:15px;padding:4px 6px;border:1px solid var(--line);border-radius:6px;
  background:var(--bg);color:var(--fg)}
.ctl input[type=number]{width:56px}.ctl input#q{width:220px}
table.g td,table.g th{text-align:left!important}
table.g td.n,table.g th.n{text-align:right!important}
table.g td.gift{white-space:normal;min-width:150px}
table.g td.stk{white-space:normal;min-width:130px;max-width:220px}
.gsub{color:var(--muted);font-size:13px;line-height:1.4;margin-top:2px}
table.g tbody tr.r{cursor:pointer}
table.g tbody tr.r:hover{background:var(--card)}
tr.d td{white-space:normal;background:var(--card);font-size:14.5px;line-height:1.7}
tr.d .dbox{position:sticky;left:0;max-width:min(760px,calc(100vw - 48px))}
tr.d dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:4px 0}
tr.d dd{overflow-wrap:anywhere}
tr.d dt{color:var(--muted)}tr.d dd{margin:0}
.hi{color:#c0392b;font-weight:600}.neg{color:var(--muted)}.na{color:var(--muted);font-size:13px}
.tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:12.5px;border:1px solid var(--line)}
details summary{cursor:pointer;font-weight:600;margin:12px 0}
"""

APP = r"""<script>
const D=__DATA__, TODAY=(()=>{const t=new Date(),p=n=>(n<10?'0':'')+n;return t.getFullYear()+'-'+p(t.getMonth()+1)+'-'+p(t.getDate())})();
const FEE=1,PX_TICKET=12,PX_ITEM=15,SHIP=53,DRIFT=0.0035;   // DRIFT：最後買進日隔天平均超額跌幅（2026 回測）
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,d=0)=>x==null||isNaN(x)?'':(+x).toLocaleString('zh-TW',{maximumFractionDigits:d,minimumFractionDigits:0});
const md=s=>s?s.slice(5).replace('-','/'):'';
function st(){return{mode:$('mode').value,box:Math.max(1,+$('box').value||10),reg:$('reg').value,mk:$('mk').value,sort:$('sort').value,sell:$('sell').checked,q:$('q').value.trim().toLowerCase()}}
function ticket(r){return r.fv!=null||r.gt==='票券'}
function proxy(r){return r.pd?1:(/不可|無法|不提供/.test(r.ps||'')?0:null)}   // 1 可代領、0 不能、null 不知道
function cost(r,S){let c=FEE+(r.p||0)*DRIFT;if(S.sell)c+=1+Math.floor((r.p||0)*0.003);
  if(S.mode==='proxy'){const x=proxy(r);if(x!==1)return null;c+=(ticket(r)?PX_TICKET:PX_ITEM)+SHIP/S.box}return c}
function calc(r,S){const c=cost(r,S),k=(r.p||0)+FEE,net=(r.v==null||c==null)?null:r.v-c;
  return{c,k,net,cp:net==null||!r.p?null:net/k,risk:r.sd&&r.p?r.p*r.sd:null}}
function why(r,S){if(r.v==null)return r.na?'金額未公布':'未估';if(S.mode==='proxy'){const x=proxy(r);if(x===0)return'不能代領';if(x===null)return'代領未知'}return''}
function listed(r){return r.mk==='上市'||r.mk==='上櫃'||(!r.mk&&r.sd!=null)}
function okRegion(r,S){return(S.mode!=='self'||!S.reg||r.ar.startsWith(S.reg))&&(S.mk==='all'||listed(r))}
function hit(r,S){return!S.q||(r.c+' '+r.n+' '+r.g+' '+r.ar+' '+r.ag).toLowerCase().includes(S.q)}
const COLS={
 lb:['最後買進日',r=>md(r.lb)], st:['股票／紀念品',r=>`${esc(r.c+' '+r.n)}${r.mk&&!listed(r)?` <span class=tag>${esc(r.mk)}</span>`:''}<div class=gsub>${esc(r.g||'未公告')}</div>`,0,'stk'], cp:['CP',(r,x)=>x.cp==null?'':`<span class="${x.cp>=2?'hi':x.cp<0?'neg':''}">${fmt(x.cp,1)}</span>`,1],
 net:['實拿',(r,x,S)=>x.net==null?`<span class="na">${why(r,S)}</span>`:fmt(x.net,1),1], v:['估值',r=>r.v==null?`<span class="na">${r.na?'金額未公布':'未估'}</span>`:fmt(r.v)+(r.vb==='收購價'?'<span class="na">收</span>':''),1],
 k:['1 股',(r,x)=>fmt(x.k,2),1], risk:['1 天風險',(r,x)=>x.risk==null?'':'±'+fmt(x.risk,1),1],
 g:['紀念品',r=>esc(r.g||'未公告'),0,'gift'], px:['代領',r=>r.pd?(r.pd>=TODAY?'可，至 '+md(r.pd):'有（已截止）'):(proxy(r)===0?'不能':'<span class="na">未知</span>')],
 ar:['領取地點',r=>esc(r.ar||'')], m:['股東會',r=>md(r.m)+' '+esc(r.k)]};
function table(id,rows,cols,S){const th=cols.map(c=>`<th${COLS[c][2]?' class=n':''}>${COLS[c][0]}</th>`).join('');
  const tb=rows.map((r,i)=>{const x=calc(r,S);return`<tr class="r" data-i="${D.indexOf(r)}">`+cols.map(c=>`<td class="${COLS[c][2]?'n':''} ${COLS[c][3]||''}">${COLS[c][1](r,x,S)}</td>`).join('')+'</tr>'}).join('');
  $(id).innerHTML=`<thead><tr>${th}</tr></thead><tbody>${tb||`<tr><td colspan=${cols.length} class=na>沒有符合的</td></tr>`}</tbody>`;
  $(id).dataset.cols=cols.length}
function detail(r,S){const x=calc(r,S),P={...S,mode:'proxy'},Q={...S,mode:'self'},xp=calc(r,P),xs=calc(r,Q);
  const pxc=(ticket(r)?PX_TICKET:PX_ITEM), ship=SHIP/S.box, sell=S.sell?1+Math.floor((r.p||0)*0.003):0, dr=(r.p||0)*DRIFT;
  const val=r.v==null?(r.na?'金額未公布（公告只寫禮券、沒寫面額，股代網也沒有收購價）':'未估（沒有面額、股代網也沒有收購價）'):`${fmt(r.v)} 元（${r.vb==='面額'?'禮券面額':'股代網最高收購出價'}）`;
  const buy=r.b1?`股代網收購出價 ${fmt(r.b0)}～${fmt(r.b1)} 元`:'股代網沒有收購出價';
  const line=(lab,xx,extra)=>xx.net==null?`<span class="na">${why(r,lab==='代領寄到家'?P:Q)}</span>`:
     `實拿 ${fmt(xx.net,1)} 元 ＝ ${fmt(r.v)} − 買進 1 − 預期跌價 ${fmt(dr,1)}${extra}${sell?` − 隔天賣 ${sell}`:''}；CP ${fmt(xx.cp,2)}`;
  return`<dl>
  <dt>紀念品</dt><dd>${esc(r.g||'未公告')}${r.gt?` <span class=tag>${esc(r.gt)}</span>`:''}</dd>
  <dt>估值</dt><dd>${val}；${buy}</dd>
  <dt>代領寄到家</dt><dd>${line('代領寄到家',xp,` − 代領費 ${pxc} − 運費 ${fmt(ship,1)}（一箱 ${S.box} 件）`)}${r.pd?`<br>代領截止 ${r.pd}${r.ps?'（'+esc(r.ps)+'）':''}`:''}</dd>
  <dt>自己去領</dt><dd>${line('自己去領',xs,'')}<br>地點：${esc(r.ag||'股代未知')} ${esc(r.at||'')}<br>${esc(r.aa||'')}${r.ar?` <span class=tag>${esc(r.ar)}</span>`:''}</dd>
  <dt>1 股成本</dt><dd>${fmt(r.p,2)} ＋ 手續費 1 ＝ ${fmt(x.k,2)} 元</dd>
  <dt>價格風險</dt><dd>${x.risk==null?'沒有足夠的股價資料':`最後買進日買、隔天賣也能領；1 天波動約 ±${fmt(x.risk,1)} 元（近 60 天單日標準差 ${fmt(r.sd*100,1)}%）`}</dd>
  <dt>時間</dt><dd>最後買進日 ${r.lb||'未知'}・股東會 ${r.m}（${esc(r.k)}${r.pl?'，'+esc(r.pl):''}）${r.ep?`・電子投票 ${esc(r.ep)}`:''}</dd>
  <dt>零股</dt><dd>${r.om==='否'?'零股寄單：否（公司不寄開會通知書給零股股東；自己電子投票或找代領）':r.om==='是'?'零股寄單：是（零股也會收到開會通知書）':'零股寄單：未知'}</dd>
  <dt>連結</dt><dd>${r.h?`<a href="https://www.gooddie.tw/stock/meeting/history/${r.h}" target=_blank rel=noopener>股代網：歷年紀念品</a> ・ `:''}<a href="https://histock.tw/stock/gift.aspx" target=_blank rel=noopener>HiStock 清單</a></dd>
  </dl>`}
function draw(){const S=st();try{localStorage.setItem('giftS',JSON.stringify({mode:S.mode,box:S.box,sell:S.sell}))}catch(e){}
  $('boxw').style.display=S.mode==='proxy'?'':'none';$('regw').style.display=S.mode==='self'?'':'none';
  const soon=D.filter(r=>r.lb&&r.lb>=TODAY&&hit(r,S)&&okRegion(r,S)).sort((a,b)=>a.lb<b.lb?-1:a.lb>b.lb?1:0);
  $('soon-n').textContent=soon.length;
  table('soon',soon,['lb','st','cp','net','v','k','risk','px','ar','m'],S);
  const key=S.sort==='net'?'net':'cp';
  const rk=D.map(r=>[r,calc(r,S)]).filter(([r,x])=>x[key]!=null&&hit(r,S)&&okRegion(r,S)).sort((a,b)=>b[1][key]-a[1][key]).slice(0,60).map(a=>a[0]);
  table('rank',rk,['st','cp','net','v','k','risk','px','ar','m'],S);
  const all=D.filter(r=>hit(r,S)&&okRegion(r,S)).sort((a,b)=>a.m<b.m?1:a.m>b.m?-1:0);
  $('all-n').textContent=all.length; table('all',all,['m','st','cp','net','v','k','px','ar','lb'],S)}
document.addEventListener('click',e=>{const tr=e.target.closest('tr.r');if(!tr||e.target.closest('a'))return;
  const nx=tr.nextElementSibling;if(nx&&nx.classList.contains('d')){nx.remove();return}
  const r=D[+tr.dataset.i],d=document.createElement('tr');d.className='d';
  d.innerHTML=`<td colspan="${tr.closest('table').dataset.cols}"><div class="dbox">${detail(r,st())}</div></td>`;tr.after(d)});
(()=>{const R={};D.forEach(r=>{if(r.ar){const c=r.ar.slice(0,3);R[c]=(R[c]||0)+1}});
  $('reg').innerHTML='<option value="">全部</option>'+Object.entries(R).sort((a,b)=>b[1]-a[1]).map(([c,n])=>`<option value="${c}">${c}（${n}）</option>`).join('');
  try{const s=JSON.parse(localStorage.getItem('giftS')||'{}');if(s.mode)$('mode').value=s.mode;if(s.box)$('box').value=s.box;if(s.sell)$('sell').checked=true}catch(e){}
  ['mode','box','reg','mk','sort','sell','q'].forEach(id=>$(id).addEventListener('input',draw));draw()})();
</script>"""


def render(today: dt.date | None = None) -> str | None:
    """產生 gifts.html 的內容；還沒有資料就回傳 None。"""
    got = load()
    if got is None:
        return None
    year, df = got
    meta = {}
    if (DATA / "meta.json").exists():
        meta = json.loads((DATA / "meta.json").read_text("utf-8"))
    rec = records(df)
    data = json.dumps(rec, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    from .weekly import CSS
    n_px = sum(1 for r in rec if r["pd"])
    body = f"""
<h1>🎁 股東紀念品（{year} 年）</h1>
<p class="meta">資料更新：{html.escape(meta.get('updated', ''))}・共 {len(rec)} 場（{n_px} 場股代網有代領）・
來源：<a href="{HISTOCK}">HiStock</a>、<a href="{GOODDIE}">股代網</a>、證交所／櫃買中心公司資料（股代地址）。實際以公司公告為準。
<b>點任何一列可以看詳細資訊。</b></p>
<div class="ctl">
 <label>領法 <select id="mode"><option value="proxy">代領寄到家</option><option value="self">自己去領</option></select></label>
 <label id="boxw">一箱寄幾件 <input id="box" type="number" min="1" value="10"></label>
 <label id="regw">領取地區 <select id="reg"></select></label>
 <label>市場 <select id="mk"><option value="listed">上市櫃（零股買得到）</option><option value="all">全部（含興櫃、公開發行）</option></select></label>
 <label>排序 <select id="sort"><option value="cp">CP 值</option><option value="net">實拿金額</option></select></label>
 <label><input type="checkbox" id="sell"> 隔天就賣</label>
 <input id="q" placeholder="搜尋代號、名稱、紀念品、地區">
</div>
<h2>還來得及買（<span id="soon-n">0</span> 場）</h2>
<div class="tbl"><table class="g" id="soon"></table></div>
<h2>CP 值排行（{year} 年，明年可參考）</h2>
<p class="meta">實拿 ＝ 估值 − 買進手續費 1 元 −（代領：代領費 12／15 元＋運費 53 元 ÷ 一箱件數）−（隔天賣：賣出 1 元＋證交稅）。
CP ＝ 實拿 ÷（股價＋1 元）。只排有估值、而且這個領法做得到的；「收」＝用收購價估值。預設只排上市櫃（零股盤中、盤後都買得到）；興櫃、公開發行的要另外跟券商議價或根本買不到，選「全部」才會出現。算法細節在下面。</p>
<div class="tbl"><table class="g" id="rank"></table></div>
{GUIDE}
<details><summary>全部 <span id="all-n">0</span> 場（可搜尋、可點開）</summary>
<div class="tbl"><table class="g" id="all"></table></div></details>
<p class="meta">僅供參考，不構成投資建議。</p>"""
    return (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>股東紀念品</title>"
            f"<style>{CSS}{CSS_EXTRA}</style></head><body><main><nav><a href='index.html'>← 每日報表</a></nav>"
            f"{body}</main>{APP.replace('__DATA__', data)}</body></html>")


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
