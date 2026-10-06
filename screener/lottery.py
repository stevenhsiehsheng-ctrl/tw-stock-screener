"""回測共用檢定：「彩券檢定」＋配對安慰劑＋按月整塊 bootstrap（協作板 10/6 定案的口徑）。

用法（每一筆交易一列，報酬單位是 %，已扣成本、已減基準）：

    from screener import lottery
    pl = lottery.placebo(close, trades, n=300)           # 同進同出、從當天有成交的股票隨機抽
    print(lottery.report(trades, placebo=pl))

trades 欄位：entry（進場日 YYYY-MM-DD）、exit（出場日）、ret（超額報酬 %）。
close 是寬表（index=日期字串、columns=代號），例如 history 的收盤價 pivot。

判定（Cowork 0726／分身 0815）：
  中位 − 安慰劑中位 ≤ 0，或 剔前5%平均 − 安慰劑剔前5%平均 ≤ 0 → 彩券組。
  沒給安慰劑就跟 0 比，並在結果註明「沒有安慰劑，偏向看衰」。
區間：平均超額的 5～95% 用「按月整塊」bootstrap（按日抽幾乎沒效果）。M < 12 標「未滿一年」。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST = 0.38   # 來回費稅 %（手續費 2.8 折＋證交稅 0.3%）


def _trim_top(x: np.ndarray, q: float = 0.05) -> float:
    """剔掉最高 q 比例後的平均（只用來檢驗正報酬是不是靠少數暴衝）。"""
    x = np.sort(x)
    k = int(np.floor(len(x) * q))
    return float(x[: len(x) - k].mean()) if len(x) - k > 0 else float("nan")


def _trim_both(x: np.ndarray, q: float = 0.05) -> float:
    """兩邊各剔 q（判斷負訊號用；單邊剔除一定往下偏）。"""
    x = np.sort(x)
    k = int(np.floor(len(x) * q))
    return float(x[k: len(x) - k].mean()) if len(x) - 2 * k > 0 else float("nan")


def _concurrency(entry: pd.Series, exit: pd.Series) -> tuple[float, float, int]:
    """同時持有筆數的中位、90 分位、最大（以進出場日之間的工作日計）。"""
    if exit is None or exit.isna().all():
        return (float("nan"),) * 2 + (0,)
    days = pd.bdate_range(entry.min(), exit.max())
    cnt = np.zeros(len(days), dtype=int)
    a = days.searchsorted(pd.to_datetime(entry))
    b = days.searchsorted(pd.to_datetime(exit))
    for i, j in zip(a, b):
        cnt[i:j] += 1   # 出場當天不算持有
    cnt = cnt[cnt > 0]
    if not len(cnt):
        return (float("nan"),) * 2 + (0,)
    return float(np.median(cnt)), float(np.percentile(cnt, 90)), int(cnt.max())


def block_boot(df: pd.DataFrame, ret: str = "ret", key: str = "ym", n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """平均報酬的 5～95% 區間，按 key（預設月份）整塊重抽。"""
    g = [v[ret].to_numpy(float) for _, v in df.groupby(key)]
    if len(g) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    sums = np.array([x.sum() for x in g])
    lens = np.array([len(x) for x in g])
    idx = rng.integers(0, len(g), size=(n, len(g)))
    means = sums[idx].sum(axis=1) / lens[idx].sum(axis=1)
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def placebo(close: pd.DataFrame, trades: pd.DataFrame, n: int = 300, cost: float = COST, seed: int = 0) -> pd.DataFrame:
    """配對安慰劑：每筆交易同一個進場日、出場日，從當天有成交的股票隨機抽一檔，
    報酬一樣減同段期間全市場等權、扣成本。回傳每次模擬的 median／trim_top／trim_both。"""
    close = close.sort_index()
    rng = np.random.default_rng(seed)
    pairs = trades[["entry", "exit"]].astype(str).to_numpy()
    pool = {}
    for e, x in {tuple(p) for p in pairs}:
        if e not in close.index or x not in close.index:
            continue
        r = (close.loc[x] / close.loc[e] - 1).dropna()
        r = r[r.abs() < 3]   # 沒還原的分割、減資會有離譜值
        if len(r):
            pool[(e, x)] = ((r - r.mean()) * 100 - cost).to_numpy()
    keys = [tuple(p) for p in pairs if tuple(p) in pool]
    out = []
    for _ in range(n):
        v = np.array([pool[k][rng.integers(len(pool[k]))] for k in keys])
        out.append({"median": float(np.median(v)), "trim_top": _trim_top(v), "trim_both": _trim_both(v)})
    return pd.DataFrame(out)


def lottery_check(trades: pd.DataFrame, ret: str = "ret", placebo: pd.DataFrame | None = None, boot: int = 2000) -> dict:
    """彩券檢定：N／D／M、平均、中位、剔尾平均、正報酬月份、按月 bootstrap 區間、同時持有、判定。"""
    t = trades.dropna(subset=[ret]).copy()
    t["entry"] = t["entry"].astype(str)
    t["ym"] = t["entry"].str[:7]
    x = t[ret].to_numpy(float)
    by_m = t.groupby("ym")[ret].mean()
    total = x.sum()
    top = np.sort(x)[::-1][: max(1, int(np.floor(len(x) * 0.05)))]
    exit_ = t["exit"].astype(str) if "exit" in t else None
    cm, c90, cmax = _concurrency(t["entry"], exit_) if exit_ is not None else (float("nan"),) * 2 + (0,)
    lo, hi = block_boot(t, ret, "ym", boot)
    r = {
        "N": int(len(x)), "D": int(t["entry"].nunique()), "M": int(len(by_m)),
        "mean": float(x.mean()), "median": float(np.median(x)), "win": float((x > 0).mean()),
        "trim_top5": _trim_top(x), "trim_both5": _trim_both(x),
        "top5_share": float(top.sum() / total) if abs(total) > 1e-9 else float("nan"),   # 只當參考
        "monthly_mean": float(by_m.mean()), "pos_months": int((by_m > 0).sum()),
        "ci90_month_block": (lo, hi),
        "hold_median": cm, "hold_p90": c90, "hold_max": cmax,
    }
    notes = []
    if r["M"] < 12:
        notes.append("未滿一年")
    if placebo is not None and len(placebo):
        pm, pt = float(placebo["median"].median()), float(placebo["trim_top"].median())
        r.update({"placebo_median": pm, "placebo_trim_top5": pt})
        lottery = (r["median"] - pm <= 0) or (r["trim_top5"] - pt <= 0)
    else:
        notes.append("沒有安慰劑，跟 0 比（偏向看衰）")
        lottery = r["median"] <= 0 or r["trim_top5"] <= 0
    r["lottery"] = bool(lottery)
    r["notes"] = notes
    return r


def report(trades: pd.DataFrame, ret: str = "ret", placebo: pd.DataFrame | None = None) -> str:
    """把 lottery_check 印成一段可以直接貼到協作板的文字。"""
    r = lottery_check(trades, ret, placebo)
    f = lambda v: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.2f}%"
    s = [f"N={r['N']}／D={r['D']}／M={r['M']}" + (f"（{'、'.join(r['notes'])}）" if r["notes"] else ""),
         f"平均 {f(r['mean'])}、中位 {f(r['median'])}、勝率 {r['win']:.0%}",
         f"剔前5% {f(r['trim_top5'])}、兩邊各剔5% {f(r['trim_both5'])}（前5%貢獻 {r['top5_share']:.0%}，只當參考）",
         f"按月平均 {f(r['monthly_mean'])}、正報酬月 {r['pos_months']}/{r['M']}、平均的 5～95%（按月整塊）{f(r['ci90_month_block'][0])}～{f(r['ci90_month_block'][1])}",
         f"同時持有 中位 {r['hold_median']:.0f}、90 分位 {r['hold_p90']:.0f}、最大 {r['hold_max']}"]
    if "placebo_median" in r:
        s.append(f"配對安慰劑 中位 {f(r['placebo_median'])}、剔前5% {f(r['placebo_trim_top5'])}")
    s.append("判定：" + ("彩券組" if r["lottery"] else "非彩券（可以談衛星）"))
    return "\n".join(s)
