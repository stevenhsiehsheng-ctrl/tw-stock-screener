"""長期投資候選池：量化篩選（Dennis 10/7 要「最看好長期投資 Top 20」，這支只負責第一關）。

不是回測過的策略：分數是把幾個長期常用的體質指標排名後加權，權重是判斷、不是最佳化出來的。
最後 20 檔還要加外部觀點（券商／外資／投信）跟 Cowork 的質化審查，見 data/longterm/。

資料：
- data/extras/rev_5y.csv.gz：月營收（2020-12 起）→ 4 年營收 CAGR（TTM）、近 36 個月年增為正比例、最新 TTM 年增
- data/extras/pe.csv：本益比、股價淨值比、殖利率 → ROE ≈ PB ÷ PE
- data/extras/shares.csv × data/history.csv.gz 最新收盤 → 市值；history 前 60 日均成交值 → 流動性
- 5 年還原價（backtest-data 的 bt.csv.gz，含下市）→ 5 年股價年化、最大回檔
用法：python tools/longterm/screen.py <bt.csv.gz> <輸出 csv>
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MIN_CAP = 300e8      # 市值 ≥ 300 億
MIN_TV = 1e8         # 前 60 日均成交值 ≥ 1 億
# 第一版把「最新一年營收年增」放進分數，結果記憶體、券商排前面——景氣循環高點的營收暴增＋本益比看起來很低，
# 長期投資最怕的就是這種。改成：拿掉近一年成長，加「營收韌性」＝這段期間 12 個月營收從高點最多掉幾 %。
W = {"g4": 0.25, "cons": 0.15, "res": 0.15, "roe": 0.20, "val": 0.10, "dd": 0.10, "yld": 0.05}


def main(bt_path: str, out: str) -> None:
    stocks = pd.read_csv(ROOT / "data/stock_list.csv", dtype=str)
    stocks = stocks[stocks.code.str.fullmatch(r"[1-9]\d{3}")]
    h = pd.read_csv(ROOT / "data/history.csv.gz", dtype={"code": str})
    h = h[h.code.isin(stocks.code)].sort_values("date")
    last = h.groupby("code").tail(1).set_index("code")
    tv60 = h.assign(tv=h.close * h.volume).groupby("code").tv.apply(lambda s: s.tail(60).mean())
    sh = pd.read_csv(ROOT / "data/extras/shares.csv", dtype={"code": str}).set_index("code").shares
    pe = pd.read_csv(ROOT / "data/extras/pe.csv", dtype={"code": str}).set_index("code")

    rev = pd.read_csv(ROOT / "data/extras/rev_5y.csv.gz", dtype={"code": str})
    cnt = rev.groupby("ym").size()
    end = max(m for m, n in cnt.items() if n >= 0.9 * cnt.max())      # 最後一個「大家都公布了」的月份
    piv = rev.pivot(index="ym", columns="code", values="revenue").sort_index().loc[:end]
    yoy = rev.pivot(index="ym", columns="code", values="yoy").sort_index().loc[:end]
    ttm = piv.rolling(12, min_periods=12).sum()
    e = ttm.index.get_loc(end)
    g4 = ((ttm.iloc[e] / ttm.iloc[e - 48]) ** (1 / 4) - 1) * 100
    g1 = (ttm.iloc[e] / ttm.iloc[e - 12] - 1) * 100
    cons = (yoy.iloc[-36:] > 0).sum() / yoy.iloc[-36:].notna().sum()
    res = (ttm / ttm.cummax() - 1).min() * 100       # 12 個月營收從高點最大跌幅（越接近 0 越不循環）

    bt = pd.read_csv(bt_path, dtype={"code": str})
    C = bt[bt.code.isin(stocks.code)].pivot(index="date", columns="code", values="close").sort_index()
    yrs = (pd.Timestamp(C.index[-1]) - pd.Timestamp(C.index[0])).days / 365.25
    first = C.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
    firstd = C.apply(lambda s: s.dropna().index[0] if s.notna().any() else None)
    cagr5 = ((C.iloc[-1] / first) ** (1 / yrs) - 1) * 100
    mdd = (C / C.cummax() - 1).min() * 100

    df = stocks.set_index("code")[["name", "market", "industry"]].copy()
    df["close"] = last.close
    df["cap_e8"] = (sh * last.close / 1e8).round(0)
    df["tv60_e8"] = (tv60 / 1e8).round(2)
    df["rev_g4"] = g4.round(1)
    df["rev_g1"] = g1.round(1)
    df["rev_cons36"] = cons.round(2)
    df["rev_ttm_dd"] = res.round(1)
    df["pe"] = pe.pe
    df["pb"] = pe.pb
    df["yield"] = pe["yield"]
    df["roe"] = (pe.pb / pe.pe * 100).round(1)
    df["peg"] = (pe.pe / g4.where(g4 > 0)).round(2)
    df["px_cagr5"] = cagr5.round(1)
    df["px_mdd5"] = mdd.round(1)
    df["px_since"] = firstd
    df["full5y"] = df.px_since.astype(str) <= "2021-10-31"
    df["rev_end"] = end

    u = df[(df.cap_e8 >= MIN_CAP / 1e8) & (df.tv60_e8 >= MIN_TV / 1e8)].copy()
    rk = lambda s, asc=True: s.rank(pct=True, ascending=asc)
    val = np.where(u.peg.notna(), rk(-u.peg.fillna(1e9)), 0.0)   # PEG 越低越好；虧損或營收衰退給 0
    parts = {"g4": rk(u.rev_g4), "cons": rk(u.rev_cons36), "roe": rk(u.roe.where(u.pe.notna(), -99)),
             "val": pd.Series(val, index=u.index), "res": rk(u.rev_ttm_dd), "dd": rk(u.px_mdd5), "yld": rk(u["yield"].fillna(0))}
    for k, s in parts.items():
        u["s_" + k] = s.fillna(0).round(3)
    u["score"] = sum(W[k] * u["s_" + k] for k in W).round(3)
    u = u.sort_values("score", ascending=False)
    u["rank"] = range(1, len(u) + 1)
    u.reset_index().to_csv(out, index=False)
    print(f"營收截至 {end}；股價 {C.index[0]}～{C.index[-1]}（{yrs:.2f} 年）；全部 {len(df)} 檔 → 市值 ≥300 億且流動性夠 {len(u)} 檔")
    cols = ["rank", "name", "industry", "cap_e8", "rev_g4", "rev_g1", "rev_cons36", "rev_ttm_dd", "roe", "pe", "peg", "yield", "px_cagr5", "px_mdd5", "score"]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(u[cols].head(60).to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
