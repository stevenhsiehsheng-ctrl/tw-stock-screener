"""強勢續攻（聯一光型）5 年統計 → data/extras/momo_5y.json（site/momo.html 讀）。

用法：python tools/momo5y.py <backtest.csv.gz>   （backtest-data 分支）
規則定義在 screener/momo.py（網站名單跟這裡用同一支）。
口徑：同一檔 60 天內只算第一次；隔天開盤買、第 60 個交易日收盤算、扣 0.585%；曾翻倍＝60 天內最高收盤 ≥ 2 倍進場價。
60 天內下市／停牌沒有收盤的，用窗內最後一個收盤算（10/14 以前是整筆跳過，會漏掉下市的爛股、數字偏樂觀）。

出場方式比較（EXITS，10/14 先定好再跑，全部列出、不挑最好的那個）：
抱 60 天、抱 20 天、從最高收盤回落 15% 停利損、跌破進場價 10% 停損、收盤跌破 20 日均線、漲 30% 停利或跌 10% 停損；
觸發當天收盤看到、隔天開盤賣（不用當天收盤，實際做不到）；沒觸發就第 60 天收盤賣。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screener import momo  # noqa: E402

H, GAP, COST = 60, 60, 0.585
EXITS = {"h60": "抱 60 天", "h20": "抱 20 天", "trail15": "最高收盤回落 15% 賣", "stop10": "跌破進場價 10% 賣",
         "ma20": "收盤跌破 20 日均線賣", "tp30sl10": "漲 30% 或跌 10% 賣"}


def exit_trade(cs: np.ndarray, os_: np.ndarray, ma: np.ndarray, ent: float, rule: str) -> tuple[float, int]:
    """cs／os_／ma：進場日起 H 天（含）之後再多 1 天的收盤、開盤、20 日均線。回傳 (報酬, 持有天數)。
    觸發看收盤、隔天開盤賣；隔天沒開盤價（停牌、下市）就用觸發當天收盤。沒觸發就第 H 天（或最後一個有收盤的日子）收盤賣。"""
    n = min(H, len(cs))
    last = np.flatnonzero(np.isfinite(cs[:n]))
    if not len(last):
        return np.nan, 0
    hi = ent
    for k in range(n):
        x = cs[k]
        if not np.isfinite(x):
            continue
        hi = max(hi, x)
        hit = ((rule == "h20" and k == 19) or (rule == "trail15" and x <= hi * 0.85) or (rule == "stop10" and x <= ent * 0.90)
               or (rule == "ma20" and np.isfinite(ma[k]) and x < ma[k]) or (rule == "tp30sl10" and (x >= ent * 1.3 or x <= ent * 0.9)))
        if hit:
            if rule == "h20":
                return x / ent - 1, k + 1
            nxt = os_[k + 1] if k + 1 < len(os_) else np.nan
            return (nxt if np.isfinite(nxt) and nxt > 0 else x) / ent - 1, k + 2
    j = last[-1]
    return cs[j] / ent - 1, j + 1


def first_only(e: pd.DataFrame) -> list[tuple[int, str]]:
    out = []
    for code in e.columns:
        last = -10**9
        for i in np.flatnonzero(e[code].to_numpy()):
            if i - last >= GAP:
                out.append((i, code))
                last = i
    return out


def main(path: str) -> dict:
    bt = pd.read_csv(path, dtype={"code": str})
    P = momo.panels(bt)
    o, c, v = P["open"], P["close"], P["volume"]
    dates = list(c.index)
    chg = c / c.shift(1) - 1
    ma20 = c.rolling(20, min_periods=15).mean()
    ewi = (1 + chg.where(chg.abs() < 0.11).mean(axis=1).fillna(0)).cumprod()
    out = {"source": "backtest-data 分支 backtest.csv.gz（5 年 Yahoo 日 K，含下市股）", "hold": H, "cost": COST}
    for rule, e in momo.events(P).items():
        rows = []
        for i, code in first_only(e):
            if i + H >= len(dates):
                continue
            ent = o[code].iat[i + 1]
            if not ent > 0:
                continue
            fut = c[code].iloc[i + 1:i + 1 + H]
            if not fut.notna().any():
                continue
            r = fut.dropna().iat[-1] / ent - 1          # 下市／停牌：窗內最後一個收盤
            cs, os_ = c[code].to_numpy()[i + 1:i + 2 + H], o[code].to_numpy()[i + 1:i + 2 + H]
            ma = ma20[code].to_numpy()[i + 1:i + 2 + H]
            ex_r = {k: exit_trade(cs, os_, ma, ent, k) for k in EXITS}
            rows.append({"y": dates[i][:4], "r": r * 100 - COST, "mx": fut.max() / ent - 1,
                         "ex": (r - (ewi.iat[i + H] / ewi.iat[i] - 1)) * 100 - COST, "delist": bool(fut.isna().iat[-1]),
                         **{f"x_{k}": v[0] * 100 - COST for k, v in ex_r.items()}, **{f"d_{k}": v[1] for k, v in ex_r.items()}})
        R = pd.DataFrame(rows)
        exits = {}
        for k, name in EXITS.items():
            x = R[f"x_{k}"].dropna()
            yr = R.groupby("y")[f"x_{k}"].mean()
            exits[k] = {"name": name, "mean": round(float(x.mean()), 1), "med": round(float(x.median()), 1),
                        "win": round(float((x > 0).mean() * 100), 1), "big": round(float((x >= 50).mean() * 100), 1),
                        "worst": round(float(x.quantile(0.1)), 1), "days": round(float(R[f"d_{k}"].mean()), 1),
                        "years_neg": int((yr < 0).sum()), "years": int(len(yr))}
        out[rule] = {"name": momo.RULES[rule], "n": len(R), "start": dates[0], "end": dates[-1 - H],
                     "dbl": round(float((R.mx >= 1).mean() * 100), 1), "half": round(float((R.mx >= 0.5).mean() * 100), 1),
                     "mean": round(float(R.r.mean()), 1), "med": round(float(R.r.median()), 1), "win": round(float((R.r > 0).mean() * 100), 1),
                     "ex_mean": round(float(R.ex.mean()), 1), "ex_med": round(float(R.ex.median()), 1),
                     "n_delist": int(R.delist.sum()), "exits": exits,
                     "by_year": {y: {"n": len(g), "dbl": round(float((g.mx >= 1).mean() * 100), 1),
                                     "mean": round(float(g.r.mean()), 1), "med": round(float(g.r.median()), 1)}
                                 for y, g in R.groupby("y")}}
    # 對照：同樣流動性門檻，隨機股票、隨機日子
    turn = (c * v).shift(1).rolling(20, min_periods=15).mean() >= 3e7
    rng = np.random.default_rng(0)
    dbl, rr = [], []
    for i, j in zip(rng.integers(130, len(dates) - H - 1, 20000), rng.integers(0, c.shape[1], 20000)):
        ent = o.iat[i + 1, j]
        if not turn.iat[i, j] or not ent > 0:
            continue
        fut = c.iloc[i + 1:i + 1 + H, j]
        if not np.isfinite(fut.iat[-1]):   # 60 天內下市／停牌沒有收盤
            continue
        dbl.append(fut.max() / ent - 1 >= 1)
        rr.append((fut.iat[-1] / ent - 1) * 100 - COST)
    out["random"] = {"n": len(rr), "dbl": round(float(np.mean(dbl) * 100), 1),
                     "mean": round(float(np.mean(rr)), 1), "med": round(float(np.median(rr)), 1)}
    return out


if __name__ == "__main__":
    res = main(sys.argv[1])
    dst = Path(__file__).resolve().parent.parent / "data" / "extras" / "momo_5y.json"
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "by_year"} if isinstance(v, dict) else v
                      for k, v in res.items()}, ensure_ascii=False, indent=1))
