# 用法：python tools/valuation/pe_pct_bt.py <放 dl/bt.csv.gz 的資料夾>
"""Cowork 20261008-1455-cw-pe-criteria：本益比 5 年位階 ≤20 vs ≥80，之後 60 日超額（同流動性五分位等權、兩邊扣 0.38）。
位階只用批次日以前的月快照（≥24 個月，不含當月）；虧損（PE≤0／空）排除另列。"""
import sys, numpy as np, pandas as pd
S = sys.argv[1]; H = 60; COST = 0.38
bt = pd.read_csv(f'{S}/dl/bt.csv.gz', dtype={'code': str}); bt = bt[bt.code.str.fullmatch(r'[1-9]\d{3}')]
C = bt.pivot(index='date', columns='code', values='close').sort_index()
V = bt.pivot(index='date', columns='code', values='volume').reindex_like(C)
days = list(C.index); pos = {d: i for i, d in enumerate(days)}
dv = (C * V).rolling(20, min_periods=20).mean().shift(1)
qn = np.ceil(dv.rank(axis=1, pct=True) * 5)
pe = pd.read_csv('data/extras/pe_5y.csv.gz', dtype={'code': str}); pe = pe[pe.code.str.fullmatch(r'[1-9]\d{3}')]
pe['pe'] = pd.to_numeric(pe.pe, errors='coerce')
P = pe.pivot(index='date', columns='code', values='pe').sort_index()
snaps = list(P.index)
rows, loss_n = [], 0
for k, sd in enumerate(snaps):
    if k < 24 or sd not in pos: continue
    i = pos[sd]
    if i + H >= len(days): continue
    hist = P.iloc[:k]                    # 只用之前的月份
    cur = P.iloc[k]
    ok_hist = hist.where(hist > 0)
    n_hist = ok_hist.notna().sum()
    valid = cur.notna() & (cur > 0) & (n_hist >= 24)
    loss_n += int((cur.isna() | (cur <= 0)).reindex(C.columns).fillna(False).sum())
    pct = ((ok_hist < cur).sum() + 0.5 * (ok_hist == cur).sum()) / n_hist * 100
    pct = pct[valid]
    e, x = days[i], days[i + H]
    r = (C.loc[x] / C.loc[e] - 1)
    r = r.where(r.abs() < 3)
    q = qn.loc[e]
    ew = {g: r[(q == g) & r.notna()].mean() for g in range(1, 6)}
    ex = (r - q.map(ew)) * 100 - COST
    for c, p in pct.items():
        if c in ex.index and pd.notna(ex[c]):
            rows.append((sd, c, p, ex[c]))
R = pd.DataFrame(rows, columns=['sd', 'code', 'pct', 'ex'])
R['g'] = np.where(R.pct <= 20, 'low', np.where(R.pct >= 80, 'high', 'mid'))
B = R[R.g != 'mid'].groupby(['sd', 'g']).ex.mean().unstack()
B['diff'] = B['low'] - B['high']
B = B.dropna()
print('批次', len(B), B.index.min(), '～', B.index.max(), '｜虧損排除（檔月）', loss_n)
n = R.g.value_counts(); print('① 樣本：低組', n.get('low', 0), '高組', n.get('high', 0), '（中間', n.get('mid', 0), '）')
print(f"② 全部批次 低−高：平均 {B['diff'].mean():+.2f}、中位 {B['diff'].median():+.2f}（低組平均 {B['low'].mean():+.2f}、高組 {B['high'].mean():+.2f}）")
for off in range(3):
    nb = B.iloc[off::3]['diff']
    print(f"③ 不重疊（每 3 批取 1，起點 {off}）：{len(nb)} 批、正的 {(nb > 0).mean():.0%}、平均 {nb.mean():+.2f}、中位 {nb.median():+.2f}")
h1, h2 = B[B.index <= '2025-03-31']['diff'], B[B.index > '2025-03-31']['diff']
print(f"④ 前半（到 2025-03）{len(h1)} 批 平均 {h1.mean():+.2f} 中位 {h1.median():+.2f}｜後半 {len(h2)} 批 平均 {h2.mean():+.2f} 中位 {h2.median():+.2f}")
print('逐批：', ' '.join(f"{d[2:7]}:{v:+.1f}" for d, v in B['diff'].items()))
