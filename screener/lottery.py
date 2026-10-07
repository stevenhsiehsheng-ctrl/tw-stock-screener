"""回測共用檢定：「彩券檢定」＋配對安慰劑＋按月整塊 bootstrap（協作板 10/6 定案的口徑）。

用法（每一筆交易一列，報酬單位是 %，已扣成本、已減基準）：

    from screener import lottery
    trades = lottery.surge_trades(hist)                  # 地基：爆量突破新高收盤進、量縮一半隔天開盤出
    pl = lottery.placebo(close, trades, n=300, exit_px=open_, volume=vol, match="liquidity")   # 同日同流動性五分位抽
    print(lottery.report(trades, placebo=pl))

trades 欄位：entry（進場日 YYYY-MM-DD）、exit（出場日）、ret（超額報酬 %）。
close 是寬表（index=日期字串、columns=代號），例如 history 的收盤價 pivot。

判定（Cowork 0726／分身 0815）：
  中位 − 安慰劑中位 ≤ 0，或 剔前5%平均 − 安慰劑剔前5%平均 ≤ 0 → 彩券組。
  沒給安慰劑就跟 0 比，並在結果註明「沒有安慰劑，偏向看衰」。
區間：平均超額的 5～95% 用「按月整塊」bootstrap（按日抽幾乎沒效果）。M < 12 標「未滿一年」。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST = 0.38   # 來回費稅 %（手續費 2.8 折＋證交稅 0.3%）


def _trim_top(x: np.ndarray, q: float = 0.05) -> float:
    """剔掉最高 q 比例後的平均（只用來檢驗正報酬是不是靠少數暴衝）。"""
    x = np.sort(x)
    k = int(np.floor(len(x) * q))
    return float(x[: len(x) - k].mean()) if len(x) - k > 0 else float("nan")


def _trim_both(x: np.ndarray, q: float = 0.05) -> float:
    """兩邊各剔 q（判斷負訊號用；單邊剔除一定往下偏）。"""
    x = np.sort(x)
    k = int(np.floor(len(x) * q))
    return float(x[k: len(x) - k].mean()) if len(x) - 2 * k > 0 else float("nan")


def _concurrency(entry: pd.Series, exit: pd.Series) -> tuple[float, float, int]:
    """同時持有筆數的中位、90 分位、最大（以進出場日之間的工作日計）。"""
    if exit is None or exit.isna().all():
        return (float("nan"),) * 2 + (0,)
    days = pd.bdate_range(entry.min(), exit.max())
    cnt = np.zeros(len(days), dtype=int)
    a = days.searchsorted(pd.to_datetime(entry))
    b = days.searchsorted(pd.to_datetime(exit))
    for i, j in zip(a, b):
        cnt[i:j] += 1   # 出場當天不算持有
    cnt = cnt[cnt > 0]
    if not len(cnt):
        return (float("nan"),) * 2 + (0,)
    return float(np.median(cnt)), float(np.percentile(cnt, 90)), int(cnt.max())


def block_boot(df: pd.DataFrame, ret: str = "ret", key: str = "ym", n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """平均報酬的 5～95% 區間，按 key（預設月份）整塊重抽。"""
    g = [v[ret].to_numpy(float) for _, v in df.groupby(key)]
    if len(g) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    sums = np.array([x.sum() for x in g])
    lens = np.array([len(x) for x in g])
    idx = rng.integers(0, len(g), size=(n, len(g)))
    means = sums[idx].sum(axis=1) / lens[idx].sum(axis=1)
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def liquidity_quintile(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """每天每檔的流動性五分位（1～5，5 最大）：進場日前 60 日平均成交值（close×volume，不含當天），至少 40 天。"""
    tv = (close * volume).shift(1).rolling(60, min_periods=40).mean()
    return tv.rank(axis=1, pct=True).mul(5).apply(np.ceil).clip(1, 5)


def placebo(close: pd.DataFrame, trades: pd.DataFrame, n: int = 300, cost: float = COST, seed: int = 0,
            exit_px: pd.DataFrame | None = None, volume: pd.DataFrame | None = None, match: str | None = None) -> pd.DataFrame:
    """配對安慰劑：每筆交易同一個進場日、出場日，從當天有成交的股票隨機抽一檔，
    報酬一樣減同段期間全市場等權、扣成本。回傳每次模擬的 median／trim_top／trim_both。
    exit_px：出場用的價格寬表（例如隔天開盤出場就傳 open），預設收盤。
    match='liquidity'：只從同日、同流動性五分位抽（要傳 volume，trades 要有 code 欄）。"""
    close = close.sort_index()
    xp = (exit_px if exit_px is not None else close).reindex_like(close)
    q = liquidity_quintile(close, volume.reindex_like(close)) if match == "liquidity" else None
    rng = np.random.default_rng(seed)
    keys, pool = [], {}
    for r in trades.itertuples():
        e, x = str(r.entry), str(r.exit)
        if e not in close.index or x not in close.index:
            continue
        g = None
        if q is not None:
            g = q.at[e, str(r.code)] if str(r.code) in q.columns else np.nan
            if pd.isna(g):
                continue
        k = (e, x, g)
        if k not in pool:
            ret = (xp.loc[x] / close.loc[e] - 1)
            ok = ret.notna() & (ret.abs() < 3)   # 沒還原的分割、減資會有離譜值
            ex = (ret - ret[ok].mean()) * 100 - cost
            if g is not None:
                ok &= q.loc[e] == g
            v = ex[ok].to_numpy()
            if not len(v):
                continue
            pool[k] = v
        keys.append(k)
    out = []
    for _ in range(n):
        v = np.array([pool[k][rng.integers(len(pool[k]))] for k in keys])
        out.append({"median": float(np.median(v)), "trim_top": _trim_top(v), "trim_both": _trim_both(v)})
    return pd.DataFrame(out)


def surge_trades(hist: pd.DataFrame, shrink: float = 0.5, max_hold: int = 20, cost: float = COST,
                 overlap: bool = False) -> pd.DataFrame:
    """地基交易清單（跟網站 K 線買賣點、positions.csv 同一套規則）：
    - 進場：爆量突破 60 日新高（tech.surge_frame）當天收盤買
    - 出場訊號：之後第一次收盤量 < 爆量日 × shrink（漲停日不算），或持有滿 max_hold 天；隔天開盤賣
    - overlap=False：同一檔持有中再出現訊號不重複進（跟 tech.signals 一樣）
    - ret：（出場開盤 ÷ 進場收盤 − 1）減同段期間全市場等權（進場收盤→出場開盤），再扣 cost，單位 %
    - lu：訊號日收在漲停價（Panel.at_limit：原始價用官方檔位，還原價用漲幅 ≥9.5% 且收＝最高）
    - lu95：近似漲停（漲幅 ≥9.5% 且收＝最高）給沒有檔位的還原價資料用
    回傳 entry、exit、code、ret、lu、lu95、hold（交易日數）。"""
    from . import tech
    from .rules import Panel
    p = Panel(hist)
    c, o, v, tr = p.close, p.open, p.volume, p.traded
    surge = tech.surge_frame(p)
    lu = p.at_limit(True)   # 原始價＝官方檔位；還原價自動走漲幅近似
    lu95 = (p.change_pct >= 9.5) & ((p.high - c).abs() <= 1e-6 * c)
    idx = list(c.index)
    ret_eq = {}
    rows = []
    for code in c.columns:
        s, l, vv, t = surge[code].values, lu[code].values, v[code].values, tr[code].values
        busy_until = -1
        for i in np.flatnonzero(s):
            if not overlap and i <= busy_until:
                continue
            j = None
            for k in range(i + 1, min(len(idx), i + max_hold + 1)):
                if t[k] and ((vv[k] < vv[i] * shrink and not l[k]) or k - i >= max_hold):
                    j = k
                    break
            if j is None or j + 1 >= len(idx):
                continue   # 還沒出場
            x = j + 1
            px_in, px_out = c[code].iat[i], o[code].iat[x]
            if not (px_in > 0 and px_out > 0):
                continue
            key = (i, x)
            if key not in ret_eq:
                r = o.iloc[x] / c.iloc[i] - 1
                ret_eq[key] = r[r.abs() < 3].mean()
            rows.append({"entry": idx[i], "exit": idx[x], "code": code,
                         "ret": ((px_out / px_in - 1) - ret_eq[key]) * 100 - cost,
                         "lu": bool(l[i]), "lu95": bool(lu95[code].iat[i]), "hold": x - i})
            busy_until = x
    return pd.DataFrame(rows, columns=["entry", "exit", "code", "ret", "lu", "lu95", "hold"]).sort_values(["entry", "code"], ignore_index=True)


def lottery_check(trades: pd.DataFrame, ret: str = "ret", placebo: pd.DataFrame | None = None, boot: int = 2000) -> dict:
    """彩券檢定：N／D／M、平均、中位、剔尾平均、正報酬月份、按月 bootstrap 區間、同時持有、判定。"""
    t = trades.dropna(subset=[ret]).copy()
    t["entry"] = t["entry"].astype(str)
    t["ym"] = t["entry"].str[:7]
    x = t[ret].to_numpy(float)
    by_m = t.groupby("ym")[ret].mean()
    total = x.sum()
    top = np.sort(x)[::-1][: max(1, int(np.floor(len(x) * 0.05)))]
    exit_ = t["exit"].astype(str) if "exit" in t else None
    cm, c90, cmax = _concurrency(t["entry"], exit_) if exit_ is not None else (float("nan"),) * 2 + (0,)
    lo, hi = block_boot(t, ret, "ym", boot)
    r = {
        "N": int(len(x)), "D": int(t["entry"].nunique()), "M": int(len(by_m)),
        "mean": float(x.mean()), "median": float(np.median(x)), "win": float((x > 0).mean()),
        "trim_top5": _trim_top(x), "trim_both5": _trim_both(x),
        "top5_share": float(top.sum() / total) if abs(total) > 1e-9 else float("nan"),   # 只當參考
        "monthly_mean": float(by_m.mean()), "pos_months": int((by_m > 0).sum()),
        "ci90_month_block": (lo, hi),
        "hold_median": cm, "hold_p90": c90, "hold_max": cmax,
    }
    notes = []
    if r["M"] < 12:
        notes.append("未滿一年")
    if placebo is not None and len(placebo):
        pm, pt = float(placebo["median"].median()), float(placebo["trim_top"].median())
        r.update({"placebo_median": pm, "placebo_trim_top5": pt})
        lottery = (r["median"] - pm <= 0) or (r["trim_top5"] - pt <= 0)
    else:
        notes.append("沒有安慰劑，跟 0 比（偏向看衰）")
        lottery = r["median"] <= 0 or r["trim_top5"] <= 0
    r["lottery"] = bool(lottery)
    r["notes"] = notes
    return r


def report(trades: pd.DataFrame, ret: str = "ret", placebo: pd.DataFrame | None = None) -> str:
    """把 lottery_check 印成一段可以直接貼到協作板的文字。"""
    r = lottery_check(trades, ret, placebo)
    f = lambda v: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.2f}%"
    s = [f"N={r['N']}／D={r['D']}／M={r['M']}" + (f"（{'、'.join(r['notes'])}）" if r["notes"] else ""),
         f"平均 {f(r['mean'])}、中位 {f(r['median'])}、勝率 {r['win']:.0%}",
         f"剔前5% {f(r['trim_top5'])}、兩邊各剔5% {f(r['trim_both5'])}（前5%貢獻 {r['top5_share']:.0%}，只當參考）",
         f"按月平均 {f(r['monthly_mean'])}、正報酬月 {r['pos_months']}/{r['M']}、平均的 5～95%（按月整塊）{f(r['ci90_month_block'][0])}～{f(r['ci90_month_block'][1])}",
         f"同時持有 中位 {r['hold_median']:.0f}、90 分位 {r['hold_p90']:.0f}、最大 {r['hold_max']}"]
    if "placebo_median" in r:
        s.append(f"配對安慰劑 中位 {f(r['placebo_median'])}、剔前5% {f(r['placebo_trim_top5'])}")
    s.append("判定：" + ("彩券組" if r["lottery"] else "非彩券（可以談衛星）"))
    return "\n".join(s)


def inst_flow(hist: pd.DataFrame, inst: pd.DataFrame, industry: pd.Series, col: str = "foreign", side: str = "buy",
              min_streak: int = 5, min_ratio: float = 0.10, liq_lots: float = 500, ret60_pos: bool = False,
              holds: tuple[int, ...] = (5, 20), cost: float = COST, liq_fallback: bool = True,
              split_at: dict | None = None) -> pd.DataFrame:
    """法人連買／連賣事件＋同日配對（協作板 0244／0319 定死的口徑，週末 5 年版三題都只准用這一支）：
    - 訊號：col（foreign／trust）連續 side（buy＝買超、sell＝賣超）≥ min_streak 天，且 min_streak 日累計買（賣）超 ÷ 同期成交量
      ≥ min_ratio；前一日為止 20 日均量 ≥ liq_lots 張；取第一次達標那天，連買（賣）中斷前不重複。ret60_pos：只留前 60 日報酬 >0。
    - 進出：訊號日 T（法人資料收盤後才公布）→ T+1 開盤進、T+k 收盤出；ex＝個股減同窗全市場等權、扣 cost（%）。
    - 配對：同日、同產業、前 5 日報酬差 ±1%、該法人沒有連買（賣）≥ min_streak 天、流動性同門檻的股票，取平均 ctrl。
      diff＝ex − ctrl（沒有配對的 diff 空白）。
    - liq_fallback（0455-cw-industry-pick (b)）：查不到產業的（多半是下市股）改配『同日、前 5 日報酬 ±1%、前一日 20 日均量同五分位、
      不看產業』，配對池含全部股票；match 欄記 ind／liq。主結果用 (b)，(a)＝丟掉 match==liq 的當敏感度。
    - split_at（投信季底倒貨說 0254）：{訊號日: 中間日}；中間日落在進出之間時，另算『進場→中間日收盤』那段的配對差 diff_a，
      後段 diff_b＝diff − diff_a。
    inst：date、code、col（張）。industry：code → 產業。回傳每個事件×持有天數一列：date、code、hold、ex、ctrl、nctrl、diff。"""
    h = hist[hist.code.astype(str).str.fullmatch(r"[1-9]\d{3}")]
    piv = lambda c: h.pivot(index="date", columns="code", values=c).sort_index()  # noqa: E731
    C, O, V = piv("close"), piv("open"), piv("volume") / 1000
    F = inst.pivot_table(index="date", columns="code", values=col, aggfunc="sum").reindex(index=C.index, columns=C.columns)
    F = F.loc[F.index >= inst.date.min()]
    sign = 1 if side == "buy" else -1
    hit = (F * sign > 0).astype(int)
    streak = hit.apply(lambda s: s.groupby((s == 0).cumsum()).cumsum())
    ratio = sign * F.rolling(min_streak).sum() / V.loc[F.index].rolling(min_streak).sum()
    liq = V.rolling(20).mean().shift(1).loc[F.index] >= liq_lots
    cond = (streak >= min_streak) & (ratio >= min_ratio) & liq
    if ret60_pos:
        cond &= (C / C.shift(60) - 1).loc[F.index] > 0
    # 每一段連買（賣）只取第一次達標那天：比率或流動性中途掉下去又回來，不算新事件（Cowork 0436；
    # 原本 cond & ~cond.shift(1) 會在同一段裡重複進場，投信那題多 7.8%）
    seg = (hit == 0).cumsum()
    first = cond & (cond.astype(int).apply(lambda s: s.groupby(seg[s.name]).cumsum()) == 1)
    ret5 = (C / C.shift(5) - 1).loc[F.index]
    ind = industry.reindex(C.columns)
    v20 = V.rolling(20).mean().shift(1)
    volq = np.ceil(v20.rank(axis=1, pct=True) * 5).loc[F.index]
    alld = list(C.index)
    pos = {d: i for i, d in enumerate(alld)}
    rows = []
    for d in F.index:
        codes = first.columns[first.loc[d].to_numpy()]
        if not len(codes):
            continue
        i = pos[d]
        pool = ((streak.loc[d] < min_streak) & liq.loc[d]).to_numpy()
        for k in holds:
            if i + k >= len(alld):
                continue
            e, x = alld[i + 1], alld[i + k]
            r = C.loc[x] / O.loc[e] - 1
            ok = r.notna() & (r.abs() < 3) & (O.loc[e] > 0)
            ex = (r - r[ok].mean()) * 100 - cost
            for c in codes:
                if not ok.get(c, False):
                    continue
                near = ((ret5.loc[d] - ret5.loc[d, c]).abs() <= 0.01).to_numpy() & ok.to_numpy()
                if pd.isna(ind.get(c)) and liq_fallback:
                    m, how = pool & near & (volq.loc[d] == volq.loc[d, c]).to_numpy(), "liq"
                else:
                    m, how = pool & (ind == ind.get(c)).to_numpy() & near, "ind"
                m[C.columns.get_loc(c)] = False
                ctrl = ex[m]
                row = {"date": d, "code": c, "hold": k, "ex": ex[c], "ctrl": ctrl.mean() if len(ctrl) else np.nan,
                       "nctrl": len(ctrl), "match": how}
                mid = (split_at or {}).get(d)
                if mid is not None and e < mid < x and len(ctrl):
                    ra = C.loc[mid] / O.loc[e] - 1
                    oka = ra.notna() & (ra.abs() < 3) & ok
                    exa = (ra - ra[oka].mean()) * 100
                    ma = m & oka.to_numpy()
                    if oka.get(c, False) and ma.any():
                        row["diff_a"] = exa[c] - exa[ma].mean()
                rows.append(row)
    out = pd.DataFrame(rows, columns=["date", "code", "hold", "ex", "ctrl", "nctrl", "match", "diff_a"])
    out["diff"] = out.ex - out.ctrl
    out["diff_b"] = out["diff"] - out["diff_a"]
    out["no_ind"] = out.code.map(ind).isna()  # 查不到產業（多半是下市股）→ 沒得配對、diff 空白（分身 0445-ac）
    return out


def inst_flow_report(ev: pd.DataFrame, seed: int = 0) -> str:
    """每個持有天數：N、有配對、訊號日、一天最多、同時持有中位／最大（用訊號日數近似持有天數，粗估）、
    ex 平均中位、diff 平均中位＋按日抽 5～95%、逐年。"""
    lines = []
    for k, g in ev.groupby("hold"):
        dd = g.dropna(subset=["diff"])
        dm = dd.groupby("date")["diff"].mean().to_numpy()
        rng = np.random.default_rng(seed)
        lo, hi = np.percentile([dm[rng.integers(0, len(dm), len(dm))].mean() for _ in range(2000)], [5, 95]) if len(dm) else (np.nan, np.nan)
        days = pd.Index(sorted(ev.date.unique()))
        cnt = g.groupby("date").size().reindex(days, fill_value=0).rolling(k, min_periods=1).sum()
        yr = dd.groupby(dd.date.str[:4])["diff"].agg(["size", "median"]).round(2)
        ni = int(g.no_ind.sum()) if "no_ind" in g else 0
        if "match" in g and (g.match == "liq").any():
            da = dd[dd.match == "ind"]
            lines.append(f"抱 {k} 日（敏感度 (a)：丟掉查不到產業的 {int((dd.match == 'liq').sum())} 筆）：配對差 平均 {da['diff'].mean():+.2f} 中位 {da['diff'].median():+.2f}")
        lines.append(f"抱 {k} 日：N={len(g)}（有配對 {len(dd)}）訊號日 {g.date.nunique()} 一天最多 {g.groupby('date').size().max()} 檔 "
                     f"同時持有中位 {cnt[cnt > 0].median():.0f}／最大 {cnt.max():.0f}｜訊號組 平均 {g.ex.mean():+.2f} 中位 {g.ex.median():+.2f}｜"
                     f"配對差 平均 {dd['diff'].mean():+.2f} 中位 {dd['diff'].median():+.2f}（按日抽 5～95% {lo:+.2f}～{hi:+.2f}）\n"
                     f"   查不到產業的事件 {ni} 筆（{ni / max(len(g), 1):.1%}，(b) 改用流動性配對）{'⚠️ 超過 3%' if ni > 0.03 * len(g) else ''}\n"
                     f"   逐年（筆數／配對差中位）：{ {y: (int(r['size']), float(r['median'])) for y, r in yr.iterrows()} }")
    return "\n".join(lines)
