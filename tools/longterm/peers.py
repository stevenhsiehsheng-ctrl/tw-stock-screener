"""長期 Top 20 的「同類」（Cowork 1355／分身 1415）：池子裡每一檔，取起點前 250 個交易日日報酬相關最高的 10 檔同池股當同類，
排除 Top 20 名單本身；兩檔要有 ≥200 個共同交易日才算（新上市、資料不足的不當任何人的同類）。|日報酬|>10.5% 當除權雜訊剔掉。
算一次就凍結（data/longterm/peers_<起點>.json），之後每天的超額都比這份。整個池子都算，隨機投組檢定才能用同一套規則。
用法：python tools/longterm/peers.py <起點 YYYY-MM-DD>
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main(asof: str) -> None:
    top = json.loads((ROOT / "data/longterm/top20.json").read_text("utf-8"))
    picks = {p["code"] for p in top["picks"]}
    pool = pd.read_csv(ROOT / "data/longterm" / top["screen"], dtype={"code": str}).code.tolist()
    h = pd.read_csv(ROOT / "data/history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    h = h[h.code.isin(pool) & (h.date <= asof)]
    px = h.pivot(index="date", columns="code", values="close").sort_index().tail(251)
    r = px.pct_change().iloc[1:]
    r = r.mask(r.abs() > 0.105)
    corr = r.corr(min_periods=200)
    cand = [c for c in corr.columns if c not in picks]
    peers = {}
    for c in corr.columns:
        s = corr.loc[c, [x for x in cand if x != c]].dropna().sort_values(ascending=False)
        if len(s) >= 10:
            peers[c] = {"peers": s.index[:10].tolist(), "avg_corr": round(float(s.iloc[:10].mean()), 3)}
    out = ROOT / f"data/longterm/peers_{asof}.json"
    out.write_text(json.dumps({"asof": asof, "window": [r.index[0], r.index[-1]], "min_periods": 200, "peers": peers},
                              ensure_ascii=False, indent=0), "utf-8")
    miss = [c for c in picks if c not in peers]
    pa = [peers[c]["avg_corr"] for c in picks if c in peers]
    print(f"{out.name}：池子 {len(pool)} 檔、有同類 {len(peers)} 檔；名單沒同類 {miss}；名單同類平均相關 {sum(pa)/len(pa):.3f}（最高 {max(pa):.2f}）")


if __name__ == "__main__":
    main(sys.argv[1])
