"""除權息：算含息報酬用（虛擬帳戶、0050 對照組）。

資料來源（2026/10 用 GitHub Actions 實測過格式）：
- 已除權息：證交所 exRight/TWT49U、櫃買 bulletin/exDailyQ（可以一次查一段日期）
- 即將除權息：證交所 openapi TWT48U_ALL、櫃買 openapi tpex_exright_prepost

檔案：
- data/extras/exdiv.csv：date, code, market, kind（息／權／權息）, prev_close（除權息前收盤）, ref_price（除權息參考價）,
  value（權值＋息值，元）, factor（prev_close ÷ ref_price）,
  cash_div（每股現金股利，元）, stock_ratio（無償配股：每 1 股配幾股，例如 0.1 = 每千股配 100 股）,
  rights_ratio（現金增資：每 1 股可認購幾股）, rights_price（認購價）
  證交所的結果表只有權值＋息值合計，「權」「權息」那幾筆另外查 TWT49UDetail 拆開；櫃買的表本來就有分開。
  含息報酬：除權息日之後的價格 × factor 才能和之前比；連乘所有 factor 就是還原權息。
- data/extras/exdiv_upcoming.csv：date, code, market, kind, cash_div（元／股）, stock_ratio（無償配股比例）
- data/extras/bench.csv 加 tr 欄：0050 含息指數（收盤 × 之前所有除息 factor 連乘），和收盤價同一天起算
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

import numpy as np
import pandas as pd

from .enrich import DIR, _col, _json, _num, _openapi_rows, _session, _tables

log = logging.getLogger("exdiv")

EXDIV = DIR / "exdiv.csv"
UPCOMING = DIR / "exdiv_upcoming.csv"
BENCH = DIR / "bench.csv"
COLS = ["date", "code", "market", "kind", "prev_close", "ref_price", "value", "factor",
        "cash_div", "stock_ratio", "rights_ratio", "rights_price"]
SPLIT = ["cash_div", "stock_ratio", "rights_ratio", "rights_price"]
UP_COLS = ["date", "code", "market", "kind", "cash_div", "stock_ratio"]
KEEP_DAYS = 800  # 保留約兩年
CODE_RE = re.compile(r"\d{4,6}[A-Z]?")


def roc_date(s) -> str | None:
    """'114年10月02日'、'114/10/02'、'1141002' → '2025-10-02'。"""
    s = str(s).strip()
    p = re.findall(r"\d+", s)
    if len(p) == 1 and len(p[0]) == 7:
        p = [p[0][:3], p[0][3:5], p[0][5:]]
    if len(p) != 3:
        return None
    try:
        return dt.date(int(p[0]) + 1911, int(p[1]), int(p[2])).isoformat()
    except ValueError:
        return None


def _kind(s) -> str:
    s = str(s).replace("除", "")
    return "權息" if "權" in s and "息" in s else "權" if "權" in s else "息" if "息" in s else s


def _rows(fields, data, market) -> list[dict]:
    i_d, i_c = _col(fields, "日期"), _col(fields, "代號")
    i_p, i_r = _col(fields, "除權息前收盤"), _col(fields, "除權息參考價")
    i_v, i_k = _col(fields, "權值", "息值"), _col(fields, "權/息")
    # 櫃買才有：現金股利、每仟股無償配股、現金增資認購價、按持股比例仟股認購
    i_cash, i_stk = _col(fields, "現金股利"), _col(fields, "無償配股")
    i_rp, i_rr = _col(fields, "認購價"), _col(fields, "仟股認購")
    if None in (i_d, i_c, i_p, i_r):
        log.warning("%s 除權息欄位對不上：%s", market, fields)
        return []
    out = []
    for r in data:
        code = str(r[i_c]).strip()
        d = roc_date(r[i_d])
        p, ref = _num(r[i_p]), _num(r[i_r])
        if not d or not CODE_RE.fullmatch(code) or not p or not ref:
            continue
        kind = _kind(r[i_k]) if i_k is not None else ""
        value = _num(r[i_v]) if i_v is not None else None
        row = {"date": d, "code": code, "market": market, "kind": kind, "prev_close": p, "ref_price": ref,
               "value": value, "factor": round(p / ref, 8)}
        if i_cash is not None:
            row.update(cash_div=_num(r[i_cash]) or 0.0, stock_ratio=(_num(r[i_stk]) or 0.0) / 1000 if i_stk is not None else 0.0,
                       rights_ratio=(_num(r[i_rr]) or 0.0) / 1000 if i_rr is not None else 0.0,
                       rights_price=_num(r[i_rp]) or 0.0 if i_rp is not None else 0.0)
        elif kind == "息":  # 純除息：權值＋息值就是現金股利
            row.update(cash_div=value, stock_ratio=0.0, rights_ratio=0.0, rights_price=0.0)
        out.append(row)
    return out


def fetch_done(s, start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = []
    j = _json(s, "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
              {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"})
    for t in _tables(j):
        rows += _rows(t["fields"], t["data"], "TWSE")
    time.sleep(2)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
              {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        rows += _rows(t["fields"], t["data"], "TPEX")
    return pd.DataFrame(rows, columns=COLS)


def _first_num(s) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def fetch_detail(s, code: str, date: str) -> dict | None:
    """證交所單一股票的除權息明細：現金股利、每千股無償配股、現金增資認購。"""
    j = _json(s, "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail",
              {"STK_NO": code, "T1": date.replace("-", ""), "response": "json"})
    if not j or not j.get("data"):
        return None
    f, r = j["fields"], j["data"][0]
    i_cash, i_stk = _col(f, "現金股利"), _col(f, "無償配股")
    i_rp, i_rr = _col(f, "認購金額"), _col(f, "每千股認購")
    if i_cash is None or i_stk is None:
        log.warning("除權息明細欄位對不上：%s", f)
        return None
    g = lambda i: (_first_num(r[i]) or 0.0) if i is not None else 0.0
    return {"cash_div": g(i_cash), "stock_ratio": g(i_stk) / 1000, "rights_ratio": g(i_rr) / 1000, "rights_price": g(i_rp)}


def fill_details(s, df: pd.DataFrame, old: pd.DataFrame, limit: int = 600) -> int:
    """證交所「權」「權息」沒拆開的：先從舊檔沿用，沒有才去查明細。"""
    for c in SPLIT:
        if c not in df.columns:
            df[c] = np.nan
    if len(old) and "cash_div" in old.columns:
        prev = old.dropna(subset=["cash_div"]).set_index(["date", "code"])[SPLIT]
        key = pd.MultiIndex.from_frame(df[["date", "code"]])
        miss = df.cash_div.isna().values & key.isin(prev.index)
        if miss.any():
            df.loc[miss, SPLIT] = prev.reindex(key[miss]).values
    cash_only = df.cash_div.isna() & (df.kind == "息")
    df.loc[cash_only, "cash_div"] = df.loc[cash_only, "value"]
    df.loc[cash_only, ["stock_ratio", "rights_ratio", "rights_price"]] = 0.0
    todo = df.index[df.cash_div.isna() & (df.market == "TWSE")][:limit]
    n = 0
    for i in todo:
        try:
            d = fetch_detail(s, df.at[i, "code"], df.at[i, "date"])
        except Exception as e:  # noqa: BLE001
            log.warning("除權息明細 %s %s 失敗：%s", df.at[i, "code"], df.at[i, "date"], e)
            d = None
        if d:
            for k, v in d.items():
                df.at[i, k] = v
            n += 1
        time.sleep(1.5)
    if len(todo):
        log.info("除權息明細補了 %d／%d 筆", n, len(todo))
    return n


def exact_factor(df: pd.DataFrame) -> None:
    """factor 改用公告的現金股利／配股算：前收 ÷ ((前收 − 現金股利) ÷ (1 + 配股率))。
    證交所的參考價會掃到檔位（台積電 901 − 4.000138 = 896.999862，公布成 896.99），低價股誤差更大。
    有現金增資（rights_ratio > 0）的維持用參考價，因為認購要花錢，不是白拿的。
    已經存在的 bench tr 不會因此改變（tr 只往後接），只影響之後的新日子。"""
    ok = df.cash_div.notna() & df.stock_ratio.notna() & (df.rights_ratio.fillna(0) == 0)
    base = (df.prev_close - df.cash_div) / (1 + df.stock_ratio)
    # 算出來的價格要跟參考價差在掃檔位的範圍內才用；差太多代表還有別的東西（特別股、減資…），維持用參考價
    ok &= (base > 0) & ((base - df.ref_price).abs() <= np.maximum(0.011, df.ref_price * 0.002))
    df.loc[ok, "factor"] = (df.prev_close / base)[ok].round(8)


def fetch_upcoming(s) -> pd.DataFrame:
    out = []
    for r in _openapi_rows(s, "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"):
        out.append({"date": roc_date(r.get("Date")), "code": str(r.get("Code", "")).strip(), "market": "TWSE",
                    "kind": _kind(r.get("Exdividend")), "cash_div": _num(r.get("CashDividend")),
                    "stock_ratio": _num(r.get("StockDividendRatio"))})
    for r in _openapi_rows(s, "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"):
        out.append({"date": roc_date(r.get("ExRrightsExDividendDate")), "code": str(r.get("SecuritiesCompanyCode", "")).strip(),
                    "market": "TPEX", "kind": _kind(r.get("ExRrightsExDividend")), "cash_div": _num(r.get("CashDividend")),
                    "stock_ratio": _num(r.get("StockDividendRatio"))})
    df = pd.DataFrame(out, columns=UP_COLS)
    return df[df.date.notna() & df.code.str.fullmatch(CODE_RE.pattern)].sort_values(["date", "code"])


def bench_tr() -> int:
    """bench.csv 的 tr（含息指數）：自己用收盤＋除權息 factor 算，不是 Yahoo 的 adjclose。
    tr_t = tr_(t-1) × close_t × factor_t ÷ close_(t-1)，factor 只在除權息當天 ≠ 1（= 除權息前收盤 ÷ 參考價）。
    已經算過的 tr 不再改（之後舊資料滾出 260 天視窗，也不會讓整條重新起算、基準漂移）；只補沒有 tr 的新日子。
    第一次（沒有 tr 欄）從檔案第一天起算：tr = 收盤。"""
    if not BENCH.exists() or not EXDIV.exists():
        return 0
    b = pd.read_csv(BENCH, dtype={"code": str})
    ex = pd.read_csv(EXDIV, dtype={"code": str})
    if "tr" not in b.columns:
        b["tr"] = np.nan
    parts, n = [], 0
    for code, g in b.sort_values("date").groupby("code"):
        f = ex[ex.code == code].drop_duplicates("date").set_index("date").factor
        g = g.reset_index(drop=True)
        tr = g.tr.astype(float).to_numpy(copy=True)
        close, step = g.close.astype(float).values, g.date.map(f).fillna(1.0).astype(float).values
        for i in range(len(g)):
            if not np.isnan(tr[i]):
                continue
            tr[i] = close[i] if i == 0 or np.isnan(tr[i - 1]) else tr[i - 1] * close[i] * step[i] / close[i - 1]
            n += 1
        parts.append(g.assign(tr=np.round(tr, 4)))
    pd.concat(parts).to_csv(BENCH, index=False)
    return n


EXDIV_LONG = DIR / "exdiv_5y.csv.gz"


def backfill_long(years: float = 5, today: dt.date | None = None, s=None) -> int:
    """除權息長歷史（研究用，data/extras/exdiv_5y.csv.gz；每天用的 exdiv.csv 不動）。
    只要『哪天哪檔除權息、權值＋息值』：5 年回測用還原價看不出除息日，判斷假突破要用（Cowork 0425）。
    證交所 TWT49U、櫃買 exDailyQ 都能一次查一段，半年一段，5 年約 10 段、幾分鐘。"""
    s = s or _session()
    today = today or dt.date.today()
    out, a = [], today - dt.timedelta(days=int(years * 365.25))
    while a <= today:
        b = min(today, a + dt.timedelta(days=180))
        try:
            out.append(fetch_done(s, a, b))
        except Exception as e:  # noqa: BLE001
            log.warning("除權息 %s~%s 失敗：%s", a, b, e)
        a = b + dt.timedelta(days=1)
        time.sleep(3)
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLS)
    if EXDIV_LONG.exists():
        df = pd.concat([pd.read_csv(EXDIV_LONG, dtype={"code": str}), df], ignore_index=True)
    df = df.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    df.reindex(columns=["date", "code", "market", "kind", "prev_close", "ref_price", "value"]).to_csv(
        EXDIV_LONG, index=False, compression="gzip")
    log.info("除權息長歷史：%d 筆（%s～%s）", len(df), df.date.min() if len(df) else "-", df.date.max() if len(df) else "-")
    return len(df)


def update(today: dt.date, s=None) -> dict:
    """第一次補兩年，之後每天重抓最近 30 天（避免漏掉晚公布的）＋即將除權息清單，順便更新 0050 含息指數。"""
    s = s or _session()
    old = pd.read_csv(EXDIV, dtype={"code": str}) if EXDIV.exists() else pd.DataFrame(columns=COLS)
    # 舊檔還沒有拆現金／配股欄位（或沒有舊檔）就整段重抓
    full = not len(old) or "cash_div" not in old.columns
    start = today - dt.timedelta(days=KEEP_DAYS if full else 30)
    new = []
    # 證交所一次查太長會被擋，分段（每段約半年）
    a = start
    while a <= today:
        b = min(today, a + dt.timedelta(days=180))
        try:
            new.append(fetch_done(s, a, b))
        except Exception as e:  # noqa: BLE001
            log.warning("除權息 %s~%s 失敗：%s", a, b, e)
        a = b + dt.timedelta(days=1)
        time.sleep(2)
    df = pd.concat([old, *new], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
    df = df[df.date >= (today - dt.timedelta(days=KEEP_DAYS)).isoformat()].sort_values(["date", "code"]).reset_index(drop=True)
    got_detail = fill_details(s, df, old)
    exact_factor(df)
    DIR.mkdir(parents=True, exist_ok=True)
    df.reindex(columns=COLS).to_csv(EXDIV, index=False)
    got = {"exdiv": len(df), "exdiv_new": int(sum(len(n) for n in new)), "exdiv_detail": got_detail,
           "exdiv_unsplit": int(df.cash_div.isna().sum())}
    try:
        up = fetch_upcoming(s)
        if len(up):
            up.to_csv(UPCOMING, index=False)
        got["exdiv_upcoming"] = len(up)
    except Exception as e:  # noqa: BLE001
        log.warning("即將除權息清單失敗：%s", e)
    try:
        got["bench_tr"] = bench_tr()
    except Exception as e:  # noqa: BLE001
        log.warning("0050 含息指數失敗：%s", e)
    log.info("除權息：%s", got)
    return got
