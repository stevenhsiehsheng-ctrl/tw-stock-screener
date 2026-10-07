"""每日篩選各策略的 5 年回測成績（含下市股），寫 data/extras/strat_5y.json 給每日篩選頁顯示。

用法：python tools/strat5y/run.py <backtest.csv.gz>
口徑（暫定，Cowork 大題 A 定案後改）：
- 訊號：config.yaml 的 base_filter＋各策略條件，跟每天篩選同一套程式（rules.CONDITIONS），對 5 年每一天算
- 主口徑 next_open：名單收盤後才出來，所以「隔天開盤買」，持有到第 h 個交易日收盤；隔天一開盤就漲停（買不到）的剔除
- 參考口徑 close：訊號當天收盤買（盤中 13:12 那條路才做得到）
- 超額＝個股報酬 − 同樣進出時點的全市場等權（|報酬|<300%），再扣來回 0.38%
- t 值按訊號日聚類（同一天多檔先平均），持有期重疊沒扣，偏樂觀
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from screener import rules  # noqa: E402

H = (1, 5, 20)
COST = 0.38


def main(bt_path: str) -> None:
    bt = pd.read_csv(bt_path, dtype={"code": str})
    bt = bt[bt.code.str.fullmatch(r"[1-9]\d{3}")]
    p = rules.Panel(bt)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))

    def ev(cond):
        m = rules.CONDITIONS[cond["type"]][0](p, cond).fillna(False).astype(bool)
        if cond.get("days_ago"):
            m = m.shift(int(cond["days_ago"]), fill_value=False)
        if cond.get("within"):
            m = m.astype(int).rolling(int(cond["within"]), min_periods=1).max().astype(bool)
        return ~m if cond.get("not") else m

    base = p.traded.copy()
    for c in cfg["base_filter"]:
        base &= ev(c)
    C = p.close.where(p.traded)
    nxo = p.open.shift(-1)
    lu_open = nxo >= rules.limit_price(p.close, True) - 1e-9
    entry = {"next_open": nxo, "close": C}
    fwd = {(k, h): (C.shift(-h) / e - 1).where(lambda r: r.abs() < 3) for k, e in entry.items() for h in H}
    ew = {k: r.mean(axis=1) for k, r in fwd.items()}
    out = []
    for s in cfg["strategies"]:
        if not s.get("enabled", True):
            continue
        m = base.copy()
        for c in s.get("conditions", []):
            m &= ev(c)
        m.iloc[:61] = False
        st = m.stack()
        idx = st[st].index
        d_all = idx.get_level_values(0)
        lo = lu_open.stack().reindex(idx).fillna(False).to_numpy()
        row = {"name": s["name"], "n": int(len(idx)), "per_day": round(len(idx) / len(p.close.index), 1),
               "lu_open_pct": round(float(lo.mean() * 100), 1)}
        for k in entry:
            for h in H:
                ex = (fwd[(k, h)].stack().reindex(idx).to_numpy() - ew[(k, h)].reindex(d_all).to_numpy()) * 100 - COST
                ok = np.isfinite(ex) & (~lo if k == "next_open" else True)
                v, d = ex[ok], d_all[ok]
                dd = pd.Series(v, index=d).groupby(level=0).mean()
                r = {"ex": round(float(v.mean()), 2), "med": round(float(np.median(v)), 2),
                     "win": round(float((v > 0).mean() * 100), 1),
                     "t": round(float(dd.mean() / (dd.std(ddof=1) / len(dd) ** .5)), 2)}
                if h in (5, 20):
                    yr = pd.Series(v, index=d.str[:4]).groupby(level=0).agg(["mean", "size"])
                    yr = yr[yr["size"] >= 80]                           # N<80 的年份不計
                    r["years"] = {y: [round(float(a), 2), int(b)] for y, (a, b) in yr.iterrows()}
                    r["pos_years"] = [int((yr["mean"] > 0).sum()), int(len(yr))]
                row[f"{k}_{h}"] = r
        out.append(row)
    res = {"period": [p.close.index[61], p.close.index[-1]], "stocks": int(p.close.shape[1]), "cost": COST,
           "method": "名單收盤後出來 → 隔天開盤買（一開盤就漲停的剔除），持有 h 個交易日收盤賣；超額＝減同時點全市場等權、扣來回 0.38%；含下市股",
           "strategies": out}
    dst = ROOT / "data" / "extras" / "strat_5y.json"
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    for r in out:
        a, b = r["next_open_5"], r["next_open_20"]
        print(f"{r['name']:<16} n={r['n']:>6}  隔天開盤買 5日 {a['ex']:+.2f}（勝 {a['win']:.0f}%，正 {a['pos_years'][0]}/{a['pos_years'][1]} 年）"
              f" 20日 {b['ex']:+.2f}（t {b['t']:.1f}）｜收盤買 5日 {r['close_5']['ex']:+.2f} 20日 {r['close_20']['ex']:+.2f}")
    print("寫入", dst)


if __name__ == "__main__":
    main(sys.argv[1])
