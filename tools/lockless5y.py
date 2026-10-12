"""鎖死跳過題（Cowork 2159-cw-talk-locklessedge）→ data/extras/lockless_5y.json（首頁系統訊號那行讀）。

突破桶 10 格、C（fill=equal）、200 種子，比 0050 含息：
A＝現行（收盤鎖漲停照收盤買到，實際排不到）；B＝鎖死跳過（真的買得到的那群）；B 另跑閒錢每天停 0050。
用法：python tools/lockless5y.py [backtest.csv.gz]（沒給就用 btsim 的快取）
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.btsim import _mdd, _prep, _runs, load, provenance, signals, trades  # noqa: E402

P = load(sys.argv[1] if len(sys.argv) > 1 else None)
T0 = trades(P, signals(P, min_turnover20=1e7), slip=True)
T = T0[~T0.corp_jump & ~T0.censored].reset_index(drop=True)
prov = provenance(min_turnover20=1e7, slip=True, drop="corp_jump,censored", slots=10, fill="equal", seeds=200)
print(prov)
OUT = {"provenance": prov}
dates = P["dates"]
d = pd.to_datetime(pd.Series(dates))
tr = P["ew"].tr_0050.reindex(dates).ffill().to_numpy()
VAR = {"A": ("現行（鎖死照收盤買到）", T, {}),
       "B": ("鎖死跳過", T[~T.lu].reset_index(drop=True), {}),
       "B_park": ("鎖死跳過＋閒錢停 0050", T[~T.lu].reset_index(drop=True), {"park": {"kind": "0050", "mode": "daily", "cost": 0.25, "min_fee": 20}})}
R0 = _prep(P, T, True)
s0 = min(R0["by_entry"])
end = len(dates) - 1
yrs = (d.iloc[end] - d.iloc[s0]).days / 365.25
a50 = (tr[end] / tr[s0]) ** (1 / yrs) - 1
mdd50 = _mdd(pd.Series(tr[s0:] / tr[s0]))
OUT.update(start=dates[s0], end=dates[end], ann_0050=round(a50 * 100, 1), mdd_0050=round(mdd50, 1))
print(f"期間 {dates[s0]}～{dates[end]}（{yrs:.2f} 年）；0050 含息年化 {a50*100:.1f}%、最大回撤 {mdd50:.1f}%、年化÷回撤 {a50*100/abs(mdd50):.2f}")
years = sorted(set(d.dt.year[s0:]))
yidx = {y: (max(s0, int(np.flatnonzero(d.dt.year == y)[0]) - 1), int(np.flatnonzero(d.dt.year == y)[-1])) for y in years}
y50 = {y: tr[b] / tr[a] - 1 for y, (a, b) in yidx.items()}
for key, (name, TT, kw) in VAR.items():
    R = _prep(P, TT, True)
    EV, _, ST = _runs(R, dict(slots=10, fill="equal", **kw), 200, [], 4, s0)
    E = EV[:, s0:]
    ann = E[:, -1] ** (1 / yrs) - 1
    mdd = np.array([_mdd(pd.Series(x)) for x in E])
    print(f"\n== {name}（逐筆 N={len(TT)}）")
    print(f"年化 中位 {np.median(ann)*100:.1f}%（5～95%：{np.percentile(ann,5)*100:.1f}～{np.percentile(ann,95)*100:.1f}）；減 0050 {np.median(ann-a50)*100:+.1f}pp；"
          f"贏 0050 的種子 {np.mean(ann>a50)*100:.0f}%")
    print(f"最大回撤 中位 {np.median(mdd):.1f}%；年化÷回撤 中位 {np.median(ann*100/np.abs(mdd)):.2f}；閒錢 {np.median([s['idle'] for s in ST]):.0f}%")
    ex = TT.ex50
    m = TT.assign(m=TT.date.str[:7]).groupby("m").ex50.mean()
    print(f"逐筆淨超額（減 0050 含息）平均 {ex.mean():+.2f}%、中位 {ex.median():+.2f}%、N={len(ex)}；月 t＝{m.mean()/(m.std(ddof=1)/np.sqrt(len(m))):.2f}（{len(m)} 個月）")
    row = []
    for y, (a, b) in yidx.items():
        ry = np.median(EV[:, b] / EV[:, a] - 1)
        row.append(f"{y}: {ry*100:+.1f}% vs 0050 {y50[y]*100:+.1f}% → {((ry-y50[y])*100):+.1f}pp")
    print("逐年（種子中位）： " + "；".join(row))
    lose = int(sum(np.median(EV[:, b] / EV[:, a] - 1) < y50[y] for y, (a, b) in yidx.items()))
    print("輸 0050 的年數：", lose, "/", len(years))
    OUT[key] = {"name": name, "n": len(TT), "ann": round(float(np.median(ann)) * 100, 1), "mdd": round(float(np.median(mdd)), 1),
                "ex_mean": round(float(ex.mean()), 2), "ex_med": round(float(ex.median()), 2), "lose_years": lose, "years": len(years)}
Path(__file__).resolve().parent.parent.joinpath("data", "extras", "lockless_5y.json").write_text(
    json.dumps(OUT, ensure_ascii=False, indent=1) + "\n", "utf-8")
