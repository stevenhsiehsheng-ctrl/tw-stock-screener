"""每日篩選各策略的 5 年回測成績（含下市股），寫 data/extras/strat_5y.json 給每日篩選頁顯示。

用法：python tools/strat5y/run.py <backtest.csv.gz> [--split 2025-09-10]  台股（config.yaml strategies；--split 另印發現前／發現窗）
      python tools/strat5y/run.py <us_bt.csv.gz> --market us  美股（config.yaml us_screener，寫 data/usx/strat_5y.json；
                                                              股票池是現在的成分股，有存活者偏差；沒有漲跌停，不剔除）
口徑（暫定，Cowork 大題 A 定案後改）：
- 訊號：config.yaml 的 base_filter＋各策略條件，跟每天篩選同一套程式（rules.CONDITIONS），對 5 年每一天算
- 主口徑 next_open：名單收盤後才出來，所以「隔天開盤買」，持有到第 h 個交易日收盤；隔天一開盤就漲停（買不到）的剔除
- 參考口徑 close：訊號當天收盤買（盤中 13:12 那條路才做得到），一樣比同流動性、過四關；low＝悲觀界（收盤鎖漲停的算買不到）
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


def nw_t(x: pd.Series, lag: int) -> float:
    """Newey-West t（日序列有重疊持有期時用；lag＝持有天數−1）。"""
    x = x.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    v = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(v / n)) if v > 0 else float("nan")


def wtrim(vals: np.ndarray, w: np.ndarray, keep: float = 0.95) -> float:
    """加權後砍掉最好的 (1−keep) 那段的平均。"""
    o = np.argsort(vals)
    v, ww = vals[o], w[o]
    c = np.cumsum(ww)
    m = c <= keep * c[-1]
    return float(np.average(v[m], weights=ww[m])) if m.any() else float("nan")


def us_members(bt_path: str, index: pd.Index, columns: pd.Index, mode: str) -> pd.DataFrame | None:
    """美股成分股遮罩（Cowork 大題 E）：pit＝每天『當時』的 S&P 500（含後來被踢出的）；fixed＝起點那天的 S&P 500 固定不動。
    用現在名單＋維基百科成分變動表（sp500_changes.csv）往回推。"""
    d = Path(bt_path).parent
    uni = pd.read_csv(d / "universe.csv", dtype=str).fillna("")
    ch = pd.read_csv(d / "sp500_changes.csv", dtype=str).fillna("").sort_values("date", ascending=False)
    cur = set(uni[uni["group"].str.contains("SP500")].code)
    rows, k, evs = {}, 0, list(ch.itertuples())
    for day in sorted(index, reverse=True):
        while k < len(evs) and evs[k].date > day:   # 變動生效日之前：新加入的還不是、被移出的還是
            cur.discard(evs[k].added)
            if evs[k].removed:
                cur.add(evs[k].removed)
            k += 1
        rows[day] = set(cur)
    if mode == "fixed":
        first = rows[index[0]]
        rows = {day: first for day in index}
    return pd.DataFrame({c: [c in rows[day] for day in index] for c in columns}, index=index)


def main(bt_path: str, market: str = "tw", members: str = "", cost_override: float | None = None, out: str = "",
         split: str = "") -> None:
    bt = pd.read_csv(bt_path, dtype={"code": str})
    full = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    if market == "us":
        cfg, cost, dst = full["us_screener"], float(full["us_screener"].get("cost_pct", 0.3)), ROOT / "data" / "usx" / "strat_5y.json"
        etf = set(pd.read_csv(Path(bt_path).with_name("universe.csv"), dtype=str).query("`group` == 'ETF'").code) \
            if Path(bt_path).with_name("universe.csv").exists() else set()
        bt = bt[~bt.code.isin(etf)]      # ETF 不進選股池（跟篩選頁一樣只看個股）
    else:
        cfg, cost, dst = full, COST, ROOT / "data" / "extras" / "strat_5y.json"
        bt = bt[bt.code.str.fullmatch(r"[1-9]\d{3}")]
    if cost_override is not None:
        cost = cost_override
    if out:
        dst = Path(out)
    p = rules.Panel(bt)
    mem = us_members(bt_path, p.close.index, p.close.columns, members) if market == "us" and members else None
    if mem is not None:
        print(f"成分股遮罩 {members}：每天平均 {mem.sum(axis=1).mean():.0f} 檔、有價格的 {int(mem.any().sum())} 檔")

    def ev(cond):
        m = rules.CONDITIONS[cond["type"]][0](p, cond).fillna(False).astype(bool)
        if cond.get("days_ago"):
            m = m.shift(int(cond["days_ago"]), fill_value=False)
        if cond.get("within"):
            m = m.astype(int).rolling(int(cond["within"]), min_periods=1).max().astype(bool)
        return ~m if cond.get("not") else m

    base = p.traded.copy() if mem is None else (p.traded & mem)
    for c in cfg["base_filter"]:
        base &= ev(c)
    C = p.close.where(p.traded)
    nxo = p.open.shift(-1)
    if market != "tw":
        lu_open = pd.DataFrame(False, index=nxo.index, columns=nxo.columns)
    elif p.adjusted:   # 還原價：隔天開盤漲幅 ≥9.5% 就當一開盤漲停（買不到）
        lu_open = (nxo / p.close - 1 >= 0.095)
    else:
        lu_open = (nxo >= rules.limit_price(p.close, True) - 1e-9)
    entry = {"next_open": nxo, "close": C}
    fwd = {(k, h): (C.shift(-h) / e - 1).where(lambda r: r.abs() < 3) for k, e in entry.items() for h in H}
    ew = {k: (r if mem is None else r.where(mem)).mean(axis=1) for k, r in fwd.items()}   # 比同一天的成分股等權（並列，不判章）
    # 判章基準（Cowork 0057／0059）：同日同流動性五分位等權；流動性＝D−1 以前 20 日均成交金額（不含訊號當天的爆量）
    univ = p.traded if mem is None else (p.traded & mem)
    dv = (p.close * p.volume).rolling(20, min_periods=20).mean().shift(1).where(univ)
    qn = np.ceil(dv.rank(axis=1, pct=True) * 5).clip(1, 5)
    ewq = {}
    for kh, r in fwd.items():
        rr = r.where(univ)
        ewq[kh] = pd.DataFrame({q: rr.where(qn == q).mean(axis=1) for q in range(1, 6)})
    days_idx = {d: i for i, d in enumerate(p.close.index)}
    E_np = {kh: E.to_numpy() for kh, E in ewq.items()}
    # 發現前／發現窗（Cowork 0226-oos-flip）：8 招是在 2025-09-10 起那一年看出來的；發現前＝出場也在 SPLIT 之前的訊號
    di = p.close.index
    pre_end = {h: (di[max(0, int((di < split).sum()) - 1 - h)] if split else None) for h in H}
    # 收盤買的悲觀界（Cowork 0255-cw-fillmodel）：收盤鎖漲停的算買不到
    if market == "tw":
        locked = p.at_limit(True)                       # 還原價自動走漲幅近似（分身 0415）
        touched = p.touch_limit(True) & ~locked
    else:
        locked = touched = pd.DataFrame(False, index=p.close.index, columns=p.close.columns)

    def metrics(k, h, idx, d_all, q_sig, drop, full=True):
        """一組訊號（idx）在進場口徑 k、持有 h 日的成績；drop＝要剔除的布林陣列。"""
        raw = fwd[(k, h)].stack().reindex(idx).to_numpy()
        ex_ew = (raw - ew[(k, h)].reindex(d_all).to_numpy()) * 100 - cost
        ri = np.array([days_idx[x] for x in d_all], dtype=int)
        qi = np.nan_to_num(q_sig, nan=0).astype(int)
        bq = np.where(qi > 0, E_np[(k, h)][ri, np.clip(qi - 1, 0, 4)], np.nan) if len(ri) else np.array([])
        ex = (raw - bq) * 100 - cost
        ok = np.isfinite(ex) & ~drop
        v, d = ex[ok], d_all[ok]
        if not len(v):
            return None
        dd = pd.Series(v, index=d).groupby(level=0).mean()
        ve = ex_ew[ok & np.isfinite(ex_ew)]
        r = {"n": int(len(v)), "ex": round(float(v.mean()), 2), "med": round(float(np.median(v)), 2),
             "ex_ew": round(float(ve.mean()), 2) if len(ve) else None,
             "trim95": round(float(np.sort(v)[: int(len(v) * 0.95)].mean()), 2),
             "win": round(float((v > 0).mean() * 100), 1),
             "t": round(float(dd.mean() / (dd.std(ddof=1) / len(dd) ** .5)), 2) if len(dd) > 2 else None}
        if h in (5, 20):
            yr = pd.Series(v, index=d.str[:4]).groupby(level=0).agg(["mean", "size"])
            yr = yr[yr["size"] >= 80]                           # N<80 的年份不計
            r["years"] = {y: [round(float(a), 2), int(b)] for y, (a, b) in yr.iterrows()}
            r["pos_years"] = [int((yr["mean"] > 0).sum()), int(len(yr))]
            r["t_nw"] = round(nw_t(dd, h - 1), 2)
            mo = pd.Series(v, index=d.str[:7]).groupby(level=0).mean()
            r["mo_drop3"] = round(float(mo.sort_values().iloc[:-3].mean()), 2) if len(mo) > 6 else None
        if full and h in (5, 20):
            # 安慰劑：同日同流動性五分位的全部股票（權重照策略在每格的筆數），同進出場、同成本
            cells = pd.Series(1, index=pd.MultiIndex.from_arrays([d, q_sig[ok]])).groupby(level=[0, 1]).size()
            Rv, Ev = fwd[(k, h)].where(univ), ewq[(k, h)]
            pv, pw = [], []
            for (day, q), cnt in cells.items():
                if not np.isfinite(q):
                    continue
                row_r = Rv.loc[day][(qn.loc[day] == q).to_numpy()].dropna().to_numpy()
                if len(row_r):
                    pv.append((row_r - Ev.at[day, int(q)]) * 100 - cost)
                    pw.append(np.full(len(row_r), cnt / len(row_r)))
            if pv:
                pv, pw = np.concatenate(pv), np.concatenate(pw)
                r["placebo_trim95"] = round(wtrim(pv, pw), 2)
                r["trim_vs_placebo"] = round(float(r["trim95"] - r["placebo_trim95"]), 2)
            if h == 20:   # 判章四關（Cowork 0125-cw-todo-nwgate）
                g = {"a_years": bool(r["pos_years"][1] >= 4 and r["pos_years"][0] >= 4),
                     "b_drop3": bool(r["mo_drop3"] is not None and r["mo_drop3"] > 0),
                     "c_nw": bool(np.isfinite(r["t_nw"]) and r["t_nw"] >= 2),
                     "d_trim": bool(r.get("trim_vs_placebo") is not None and r["trim_vs_placebo"] >= 0.5),
                     "mean": bool(r["ex"] >= 0.5)}
                r["gates"] = g
                r["pass"] = all(g.values())
        if split and h == 20:
            seg = {}
            for nm, mk in (("pre", d <= pre_end[h]), ("post", d >= split)):
                vv, dd2 = v[mk], d[mk]
                if len(vv):
                    s2 = pd.Series(vv, index=dd2).groupby(level=0).mean()
                    yr2 = pd.Series(vv, index=dd2.str[:4]).groupby(level=0).agg(["mean", "size"])
                    yr2 = yr2[yr2["size"] >= 80]
                    seg[nm] = {"n": int(len(vv)), "ex": round(float(vv.mean()), 2), "med": round(float(np.median(vv)), 2),
                               "t_nw": round(nw_t(s2, h - 1), 2), "pos_years": [int((yr2["mean"] > 0).sum()), int(len(yr2))]}
            r["split"] = seg
        return r

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
        q_sig = qn.stack().reindex(idx).to_numpy()
        none = np.zeros(len(idx), dtype=bool)
        for k in entry:
            for h in H:
                r = metrics(k, h, idx, d_all, q_sig, lo if k == "next_open" else none)
                if r is not None:
                    row[f"{k}_{h}"] = r
        # 收盤買悲觀界：收盤鎖漲停的算沒買到；整招都是鎖漲停（漲停那招）就改用「摸到漲停、收盤沒鎖、用收盤價買」那批
        lk = locked.stack().reindex(idx).fillna(False).to_numpy()
        if lk.mean() < 0.95:
            low = metrics("close", 20, idx, d_all, q_sig, lk)
            how, share = "收盤鎖漲停的算買不到", 1 - lk.mean()
        else:
            m2 = base & touched
            m2.iloc[:61] = False
            st2 = m2.stack()
            idx2 = st2[st2].index
            low = metrics("close", 20, idx2, idx2.get_level_values(0), qn.stack().reindex(idx2).to_numpy(), np.zeros(len(idx2), dtype=bool))
            how, share = "鎖死的都買不到，只算摸到漲停、收盤沒鎖、用收盤價買的那批", len(idx2) / (len(idx2) + len(idx))
        if low is not None and "close_20" in row:
            low["how"], low["buyable_pct"] = how, round(float(share * 100), 1)
            row["close_20"]["low"] = low
        out.append(row)
    res = {"period": [p.close.index[61], p.close.index[-1]], "stocks": int(p.close.shape[1]), "cost": cost, "market": market,
           "bench": "同日同流動性五分位等權（D−1 以前 20 日均成交金額）；ex_ew＝全市場等權並列",
           "gate_rule": "20 日：平均 ≥+0.5、≥4/5 年正、拿掉最好 3 個月後月平均 >0、Newey-West t ≥2、截最好 5% 平均比同流動性安慰劑高 ≥0.5",
           "method": ("名單收盤後出來 → 隔天開盤買（一開盤就漲停的剔除），持有 h 個交易日收盤賣；超額＝減同日同流動性五分位等權、扣來回 0.38%；含下市股"
                      if market == "tw" else f"名單收盤後出來 → 隔天開盤買，持有 h 個交易日收盤賣；超額＝減同時點選股池等權、扣來回 {cost}%；現在的成分股（存活者偏差）"),
           "split": split or None,
           "close_note": "收盤買＝訊號當天收盤買（盤中 13:12 那條路）；low＝悲觀界，收盤鎖漲停的算買不到（Cowork 0255）",
           "strategies": out}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    for r in out:
        a, b = r["next_open_5"], r["next_open_20"]
        print(f"{r['name']:<16} n={r['n']:>6}  隔天開盤買 5日 {a['ex']:+.2f}（勝 {a['win']:.0f}%，正 {a['pos_years'][0]}/{a['pos_years'][1]} 年）"
              f" 20日 {b['ex']:+.2f}（全市場 {b['ex_ew']:+.2f}，NW t {b.get('t_nw', float('nan')):.1f}，去前3月 {b.get('mo_drop3')}，截後−安慰劑 {b.get('trim_vs_placebo')}）"
              f"{' ✅' if b.get('pass') else ''}")
        c = r.get("close_20")
        if c:
            lw = c.get("low") or {}
            sp = c.get("split", {})
            print(f"{'':<16} 收盤買 20日 {c['ex']:+.2f}（NW t {c.get('t_nw')}，{'過' if c.get('pass') else '沒過'}）"
                  f" 悲觀界 {lw.get('ex', float('nan')):+.2f}（買得到 {lw.get('buyable_pct')}%，{'過' if lw.get('pass') else '沒過'}）"
                  f" 發現前 {sp.get('pre', {}).get('ex')} 發現窗 {sp.get('post', {}).get('ex')}")
    print("寫入", dst)


if __name__ == "__main__":
    a = sys.argv
    opt = lambda k: a[a.index(k) + 1] if k in a else None  # noqa: E731
    main(a[1], "us" if opt("--market") == "us" else "tw", members=opt("--members") or "",
         cost_override=float(opt("--cost")) if opt("--cost") else None, out=opt("--out") or "",
         split=opt("--split") or "")
