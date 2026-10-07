"""法人籌碼 5 年版四題（協作板事前登記，跑完照判準、不准改口徑）：
1. 外資連買（0225）：連買 ≥5 天、5 日買超÷量 ≥10%，T+1 開盤進，配對差 20 日平均 ≤0 或中位 <0 → 只是動能
2. 外資連賣當出場警訊（0255）：同門檻反過來、前 60 日報酬 >0；20 日配對差平均 <0 且中位 <0 且 N≥200、訊號日 ≥60 → 待驗
3. 投信季底倒貨說（0254／0325 主判準＝季底距離分桶）：投信連買 ≥3 天、3 日買超÷量 ≥3%；
   季底前 ≤15 日桶 ≥4/5 年 20 日配對差中位 <0，且虧損 ≥2/3 落在季底之後那段 → 倒貨說成立
4. 持有中外資連賣（0325-surge）：surge_trades 持有中出現外資連賣（同 2 的門檻、不看前 60 日），隔天開盤提前賣 vs 照原規則；
   N≥100、訊號日 ≥40、提前賣的平均與中位都比原規則好 ≥0.4 → 待驗
用法：python tools/inst5y/run.py <bt.csv.gz（含下市股 5 年）> [題號...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from screener import lottery  # noqa: E402


def load(bt_path: str):
    h = pd.read_csv(bt_path, dtype={"code": str})
    h = h[h.code.str.fullmatch(r"[1-9]\d{3}")]
    inst = pd.read_csv(ROOT / "data" / "extras" / "inst_5y.csv.gz", dtype={"code": str})
    ind = pd.read_csv(ROOT / "data" / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code").industry
    h = h[h.date >= (pd.Timestamp(inst.date.min()) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")]
    print(f"價格 {h.date.min()}～{h.date.max()}、{h.code.nunique()} 檔；法人 {inst.date.min()}～{inst.date.max()}；查得到產業 {h.code.isin(ind.index).mean():.1%}")
    return h, inst, ind


def q_buckets(days: list[str]) -> tuple[dict, dict]:
    """每個交易日 → 季底前（≤15 日）／季底後（≤15 日）／其他；以及季底前那桶的中間日（季底最後交易日）。"""
    s = pd.Series(days, index=pd.to_datetime(days))
    qend = s.groupby([s.index.year, s.index.quarter]).max().tolist()
    pos = {d: i for i, d in enumerate(days)}
    qi = [pos[q] for q in qend]
    bucket, mid = {}, {}
    for d, i in pos.items():
        nxt = [j for j in qi if j >= i]
        prv = [j for j in qi if j < i]
        if nxt and nxt[0] - i <= 15 and nxt[0] != len(days) - 1:
            bucket[d], mid[d] = "季底前", days[nxt[0]]
        elif prv and i - prv[-1] <= 15:
            bucket[d] = "季底後"
        else:
            bucket[d] = "其他"
    return bucket, mid


def per_year(ev, k=20):
    g = ev[(ev.hold == k)].dropna(subset=["diff"])
    t = g.groupby(g.date.str[:4]).agg(N=("diff", "size"), 訊號日=("date", "nunique"), 平均=("diff", "mean"), 中位=("diff", "median"))
    t["一天最多"] = g.groupby([g.date.str[:4], "date"]).size().groupby(level=0).max()
    return t.round(2)


def q1(h, inst, ind):
    print("\n== 1. 外資連買（0225）==")
    ev = lottery.inst_flow(h, inst, ind, col="foreign", side="buy")
    print(lottery.inst_flow_report(ev)); print(per_year(ev))
    g = ev[ev.hold == 20].dropna(subset=["diff"])
    print("判準：", "只是動能（不進規則）" if g["diff"].mean() <= 0 or g["diff"].median() < 0 else
          ("進待驗" if len(g) >= 200 and g.date.nunique() >= 60 else "方向過但樣本不足"))


def q2(h, inst, ind):
    print("\n== 2. 外資連賣當出場警訊（0255）==")
    ev = lottery.inst_flow(h, inst, ind, col="foreign", side="sell", ret60_pos=True)
    print(lottery.inst_flow_report(ev)); print(per_year(ev))
    g = ev[ev.hold == 20].dropna(subset=["diff"])
    ok = g["diff"].mean() < 0 and g["diff"].median() < 0 and len(g) >= 200 and g.date.nunique() >= 60
    print("判準：", "進待驗當減碼警訊" if ok else "出場維持純價格")


def q3(h, inst, ind):
    print("\n== 3. 投信季底倒貨說（0254，主判準＝季底距離分桶）==")
    days = sorted(h.date.unique())
    bucket, mid = q_buckets(days)
    ev = lottery.inst_flow(h, inst, ind, col="trust", side="buy", min_streak=3, min_ratio=0.03, holds=(20,), split_at=mid)
    ev["bucket"] = ev.date.map(bucket)
    g = ev.dropna(subset=["diff"])
    for b, x in g.groupby("bucket"):
        print(f"{b}：N={len(x)} 訊號日 {x.date.nunique()} 一天最多 {x.groupby('date').size().max()} 配對差 平均 {x['diff'].mean():+.2f} 中位 {x['diff'].median():+.2f}")
    pre = g[g.bucket == "季底前"]
    yr = pre.groupby(pre.date.str[:4]).agg(N=("diff", "size"), 訊號日=("date", "nunique"), 中位=("diff", "median"),
                                           前段平均=("diff_a", "mean"), 後段平均=("diff_b", "mean")).round(2)
    print(yr)
    seg = pre.dropna(subset=["diff_a"])
    neg_years = int((yr["中位"] < 0).sum())
    loss_a, loss_b = -seg["diff_a"].clip(upper=0).sum(), -seg["diff_b"].clip(upper=0).sum()
    share_b = loss_b / (loss_a + loss_b) if loss_a + loss_b > 0 else float("nan")
    print(f"季底前桶：中位 <0 的年份 {neg_years}/{len(yr)}；虧損落在季底後那段的占比 {share_b:.0%}（前段平均 {seg['diff_a'].mean():+.2f}、後段 {seg['diff_b'].mean():+.2f}，N={len(seg)}）")
    print("判準：", "倒貨說成立" if neg_years >= 4 and share_b >= 2 / 3 else "巧合，維持『無證據』")
    print("附錄（月份分，不准拿來下結論）：", g.groupby(g.date.str[5:7])["diff"].median().round(2).to_dict())


def q4(h, inst, ind):
    print("\n== 4. 持有中外資連賣（0325-surge）==")
    tr = lottery.surge_trades(h)
    tr = tr[tr.entry >= inst.date.min()]
    from screener.rules import Panel
    p = Panel(h)
    C, O, V = p.close, p.open, p.volume / 1000
    F = inst.pivot_table(index="date", columns="code", values="foreign", aggfunc="sum").reindex(index=C.index, columns=C.columns)
    hit = (F < 0).astype(int)
    streak = hit.apply(lambda s: s.groupby((s == 0).cumsum()).cumsum())
    ratio = -F.rolling(5).sum() / V.rolling(5).sum()
    sig = (streak >= 5) & (ratio >= 0.10)
    idx = list(C.index)
    pos = {d: i for i, d in enumerate(idx)}
    rows = []
    for t in tr.itertuples():
        i, x = pos[t.entry], pos[t.exit]
        s = sig[t.code].to_numpy()
        hits = [j for j in range(i + 1, x - 1) if s[j]]      # 出場前一天以前出現訊號，隔天開盤才賣得比原規則早
        if not hits:
            continue
        j = hits[0]
        r = O.iloc[j + 1] / C.iloc[i] - 1
        mk = r[r.abs() < 3].mean()
        early = ((O[t.code].iat[j + 1] / C[t.code].iat[i] - 1) - mk) * 100 - lottery.COST
        rows.append({"entry": t.entry, "sig": idx[j], "code": t.code, "early": early, "orig": t.ret, "gain": early - t.ret})
    d = pd.DataFrame(rows)
    if d.empty:
        print("沒有事件"); return
    print(f"N={len(d)} 訊號日 {d.sig.nunique()} 一天最多 {d.groupby('sig').size().max()}；提前賣 平均 {d.early.mean():+.2f} 中位 {d.early.median():+.2f}；"
          f"原規則 平均 {d.orig.mean():+.2f} 中位 {d.orig.median():+.2f}；差（提前−原）平均 {d.gain.mean():+.2f} 中位 {d.gain.median():+.2f}")
    print(d.groupby(d.sig.str[:4]).agg(N=("gain", "size"), 平均=("gain", "mean"), 中位=("gain", "median")).round(2))
    ok = len(d) >= 100 and d.sig.nunique() >= 40 and d.early.mean() - d.orig.mean() >= 0.4 and d.early.median() - d.orig.median() >= 0.4
    print("判準：", "進待驗" if ok else "結案（出場維持純價格）")


if __name__ == "__main__":
    h, inst, ind = load(sys.argv[1])
    want = sys.argv[2:] or ["1", "2", "3", "4"]
    for k in want:
        {"1": q1, "2": q2, "3": q3, "4": q4}[k](h, inst, ind)
