# 用法：python tools/etf/hd_vs_0050.py data/extras/etf_long.csv.gz（原始檔有 Yahoo 0050 錯誤，網頁用 etfpage.hd_compare 的校正版）
"""Cowork 20261008-1456-cw-etf-criteria：0056 vs 0050 含息，不重疊 60 交易日窗，依 0050 同窗報酬三分位。"""
import sys, numpy as np, pandas as pd
e = pd.read_csv(sys.argv[1], dtype={'code': str})
print(e.groupby('code').date.agg(['min', 'max', 'size']).to_string())
# 資料檢查：原始收盤單日跳 >12%（台股 ±10% 漲跌幅）、還原比（adj/close）在非除息日跳動
for c in ['0050', '0056']:
    x = e[e.code == c].sort_values('date').set_index('date')
    jr = x.close.pct_change().abs(); ja = x.adj.pct_change().abs()
    print(c, '原始單日 >12%:', jr[jr > 0.12].round(3).to_dict(), '| 還原單日 >12%:', ja[ja > 0.12].round(3).to_dict())
A = e.pivot(index='date', columns='code', values='adj').sort_index()
d = A[['0050', '0056']].dropna()
print('共同日期', d.index[0], '～', d.index[-1], len(d))
yrs = (pd.to_datetime(d.index[-1]) - pd.to_datetime(d.index[0])).days / 365.25
for c in ['0050', '0056']:
    print(f'{c} 全期含息年化 {((d[c].iloc[-1] / d[c].iloc[0]) ** (1 / yrs) - 1) * 100:.2f}%')
# 分年
y = d.groupby(d.index.str[:4]).agg(['first', 'last'])
prev = d.groupby(d.index.str[:4]).last().shift(1)
yr = (d.groupby(d.index.str[:4]).last() / prev - 1) * 100
yr.iloc[0] = (y.xs('last', axis=1, level=1).iloc[0] / y.xs('first', axis=1, level=1).iloc[0] - 1) * 100
yr['diff'] = yr['0056'] - yr['0050']
print('分年含息報酬 %：'); print(yr.round(1).to_string())
# 不重疊 60 日窗
idx = list(range(0, len(d) - 60, 60))
W = pd.DataFrame([{'start': d.index[i], 'r50': (d['0050'].iloc[i + 60] / d['0050'].iloc[i] - 1) * 100,
                   'r56': (d['0056'].iloc[i + 60] / d['0056'].iloc[i] - 1) * 100} for i in idx])
W['diff'] = W.r56 - W.r50
W['t'] = pd.qcut(W.r50, 3, labels=['跌', '平', '漲'])
print('窗數', len(W))
for t, g in W.groupby('t', observed=True):
    print(f'{t}（0050 {g.r50.min():+.1f}～{g.r50.max():+.1f}%）：{len(g)} 窗、0056−0050 平均 {g["diff"].mean():+.2f}、中位 {g["diff"].median():+.2f}、0056 勝 {(g["diff"] > 0).mean():.0%}')
dn = W[W.t == '跌']
a, b = dn[dn.start < '2017'], dn[dn.start >= '2017']
print(f'③ 跌組 2008～2016：{len(a)} 窗 平均 {a["diff"].mean():+.2f}｜2017～2026：{len(b)} 窗 平均 {b["diff"].mean():+.2f}')
print('全部窗 0056−0050 平均', round(W['diff'].mean(), 2), '中位', round(W['diff'].median(), 2), '0056 勝', f"{(W['diff'] > 0).mean():.0%}")
if '0052' in A:
    k = A[['0050', '0056', '0052']].dropna()
    ii = list(range(0, len(k) - 60, 60))
    P = pd.DataFrame([{'start': k.index[i], 'tech_minus_50': (k['0052'].iloc[i + 60] / k['0052'].iloc[i] - k['0050'].iloc[i + 60] / k['0050'].iloc[i]) * 100,
                       'diff': (k['0056'].iloc[i + 60] / k['0056'].iloc[i] - k['0050'].iloc[i + 60] / k['0050'].iloc[i]) * 100} for i in ii])
    g = P[P.tech_minus_50 < 0]
    print(f'另列（代理：0052 科技輸 0050 的窗）：{len(g)}／{len(P)} 窗，0056−0050 平均 {g["diff"].mean():+.2f}、中位 {g["diff"].median():+.2f}、0056 勝 {(g["diff"] > 0).mean():.0%}')
