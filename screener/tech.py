"""技術面：Minervini 趨勢樣板、大盤寬度歷史、K 線上的訊號點。

趨勢樣板（tpl）：收盤 > MA50 > MA150 > MA200、MA200 比 20 個交易日前高、
收盤 ≥ 52 週最低價 × 1.3、收盤 ≥ 52 週最高價 × 0.75（52 週 = 250 個交易日，用每日最高／最低價）。
需要至少 220 個交易日（MA200 加上 20 日前的 MA200），資料不夠的股票為 None。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

WEEKS52 = 250
TPL_MIN_DAYS = 220
LIQUID_LOTS = 100  # 寬度只算 20 日均量 ≥ 100 張的股票


# ------------------------------------------------------------------ 趨勢樣板
def trend_template(p) -> pd.Series:
    """收盤後：每檔最新一天是否符合趨勢樣板（True / False / NaN = 資料不足）。"""
    c = p.close
    ma50, ma150, ma200 = p.ma(50), p.ma(150), p.ma(200)
    hi = p.high.iloc[-WEEKS52:].max()
    lo = p.low.iloc[-WEEKS52:].min()
    last = c.iloc[-1]
    ok = ((last > ma50.iloc[-1]) & (ma50.iloc[-1] > ma150.iloc[-1]) & (ma150.iloc[-1] > ma200.iloc[-1])
          & (ma200.iloc[-1] > ma200.iloc[-21]) & (last >= lo * 1.3) & (last >= hi * 0.75)) if len(c) > 21 else None
    if ok is None:
        return pd.Series(np.nan, index=c.columns, dtype=object)
    valid = ma200.iloc[-21].notna() & p.traded.iloc[-1]
    return ok.astype(object).where(valid, np.nan)


def tpl_ref(p) -> pd.DataFrame:
    """盤中用：昨天為止的資料先算好，盤中只要帶入現價就能判斷趨勢樣板。

    今天的 MA_n = (前 n-1 天收盤合計 + 現價) / n；今天的 20 日前 MA200 = 昨天資料往前 20~219 天的平均。
    """
    c = p.close
    n = len(c)
    out = pd.DataFrame(index=c.columns)
    for k in (50, 150, 200):
        out[f"s{k}"] = c.iloc[-(k - 1):].sum(min_count=k - 1) if n >= k - 1 else np.nan
    if n >= 219:
        w = c.iloc[-219:-19]
        out["ma200_20"] = w.mean().where(w.notna().sum() >= 200)
    else:
        out["ma200_20"] = np.nan
    out["hi"] = p.high.iloc[-(WEEKS52 - 1):].max()
    out["lo"] = p.low.iloc[-(WEEKS52 - 1):].min()
    return out


def tpl_live(price, high, low, ref: pd.DataFrame) -> pd.Series:
    """盤中：price/high/low 為以代號為 index 的 Series。"""
    r = ref.reindex(price.index)
    ma = {k: (r[f"s{k}"] + price) / k for k in (50, 150, 200)}
    hi = np.fmax(r.hi, high.reindex(price.index))
    lo = np.fmin(r.lo, low.reindex(price.index))
    ok = ((price > ma[50]) & (ma[50] > ma[150]) & (ma[150] > ma[200]) & (ma[200] > r.ma200_20)
          & (price >= lo * 1.3) & (price >= hi * 0.75))
    valid = r.ma200_20.notna() & price.notna()
    return ok.astype(object).where(valid, np.nan)


def high52_dist(p) -> pd.Series:
    """收盤離 52 週最高價的距離（%，負數 = 低於高點）。"""
    return (p.close.iloc[-1] / p.high.iloc[-WEEKS52:].max() - 1) * 100


# ------------------------------------------------------------------ 大盤寬度
BREADTH_COLS = ["date", "above_ma20", "up_pct", "limit_up", "limit_down", "ew_ret", "mkt20", "n_liquid", "n_traded"]


def breadth(p) -> pd.DataFrame:
    """每天的大盤寬度（口徑）：
    - above_ma20：20 日均量（含當日）≥ 100 張的股票中，收盤 > 20 日均線的比例（%）
    - up_pct：當天有成交、前一天也有成交的全部股票中，收盤上漲的比例（%）
    - limit_up / limit_down：同上母體，收在漲停／跌停價的家數（官方檔位，rules.limit_hits）
    - ew_ret：同上母體的等權平均日報酬（%），個股日報酬先截在 ±11%
    - mkt20：最近 20 天 ew_ret 連乘的累積報酬（%）
    """
    c, v = p.close, p.volume
    traded = p.traded & p.traded.shift(1, fill_value=False)
    chg = (c / c.shift(1) - 1).where(traded)
    liquid = p.traded & (v.rolling(20, min_periods=20).mean() >= LIQUID_LOTS * 1000)
    ma20 = p.ma(20)
    nl = liquid.sum(axis=1)
    nt = traded.sum(axis=1)
    ew = chg.clip(-0.11, 0.11).mean(axis=1)
    from .rules import limit_hits
    lu, ld = limit_hits(c, p.traded)
    df = pd.DataFrame({
        "above_ma20": ((c > ma20) & liquid).sum(axis=1) / nl.replace(0, np.nan) * 100,
        "up_pct": (chg > 0).sum(axis=1) / nt.replace(0, np.nan) * 100,
        "limit_up": lu.sum(axis=1),
        "limit_down": ld.sum(axis=1),
        "ew_ret": ew * 100,
        "mkt20": ((1 + ew.fillna(0)).rolling(20, min_periods=20).apply(np.prod, raw=True) - 1) * 100,
        "n_liquid": nl, "n_traded": nt,
    })
    df = df[ma20.notna().any(axis=1) & (nl > 0)]
    df.index.name = "date"
    return df.reset_index().round({"above_ma20": 2, "up_pct": 2, "ew_ret": 3, "mkt20": 2})[BREADTH_COLS]


def save_breadth(path: Path, p) -> pd.DataFrame:
    """重算目前歷史範圍內的寬度，和舊檔合併（舊檔中更早的日子保留）。"""
    new = breadth(p)
    if path.exists():
        old = pd.read_csv(path)
        new = pd.concat([old[~old.date.isin(new.date)], new], ignore_index=True)
    new = new.dropna(subset=["mkt20"]).sort_values("date")
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False)
    return new


def live_mkt20(breadth_df: pd.DataFrame, q: pd.DataFrame, today: str) -> float | None:
    """盤中的近 20 日等權報酬：今天以前 19 天收盤的 ew_ret ＋ 今天盤中的等權報酬。"""
    if breadth_df is None:
        return None
    breadth_df = breadth_df[breadth_df.date < today]
    if len(breadth_df) < 19:
        return None
    d = q[(q.price > 0) & (q.yclose > 0)]
    if d.empty:
        return None
    today = float((d.price / d.yclose - 1).clip(-0.11, 0.11).mean())
    prev = breadth_df.sort_values("date").ew_ret.iloc[-19:] / 100
    return round((float(np.prod(1 + prev.fillna(0))) * (1 + today) - 1) * 100, 2)


# ------------------------------------------------------------------ K 線訊號點
def surge_frame(p) -> pd.DataFrame:
    """爆量突破 60 日新高（與「爆量突破新高（收盤確認）」策略同條件）：
    量 ≥ 前 5 日均量 3 倍、漲 ≥ 3%、紅K、收盤 > 前 60 日最高價。"""
    c = p.close
    return ((p.volume >= 3 * p.vol_avg(5)) & (p.change_pct >= 3) & ((c - p.open) / p.prev_close > 0)
            & (c > p.high.shift(1).rolling(60, min_periods=60).max()) & p.traded).fillna(False)


def signals(p, codes: list[str], days: int, shrink: float = 0.5) -> dict[str, list[list]]:
    """▲ 爆量突破 60 日新高（surge_frame）。
    ▼ 之後第一次收盤量 < 爆量日 × shrink（漲停日的量縮不算）。回傳 {code: [[日期, "B"/"S"], ...]}。"""
    vol, c = p.volume, p.close
    surge = surge_frame(p)
    limit = p.traded & p.traded.shift(1, fill_value=False) & (c >= p.limit_price(True) - 1e-6) & (p.change_pct.abs() <= 10.5)
    idx = c.index
    start = max(0, len(idx) - days)
    out = {}
    for code in codes:
        if code not in c.columns:
            continue
        s, lu, v, tr = surge[code].values, limit[code].values, vol[code].values, p.traded[code].values
        marks, hold = [], None
        for i in range(len(idx)):
            if hold is not None and i > hold[0] and tr[i] and v[i] < hold[1] * shrink and not lu[i]:
                if i >= start:
                    marks.append([str(idx[i]), "S"])
                hold = None
            if s[i] and hold is None:
                hold = (i, v[i])
                if i >= start:
                    marks.append([str(idx[i]), "B"])
        if marks:
            out[code] = marks
    return out
