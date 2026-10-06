"""Cowork 0056 ②：revdrift 亂數對照（同樣本池、同判準 (a)～(d)）。
(i) 成員打亂：每批籃子每一檔換成『同日同流動性五分位』隨機一檔（籃子大小、流動性組成不變）
(ii) 時間打亂：每批改用 2～7 個月前（隨機）那個月的籃子，在本批批次日進場（不偷看未來，只是『舊消息』）
判準照 2311／2314：(a) 差平均 >0.5 且按批 bootstrap 5% >0；(b) 贏安慰劑 ≥55%；(c) 2023/24/25 至少 2 年 >0；(d) 對 0050 平均 >0
安慰劑中位用解析近似：Σ(各五分位檔數×該五分位平均)/n（300 次抽樣的中位≈期望值，下面會印實際 vs 近似）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd
from screener import revdrift as R
from screener.lottery import COST, liquidity_quintile
S = sys.argv[1]; NIT = int(sys.argv[2]) if len(sys.argv) > 2 else 200
bt = pd.read_csv(f"{S}/bt.csv.gz", dtype={"code": str}); bt = bt[bt.code.str.fullmatch(r"[1-9]\d{3}")]
rev = pd.read_csv("data/extras/rev_5y.csv.gz", dtype={"code": str})
act = R.compute(bt, rev).set_index("rev_month")
piv = lambda c: bt.pivot(index="date", columns="code", values=c).sort_index()
C = piv("close"); O = piv("open").reindex_like(C); V = piv("volume").fillna(0).reindex_like(C)
Q = liquidity_quintile(C, V)
days = list(C.index); bk = R.baskets(rev)
bl = pd.read_csv("data/extras/bench_long.csv", dtype={"code": str}); z = bl[bl.code == "0050"].set_index("date").close_adj.sort_index()
yms = sorted(m for m in bk if m <= "2025-08")
B = {}
for ym in yms:
    if ym not in act.index or not act.loc[ym, "done"] or pd.isna(act.loc[ym, "placebo_avg_median"]):
        continue
    i = R._batch_day(ym, days); e, x = days[i], days[i + R.HOLD]
    r = C.loc[x] / O.loc[e] - 1
    valid = r.notna() & (r.abs() < 3)
    mkt = r[valid].mean()
    ex = (r - mkt) * 100 - COST
    qe = Q.loc[e]
    ok = valid & qe.notna()
    pools = {g: ex[ok & (qe == g)].index.to_numpy() for g in range(1, 6)}
    mu = {g: ex[pools[g]].mean() for g in range(1, 6)}
    zp = z[z.index < e].iloc[-1]; zx = z[z.index <= x].iloc[-1]
    B[ym] = dict(e=e, ex=ex, ok=ok, qe=qe, pools=pools, mu=mu, mkt=mkt, z=(zx / zp - 1) * 100, pm=act.loc[ym, "placebo_avg_median"])
yms = list(B)
print(f"未看過的完成批 {len(yms)}（{yms[0]}～{yms[-1]}）")

rng = np.random.default_rng(20261007)
def boot_lo(v):
    v = np.asarray(v); return np.percentile([v[rng.integers(0, len(v), len(v))].mean() for _ in range(1000)], 5)

def judge(rows):
    df = pd.DataFrame(rows)
    d = df["avg"] - df["pm"]
    a = d.mean() > 0.5 and boot_lo(d.values) > 0
    b = (d > 0).mean() >= 0.55
    yr = d.groupby(df.year).mean()
    c = sum(yr.get(y, -1) > 0 for y in ("2023", "2024", "2025")) >= 2
    dd = (df["raw"] - 0.38 - 0.84 - 0.18 - df["z"]).mean() > 0
    return a, b, c, dd, d.mean(), (df["raw"] - 0.38 - 0.84 - 0.18 - df["z"]).mean()

def row(ym, codes):
    t = B[ym]
    codes = [c for c in codes if t["ok"].get(c, False)]
    if not codes:
        return None
    gs = t["qe"][codes]
    pm = float(sum(t["mu"][g] for g in gs) / len(gs))
    avg = float(t["ex"][codes].mean())
    raw = avg + t["mkt"] * 100 + COST            # 原始報酬 %
    return {"ym": ym, "year": t["e"][:4], "avg": avg, "pm": pm, "raw": raw, "z": t["z"]}

# 實際
real = [row(ym, bk[ym]) for ym in yms]
ra = judge(real)
pmr = pd.DataFrame(real).set_index("ym")
print("實際（解析安慰劑）: (a)%s (b)%s (c)%s (d)%s 差平均 %+.2f 對0050 %+.2f" % (*ra[:4], ra[4], ra[5]))
print("  解析安慰劑 vs 300 次中位：平均差 %+.3f、最大 |差| %.3f" % ((pmr.pm - act.loc[yms, 'placebo_avg_median']).mean(), (pmr.pm - act.loc[yms, 'placebo_avg_median']).abs().max()))

res = {"i": [], "ii": []}
for it in range(NIT):
    rows1, rows2 = [], []
    for k, ym in enumerate(yms):
        t = B[ym]
        b = [c for c in bk[ym] if t["ok"].get(c, False)]
        gs = t["qe"][b]
        pick = [t["pools"][g][rng.integers(len(t["pools"][g]))] for g in gs]
        rows1.append(row(ym, pick))
        # 舊籃子：2～7 個月前
        allm = sorted(bk)
        j = allm.index(ym) - int(rng.integers(2, 8))
        old = bk[allm[j]] if j >= 0 else []
        r2 = row(ym, old)
        if r2: rows2.append(r2)
    res["i"].append(judge(rows1)); res["ii"].append(judge(rows2))
for k, name in (("i", "成員打亂（同日同流動性隨機）"), ("ii", "時間打亂（2～7 個月前的舊籃子）")):
    v = np.array([[x[0], x[1], x[2], x[3]] for x in res[k]])
    m = np.array([x[4] for x in res[k]]); d = np.array([x[5] for x in res[k]])
    print(f"{name}：{NIT} 次")
    print(f"  過 (a) {v[:,0].mean():.1%}｜過 (a)+(d) {(v[:,0]&v[:,3]).mean():.1%}｜四條全過 {v.all(1).mean():.1%}｜(b) {v[:,1].mean():.1%} (c) {v[:,2].mean():.1%} (d) {v[:,3].mean():.1%}")
    print(f"  差平均 5/50/95% {np.percentile(m,5):+.2f}/{np.median(m):+.2f}/{np.percentile(m,95):+.2f}（實際 {ra[4]:+.2f}）；對 0050 5/50/95% {np.percentile(d,5):+.2f}/{np.median(d):+.2f}/{np.percentile(d,95):+.2f}（實際 {ra[5]:+.2f}）")
