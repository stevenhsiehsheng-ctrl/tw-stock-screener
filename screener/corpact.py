"""減資、變更面額、大比例配股：原始股價會「假跳」，選股計算前要把之前的價格接起來。

例：寶雅變更面額 720 → 79.2、台揚減資 10.3 → 22.9。不處理的話 60 日新高、均線、RS、趨勢樣板全部會錯，
減資恢復買賣那天價格跳上去又爆量，還可能被當成「爆量突破新高」。

資料來源（2026/10 用 GitHub Actions 實測過格式）：
- 證交所 減資恢復買賣：rwd/zh/reducation/TWTAUU（停止買賣前收盤價格、恢復買賣參考價）
- 證交所 變更面額：rwd/zh/change/TWTB8U（停止買賣前收盤價格、恢復買賣參考價）
- 櫃買 減資恢復買賣：www/zh-tw/bulletin/revivt（最後交易日之收盤價格、減資恢復買賣開始日參考價格）
- 大比例配股：data/extras/exdiv.csv 裡 factor 偏離 1 超過 10% 的
- 以上都對不上、但單日漲跌超過 11%（超過漲跌停）的：用「前收 ÷ 當天開盤」推估（櫃買變更面額目前沒找到資料，靠這個補）

data/extras/corp_actions.csv：date（恢復買賣日）, code, market, kind, prev_close, ref_price, factor（前收 ÷ 參考價）, src
history.csv.gz 存的一律是原始價；只在選股計算時用 adjust() 往回調整（之前的價格 ÷ factor、成交量 × factor）。
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

import pandas as pd

from .enrich import DIR, _col, _json, _num, _session, _tables
from .exdiv import EXDIV, roc_date

log = logging.getLogger("corpact")

CORP_F = DIR / "corp_actions.csv"
COLS = ["date", "code", "market", "kind", "prev_close", "ref_price", "factor", "src"]
KEEP_DAYS = 800
CODE_RE = re.compile(r"\d{4,6}[A-Z]?")
BIG = 0.10      # 除權息 factor 偏離 1 超過這個才調整（一般現金股利維持原始價，和以前一樣）
GAP = 0.11      # 超過漲跌停的單日變動才推估
PRICE_COLS = ["open", "high", "low", "close"]


def _rows(fields, data, market: str, kind: str | None, src: str) -> list[dict]:
    i_d, i_c = _col(fields, "恢復買賣"), _col(fields, "代號")
    i_p = _col(fields, "收盤")
    i_r = _col(fields, "參考價", exclude=("除權",))
    i_k = _col(fields, "減資原因")
    i_x = _col(fields, "除權參考價")  # 減資同一天又除權：最後的參考價看這欄
    if None in (i_d, i_c, i_p, i_r):
        log.warning("%s 欄位對不上：%s", src, fields)
        return []
    out = []
    for r in data:
        code, d = str(r[i_c]).strip(), roc_date(r[i_d])
        p, ref = _num(r[i_p]), _num(r[i_r])
        x = _num(r[i_x]) if i_x is not None else None
        if x and x > 0:
            ref = x
        if not d or not CODE_RE.fullmatch(code) or not p or not ref:
            continue
        k = kind or ("減資" + (f"（{str(r[i_k]).strip()}）" if i_k is not None else ""))
        out.append({"date": d, "code": code, "market": market, "kind": k, "prev_close": p, "ref_price": ref,
                    "factor": round(p / ref, 8), "src": src})
    return out


def fetch_range(s, start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = []
    for url, kind, src in [("https://www.twse.com.tw/rwd/zh/reducation/TWTAUU", None, "TWTAUU"),
                           ("https://www.twse.com.tw/rwd/zh/change/TWTB8U", "變更面額", "TWTB8U")]:
        j = _json(s, url, {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"})
        for t in _tables(j):
            rows += _rows(t["fields"], t["data"], "TWSE", kind, src)
        time.sleep(2)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt",
              {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        rows += _rows(t["fields"], t["data"], "TPEX", None, "revivt")
    return pd.DataFrame(rows, columns=COLS)


def update(today: dt.date, s=None) -> int:
    """第一次補兩年，之後每天重抓最近 60 天（恢復買賣日通常會提前公告）。"""
    s = s or _session()
    old = pd.read_csv(CORP_F, dtype={"code": str}) if CORP_F.exists() else pd.DataFrame(columns=COLS)
    start = today - dt.timedelta(days=60 if len(old) else KEEP_DAYS)
    new = []
    a = start
    while a <= today + dt.timedelta(days=30):
        b = min(today + dt.timedelta(days=30), a + dt.timedelta(days=180))
        try:
            new.append(fetch_range(s, a, b))
        except Exception as e:  # noqa: BLE001
            log.warning("減資／變更面額 %s~%s 失敗：%s", a, b, e)
        a = b + dt.timedelta(days=1)
    df = pd.concat([old, *new], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
    df = df[df.date >= (today - dt.timedelta(days=KEEP_DAYS)).isoformat()].sort_values(["date", "code"])
    DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(CORP_F, index=False)
    log.info("減資／變更面額：%d 筆", len(df))
    return len(df)


def events(hist: pd.DataFrame) -> pd.DataFrame:
    """要調整的事件：官方減資／變更面額 ＋ 大比例除權 ＋ 推估。回傳 date, code, factor, src。"""
    ev = []
    if CORP_F.exists():
        c = pd.read_csv(CORP_F, dtype={"code": str})
        ev.append(c[["date", "code", "factor"]].assign(src=c.src))
    key = set()
    if EXDIV.exists():
        e = pd.read_csv(EXDIV, dtype={"code": str})
        key |= set(zip(e.date, e.code))  # 一般除權息日也不推估（跌幅本來就含股利）
        e = e[(e.factor - 1).abs() > BIG]
        ev.append(e[["date", "code", "factor"]].assign(src="exdiv"))
    known = pd.concat(ev, ignore_index=True) if ev else pd.DataFrame(columns=["date", "code", "factor", "src"])
    key |= set(zip(known.date, known.code))
    # 推估：單日變動超過漲跌停、前面有停止買賣（少了至少一個交易日）、而且不是剛上市（前 5 天沒有漲跌幅限制）
    days = sorted(hist.date.unique())
    prev_day = dict(zip(days[1:], days[:-1]))
    h = hist.sort_values(["code", "date"])
    g = h.groupby("code")
    prev, prev_date, nth = g.close.shift(1), g.date.shift(1), g.cumcount()
    halted = prev_date.notna() & (prev_date != h.date.map(prev_day))
    jump = h[((h.close / prev - 1).abs() > GAP) & prev.notna() & (h.open > 0) & halted & (nth >= 6)].assign(prev=prev)
    jump = jump[[(d, c) not in key for d, c in zip(jump.date, jump.code)]]
    guess = pd.DataFrame({"date": jump.date, "code": jump.code, "factor": (jump.prev / jump.open).round(8), "src": "推估"})
    out = pd.concat([known, guess], ignore_index=True)
    out = out[out.factor.notna() & (out.factor > 0) & ((out.factor - 1).abs() > 1e-6)]
    out = out.drop_duplicates(["date", "code"])
    # 合理性檢查：調整後那天的漲跌要在漲跌停範圍內，不然代表事件沒真的反映在股價上（例：天瀚 2026/8/18
    # 除權表說參考價 30.04，實際照樣從 44.4 漲停到 48.8），硬調反而做出假的大跳，這種跳過
    day = h.assign(prev=prev)[["date", "code", "close", "prev"]]
    chk = out.merge(day, on=["date", "code"], how="left")
    move = chk.close * chk.factor / chk.prev - 1
    bad = chk.prev.notna() & chk.close.notna() & (move.abs() > GAP)
    if bad.any():
        log.info("除權／減資事件和股價對不上、略過：%s", ", ".join(f"{d} {c}" for d, c in zip(chk.date[bad], chk.code[bad])))
    out = chk[~bad.values][["date", "code", "factor", "src"]]
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def adjust(hist: pd.DataFrame, upto: str | None = None) -> pd.DataFrame:
    """把事件日之前的價格 ÷ factor、成交量 × factor，讓整段歷史和最新的原始價接得起來。
    upto：盤中用今天的日期，今天恢復買賣的（官方提前公告）也先調整，免得拿原始的舊價跟今天的新價比。"""
    try:
        ev = events(hist)
    except Exception as e:  # noqa: BLE001
        log.warning("減資／變更面額調整失敗，用原始價：%s", e)
        return hist
    ev = ev[ev.code.isin(set(hist.code)) & (ev.date > hist.date.min()) & (ev.date <= (upto or hist.date.max()))]
    if ev.empty:
        return hist
    h = hist.copy()
    mult = pd.Series(1.0, index=h.index)
    for code, g in ev.groupby("code"):
        rows = h.index[h.code == code]
        dates = h.loc[rows, "date"]
        for d, f in zip(g.date, g.factor):
            mult.loc[rows[(dates < d).values]] /= f
    adj = mult != 1.0
    for c in PRICE_COLS:
        h.loc[adj, c] = h.loc[adj, c] * mult[adj]
    h.loc[adj, "volume"] = (h.loc[adj, "volume"] / mult[adj]).round()
    log.info("減資／變更面額／大比例配股：調整 %d 檔、%d 個事件（推估 %d）", ev.code.nunique(), len(ev), int((ev.src == "推估").sum()))
    return h
