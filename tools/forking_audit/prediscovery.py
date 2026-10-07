"""發現前樣本外（Cowork 0226-oos-flip）：8 招在 2021-12～2025-09-09（data/history.csv 那一年以前）重判。
用法：python tools/forking_audit/prediscovery.py <bt.csv.gz> <out.json> [extra]
訊號日收盤買 vs 隔天開盤買，20 日，減同日同流動性五分位等權，扣 0.38；出場也在發現窗之前。extra＝強勢創新高扣漲停／依漲幅分。"""
import sys, json
import numpy as np, pandas as pd, yaml
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools' / 'strat5y'))
from screener import rules
from run import nw_t, wtrim
BT = sys.argv[1]; H = 20; COST = 0.38; END = '2025-09-09'
bt = pd.read_csv(BT, dtype={'code': str}); bt = bt[bt.code.str.fullmatch(r'[1-9]\d{3}')]
cfg = yaml.safe_load(open(ROOT / 'config.yaml', encoding='utf-8'))
p = rules.Panel(bt)
def ev(c):
    m = rules.CONDITIONS[c['type']][0](p, c).fillna(False).astype(bool)
    if c.get('days_ago'): m = m.shift(int(c['days_ago']), fill_value=False)
    if c.get('within'): m = m.astype(int).rolling(int(c['within']), min_periods=1).max().astype(bool)
    return ~m if c.get('not') else m
base = p.traded.copy()
for c in cfg['base_filter']: base &= ev(c)
C = p.close.where(p.traded); nxo = p.open.shift(-1)
lu_open = nxo >= rules.limit_price(p.close, True) - 1e-9
idx = p.close.index; pos = {d: i for i, d in enumerate(idx)}
last_sig = idx[pos[idx[idx <= END][-1]] - H]   # 出場也要在發現窗之前
print('驗證期訊號日', idx[61], '～', last_sig)
univ = p.traded
dv = (p.close * p.volume).rolling(20, min_periods=20).mean().shift(1).where(univ)
qn = np.ceil(dv.rank(axis=1, pct=True) * 5).clip(1, 5)
fwd = {'close': (C.shift(-H) / C - 1).where(lambda r: r.abs() < 3), 'next_open': (C.shift(-H) / nxo - 1).where(lambda r: r.abs() < 3)}
ewq = {k: pd.DataFrame({q: r.where(univ).where(qn == q).mean(axis=1) for q in range(1, 6)}) for k, r in fwd.items()}
# 0050 多頭日：站上 60 日線且 60 日報酬 >+10%
b = pd.read_csv(ROOT / 'data' / 'extras' / 'bench_long.csv', dtype={'code': str}); b = b[b.code == '0050'].set_index('date').close_adj
bull = ((b > b.rolling(60).mean()) & (b / b.shift(60) - 1 > 0.10)).reindex(idx).fillna(False)
# 鎖漲停近似（Cowork 附帶 i 的替代）：漲幅 ≥9.5% 且收＝高
pc = p.close.shift(1)
lock95 = p.traded & (p.close / pc - 1 >= 0.095) & (p.close >= p.high - 1e-9) & (p.close / pc - 1 <= 0.105)
strats = [(s['name'], s.get('conditions', [])) for s in cfg['strategies'] if s.get('enabled', True)]
res = {}
def stats(name, m):
    m = m.copy(); m.iloc[:61] = False; m.loc[m.index > last_sig] = False
    st = m.stack(); ii = st[st].index; d_all = ii.get_level_values(0)
    q = qn.stack().reindex(ii).to_numpy(); qi = np.nan_to_num(q, nan=0).astype(int)
    ri = np.array([pos[x] for x in d_all]); lo = lu_open.stack().reindex(ii).fillna(False).to_numpy()
    out = {}
    for k in ('close', 'next_open'):
        raw = fwd[k].stack().reindex(ii).to_numpy()
        bq = np.where(qi > 0, ewq[k].to_numpy()[ri, np.clip(qi - 1, 0, ewq[k].shape[1] - 1)], np.nan)
        ex = (raw - bq) * 100 - COST
        ok = np.isfinite(ex) & (~lo if k == 'next_open' else True)
        v, d = ex[ok], d_all[ok]
        dd = pd.Series(v, index=d).groupby(level=0).mean()
        yr = pd.Series(v, index=d.str[:4]).groupby(level=0).agg(['mean', 'size']); yr = yr[yr['size'] >= 80]
        mo = pd.Series(v, index=d.str[:7]).groupby(level=0).mean()
        conc = pd.Series(1, index=d).groupby(level=0).size().reindex(idx[61:pos[last_sig] + 1], fill_value=0).rolling(H, min_periods=1).sum()
        # 安慰劑 trim95
        cells = pd.Series(1, index=pd.MultiIndex.from_arrays([d, q[ok]])).groupby(level=[0, 1]).size()
        R = fwd[k].where(univ); pv, pw = [], []
        for (day, qq), cnt in cells.items():
            if not np.isfinite(qq): continue
            rr = R.loc[day][(qn.loc[day] == qq).to_numpy()].dropna().to_numpy()
            if len(rr): pv.append((rr - ewq[k].at[day, int(qq)]) * 100 - COST); pw.append(np.full(len(rr), cnt / len(rr)))
        trim = float(np.sort(v)[: int(len(v) * .95)].mean())
        plc = wtrim(np.concatenate(pv), np.concatenate(pw)) if pv else float('nan')
        bm = bull.reindex(d).fillna(False).to_numpy().astype(bool)
        out[k] = dict(n=int(len(v)), days=int(dd.size), conc_med=float(conc[conc > 0].median()) if (conc > 0).any() else 0,
                      mean=round(float(v.mean()), 2), med=round(float(np.median(v)), 2), t_nw=round(nw_t(dd, H - 1), 2),
                      years={y: [round(float(a), 2), int(b_)] for y, (a, b_) in yr.iterrows()},
                      pos_years=[int((yr['mean'] > 0).sum()), int(len(yr))],
                      drop3=round(float(mo.sort_values().iloc[:-3].mean()), 2), trim_vs_plc=round(trim - plc, 2),
                      bull=[round(float(v[bm].mean()), 2) if bm.any() else None, int(bm.sum())],
                      nonbull=[round(float(v[~bm].mean()), 2) if (~bm).any() else None, int((~bm).sum())])
    res[name] = out
    for k, r in out.items():
        print(f"{name[:12]:<12} {k:<9} N={r['n']:>6} 日={r['days']:>4} 同持中位={r['conc_med']:.0f} 平均={r['mean']:+.2f} 中位={r['med']:+.2f} NWt={r['t_nw']:+.2f} 年正={r['pos_years']} 去前3月={r['drop3']:+.2f} 截−安慰={r['trim_vs_plc']:+.2f} 多頭日={r['bull']} 其他={r['nonbull']} 年={r['years']}", flush=True)
if len(sys.argv) < 4:
    for name, conds in strats:
        m = base.copy()
        for c in conds: m &= ev(c)
        stats(name, m)
    stats('鎖漲停近似（≥9.5%且收＝高）', base & lock95)
elif sys.argv[3] in ('mom', 'hot'):
    # Cowork 0327-cw-talk-mommatch：基準改『同日 同流動性五分位 × 過去 20 日報酬五分位』25 格等權
    lp = rules.limit_price(pc, True)
    lock = base & (p.close >= lp - 1e-6) & (p.close / pc - 1 <= 0.105)
    stats('原基準：鎖漲停收盤買', lock)
    r20p = (p.close / p.close.shift(20)).where(univ)
    mq = np.ceil(r20p.rank(axis=1, pct=True) * 5).clip(1, 5)
    cell = (qn - 1) * 5 + mq            # 1..25
    qn = cell
    for k in fwd:
        ewq[k] = pd.DataFrame({q: fwd[k].where(univ).where(cell == q).mean(axis=1) for q in range(1, 26)})
    stats('雙排序基準：鎖漲停收盤買', lock)
    stats('雙排序基準：鎖漲停扣一字', lock & ~(lock & (p.open >= lp - 1e-6) & (p.low >= lp - 1e-6)))
    if sys.argv[3] == 'hot':
        # Cowork 0356-cw-talk-hotday：逐年每筆等權 vs 每訊號日先平均；訊號日按當天鎖漲停家數三分位（冷／溫／熱）
        m = lock.copy(); m.iloc[:61] = False; m.loc[m.index > last_sig] = False
        st = m.stack(); ii = st[st].index; d_all = ii.get_level_values(0)
        q = qn.stack().reindex(ii).to_numpy(); qi = np.nan_to_num(q, nan=0).astype(int)
        ri = np.array([pos[x] for x in d_all])
        raw = fwd['close'].stack().reindex(ii).to_numpy()
        bq = np.where(qi > 0, ewq['close'].to_numpy()[ri, np.clip(qi - 1, 0, 24)], np.nan)
        ex = (raw - bq) * 100 - COST
        ok = np.isfinite(ex); v, d = ex[ok], d_all[ok]
        S = pd.Series(v, index=d)
        dm = S.groupby(level=0).mean()
        print('逐年：N／訊號日／每筆等權／每日先平均再等權')
        for y in sorted(set(d.str[:4])):
            sy = S[S.index.str[:4] == y]; dy = dm[dm.index.str[:4] == y]
            print(f"  {y}: N={len(sy)} 日={len(dy)} 每筆 {sy.mean():+.2f} 日等權 {dy.mean():+.2f}")
        print(f"全期：每筆 {S.mean():+.2f}、日等權 {dm.mean():+.2f}（NW t {nw_t(dm, H - 1):+.2f}）")
        cnt = lock.sum(axis=1)                       # 當天全市場鎖漲停家數（含不在名單的也算，用 base 範圍）
        cd = cnt.reindex(dm.index)
        t1, t2 = np.quantile(cd, [1 / 3, 2 / 3])
        lab = pd.Series(np.where(cd <= t1, '冷', np.where(cd <= t2, '溫', '熱')), index=dm.index)
        tot = S.sum()
        for b in ['冷', '溫', '熱']:
            days_b = lab[lab == b].index
            sb = S[S.index.isin(days_b)]; db = dm[dm.index.isin(days_b)]
            print(f"  {b}（當天鎖漲停 {int(cd[days_b].min())}～{int(cd[days_b].max())} 家）：N={len(sb)} 日={len(db)} 平均 {sb.mean():+.2f} 中位 {sb.median():+.2f} 日等權 {db.mean():+.2f} NW t {nw_t(db.reindex(dm.index).dropna(), H - 1):+.2f} 占總超額 {sb.sum() / tot:.0%}")
elif sys.argv[3] == 'fill':
    # Cowork 0255-cw-fillmodel 上下界＋0256-cw-talk-luliq 流動性拆格
    lp = rules.limit_price(pc, True)
    touch = base & (p.high >= lp - 1e-6) & (p.close / pc - 1 <= 0.105)
    lock = touch & (p.close >= lp - 1e-6)
    unlock = touch & ~lock
    one = lock & (p.open >= lp - 1e-6) & (p.low >= lp - 1e-6)
    print('觸及', int(touch.sum().sum()), '鎖', int(lock.sum().sum()), '沒鎖', int(unlock.sum().sum()), '一字', int(one.sum().sum()))
    stats('上界：收盤鎖漲停全成交', lock)
    stats('悲觀界：觸及沒鎖、收盤買', unlock)
    stats('鎖漲停扣一字', lock & ~one)
    for q in range(1, 6):
        stats(f'鎖漲停 流動性Q{q}', lock & (qn == q))
        stats(f'鎖漲停 流動性Q{q} 一字', lock & one & (qn == q))
    t95 = base & (p.high / pc - 1 >= 0.095) & (p.close / pc - 1 <= 0.105)
    l95 = t95 & (p.close >= p.high - 1e-9) & (p.close / pc - 1 >= 0.095)
    stats('≥9.5% 悲觀界：觸及沒鎖', t95 & ~l95)
else:
    lu = ev({'type': 'limit_up'})
    chg = p.close / pc - 1
    for name, conds in strats:
        if name != '強勢創新高': continue
        m = base.copy()
        for c in conds: m &= ev(c)
        stats(name + '｜扣漲停', m & ~lu & ~lock95)
        stats(name + '｜漲幅<7%', m & (chg < 0.07))
        stats(name + '｜漲幅<3%', m & (chg < 0.03))
json.dump(res, open(sys.argv[2], 'w'), ensure_ascii=False, indent=1)
