"""市場情緒儀表板：用全市場價量算出情緒，判斷對「爆量突破」策略是否有利。

分級依據 2021~2026 回測（以「站上月線比例」五等分）：
  <33% 極度悲觀：策略平均每筆跑贏大盤 +0.2%（最差）
  33~46 偏悲觀 +0.9%｜46~57 中性 +1.45%（最佳）｜57~69 偏樂觀 +0.9%｜>69 極度樂觀 +0.4%
另外：情緒極度悲觀時，大盤之後 20 天平均 +3%、上漲機率 76%（恐慌常是短線低點）。

2026/10 起「站上月線比例」與「大盤近 20 日」改用和 data/extras/breadth.csv 相同的口徑（見 tech.breadth）：
站上月線只算 20 日均量 ≥ 100 張的股票；近 20 日 = 全部有成交股票的等權日報酬（截 ±11%）連乘。
Cowork 用 5 年資料比對過：新舊站上月線相關 0.999（新的平均低 1.5 個百分點），分級門檻照用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LEVELS = [
    (33, "極度悲觀", "🥶", "大盤恐慌。歷史上之後 20 天反彈機率高（76%），但個股爆量突破容易被拖累，策略效果最差，建議減碼。"),
    (46, "偏悲觀", "😟", "市場偏弱，爆量突破策略效果尚可，挑最強的做。"),
    (57, "中性", "😐", "情緒中性，歷史上是爆量突破策略表現最好的環境。"),
    (69, "偏樂觀", "🙂", "市場偏熱，策略效果尚可。"),
    (101, "極度樂觀", "🔥", "市場過熱、到處爆量，追價的人多，策略效果明顯下降，注意追高風險。"),
]


def classify(pct_above_ma20: float) -> dict:
    for th, name, icon, advice in LEVELS:
        if pct_above_ma20 < th:
            return {"level": name, "icon": icon, "advice": advice}
    return {"level": "?", "icon": "", "advice": ""}


def from_history(hist: pd.DataFrame) -> dict:
    """收盤後：用歷史資料算今天（最後一天）的情緒，並附上 5 天前做比較。"""
    h = hist.dropna(subset=["close"])
    C = h.pivot(index="date", columns="code", values="close").sort_index()
    H = h.pivot(index="date", columns="code", values="high").reindex_like(C)
    L = h.pivot(index="date", columns="code", values="low").reindex_like(C)
    V = h.pivot(index="date", columns="code", values="volume").reindex_like(C).fillna(0)
    traded = C.notna() & C.shift(1).notna()
    univ = traded & (V / 1000 >= 100)
    n = univ.sum(axis=1).replace(0, np.nan)
    chg = C / C.shift(1) - 1
    ma60 = C.rolling(60).mean()
    nh = C > H.shift(1).rolling(60, min_periods=60).max()
    nl = C < L.shift(1).rolling(60, min_periods=60).min()
    from .rules import limit_hits
    lu, ld = limit_hits(C, C.notna())
    s = pd.DataFrame({
        "up_pct": ((chg > 0) & univ).sum(axis=1) / n * 100,
        "above_ma60": ((C > ma60) & univ).sum(axis=1) / n * 100,
        "new_high": (nh & univ).sum(axis=1),
        "new_low": (nl & univ).sum(axis=1),
        "limit_up": (lu & univ).sum(axis=1),
        "limit_down": (ld & univ).sum(axis=1),
        "surge_pct": ((V >= 3 * V.shift(1).rolling(5).mean()) & univ).sum(axis=1) / n * 100,
    })
    # 站上月線、近 20 日：和 data/extras/breadth.csv 完全同一套算法
    from . import rules, tech
    b = tech.breadth(rules.Panel(hist)).set_index("date")
    s["above_ma20"] = b.above_ma20.reindex(s.index)
    s["mkt20"] = b.mkt20.reindex(s.index)
    s = s.dropna(subset=["above_ma20"])
    last = s.iloc[-1].to_dict()
    prev = s.iloc[-6].to_dict() if len(s) > 6 else {}
    out = {"date": s.index[-1], **{k: round(float(v), 1) for k, v in last.items()},
           "above_ma20_5d_ago": round(float(prev.get("above_ma20", np.nan)), 1) if prev else None,
           "history": s["above_ma20"].iloc[-60:].round(1).tolist()}
    out.update(classify(out["above_ma20"]))
    out["weak_market"] = out["mkt20"] < -3
    return out


def from_quotes(q: pd.DataFrame, hist: pd.DataFrame) -> dict:
    """盤中：用即時報價估算。q 需要 code, price, yclose, vol_lots。
    站上月線的母體 = 到昨天為止 20 日均量 ≥ 100 張的股票（盤中量還沒走完，用昨天的 20 日均量判斷）。"""
    h = hist.sort_values("date")
    g = h.groupby("code")
    last19 = g.close.apply(lambda x: x.iloc[-19:].sum() if len(x) >= 19 else np.nan)
    avg20 = g.volume.apply(lambda x: x.iloc[-20:].mean() if len(x) >= 20 else np.nan)
    d = q.set_index("code").join(last19.rename("sum19")).join(avg20.rename("avg20"))
    d = d[(d.price > 0) & (d.yclose > 0)]
    liq = d[d.avg20 / 1000 >= 100]
    ma20 = (liq.sum19 + liq.price) / 20
    above = float((liq.price > ma20).mean() * 100) if len(liq) else float("nan")
    d = d[d.vol_lots >= 100]
    # 漲跌停價用官方檔位（跟收盤後 breadth／rules.limit_hits 同口徑），不用 ±9.5% 近似
    from .rules import limit_price
    yc = d[["yclose"]].astype(float)
    lup, ldn = limit_price(yc, True)["yclose"], limit_price(yc, False)["yclose"]
    out = {"up_pct": round(float((d.price > d.yclose).mean() * 100), 1),
           "above_ma20": round(above, 1),
           "limit_up": int((d.price >= lup - 1e-6).sum()),
           "limit_down": int((d.price <= ldn + 1e-6).sum())}
    out.update(classify(above))
    return out


def md_line(s: dict, intraday: bool = False) -> str:
    t = "盤中" if intraday else "收盤"
    parts = [f"**市場情緒（{t}）：{s['icon']} {s['level']}**",
             f"站上月線 {s['above_ma20']:.0f}%", f"上漲家數 {s['up_pct']:.0f}%",
             f"漲停 {s['limit_up']} / 跌停 {s['limit_down']}"]
    if "new_high" in s:
        parts.append(f"創60日新高 {s['new_high']:.0f} / 新低 {s['new_low']:.0f}")
    if "mkt20" in s and s["mkt20"] == s["mkt20"]:
        parts.append(f"大盤近20日 {s['mkt20']:+.1f}%")
    line = "｜".join(parts)
    return f"> {line}\n> {s['advice']}\n"
