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


def yahoo_adjusted(stocks: pd.DataFrame, start: dt.date, end: dt.date | None = None) -> pd.DataFrame:
    import yfinance as yf

    suffix = {"TWSE": ".TW", "TPEX": ".TWO"}
    tickers = {f"{c}{suffix[m]}": c for c, m in zip(stocks.code, stocks.market)}
    names = list(tickers)
    frames = []
    for i in range(0, len(names), 100):
        chunk = names[i : i + 100]
        log.info("Yahoo %d-%d / %d", i + 1, i + len(chunk), len(names))
        raw = yf.download(chunk, start=start.isoformat(), end=end.isoformat() if end else None, auto_adjust=True, group_by="ticker",
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


def _session():
    import requests
    s = requests.Session()
    s.headers.update(fetch.HEADERS)
    return s


def add_delisted(df: pd.DataFrame, start: dt.date, end: dt.date, out_list: str, budget_min: float = 0) -> pd.DataFrame:
    """補期間內下市的股票（官方逐月日線）。上市、上櫃是不同主機、各自限速，所以分兩條線同時抓。
    budget_min > 0：超過這麼多分鐘就不再抓新的一檔，已抓到的照樣存（rows=-1 表示沒抓到）。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.monotonic()
    dl = delisted(_session(), start)
    dl = dl[~dl.code.isin(set(df.code))].reset_index(drop=True)
    log.info("期間內下市 %d 檔要補（上市 %d、上櫃 %d）", len(dl), (dl.market == "TWSE").sum(), (dl.market == "TPEX").sum())
    got: dict[str, pd.DataFrame] = {}
    lock = threading.Lock()

    def work(market):
        s = _session()
        todo = dl[dl.market == market]
        for k, r in enumerate(todo.itertuples(), 1):
            if budget_min and (time.monotonic() - t0) / 60 > budget_min:
                log.warning("%s 超過時間預算 %.0f 分鐘，剩 %d 檔沒抓", market, budget_min, len(todo) - k + 1)
                return
            last = min(end, dt.date.fromisoformat(r.delist_date))
            try:
                x = official_stock(s, r.code, r.market, start, last)
            except Exception as e:  # noqa: BLE001 — 單一檔壞掉不能拖垮全部
                log.warning("下市 %s %s 補抓失敗：%s", r.code, r.name, e)
                x = pd.DataFrame(columns=fetch.COLS)
            x = x[x.date.notna() & (x.date <= r.delist_date)]
            with lock:
                got[r.code] = x
            log.info("下市 %s %s %s：%d 筆（%s %d/%d）", r.market, r.code, r.name, len(x), market, k, len(todo))

    with ThreadPoolExecutor(2) as ex:
        list(ex.map(work, ["TWSE", "TPEX"]))
    dl.assign(rows=[len(got[c]) if c in got else -1 for c in dl.code]).to_csv(out_list, index=False)
    parts = [x.assign(adjusted=0) for x in got.values() if len(x)]
    log.info("下市股補了 %d／%d 檔、%d 筆，花 %.0f 分鐘", len(got), len(dl), sum(map(len, parts)), (time.monotonic() - t0) / 60)
    return pd.concat([df, *parts], ignore_index=True) if parts else df


def find_holes(df: pd.DataFrame, tol: float = 0.98, win: int = 5) -> list[str]:
    """資料洞：當天檔數比前後各 win 天的中位少 (1-tol) 以上（Yahoo 偶爾整天缺一大批）。"""
    n = df.groupby("date").code.size().sort_index()
    ref = pd.concat([n.shift(k) for k in [*range(1, win + 1), *range(-win, 0)]], axis=1).median(axis=1)
    return sorted(n[n < ref * tol].index)


def _ex_dates(path: str = "data/extras/exdiv_5y.csv.gz") -> dict[str, list[str]]:
    try:
        x = pd.read_csv(path, dtype={"code": str})
        x2 = pd.read_csv("data/extras/exdiv.csv", dtype={"code": str})
        x = pd.concat([x[["date", "code"]], x2[["date", "code"]]])
        return x.drop_duplicates().groupby("code").date.apply(sorted).to_dict()
    except Exception as e:  # noqa: BLE001
        log.warning("讀不到除權息日：%s", e)
        return {}


def fill_holes(df: pd.DataFrame, holes: list[str] | None = None) -> pd.DataFrame:
    """用官方當日全市場行情補 Yahoo 資料洞，換算到 Yahoo 還原價的刻度：
    因子＝前一個（或後一個）正常日的 還原收盤／官方收盤；兩邊因子不同（中間有除權息）就看除權息日在洞的前後決定用哪邊。"""
    holes = find_holes(df) if holes is None else holes
    if not holes:
        log.info("沒有資料洞")
        return df
    dates = sorted(df.date.unique())
    pos = {d: i for i, d in enumerate(dates)}
    hs = set(holes)
    plan, need = [], set(holes)
    for h in holes:
        i = pos[h]
        p = next((dates[j] for j in range(i - 1, -1, -1) if dates[j] not in hs), None)
        a = next((dates[j] for j in range(i + 1, len(dates)) if dates[j] not in hs), None)
        plan.append((h, p, a))
        need |= {x for x in (p, a) if x}
    log.info("資料洞 %d 天：%s；連前後正常日共抓官方 %d 天", len(holes), holes, len(need))
    off = fetch.fetch_official_range([dt.date.fromisoformat(d) for d in sorted(need)], ["TWSE", "TPEX"])
    for c in ("open", "high", "low", "close", "volume"):
        off[c] = pd.to_numeric(off[c], errors="coerce")
    off = off.dropna(subset=["close"])
    off = off[(off.close > 0) & (off.volume > 0)]
    raw = off.set_index(["date", "code"]).close
    yah = df[df.get("adjusted", 1) != 0]
    adj = yah.set_index(["date", "code"]).close
    have = set(zip(df.date, df.code))
    exd = _ex_dates()
    add, skipped = [], 0
    for h, p, a in plan:
        lo_d = dates[max(0, pos[h] - 5)]
        hi_d = dates[min(len(dates) - 1, pos[h] + 5)]
        near = set(yah[(yah.date >= lo_d) & (yah.date <= hi_d)].code)
        for r in off[off.date == h].itertuples():
            c = r.code
            if (h, c) in have or c not in near:
                continue
            fp = adj.get((p, c)) / raw.get((p, c)) if p and (p, c) in adj.index and (p, c) in raw.index else None
            fa = adj.get((a, c)) / raw.get((a, c)) if a and (a, c) in adj.index and (a, c) in raw.index else None
            if fp and fa and abs(fp / fa - 1) > 0.002:
                e = next((x for x in exd.get(c, []) if p < x <= a), None)
                f = (fp if h < e else fa) if e else None
            else:
                f = fp or fa
            if not f:
                skipped += 1
                continue
            add.append({"date": h, "code": c, "open": r.open * f, "high": r.high * f, "low": r.low * f,
                        "close": r.close * f, "volume": r.volume, "adjusted": 1})
    log.info("補洞 %d 筆（換算不了略過 %d 筆）", len(add), skipped)
    out = pd.concat([df, pd.DataFrame(add)], ignore_index=True) if add else df
    for h in holes:
        log.info("  %s：%d → %d 檔", h, (df.date == h).sum(), (out.date == h).sum())
    return out


def prepend(df: pd.DataFrame, start: dt.date, stocks: pd.DataFrame, list_out: str) -> pd.DataFrame:
    """回測檔往前延伸到 start：現存股用 Yahoo 還原價（在接縫重疊 2 週、逐檔對齊刻度），
    下市股（期間內下市、或檔裡本來就是官方原始價的）用官方當日全市場行情（未還原，adjusted=0，同 add_delisted）。"""
    first = df.date.min()
    f0 = dt.date.fromisoformat(first)
    y = yahoo_adjusted(stocks, start, f0 + dt.timedelta(days=15))
    ov = y[y.date >= first].merge(df[["date", "code", "close"]], on=["date", "code"], suffixes=("", "_old"))
    k = (ov.close_old / ov.close).groupby(ov.code).median()
    y = y[(y.date < first) & y.code.isin(k.index)].copy()
    for c in ("open", "high", "low", "close"):
        y[c] = y[c] * y.code.map(k)
    y["adjusted"] = 1
    log.info("Yahoo 往前補 %d 檔、%d 筆（%s～%s）", y.code.nunique(), len(y), y.date.min(), y.date.max())
    dl = delisted(_session(), start)
    raw_codes = set(dl.code) | set(df.loc[df.get("adjusted", 1) == 0, "code"])
    raw_codes -= set(y.code)
    dates = [start + dt.timedelta(days=i) for i in range((f0 - start).days)]
    off = fetch.fetch_official_range([d for d in dates if d.weekday() < 5], ["TWSE", "TPEX"])
    for c in ("open", "high", "low", "close", "volume"):
        off[c] = pd.to_numeric(off[c], errors="coerce")
    off = off.dropna(subset=["close"])
    off = off[(off.close > 0) & (off.volume > 0)]
    o = off[off.code.isin(raw_codes)][fetch.COLS].assign(adjusted=0)
    o = o.merge(dl[["code", "delist_date"]], on="code", how="left")
    o = o[o.delist_date.isna() | (o.date <= o.delist_date)].drop(columns="delist_date")
    log.info("下市股往前補 %d 檔、%d 筆", o.code.nunique(), len(o))
    old = pd.read_csv(list_out) if Path(list_out).exists() else pd.DataFrame()
    dl.assign(rows=dl.code.map(pd.concat([o, df[df.get("adjusted", 1) == 0]]).groupby("code").size()).fillna(0).astype(int)) \
        .to_csv(list_out, index=False)
    log.info("下市名單 %d → %d 檔", len(old), len(dl))
    out = pd.concat([y, o, df], ignore_index=True)
    # 往前那段也可能有 Yahoo 洞：用剛抓的官方當日行情一起補
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--out", default="backtest.csv.gz")
    ap.add_argument("--source", default="yahoo", choices=["yahoo", "official"])
    ap.add_argument("--no-delisted", action="store_true", help="不補期間內下市的股票")
    ap.add_argument("--append-delisted", metavar="CSV", help="不重抓 Yahoo：讀現有的回測檔，只補下市股")
    ap.add_argument("--budget-min", type=float, default=0, help="補下市股最多花幾分鐘（0＝不限），超過就存已抓到的")
    ap.add_argument("--extend", metavar="CSV", help="讀現有回測檔：往前延伸到 --start、再補資料洞（不重抓整段）")
    ap.add_argument("--start", help="--extend 用：新的起點 YYYY-MM-DD（不填就只補洞）")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    stocks = fetch.load_stock_list(["TWSE", "TPEX"])
    if a.extend:
        df = pd.read_csv(a.extend, dtype={"code": str})
        n0 = len(df)
        if a.start and a.start < df.date.min():
            df = prepend(df, dt.date.fromisoformat(a.start), stocks, str(Path(a.out).with_name("delisted.csv")))
        df = fill_holes(df)
        df = df.drop_duplicates(["date", "code"], keep="last")
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c].astype(float).round(3)
        df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
        df.sort_values(["date", "code"]).to_csv(a.out, index=False, compression="gzip")
        log.info("完成：%d → %d 筆，%s ~ %s，%d 檔", n0, len(df), df.date.min(), df.date.max(), df.code.nunique())
        return
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
            df = add_delisted(df, start, end, str(Path(a.out).with_name("delisted.csv")), a.budget_min)
        except Exception as e:  # noqa: BLE001
            log.warning("補下市股失敗（回測資料仍只有存活股）：%s", e)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float).round(3)
    df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
    df.sort_values(["date", "code"]).to_csv(a.out, index=False, compression="gzip")
    log.info("完成：%d 筆，%s ~ %s，%d 檔", len(df), df.date.min(), df.date.max(), df.code.nunique())


if __name__ == "__main__":
    main()
