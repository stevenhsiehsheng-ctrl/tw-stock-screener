"""系統訊號『合計超額』的雜訊帶（分身 0445-cc-ab、Cowork 0455-cw-band-accept 定案）→ data/extras/sys_band.json。

問題：首頁／15:47 的系統訊號合計（持有中按收盤估）開帳才幾天，數字上下跳只是雜訊。
做法（tools/btsim）：6 年回測裡每個交易日當『假開帳日』s，到第 k 個交易日 d＝s+k−1 時，
把 s～d 之間進場的訊號全部平均——已出場的用實際出場報酬、還抱著的用 d 那天收盤估、d 當天的訊號算 −0.38——
每筆減同期 0050 含息（進場日收盤 → 出場訊號日／d 收盤），得到一個『合計超額』。所有 s 的分布取 5／50／95 分位。
口徑＝原則寫死的那版：前 20 日均成交額 ≥1,000 萬、剔 ≥100 元、去 corp_jump／censored、只扣 0.38（不扣零股滑價，對齊帳本）。

用法：python -m tools.ledger_band   → 印表、寫 data/extras/sys_band.json（首頁讀它畫帶、判『帶內不下結論』）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "extras" / "sys_band.json"
KS = (5, 10, 20, 40, 60)
COST = 0.38


def band(P: dict, T: pd.DataFrame, ks=KS) -> pd.DataFrame:
    dates = P["dates"]
    n = len(dates)
    di = {d: i for i, d in enumerate(dates)}
    ci = {c: i for i, c in enumerate(P["close"].columns)}
    CF = P["cf"].to_numpy()
    tr = P["ew"].tr_0050.reindex(dates).ffill().to_numpy()
    ei = np.array([di[d] for d in T.date])
    xi = np.array([di[d] for d in T.exit_date])          # 出場開盤那天（censored＝最後一筆收盤那天）
    cens = T.censored.to_numpy(bool)
    col = np.array([ci[c] for c in T.code])
    entry = T.entry.to_numpy(float)
    net = T.gross.to_numpy(float) - COST                  # 只扣 0.38
    order = np.argsort(ei)
    ei, xi, cens, col, entry, net = ei[order], xi[order], cens[order], col[order], entry[order], net[order]
    rows = []
    maxk = max(ks)
    for s in range(int(ei.min()) if len(ei) else 0, n - maxk + 1):
        lo = np.searchsorted(ei, s, "left")
        for k in ks:
            d = s + k - 1
            hi = np.searchsorted(ei, d, "right")
            if hi <= lo:
                rows.append({"s": dates[s], "k": k, "n": 0, "ex": np.nan})
                continue
            e_, x_, c_, j_, p_, r_ = ei[lo:hi], xi[lo:hi], cens[lo:hi], col[lo:hi], entry[lo:hi], net[lo:hi]
            done = (~c_) & (x_ <= d)
            val = np.where(done, r_, (CF[d, j_] / p_ - 1) * 100 - COST)
            end = np.where(done, x_ - 1, d)               # 已出場：基準算到出場訊號日收盤
            b = (tr[end] / tr[e_] - 1) * 100
            rows.append({"s": dates[s], "k": k, "n": int(hi - lo), "ex": float(np.mean(val - b))})
    return pd.DataFrame(rows)


def summarize(B: pd.DataFrame) -> dict:
    out = {}
    for k, g in B.dropna().groupby("k"):
        x = g.ex
        out[int(k)] = {"p5": round(float(np.percentile(x, 5)), 2), "p50": round(float(np.percentile(x, 50)), 2),
                       "p95": round(float(np.percentile(x, 95)), 2), "gt0": round(float((x > 0).mean() * 100), 1),
                       "n_med": int(g.n.median()), "starts": int(len(g))}
    return out


def main():
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.btsim import load, signals, trades, provenance
    params = dict(min_turnover20=1e7, slip=False, drop="entry>=100,corp_jump,censored", cost=COST)
    P = load()
    T = trades(P, signals(P, min_turnover20=1e7), slip=False)
    T = T[(T.entry < 100) & ~T.corp_jump & ~T.censored]
    B = band(P, T)
    S = summarize(B)
    prov = provenance(**params)
    print(prov)
    print(f"{'k':>3} {'起點':>6} {'N中位':>6} {'5%':>7} {'50%':>7} {'95%':>7} {'帶寬':>6} {'>0':>5}")
    for k, v in S.items():
        print(f"{k:>3} {v['starts']:>6} {v['n_med']:>6} {v['p5']:>+7.2f} {v['p50']:>+7.2f} {v['p95']:>+7.2f} "
              f"{v['p95'] - v['p5']:>6.2f} {v['gt0']:>4.0f}%")
    OUT.write_text(json.dumps({"calc": prov, "asof": P["dates"][-1], "band": S,
                               "note": "合計超額（持有中按收盤估、只扣 0.38、減同期 0050 含息）在同 k 個交易日的歷史 5～95% 帶；"
                                       "落在帶內只寫數字＋帶、不下結論；k<60 一律不下結論（Cowork 0455／0557）"},
                              ensure_ascii=False, indent=1), "utf-8")
    print("寫出", OUT)


if __name__ == "__main__":
    main()
