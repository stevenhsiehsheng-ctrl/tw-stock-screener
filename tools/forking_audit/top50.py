"""Cowork 0056 ②：新進前 50 觀察 20 日（帳本 [15]）亂數對照。
每週含下市股 5 年版每週 A（前 60 日在榜 ≤5 天）／B（60 日全在榜）每筆 20 日超額。
打亂：每一週內把 A/B 標籤在 A∪B 之間隨機重排（每週 A 檔數不變），照 1238 判準：
①A 中位 − B 中位 ≤ −1 且 A ≥30 週；②每 4 週取一次也 ≤ −1；③逐年中位差 <0 的年 ≥3。"""
import sys, numpy as np, pandas as pd
S = sys.argv[1]; NIT = int(sys.argv[2]) if len(sys.argv) > 2 else 200
bt=pd.read_csv(f'{S}/bt.csv.gz',dtype={'code':str}); bt=bt[bt.code.str.fullmatch(r'[1-9]\d{3}')]
piv=lambda c: bt.pivot(index='date',columns='code',values=c).sort_index()
C=piv('close'); O=piv('open').reindex_like(C); V=piv('volume').reindex_like(C).fillna(0)
tv=(C*V).rolling(20,min_periods=20).mean().shift(1)          # 前一交易日的 20 日均成交值
rk=tv.rank(axis=1,ascending=False)
top=(rk<=50)
cnt60=top.shift(1).rolling(60,min_periods=60).sum()             # 前 60 日（不含當天）在榜天數
dates=list(C.index); idx={d:i for i,d in enumerate(dates)}
wk=pd.Series(dates,index=pd.to_datetime(dates)).groupby(pd.to_datetime(dates).to_period('W')).first()
rows=[]
for d in wk.values:
    i=idx[d]
    if i+19>=len(dates): continue
    x=dates[i+19]
    r=C.loc[x]/O.loc[d]-1
    ok=r.notna()&(r.abs()<3)
    ex=(r-r[ok].mean())*100-0.38
    t=top.loc[d]&ok; c=cnt60.loc[d]
    A=t&(c<=5); B=t&(c>=60)
    for g,m in (('A',A),('B',B)):
        for code in m[m].index: rows.append({'week':d,'g':g,'code':code,'ret':ex[code]})
df = pd.DataFrame(rows).reset_index(drop=True)
wl = sorted(df.week.unique()); keep = set(wl[::4])
def judge(g):
    a, b = df.ret[g == "A"], df.ret[g == "B"]
    c1 = a.median() - b.median() <= -1 and df.week[g == "A"].nunique() >= 30
    k = df.week.isin(keep)
    c2 = df.ret[k & (g == "A")].median() - df.ret[k & (g == "B")].median() <= -1
    yrs = 0
    for y in sorted(df.week.str[:4].unique()):
        m = df.week.str[:4] == y
        aa, bb = df.ret[m & (g == "A")], df.ret[m & (g == "B")]
        if len(aa) >= 10 and len(bb) >= 10 and aa.median() - bb.median() < 0:
            yrs += 1
    return c1, c2, yrs >= 3, a.median() - b.median()
r = judge(df.g)
print(f"實際：①{r[0]} ②{r[1]} ③{r[2]} 中位差 {r[3]:+.2f}；A N={int((df.g=='A').sum())}、{df.week[df.g=='A'].nunique()} 週，B N={int((df.g=='B').sum())}")
rng = np.random.default_rng(20261007)
grp = df.groupby("week").indices
out = []
for _ in range(NIT):
    g = df.g.to_numpy().copy()
    for w, ix in grp.items():
        g[ix] = g[ix][rng.permutation(len(ix))]
    out.append(judge(pd.Series(g)))
v = np.array([o[:3] for o in out]); d = np.array([o[3] for o in out])
print(f"週內打亂 {NIT} 次：① {v[:,0].mean():.1%} ② {v[:,1].mean():.1%} ③ {v[:,2].mean():.1%}｜三條全過 {v.all(1).mean():.1%}；中位差 5/50/95% {np.percentile(d,5):+.2f}/{np.median(d):+.2f}/{np.percentile(d,95):+.2f}")
print(f"打亂版中位差最小 {d.min():+.2f}；≤ 實際 {r[3]:+.2f} 的次數 {(d <= r[3]).sum()}/{NIT}")
# 為什麼打亂也會負：A 檔數多的週，那週整體報酬比較差（混合比重偏差）
wk = df.groupby("week").agg(nA=("g", lambda s: (s == "A").sum()), allmed=("ret", "median"))
print("每週 A 檔數 vs 該週全部中位：Spearman %.2f；A≥5 檔的週 全部中位的中位 %+.2f，其他週 %+.2f" % (
    wk.nA.rank().corr(wk.allmed.rank()), wk.allmed[wk.nA >= 5].median(), wk.allmed[wk.nA < 5].median()))
# 事後（不拿來翻判）：週內配對＝每週 (A 中位 − B 中位) 再取平均
def paired(g):
    x = pd.DataFrame({"w": df.week, "g": g, "r": df.ret}).groupby(["w", "g"]).r.median().unstack()
    x = x.dropna(); return (x["A"] - x["B"]).mean(), (x["A"] - x["B"]).median(), len(x)
p = paired(df.g)
pp = []
for _ in range(NIT):
    g = df.g.to_numpy().copy()
    for w, ix in grp.items():
        g[ix] = g[ix][rng.permutation(len(ix))]
    pp.append(paired(g)[0])
pp = np.array(pp)
print(f"週內配對（事後，只參考）：每週 A中位−B中位 平均 {p[0]:+.2f}、中位 {p[1]:+.2f}、{p[2]} 週；打亂 5/50/95% {np.percentile(pp,5):+.2f}/{np.median(pp):+.2f}/{np.percentile(pp,95):+.2f}，≤實際 {(pp<=p[0]).sum()}/{NIT}")
