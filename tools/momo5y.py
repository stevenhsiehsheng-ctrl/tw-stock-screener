"""強勢續攻（聯一光型）5 年統計 → data/extras/momo_5y.json（site/momo.html 讀）。

用法：python tools/momo5y.py <backtest.csv.gz>   （backtest-data 分支）
規則定義在 screener/momo.py（網站名單跟這裡用同一支）。
口徑：同一檔 60 天內只算第一次；隔天開盤買、第 60 個交易日收盤算、扣 0.585%；曾翻倍＝60 天內最高收盤 ≥ 2 倍進場價。
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
            r = fut.iat[-1] / ent - 1
            if not np.isfinite(r):
                continue
            rows.append({"y": dates[i][:4], "r": r * 100 - COST, "mx": fut.max() / ent - 1,
                         "ex": (r - (ewi.iat[i + H] / ewi.iat[i] - 1)) * 100 - COST})
        R = pd.DataFrame(rows)
        out[rule] = {"name": momo.RULES[rule], "n": len(R), "start": dates[0], "end": dates[-1 - H],
                     "dbl": round(float((R.mx >= 1).mean() * 100), 1), "half": round(float((R.mx >= 0.5).mean() * 100), 1),
                     "mean": round(float(R.r.mean()), 1), "med": round(float(R.r.median()), 1), "win": round(float((R.r > 0).mean() * 100), 1),
                     "ex_mean": round(float(R.ex.mean()), 1), "ex_med": round(float(R.ex.median()), 1),
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
