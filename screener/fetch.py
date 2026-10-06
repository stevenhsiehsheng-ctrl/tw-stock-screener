"""資料抓取：證交所（上市）＋櫃買中心（上櫃）每日全市場行情，Yahoo Finance 當備援。

歷史資料快取在 data/history.csv.gz，每天只需補抓新的交易日。
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "history.csv.gz"
STOCK_LIST_FILE = ROOT / "data" / "stock_list.csv"

COLS = ["date", "code", "open", "high", "low", "close", "volume"]
# 大盤對照組（ETF）：另存 data/extras/bench.csv，不放進 history，免得 ETF 混進選股母體
BENCH_F = ROOT / "data" / "extras" / "bench.csv"
BENCH_CODES = {"0050": "TWSE"}
# 全部上市櫃 ETF（代號 00 開頭）的每日行情：Cowork 的持股日報、虛擬帳戶結算用
ETF_F = ROOT / "data" / "extras" / "etf.csv.gz"
SRC_LOG = ROOT / "data" / "extras" / "src_log.csv"   # 每個交易日的行情來源
ETF_FOCUS = {"0050": "TWSE", "009816": "TWSE", "00981A": "TWSE"}   # 第一次沒有就用 Yahoo 補一年
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
REQUEST_GAP = 3.5  # 秒；證交所請求太密會被暫時封鎖

FIELD_ALIASES = {
    "code": ["證券代號", "代號"],
    "name": ["證券名稱", "名稱"],
    "open": ["開盤價", "開盤"],
    "high": ["最高價", "最高"],
    "low": ["最低價", "最低"],
    "close": ["收盤價", "收盤"],
    "volume": ["成交股數"],
}


class SourceUnavailable(Exception):
    """資料來源連不上（不是休市，而是網路/封鎖問題）。"""


# ---------------------------------------------------------------- 工具函式
def _num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "--", "---", "----", "X", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _map_fields(fields: list[str]) -> dict[str, int]:
    clean = [str(f).replace(" ", "").replace("　", "").strip() for f in fields]
    out: dict[str, int] = {}
    for key, aliases in FIELD_ALIASES.items():
        for i, f in enumerate(clean):
            if f in aliases:
                out[key] = i
                break
        else:
            for i, f in enumerate(clean):
                if any(f.startswith(a) for a in aliases):
                    out[key] = i
                    break
    return out


def _rows_to_df(fields, rows, date: dt.date) -> pd.DataFrame:
    idx = _map_fields(fields)
    need = {"code", "open", "high", "low", "close", "volume"}
    if not need.issubset(idx):
        raise ValueError(f"欄位對不上：{fields}")
    recs = []
    for r in rows:
        code = str(r[idx["code"]]).strip()
        recs.append(
            {
                "date": date.isoformat(),
                "code": code,
                "name": str(r[idx["name"]]).strip() if "name" in idx else "",
                "open": _num(r[idx["open"]]),
                "high": _num(r[idx["high"]]),
                "low": _num(r[idx["low"]]),
                "close": _num(r[idx["close"]]),
                "volume": _num(r[idx["volume"]]),
            }
        )
    return pd.DataFrame(recs, columns=COLS + ["name"])


def _get_json(session: requests.Session, url: str, params: dict) -> dict:
    last = None
    for attempt in range(5):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    # 被封鎖時常回傳 HTML
                    last = f"非 JSON 回應（可能被暫時封鎖）：{r.text[:120]!r}"
            else:
                last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(REQUEST_GAP * (attempt + 2))
    raise SourceUnavailable(f"{url} 失敗：{last}")


# ---------------------------------------------------------------- 官方來源
def fetch_twse_day(session: requests.Session, date: dt.date) -> pd.DataFrame | None:
    """證交所：某日全部上市股票收盤行情。休市回傳 None。"""
    j = _get_json(
        session,
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        {"date": date.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
    )
    if str(j.get("stat", "")).upper() != "OK":
        return None
    tables = j.get("tables")
    if tables is None:  # 舊版格式 fields9/data9
        tables = [
            {"fields": j[k], "data": j.get("data" + k[6:], [])}
            for k in j
            if k.startswith("fields")
        ]
    for t in tables:
        fields = t.get("fields") or []
        f = "".join(fields)
        if "證券代號" in f and "收盤價" in f and t.get("data"):
            return _rows_to_df(fields, t["data"], date)
    return None


def fetch_tpex_day(session: requests.Session, date: dt.date) -> pd.DataFrame | None:
    """櫃買中心：某日全部上櫃股票收盤行情。休市回傳 None。"""
    try:
        j = _get_json(
            session,
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
            {"date": date.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        )
        for t in j.get("tables") or []:
            if t.get("data") and t.get("fields"):
                return _rows_to_df(t["fields"], t["data"], date)
        if j.get("tables") is not None:
            return None
    except (SourceUnavailable, ValueError) as e:
        log.warning("櫃買新版 API 失敗，改試舊版：%s", e)

    # 舊版 API（民國日期，固定欄位順序）
    roc = f"{date.year - 1911}/{date.month:02d}/{date.day:02d}"
    j = _get_json(
        session,
        "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
        {"l": "zh-tw", "d": roc, "o": "json"},
    )
    rows = j.get("aaData") or []
    if not rows:
        return None
    fields = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數"]
    return _rows_to_df(fields, [r[:9] for r in rows], date)


def fetch_official_range(dates: list[dt.date], markets: list[str]) -> pd.DataFrame:
    session = requests.Session()
    frames = []
    missing: list[tuple[dt.date, str]] = []
    fails = 0  # 連續失敗次數；太多代表整個來源被擋，改用 Yahoo
    for i, d in enumerate(dates):
        got, failed = [], []
        for mk, fn in (("TWSE", fetch_twse_day), ("TPEX", fetch_tpex_day)):
            if mk not in markets:
                continue
            try:
                df = fn(session, d)
            except SourceUnavailable as e:
                log.warning("%s %s 抓取失敗：%s", d, mk, e)
                failed.append(mk)
                df = None
            time.sleep(REQUEST_GAP)
            if df is not None:
                got.append(df.assign(market=mk))
        fails = fails + 1 if failed and not got else 0
        if fails >= 3:
            raise SourceUnavailable("官方來源連續失敗")
        status = f"{sum(len(g) for g in got)} 筆" if got else ("失敗" if failed else "休市/無資料")
        log.info("[%d/%d] %s %s", i + 1, len(dates), d, status)
        if got:
            have = {g.market.iloc[0] for g in got}
            missing += [(d, m) for m in markets if m not in have]
        frames.extend(got)
    # 只抓到一個市場的日子（另一個市場連線失敗），最後再補抓一次
    for d, mk in missing:
        time.sleep(REQUEST_GAP * 3)
        try:
            df = (fetch_twse_day if mk == "TWSE" else fetch_tpex_day)(session, d)
        except SourceUnavailable as e:
            log.warning("補抓 %s %s 失敗：%s", d, mk, e)
            continue
        if df is not None:
            frames.append(df.assign(market=mk))
            log.info("補抓 %s %s：%d 筆", d, mk, len(df))
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- Yahoo 備援
def fetch_yahoo(stocks: pd.DataFrame, start: dt.date) -> pd.DataFrame:
    import yfinance as yf

    suffix = {"TWSE": ".TW", "TPEX": ".TWO"}
    tickers = {f"{c}{suffix[m]}": c for c, m in zip(stocks.code, stocks.market)}
    names = list(tickers)
    frames = []
    for i in range(0, len(names), 200):
        chunk = names[i : i + 200]
        log.info("Yahoo 下載 %d-%d / %d", i + 1, i + len(chunk), len(names))
        raw = yf.download(
            chunk,
            start=start.isoformat(),
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            if sub.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "date": sub.index.strftime("%Y-%m-%d"),
                        "code": tickers[t],
                        "open": sub["Open"].values,
                        "high": sub["High"].values,
                        "low": sub["Low"].values,
                        "close": sub["Close"].values,
                        "volume": sub["Volume"].values,
                    }
                )
            )
        time.sleep(2)
    if not frames:
        raise SourceUnavailable("Yahoo Finance 也抓不到資料")
    return pd.concat(frames, ignore_index=True)


def update_bench(new: pd.DataFrame | None, keep_days: int) -> None:
    """從當天抓到的全市場行情挑出對照組 ETF；檔案裡天數不夠時先用 Yahoo 補一年。失敗不影響主流程。"""
    try:
        old = pd.read_csv(BENCH_F, dtype={"code": str}) if BENCH_F.exists() else pd.DataFrame(columns=COLS)
        add = [new[new.code.isin(list(BENCH_CODES))][COLS]] if new is not None and len(new) else []
        n = old.groupby("code").size().reindex(list(BENCH_CODES)).fillna(0)
        if (n < keep_days * 0.9).any():
            start = dt.date.today() - dt.timedelta(days=int(keep_days * 1.5) + 10)
            try:
                y = fetch_yahoo(pd.DataFrame({"code": list(BENCH_CODES), "market": list(BENCH_CODES.values())}), start)
                add.insert(0, y[COLS])
                log.info("對照組 ETF 用 Yahoo 補 %d 筆", len(y))
            except Exception as e:  # noqa: BLE001
                log.warning("對照組 ETF 用 Yahoo 補歷史失敗（明天再試）：%s", e)
        df = pd.concat([old, *add], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
        # 和 history 的交易日對齊：Yahoo 偶爾缺某天、或休市日（颱風假）多一筆
        days = set(load_history().date.unique())
        if days:
            df = df[df.date.isin(days) | (df.date > max(days))]
            gap = sorted(days - set(df.date))[-10:]
            if gap:
                s = requests.Session()
                for d in gap:
                    try:
                        t = fetch_twse_day(s, dt.date.fromisoformat(d))
                        if t is not None:
                            df = pd.concat([df, t[t.code.isin(list(BENCH_CODES))][COLS]], ignore_index=True)
                    except Exception as e:  # noqa: BLE001
                        log.warning("對照組 ETF 補 %s 失敗：%s", d, e)
                    time.sleep(REQUEST_GAP)
                log.info("對照組 ETF 補缺漏日 %s", gap)
        px = ["open", "high", "low", "close"]
        df[px] = df[px].apply(pd.to_numeric, errors="coerce").round(2)
        keep = sorted(df.date.unique())[-keep_days:]
        BENCH_F.parent.mkdir(parents=True, exist_ok=True)
        df[df.date.isin(keep)].sort_values(["code", "date"]).to_csv(BENCH_F, index=False)
    except Exception as e:  # noqa: BLE001
        log.warning("對照組 ETF 更新失敗：%s", e)


def update_etf(new: pd.DataFrame | None, keep_days: int) -> None:
    """當天抓到的全市場行情裡挑出 ETF（代號 00 開頭）存 data/extras/etf.csv.gz。失敗不影響主流程。"""
    try:
        cols = COLS + ["name"]
        old = pd.read_csv(ETF_F, dtype={"code": str}) if ETF_F.exists() else pd.DataFrame(columns=cols)
        add = []
        if new is not None and len(new):
            e = new[new.code.astype(str).str.fullmatch(r"00\d{2,4}[A-Z]?")]
            add.append(e.reindex(columns=cols))
        need = [c for c in ETF_FOCUS if c not in set(old.code)]
        if need:
            start = dt.date.today() - dt.timedelta(days=int(keep_days * 1.5) + 10)
            try:
                y = fetch_yahoo(pd.DataFrame({"code": need, "market": [ETF_FOCUS[c] for c in need]}), start)
                add.insert(0, y.reindex(columns=cols))
                log.info("ETF 用 Yahoo 補 %s：%d 筆", need, len(y))
            except Exception as e:  # noqa: BLE001
                log.warning("ETF 用 Yahoo 補歷史失敗（明天再試）：%s", e)
        df = pd.concat([old, *add], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
        if df.empty:
            return
        # 跟 history 的交易日對齊；重點 ETF 近 10 個交易日有缺（Yahoo 晚一天）就用證交所當天行情補
        days = set(load_history().date.unique())
        if days:
            df = df[df.date.isin(days) | (df.date > max(days))]
            have = set(zip(df.date, df.code))
            gap = [d for d in sorted(days)[-10:] if any((d, c) not in have for c in ETF_FOCUS if c in set(df.code))]
            s = requests.Session()
            for d in gap:
                try:
                    t = fetch_twse_day(s, dt.date.fromisoformat(d))
                    if t is not None:
                        t = t[t.code.astype(str).str.fullmatch(r"00\d{2,4}[A-Z]?")].reindex(columns=cols)
                        df = pd.concat([df, t], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
                except Exception as e:  # noqa: BLE001
                    log.warning("ETF 補 %s 失敗：%s", d, e)
                time.sleep(REQUEST_GAP)
            if gap:
                log.info("ETF 補缺漏日 %s", gap)
        # Yahoo 給的是浮點數（112.050003），價格一律取到小數兩位，跟官方行情、bench.csv 一致
        px = ["open", "high", "low", "close"]
        df[px] = df[px].apply(pd.to_numeric, errors="coerce").round(2)
        df["name"] = df.groupby("code")["name"].transform(lambda x: x.replace("", pd.NA).ffill().bfill()).fillna("")
        keep = sorted(df.date.unique())[-keep_days:]
        ETF_F.parent.mkdir(parents=True, exist_ok=True)
        df[df.date.isin(keep)].sort_values(["code", "date"]).to_csv(ETF_F, index=False)
        log.info("ETF %d 檔、%d 天", df.code.nunique(), len(keep))
    except Exception as e:  # noqa: BLE001
        log.warning("ETF 行情更新失敗：%s", e)


# ---------------------------------------------------------------- 對外介面
def _build_stock_list() -> None:
    """第一次執行時，用 twstock 套件內建的代號表建立股票清單。"""
    import importlib.util

    base = Path(importlib.util.find_spec("twstock").origin).parent / "codes"
    out = []
    for f, mk_name, mk in [("twse", "上市", "TWSE"), ("tpex", "上櫃", "TPEX")]:
        d = pd.read_csv(base / f"{f}_equities.csv", dtype=str)
        d = d[d.type.isin(["股票", "創新板"]) & d.code.str.fullmatch(r"[1-9]\d{3}")]
        out.append(
            pd.DataFrame({"code": d.code, "name": d.name, "market": mk, "industry": d.group})
        )
    full = pd.concat(out).drop_duplicates("code").sort_values("code")
    STOCK_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(STOCK_LIST_FILE, index=False)
    log.info("已建立股票清單：%d 檔", len(full))


def load_stock_list(markets: list[str]) -> pd.DataFrame:
    if not STOCK_LIST_FILE.exists():
        _build_stock_list()
    s = pd.read_csv(STOCK_LIST_FILE, dtype=str)
    return s[s.market.isin(markets)].reset_index(drop=True)


def _add_new_listings(new: pd.DataFrame, stocks: pd.DataFrame, markets: list[str]) -> None:
    """官方資料出現清單裡沒有的新上市櫃股票時，自動加入股票清單。"""
    if "name" not in new or "market" not in new:
        return
    fresh = new[~new.code.isin(set(stocks.code))].drop_duplicates("code", keep="last")
    if fresh.empty:
        return
    full = pd.read_csv(STOCK_LIST_FILE, dtype=str)
    add = pd.DataFrame(
        {"code": fresh.code, "name": fresh.name, "market": fresh.market, "industry": ""}
    )
    full = pd.concat([full, add]).drop_duplicates("code").sort_values("code")
    full.to_csv(STOCK_LIST_FILE, index=False)
    log.info("新增 %d 檔新股到清單：%s", len(add), ", ".join(add.code))


def _repair_gaps(hist: pd.DataFrame, stocks: pd.DataFrame, markets: list[str], limit: int = 10) -> pd.DataFrame:
    """找出某個市場整天資料缺漏的日子（例如當時連線中斷），重新補抓。"""
    mk = hist.code.map(stocks.set_index("code").market)
    counts = hist.assign(mk=mk).groupby(["date", "mk"]).size().unstack(fill_value=0)
    todo = []
    for m in markets:
        if m not in counts:
            continue
        med = counts[m].median()
        bad = counts.index[counts[m] < med * 0.5]
        todo += [(d, m) for d in bad]
    if not todo:
        return hist
    session = requests.Session()
    added = []
    for d, m in todo[:limit]:
        day = dt.date.fromisoformat(d)
        try:
            df = (fetch_twse_day if m == "TWSE" else fetch_tpex_day)(session, day)
        except SourceUnavailable as e:
            log.warning("補缺 %s %s 失敗：%s", d, m, e)
            continue
        finally:
            time.sleep(REQUEST_GAP)
        if df is not None:
            added.append(df[COLS])
            log.info("補齊缺漏：%s %s %d 筆", d, m, len(df))
    if not added:
        return hist
    new = pd.concat(added, ignore_index=True)
    new = new[new.code.isin(set(stocks.code))]
    merged = pd.concat([hist, new], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
    return merged


def load_history() -> pd.DataFrame:
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE, dtype={"code": str})
    return pd.DataFrame(columns=COLS)


def save_history(df: pd.DataFrame, keep_days: int) -> None:
    dates = sorted(df.date.unique())[-keep_days:]
    df = df[df.date.isin(dates)].sort_values(["date", "code"]).copy()
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float).round(2)
    df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(HISTORY_FILE, index=False, compression="gzip")


def _extend_back(hist: pd.DataFrame, stocks: pd.DataFrame, markets: list[str], keep_days: int,
                 max_weekdays: int = 150) -> pd.DataFrame:
    """歷史天數不到 keep_days（例如把 history_days 調大）時，往前補抓較早的交易日。

    每次最多抓 max_weekdays 個工作日（約 15~20 分鐘），不夠的下次執行再補；抓不到就維持原樣。
    """
    have = hist.date.nunique()
    if hist.empty or have >= keep_days:
        return hist
    need = keep_days - have
    first = dt.date.fromisoformat(hist.date.min())
    n = min(max_weekdays, int(need * 1.1) + 5)  # 多抓一點，抵銷國定假日
    days, d = [], first
    while len(days) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    days.reverse()
    log.info("歷史只有 %d 天（設定 %d 天），往前補抓 %d 個工作日（%s ~ %s）", have, keep_days, len(days), days[0], days[-1])
    try:
        old = fetch_official_range(days, markets)
    except Exception as e:  # noqa: BLE001 — 補抓失敗不能影響今天的篩選
        log.warning("往前補抓失敗，下次再試：%s", e)
        return hist
    if old.empty:
        return hist
    old = old[old.code.isin(set(stocks.code))][COLS]
    log.info("往前補抓完成：%d 筆、%d 個交易日", len(old), old.date.nunique())
    return pd.concat([old, hist], ignore_index=True).drop_duplicates(["date", "code"], keep="last")


def _log_source(new: pd.DataFrame, src: str) -> None:
    """記下每個交易日的行情來源（official／yahoo），事後才分得出哪幾天走了 Yahoo 備援。"""
    try:
        days = sorted(new.date.unique())
        if not days:
            return
        old = pd.read_csv(SRC_LOG) if SRC_LOG.exists() else pd.DataFrame(columns=["date", "src"])
        df = pd.concat([old, pd.DataFrame({"date": days, "src": src})]).drop_duplicates("date", keep="last")
        df.sort_values("date").to_csv(SRC_LOG, index=False)
        if src != "official":
            log.warning("這批行情走 Yahoo 備援：%s ~ %s（已記到 %s）", days[0], days[-1], SRC_LOG.name)
    except Exception as e:  # noqa: BLE001
        log.warning("記錄行情來源失敗：%s", e)


def update_history(
    target: dt.date, markets: list[str], keep_days: int, source: str = "auto"
) -> pd.DataFrame:
    """補齊歷史資料到 target 日，回傳完整歷史（長表）。"""
    hist = load_history()
    stocks = load_stock_list(markets)
    if source in ("auto", "official"):
        hist = _extend_back(hist, stocks, markets, keep_days)

    if hist.empty:
        # 首次執行：約 keep_days 個交易日 ≈ keep_days*1.5 個日曆天
        start = target - dt.timedelta(days=int(keep_days * 1.5) + 10)
    else:
        start = dt.date.fromisoformat(hist.date.max()) + dt.timedelta(days=1)

    dates = [
        start + dt.timedelta(days=i)
        for i in range((target - start).days + 1)
        if (start + dt.timedelta(days=i)).weekday() < 5
    ]
    if source in ("auto", "official") and not hist.empty:
        hist = _repair_gaps(hist, stocks, markets)
    if not dates:
        log.info("歷史資料已是最新（%s）", hist.date.max())
        if source in ("auto", "official"):
            update_bench(None, keep_days)
            update_etf(None, keep_days)   # 只補重點 ETF 的歷史（例如前一次中途失敗）
        save_history(hist, keep_days)
        return load_history()

    new = None
    if source in ("auto", "official"):
        try:
            log.info("從證交所/櫃買中心抓取 %d 個工作日（%s ~ %s）", len(dates), dates[0], dates[-1])
            new = fetch_official_range(dates, markets)
        except SourceUnavailable as e:
            if source == "official":
                raise
            log.warning("官方來源無法使用，改用 Yahoo Finance：%s", e)
    src = "official"
    if new is None:
        new = fetch_yahoo(stocks, start)
        new = new[new.date <= target.isoformat()]
        src = "yahoo"
    _log_source(new, src)

    update_bench(new, keep_days)
    update_etf(new, keep_days)
    new = new[new.code.str.fullmatch(r"[1-9]\d{3}")]
    _add_new_listings(new, stocks, markets)
    stocks = load_stock_list(markets)
    new = new[new.code.isin(set(stocks.code))][COLS]
    merged = pd.concat([hist, new], ignore_index=True)
    merged = merged.drop_duplicates(["date", "code"], keep="last")
    save_history(merged, keep_days)
    return load_history()
