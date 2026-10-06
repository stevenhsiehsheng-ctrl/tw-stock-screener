"""下載多年歷史資料供回測用（不影響每日篩選）。

優先用 Yahoo Finance（含除權息還原），失敗再改用證交所/櫃買官方資料（未還原、較慢）。
Yahoo 只抓得到「現在還在」的股票，期間內下市的另外用證交所 STOCK_DAY／櫃買 tradingStock 逐月補（未還原，adjusted=0），
名單存成 delisted.csv（code, name, market, delist_date, rows）。不補就是倖存者偏差（下市的最慘那群被剪掉）。
用法：python -m screener.backfill --years 5 --out backtest.csv.gz
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import time

from pathlib import Path

import pandas as pd

from . import fetch

log = logging.getLogger("backfill")


def yahoo_adjusted(stocks: pd.DataFrame, start: dt.date) -> pd.DataFrame:
    import yfinance as yf

    suffix = {"TWSE": ".TW", "TPEX": ".TWO"}
    tickers = {f"{c}{suffix[m]}": c for c, m in zip(stocks.code, stocks.market)}
    names = list(tickers)
    frames = []
    for i in range(0, len(names), 100):
        chunk = names[i : i + 100]
        log.info("Yahoo %d-%d / %d", i + 1, i + len(chunk), len(names))
        raw = yf.download(chunk, start=start.isoformat(), auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            sub = sub[sub["Volume"] > 0]
            if sub.empty:
                continue
            frames.append(pd.DataFrame({
                "date": sub.index.strftime("%Y-%m-%d"), "code": tickers[t],
                "open": sub["Open"].values, "high": sub["High"].values, "low": sub["Low"].values,
                "close": sub["Close"].values, "volume": sub["Volume"].values,
            }))
    if not frames:
        raise fetch.SourceUnavailable("Yahoo 沒有資料")
    return pd.concat(frames, ignore_index=True)


def _roc(t: str) -> str | None:
    m = re.match(r"\s*(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})", str(t))
    return f"{int(m[1]) + 1911}-{int(m[2]):02d}-{int(m[3]):02d}" if m else None


def delisted(s, start: dt.date) -> pd.DataFrame:
    """start 之後終止上市（證交所）／終止上櫃（櫃買）的四碼股票。"""
    out = []
    j = fetch._get_json(s, "https://www.twse.com.tw/rwd/zh/company/suspendListing", {"response": "json"})
    for d, name, code in j.get("data") or []:
        out.append({"code": str(code).strip(), "name": str(name).strip(), "market": "TWSE", "delist_date": _roc(d)})
    for y in range(start.year, dt.date.today().year + 1):
        try:
            j = fetch._get_json(s, "https://www.tpex.org.tw/www/zh-tw/company/deListed", {"date": str(y), "response": "json"})
        except fetch.SourceUnavailable as e:
            log.warning("櫃買 %d 年下櫃名單抓不到：%s", y, e)
            continue
        for t in j.get("tables") or []:
            for r in t.get("data") or []:
                out.append({"code": str(r[0]).strip(), "name": str(r[1]).strip(), "market": "TPEX", "delist_date": _roc(r[2])})
        time.sleep(fetch.REQUEST_GAP)
    df = pd.DataFrame(out, columns=["code", "name", "market", "delist_date"]).dropna(subset=["delist_date"])
    df = df[df.code.str.fullmatch(r"\d{4}") & (df.delist_date >= start.isoformat())]
    return df.drop_duplicates("code", keep="first")


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def official_stock(s, code: str, market: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """單一股票逐月抓官方日K（未還原）。"""
    rows, m = [], dt.date(start.year, start.month, 1)
    while m <= end:
        try:
            if market == "TWSE":
                j = fetch._get_json(s, "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
                                    {"date": m.strftime("%Y%m%d"), "stockNo": code, "response": "json"})
                tabs = [(j.get("fields") or [], j.get("data") or [])]
            else:
                j = fetch._get_json(s, "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock",
                                    {"code": code, "date": m.strftime("%Y/%m/01"), "response": "json"})
                # 這支 API 的 table 沒有 fields，欄位順序固定（成交量單位是張）
                tpx = ["日期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"]
                tabs = [(t.get("fields") or tpx, t.get("data") or []) for t in j.get("tables") or []]
        except fetch.SourceUnavailable as e:
            log.warning("%s %s 抓不到：%s", code, m, e)
            tabs = []
        for fields, data in tabs:
            f = [str(x).replace(" ", "") for x in fields]
            ix = {k: next((i for i, x in enumerate(f) if k in x), None)
                  for k in ("日期", "開盤", "最高", "最低", "收盤", "成交股數", "成交張數", "成交仟股")}
            if ix["日期"] is None or ix["收盤"] is None:
                log.warning("%s %s 欄位對不上：%s", code, m, f)
                continue
            get = lambda r, k: _num(r[ix[k]]) if ix[k] is not None and ix[k] < len(r) else None
            for r in data:
                c = get(r, "收盤")
                if not c:
                    continue
                if ix["成交股數"] is not None:
                    v = get(r, "成交股數")
                else:   # 櫃買：成交張數／成交仟股，單位都是千股
                    v = (get(r, "成交張數") or get(r, "成交仟股") or 0) * 1000
                rows.append({"date": _roc(r[ix["日期"]]), "code": code, "open": get(r, "開盤"), "high": get(r, "最高"),
                             "low": get(r, "最低"), "close": c, "volume": v})
        time.sleep(fetch.REQUEST_GAP)
        m = (m + dt.timedelta(days=32)).replace(day=1)
    return pd.DataFrame(rows, columns=fetch.COLS)


def add_delisted(df: pd.DataFrame, start: dt.date, end: dt.date, out_list: str) -> pd.DataFrame:
    import requests
    s = requests.Session()
    s.headers.update(fetch.HEADERS)
    dl = delisted(s, start)
    dl = dl[~dl.code.isin(set(df.code))]
    log.info("期間內下市 %d 檔要補（%s）", len(dl), ", ".join(dl.code.head(20)))
    parts, n = [], []
    for r in dl.itertuples():
        last = min(end, dt.date.fromisoformat(r.delist_date))
        try:
            x = official_stock(s, r.code, r.market, start, last)
        except Exception as e:  # noqa: BLE001 — 單一檔壞掉不能拖垮全部
            log.warning("下市 %s %s 補抓失敗：%s", r.code, r.name, e)
            x = pd.DataFrame(columns=fetch.COLS)
        x = x[x.date.notna() & (x.date <= r.delist_date)]
        n.append(len(x))
        if len(x):
            parts.append(x.assign(adjusted=0))
        log.info("下市 %s %s：%d 筆", r.code, r.name, len(x))
    dl.assign(rows=n).to_csv(out_list, index=False)
    return pd.concat([df, *parts], ignore_index=True) if parts else df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--out", default="backtest.csv.gz")
    ap.add_argument("--source", default="yahoo", choices=["yahoo", "official"])
    ap.add_argument("--no-delisted", action="store_true", help="不補期間內下市的股票")
    ap.add_argument("--append-delisted", metavar="CSV", help="不重抓 Yahoo：讀現有的回測檔，只補下市股")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    stocks = fetch.load_stock_list(["TWSE", "TPEX"])
    end = dt.date.today()
    start = end - dt.timedelta(days=int(a.years * 365.25))
    df = None
    if a.append_delisted:
        df = pd.read_csv(a.append_delisted, dtype={"code": str})
        df = df[df.get("adjusted", 1) != 0] if "adjusted" in df else df   # 舊的下市股列先拿掉再重補
        start = dt.date.fromisoformat(df.date.min())
    elif a.source == "yahoo":
        try:
            df = yahoo_adjusted(stocks, start)
            df["adjusted"] = 1
        except Exception as e:  # noqa: BLE001
            log.warning("Yahoo 失敗，改用官方資料：%s", e)
    if df is None:
        dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        dates = [d for d in dates if d.weekday() < 5]
        df = fetch.fetch_official_range(dates, ["TWSE", "TPEX"])
        df = df[df.code.isin(set(stocks.code))][fetch.COLS]
        df["adjusted"] = 0
    if (a.source == "yahoo" or a.append_delisted) and not a.no_delisted:
        try:
            df = add_delisted(df, start, end, str(Path(a.out).with_name("delisted.csv")))
        except Exception as e:  # noqa: BLE001
            log.warning("補下市股失敗（回測資料仍只有存活股）：%s", e)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float).round(3)
    df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
    df.sort_values(["date", "code"]).to_csv(a.out, index=False, compression="gzip")
    log.info("完成：%d 筆，%s ~ %s，%d 檔", len(df), df.date.min(), df.date.max(), df.code.nunique())


if __name__ == "__main__":
    main()
