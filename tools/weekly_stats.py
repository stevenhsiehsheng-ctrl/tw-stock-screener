#!/usr/bin/env python3
"""市場週報數字（公開週報任務 8 用；只算全市場，不碰任何帳戶或個人持股）。
用法：python3 weekly_stats.py [REPO 路徑] [--end YYYY-MM-DD] [--prev YYYY-MM-DD] [--json out.json]
- end：本週最後交易日（預設 history 最後一天）；prev：上週最後交易日（預設 end 所在 ISO 週之前的最後一個交易日）
- 週報酬＝收盤(end)×除權息／減資調整因子 ÷ 收盤(prev) − 1（exdiv.csv、corp_actions.csv 的 factor，區間 (prev, end]）
- 流動性股票＝prev 當天前 20 日均量 ≥100 張的 4 碼普通股（同 breadth.csv n_liquid 口徑）
- 漲停／跌停家數、above_ma20、mkt20 直接讀 breadth.csv（官方檔位口徑）
- 60 日新高＝收盤 > 前 60 個交易日最高價
- 法人：inst_hist（張）；金額估計＝每日張數×1000×當日收盤加總
- 融資：margin_hist margin_bal（張）
資訊用，不構成投資建議。"""
import sys, json, argparse
from pathlib import Path
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument('repo', nargs='?', default='.')
ap.add_argument('--end'); ap.add_argument('--prev'); ap.add_argument('--json')
a = ap.parse_args()
R = Path(a.repo)
X = R / 'data' / 'extras'
h = pd.read_csv(R / 'data' / 'history.csv.gz', dtype={'code': str})
h = h[h.code.str.fullmatch(r'[1-9]\d{3}')]
C = h.pivot(index='date', columns='code', values='close').sort_index()
H = h.pivot(index='date', columns='code', values='high').reindex_like(C)
V = h.pivot(index='date', columns='code', values='volume').reindex_like(C)
days = list(C.index)
end = a.end or days[-1]
if end not in days:
    end = [d for d in days if d <= end][-1]
if a.prev:
    prev = [d for d in days if d <= a.prev][-1]
else:
    iso = pd.Timestamp(end).isocalendar()
    monday = (pd.Timestamp(end) - pd.Timedelta(days=int(iso[2]) - 1)).strftime('%Y-%m-%d')
    prev = [d for d in days if d < monday][-1]
wk = [d for d in days if prev < d <= end]
sl = pd.read_csv(R / 'data' / 'stock_list.csv', dtype={'code': str}).set_index('code')
name = sl.name.to_dict(); ind = sl.industry.to_dict()

# 調整因子（區間內除權息、減資）
fac = pd.Series(1.0, index=C.columns)
for f in ('exdiv.csv', 'corp_actions.csv'):
    p = X / f
    if p.exists():
        e = pd.read_csv(p, dtype={'code': str})
        e = e[(e.date > prev) & (e.date <= end) & e.code.isin(C.columns)]
        for r in e.itertuples():
            if pd.notna(r.factor) and r.factor > 0:
                fac[r.code] *= float(r.factor)
ret = (C.loc[end] * fac / C.loc[prev] - 1) * 100
v20 = V.loc[:prev].tail(20).mean()
liq = ret.index[(v20 >= 100_000) & ret.notna()]
rl = ret[liq]
out = {'prev': prev, 'end': end, 'days': wk, 'n_liquid': int(len(liq))}

# 0050、等權
bench = pd.read_csv(X / 'bench.csv', dtype={'code': str})
b = bench[bench.code == '0050'].set_index('date')
if prev in b.index and end in b.index:
    out['r0050_tr'] = round(float((b.tr[end] / b.tr[prev] - 1) * 100), 2)
out['ew_liquid'] = round(float(rl.mean()), 2)
out['median_liquid'] = round(float(rl.median()), 2)
out['up_down_flat_liquid'] = [int((rl > 0).sum()), int((rl < 0).sum()), int((rl == 0).sum())]

# breadth
br = pd.read_csv(X / 'breadth.csv').set_index('date')
cols = [c for c in ('above_ma20', 'up_pct', 'limit_up', 'limit_down', 'mkt20', 'n_liquid') if c in br.columns]
out['breadth_prev'] = {c: (float(br.loc[prev, c]) if prev in br.index else None) for c in cols}
out['breadth_days'] = {d: {c: float(br.loc[d, c]) for c in cols} for d in wk if d in br.index}
out['limit_up_sum'] = int(sum(v['limit_up'] for v in out['breadth_days'].values()))
out['limit_down_sum'] = int(sum(v['limit_down'] for v in out['breadth_days'].values()))

# 60 日新高
hi60 = H.shift(1).rolling(60, min_periods=60).max()
nh = (C > hi60) & C.notna()
out['new_high60'] = {d: int(nh.loc[d].sum()) for d in wk}

# 融資
m = pd.read_csv(X / 'margin_hist.csv.gz', dtype={'code': str})
mt = m.groupby('date').margin_bal.sum()
if prev in mt.index and end in mt.index:
    out['margin_total_lots'] = [float(mt[prev]), float(mt[end]), round(float(mt[end] - mt[prev])), round(float((mt[end] / mt[prev] - 1) * 100), 2)]
mp = m[m.date == prev].set_index('code').margin_bal; me = m[m.date == end].set_index('code').margin_bal
dm = (me - mp).dropna()
dm = dm[dm.index.isin(liq)]
out['margin_up_top10'] = [[c, name.get(c, ''), int(dm[c]), round(float(ret[c]), 2)] for c in dm.sort_values(ascending=False).index[:10]]
out['margin_down_top10'] = [[c, name.get(c, ''), int(dm[c]), round(float(ret[c]), 2)] for c in dm.sort_values().index[:10]]

# 產業（流動性股票、≥5 檔）
g = pd.DataFrame({'ret': rl, 'ind': [ind.get(c) for c in liq]}).dropna()
ig = g.groupby('ind').ret.agg(['mean', 'median', 'size'])
ig = ig[ig['size'] >= 5].sort_values('mean', ascending=False)
def best(i, k=2, asc=False):
    x = g[g.ind == i].ret.sort_values(ascending=asc).head(k)
    return [f"{name.get(c, c)} {v:+.1f}%" for c, v in x.items()]
out['industry_top5'] = [[i, round(r['mean'], 2), round(r['median'], 2), int(r['size']), best(i)] for i, r in ig.head(5).iterrows()]
out['industry_bottom5'] = [[i, round(r['mean'], 2), round(r['median'], 2), int(r['size']), best(i, asc=True)] for i, r in ig.tail(5).iloc[::-1].iterrows()]

# 題材（themes.yaml，全部成員）
try:
    import yaml
    th = yaml.safe_load(open(R / 'themes.yaml', encoding='utf-8')) or {}
except Exception as ex:  # noqa: BLE001
    th = {}; out['themes_error'] = str(ex)
tl = []
for t, codes in th.items():
    cs = [str(c) for c in codes if str(c) in ret.index and pd.notna(ret[str(c)])]
    if cs:
        x = ret[cs]
        tl.append([t, round(float(x.mean()), 2), int(len(cs)), [f"{name.get(c, c)} {ret[c]:+.1f}%" for c in x.sort_values(ascending=False).index]])
out['themes'] = sorted(tl, key=lambda r: -r[1])

# 成交值前 30
tv = (C.loc[wk] * V.loc[wk]).sum()
top = tv.sort_values(ascending=False).index[:30]
out['turnover_top30'] = [[c, name.get(c, ''), round(float(tv[c]) / 1e8, 1), round(float(ret[c]), 2)] for c in top]
t30 = ret[top]
out['turnover_top30_summary'] = {'up': int((t30 > 0).sum()), 'down': int((t30 < 0).sum()), 'mean': round(float(t30.mean()), 2), 'median': round(float(t30.median()), 2)}

# 法人
ih = pd.read_csv(X / 'inst_hist.csv.gz', dtype={'code': str})
iw = ih[ih.date.isin(wk) & ih.code.isin(C.columns)].copy()
iw['px'] = [C.at[d, c] for d, c in zip(iw.date, iw.code)]
for col in ('foreign', 'trust'):
    s = iw.groupby('code')[col].sum()
    amt = (iw[col] * 1000 * iw.px).groupby(iw.code).sum()
    out[f'{col}_total_amt_yi'] = round(float(amt.sum()) / 1e8, 1)
    out[f'{col}_buy_top10'] = [[c, name.get(c, ''), int(round(s[c])), round(float(amt[c]) / 1e8, 2), round(float(ret.get(c, np.nan)), 2)] for c in amt.sort_values(ascending=False).index[:10]]
    out[f'{col}_sell_top10'] = [[c, name.get(c, ''), int(round(s[c])), round(float(amt[c]) / 1e8, 2), round(float(ret.get(c, np.nan)), 2)] for c in amt.sort_values().index[:10]]
out['inst_days_present'] = sorted(iw.date.unique().tolist())

# markdown
L = [f"### 市場數字 {prev}（收）→ {end}（收），{len(wk)} 個交易日",
     f"- 0050 含息 {out.get('r0050_tr', 'NA'):+}%；流動性股票（{out['n_liquid']} 檔）等權 {out['ew_liquid']:+}%、中位 {out['median_liquid']:+}%，漲／跌／平 {out['up_down_flat_liquid']}",
     f"- breadth.csv above_ma20：{out['breadth_prev'].get('above_ma20')}% → {list(out['breadth_days'].values())[-1]['above_ma20'] if out['breadth_days'] else 'NA'}%；mkt20 {list(out['breadth_days'].values())[-1].get('mkt20') if out['breadth_days'] else 'NA'}",
     f"- 收盤漲停／跌停家數（每日）：" + '、'.join(f"{d[5:]} {int(v['limit_up'])}／{int(v['limit_down'])}" for d, v in out['breadth_days'].items()),
     f"- 60 日新高家數：" + '、'.join(f"{d[5:]} {n}" for d, n in out['new_high60'].items()),
     f"- 融資餘額（張）：{out.get('margin_total_lots')}",
     f"- 外資本週估計 {out['foreign_total_amt_yi']:+} 億、投信 {out['trust_total_amt_yi']:+} 億（張數×當日收盤估算；有資料的日子 {out['inst_days_present']}）",
     "", "| 產業前 5（等權） | 平均 | 中位 | 檔數 | 領頭 |", "|---|---:|---:|---:|---|"]
L += [f"| {r[0]} | {r[1]:+.2f}% | {r[2]:+.2f}% | {r[3]} | {'、'.join(r[4])} |" for r in out['industry_top5']]
L += ["", "| 產業後 5（等權） | 平均 | 中位 | 檔數 | 最弱 |", "|---|---:|---:|---:|---|"]
L += [f"| {r[0]} | {r[1]:+.2f}% | {r[2]:+.2f}% | {r[3]} | {'、'.join(r[4])} |" for r in out['industry_bottom5']]
L += ["", "| 題材（themes.yaml） | 等權 | 檔數 |", "|---|---:|---:|"]
L += [f"| {r[0]} | {r[1]:+.2f}% | {r[2]} |" for r in out['themes']]
L += ["", f"成交值前 30：漲 {out['turnover_top30_summary']['up']}、跌 {out['turnover_top30_summary']['down']}，平均 {out['turnover_top30_summary']['mean']:+}%", "",
      "| 代號 | 名稱 | 成交值(億) | 週漲跌 |", "|---|---|---:|---:|"]
L += [f"| {r[0]} | {r[1]} | {r[2]:,} | {r[3]:+.2f}% |" for r in out['turnover_top30']]
for col, lab in (('foreign', '外資'), ('trust', '投信')):
    L += ["", f"| {lab}買超前 10 | 張 | 金額(億) | 週漲跌 |", "|---|---:|---:|---:|"]
    L += [f"| {r[1]}（{r[0]}） | {r[2]:,} | {r[3]:+.2f} | {r[4]:+.2f}% |" for r in out[f'{col}_buy_top10']]
    L += ["", f"| {lab}賣超前 10 | 張 | 金額(億) | 週漲跌 |", "|---|---:|---:|---:|"]
    L += [f"| {r[1]}（{r[0]}） | {r[2]:,} | {r[3]:+.2f} | {r[4]:+.2f}% |" for r in out[f'{col}_sell_top10']]
print('\n'.join(L))
if a.json:
    json.dump(out, open(a.json, 'w'), ensure_ascii=False, indent=1)
