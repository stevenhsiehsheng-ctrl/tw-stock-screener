"""估值位階：現在的本益比／股價淨值比／殖利率，在這檔自己過去 5 年（每月第一個交易日快照）的第幾百分位。

資料：data/extras/pe_5y.csv.gz（2021-09 起每月一列，backtest-pe 分支回補，之後每日 enrich.refresh 遇到新月份補一列）
＋ data/extras/pe.csv（最新一天）。本益比 ≤0 或空白（虧損）不算；至少要 24 個月才給百分位。
百分位＝歷史月份裡比現在低的比例（同值算一半）：本益比 10％＝比過去 9 成時間都便宜；殖利率 90％＝比過去 9 成時間都高。
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "data" / "extras"
HIST = EX / "pe_5y.csv.gz"
MIN_MONTHS = 24
YEARS = 5
KEYS = ("pe", "pb", "yield")
log = logging.getLogger(__name__)


def load() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    if not HIST.exists() or not (EX / "pe.csv").exists():
        return None
    h = pd.read_csv(HIST, dtype={"code": str})
    now = pd.read_csv(EX / "pe.csv", dtype={"code": str}).drop_duplicates("code").set_index("code")
    h["ym"] = h.date.str[:7]
    cut = f"{int(h.ym.max()[:4]) - YEARS}{h.ym.max()[4:]}"
    h = h[h.ym > cut].drop_duplicates(["code", "ym"], keep="last")
    return h, now


def _ok(v: pd.Series, key: str) -> pd.Series:
    v = pd.to_numeric(v, errors="coerce")
    return v[v > 0] if key in ("pe", "pb") else v[v >= 0]


def stats(g: pd.DataFrame | None, now: pd.DataFrame, code: str, series: bool = False) -> dict:
    """g＝這檔的月快照（load() 的 h 按 code 分組）。回傳 {pe: {now, pct, lo, p25, med, p75, hi, n, from, s}, ...}；沒資料的鍵不放。"""
    if g is None or g.empty or code not in now.index:
        return {}
    g = g.sort_values("ym")
    out = {}
    for k in KEYS:
        cur = now[k].get(code) if k in now.columns else None
        try:
            cur = float(cur)
        except (TypeError, ValueError):
            cur = np.nan
        v = _ok(g.set_index("ym")[k], k) if k in g.columns else pd.Series(dtype=float)
        if len(v) < MIN_MONTHS or not np.isfinite(cur) or (k in ("pe", "pb") and cur <= 0):
            continue
        q = np.quantile(v, [0, .25, .5, .75, 1])
        d = {"now": round(cur, 2), "pct": round(float(((v < cur).mean() + 0.5 * (v == cur).mean()) * 100), 0),
             "lo": round(q[0], 2), "p25": round(q[1], 2), "med": round(q[2], 2), "p75": round(q[3], 2), "hi": round(q[4], 2),
             "n": int(len(v)), "from": v.index[0]}
        if series:
            d["s"] = [[ym, round(float(x), 2)] for ym, x in v.items()]
        out[k] = d
    return out


def table() -> pd.DataFrame:
    """每檔一列：pe_pct、pb_pct、y_pct（給自訂選股）。"""
    x = load()
    if x is None:
        return pd.DataFrame(columns=["pe_pct", "pb_pct", "y_pct"])
    h, now = x
    rows = {}
    for code, g in h.groupby("code"):
        if code not in now.index:
            continue
        r = {}
        for k, col in zip(KEYS, ("pe_pct", "pb_pct", "y_pct")):
            v = _ok(g[k], k)
            try:
                cur = float(now.at[code, k])
            except (TypeError, ValueError, KeyError):
                continue
            if len(v) < MIN_MONTHS or not np.isfinite(cur) or (k in ("pe", "pb") and cur <= 0):
                continue
            r[col] = round(float(((v < cur).mean() + 0.5 * (v == cur).mean()) * 100), 0)
        if r:
            rows[code] = r
    return pd.DataFrame.from_dict(rows, orient="index")


def append_month(today: pd.DataFrame, d) -> int:
    """每日 refresh 抓到的本益比，這個月還沒有的話補一列（當月第一次抓到的那天當快照）。回傳月份數。"""
    if today is None or today.empty or not HIST.exists():
        return 0
    h = pd.read_csv(HIST, dtype={"code": str})
    ym = d.strftime("%Y-%m")
    if (h.date.str[:7] == ym).any():
        return h.date.str[:7].nunique()
    mk = pd.read_csv(ROOT / "data" / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code").market
    add = today.reindex(columns=["code", "pe", "pb", "yield"]).copy()
    add.insert(0, "date", d.isoformat())
    add.insert(2, "market", add.code.map(mk))
    h = pd.concat([h, add[h.columns]], ignore_index=True)
    keep = sorted(h.date.str[:7].unique())[-(12 * (YEARS + 1)):]
    h = h[h.date.str[:7].isin(keep)]
    h.to_csv(HIST, index=False, compression="gzip")
    log.info("估值月檔補 %s：%d 檔", ym, len(add))
    return len(keep)
