"""共用回測模擬器：訊號、現行出場、減對照組、帳戶模擬寫死在這裡，以後回測題一律呼叫，不准各自手刻
（分身 2345-cc-ac：同一批 10,093 筆，三種手刻對照組算出 +0.39／+0.59／+0.76）。

口徑：
- 資料：backtest-data 分支的 backtest.csv.gz（2020-10 起、含下市股；Yahoo 還原價＋下市股官方原始價）。
  data/extras/corp_actions.csv 有記、而且檔案裡真的出現跳價的變更面額／減資，往回補還原（Yahoo 漏掉的，例 6949 2026-09-07 1 拆 20）
- 訊號＝config.yaml「爆量突破新高（收盤確認）」＋base_filter＋盤中提醒上限（13:12 正式訊號的收盤版）：
  量 ≥ 前 5 日均量 3 倍、漲幅 3%～10.5%、紅 K、收盤 > 前 60 日最高價、量 ≥ 500 張、收盤 ≥ 10 元（還原價）。
  min_turnover20：另加前 20 日均成交額門檻（元，不含當天），分身那幾班用 1,000 萬
- 進場：訊號日收盤
- 出場＝screener/positions.update：之後每個有成交的日子收盤檢查——量 < 爆量日 × 0.5 且當天不是收盤鎖漲停
  → 隔天開盤賣；持有滿 20 個交易日 → 隔天開盤賣。資料結束還沒出場＝censored（用最後收盤估、旗標標出來，不丟）
- 收盤鎖漲停（P["lu"]，lu_rule="v2" 預設；分身 0315-cc-ac、Cowork 0326／0356 定案）：
  有原始價的日子（data/history.csv.gz，約最近一年）用官方檔位精算漲停價，基準＝前一日原始收盤，
  除權息日用 exdiv.csv 的參考價、減資／變更面額恢復買賣日用 corp_actions.csv 的參考價；
  沒有原始價的舊年份用『還原漲幅 ≥9.5% 且收盤＝當天最高』。lu_rule="approx95" 是 #222 的舊口徑（只看漲幅 ≥9.5%）。
  重疊期兩種算法逐年的誤抓／漏抓用 lu_check(P) 印。trades() 的 lu 欄＝訊號日收盤鎖漲停（真鎖）
- 成本：來回 0.38%（買 0.04、賣 0.34）；slip=True 再加零股滑價每邊 <50 元 0、50～100 元 0.15%、≥100 元 0.3%
  （價格段用還原價，舊年份還原價偏低 → 會少扣，偏樂觀）
- 對照組：data/extras/ew_index.csv 的 ew_close（網站盤勢那條）進場日收盤 → 出場訊號日收盤，再接出場日的等權隔夜（開盤÷前收）
- 髒單旗標（不丟，自己決定要不要排除）：censored；corp_jump＝進場前 60 日到出場之間有單日 |漲跌| >11%
  （超過漲跌停＝沒還原的股本變動、興櫃價、新上市前 5 日）
- 帳戶：account()——slots 格、每筆開倉時權益 × frac、滿格或現金不夠就跳過；同一天多筆的順序隨機，
  種子＝(seed, 當天日期)（Cowork 2226／2356 定案：排序用隨機，比較兩案用同一組 20 個種子配對算比值）

用法：
  from tools.btsim import load, signals, trades, account, paired
  P = load()                              # 第一次會從 backtest-data 分支取檔（快取在 ~/.cache/tw-screener/）
  print(provenance(min_turnover20=1e7))   # 回測留言第一行貼這串：commit 短碼＋clean/DIRTY＋參數（DIRTY 只能當討論）
  T = trades(P, signals(P))               # 逐筆：date code entry exit_date days gross net bench ex lu censored corp_jump above above_0050
  from tools.btstats import summarize, account as acct_summary
  print(summarize(T[~T.corp_jump & ~T.censored], ret="ex"))
  eq = account(P, T, seed=0)              # 每日權益
  r = paired(P, T, T.entry < 100, seeds=20)   # 兩案配對：同種子 variant/base 的總報酬比值
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BT_CACHE", Path.home() / ".cache" / "tw-screener"))
EW = ROOT / "data" / "extras" / "ew_index.csv"
CORP = ROOT / "data" / "extras" / "corp_actions.csv"
BUY_COST, SELL_COST = 0.04, 0.34          # %，合計 0.38
LIMIT_APPROX = 9.5                        # 還原價判漲停用漲幅近似
HIST = ROOT / "data" / "history.csv.gz"   # 原始價（約最近一年）：漲停用檔位精算
EXDIV = ROOT / "data" / "extras" / "exdiv.csv"
JUMP = 11.0                               # 超過漲跌停的單日變動 → corp_jump


def slip_pct(px):
    """零股滑價（每邊，%）：Cowork 10/7 定案三段中位。"""
    px = np.asarray(px, dtype=float)
    return np.where(px >= 100, 0.3, np.where(px >= 50, 0.15, 0.0))


def provenance(**params) -> str:
    """『btsim <commit 短碼> clean|DIRTY｜參數』：回測留言第一行貼這串，DIRTY（tools／screener 有沒 commit 的改動）的結果只能當討論。"""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "tools", "screener"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        h, dirty = "nogit", "?"
    p = bt_path() if (CACHE / "backtest.csv.gz").exists() else None
    data = f"bt {p.stat().st_size // 1_000_000}MB" if p else "bt 未取"
    kv = "、".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"btsim {h or 'nogit'} {'DIRTY' if dirty else 'clean'}｜{data}" + (f"｜{kv}" if kv else "")


def _raw_limit_up(dates, cols) -> pd.DataFrame | None:
    """有原始價的日子：收盤＝官方檔位漲停價（基準＝前一日原始收盤；除權息日／恢復買賣日用參考價）。沒資料的格子 NaN。"""
    if not HIST.exists():
        return None
    from screener.rules import limit_price
    h = pd.read_csv(HIST, dtype={"code": str})
    h = h[h.code.isin(set(cols)) & h.close.notna() & (h.volume.fillna(0) > 0)]
    rc = h.pivot(index="date", columns="code", values="close").sort_index()
    ref = rc.ffill().shift(1)
    for f in (EXDIV, CORP):
        if f.exists():
            x = pd.read_csv(f, dtype={"code": str})
            x = x[x.date.isin(set(rc.index)) & x.code.isin(set(rc.columns))]
            for r in x.itertuples():
                if r.ref_price and r.ref_price > 0:
                    ref.at[r.date, r.code] = r.ref_price
    lu = rc >= limit_price(ref, True) - 1e-6
    lu = lu.where(rc.notna() & ref.notna())
    return lu.iloc[1:].reindex(index=[d for d in dates if d in lu.index], columns=cols)


def lu_matrix(P: dict, rule: str = "v2") -> pd.DataFrame:
    c, h, chg = P["close"], P["high"], P["chg"]
    approx = (chg >= LIMIT_APPROX) & (chg <= 10.5) & P["traded"]
    if rule == "approx95":
        return approx
    approx = approx & (c >= h * (1 - 1e-6))
    raw = P.get("lu_raw")
    if raw is None or raw.empty:
        return approx
    out = approx.copy()
    sub = raw.reindex_like(out)
    has = sub.notna()
    out = out.where(~has, sub.fillna(False).astype(bool))
    return out & P["traded"]


def lu_check(P: dict) -> pd.DataFrame:
    """重疊期（有原始價的日子）：還原價近似 vs 原始價精算，逐年 抓到／誤抓／漏抓。"""
    raw = P.get("lu_raw")
    if raw is None or raw.empty:
        return pd.DataFrame()
    c, h, chg = P["close"], P["high"], P["chg"]
    rows = []
    for name, ap in [("9.5", (chg >= LIMIT_APPROX) & (chg <= 10.5)), ("9.5＋收＝高", (chg >= LIMIT_APPROX) & (chg <= 10.5) & (c >= h * (1 - 1e-6)))]:
        a = ap.reindex_like(raw) & P["traded"].reindex_like(raw)
        has = raw.notna()
        r = raw.fillna(False).astype(bool)
        for y in sorted({d[:4] for d in raw.index}):
            m = [d for d in raw.index if d.startswith(y)]
            hh = has.loc[m]
            aa, rr = a.loc[m] & hh, r.loc[m] & hh
            rows.append({"近似": name, "年": y, "精算漲停": int(rr.sum().sum()), "抓到": int((aa & rr).sum().sum()),
                         "誤抓": int((aa & ~rr).sum().sum()), "漏抓": int((~aa & rr).sum().sum())})
    return pd.DataFrame(rows)


def bt_path(path: str | None = None) -> Path:
    if path:
        return Path(path)
    p = CACHE / "backtest.csv.gz"
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "fetch", "-q", "origin", "backtest-data"], cwd=ROOT, check=True)
        with open(p, "wb") as f:
            subprocess.run(["git", "show", "origin/backtest-data:backtest.csv.gz"], cwd=ROOT, check=True, stdout=f)
    return p


def _corp_adjust(o, h, lo, c, v):
    """corp_actions.csv 的事件：檔案裡當天真的有跳價（前收÷當天開盤 跟 factor 差 <20%）才往回還原。"""
    if not CORP.exists():
        return 0
    ca = pd.read_csv(CORP, dtype={"code": str})
    n = 0
    for r in ca.itertuples():
        if r.code not in c.columns or r.date not in c.index or not (r.factor > 0):
            continue
        i = c.index.get_loc(r.date)
        prev = c[r.code].iloc[:i].dropna()
        op = o.at[r.date, r.code]
        if prev.empty or not (op > 0):
            continue
        if abs(prev.iloc[-1] / op / r.factor - 1) > 0.2:
            continue                       # 檔案裡沒有跳（Yahoo 已還原過），不要再除一次
        rows = c.index[:i]
        for m in (o, h, lo, c):
            m.loc[rows, r.code] = m.loc[rows, r.code] / r.factor
        v.loc[rows, r.code] = v.loc[rows, r.code] * r.factor
        n += 1
    return n


def load(path: str | None = None) -> dict:
    df = pd.read_csv(bt_path(path), dtype={"code": str})
    df = df[df.code.str.fullmatch(r"\d{4}") & ~df.code.str.startswith("00")]
    piv = lambda col: df.pivot(index="date", columns="code", values=col).sort_index()
    c = piv("close")
    o, h, lo, v = (piv(x).reindex_like(c) for x in ("open", "high", "low", "volume"))
    n_adj = _corp_adjust(o, h, lo, c, v)
    traded = c.notna() & (v.fillna(0) > 0)
    cf = c.ffill()
    chg = (c / cf.shift(1) - 1) * 100
    ew = pd.read_csv(EW).set_index("date") if EW.exists() else pd.DataFrame()
    on = (o / cf.shift(1) - 1) * 100
    ew_on = on.where(on.abs() <= JUMP).mean(axis=1)          # 等權隔夜（開盤÷前收）
    P = {"open": o, "high": h, "low": lo, "close": c, "cf": cf, "volume": v.fillna(0), "traded": traded,
         "chg": chg, "ew": ew, "ew_on": ew_on, "dates": list(c.index), "corp_adjusted": n_adj}
    try:
        P["lu_raw"] = _raw_limit_up(list(c.index), list(c.columns))
    except Exception as e:  # noqa: BLE001
        print("原始價漲停精算失敗，全部改用近似：", e)
        P["lu_raw"] = None
    P["lu"] = lu_matrix(P, "v2")
    return P


def signals(P: dict, vol_mult: float = 3, chg_min: float = 3, chg_max: float = 10.5, high_days: int = 60,
            min_lots: float = 500, min_price: float = 10, min_turnover20: float | None = None) -> pd.DataFrame:
    c, o, h, v, chg = P["close"], P["open"], P["high"], P["volume"], P["chg"]
    prev = P["cf"].shift(1)
    vol5 = v.shift(1).rolling(5, min_periods=5).mean()
    m = (P["traded"] & (v >= vol_mult * vol5) & (chg >= chg_min) & (chg <= chg_max)
         & ((c - o) / prev * 100 >= 0.0001)
         & (c > h.shift(1).rolling(high_days, min_periods=high_days).max())
         & (v >= min_lots * 1000) & (c >= min_price))
    if min_turnover20:
        m &= (c * v).shift(1).rolling(20, min_periods=20).mean() >= min_turnover20
    s = m.stack()
    s = s[s].reset_index()
    s.columns = ["date", "code", "_"]
    return s[["date", "code"]].sort_values(["date", "code"]).reset_index(drop=True)


def trades(P: dict, sig: pd.DataFrame, shrink: float = 0.5, max_hold: int = 20, slip: bool = True,
           lu_rule: str = "v2") -> pd.DataFrame:
    dates = P["dates"]
    di = {d: i for i, d in enumerate(dates)}
    cols = list(P["close"].columns)
    ci = {c: i for i, c in enumerate(cols)}
    C, O, V = P["close"].to_numpy(), P["open"].to_numpy(), P["volume"].to_numpy()
    TR, CHG = P["traded"].to_numpy(), P["chg"].to_numpy()
    LU = (P["lu"] if lu_rule == "v2" else lu_matrix(P, lu_rule)).to_numpy()
    big = (np.abs(CHG) > JUMP) & TR
    ewc = P["ew"].ew_close.reindex(dates).to_numpy() if len(P["ew"]) else np.full(len(dates), np.nan)
    ewon = P["ew_on"].reindex(dates).to_numpy()
    ab = P["ew"].above.reindex(dates).to_numpy() if len(P["ew"]) else np.full(len(dates), np.nan)
    ab50 = P["ew"].above_0050.reindex(dates).to_numpy() if len(P["ew"]) else np.full(len(dates), np.nan)
    out = []
    n = len(dates)
    for d, code in zip(sig.date, sig.code):
        i, j = di[d], ci[code]
        entry, surge = C[i, j], V[i, j]
        xs = None                                         # 出場訊號日
        for k in range(i + 1, n):
            if not TR[k, j]:
                continue
            if (V[k, j] < shrink * surge and not LU[k, j]) or k - i >= max_hold:
                xs = k
                break
        xo = None
        if xs is not None:
            xo = next((k for k in range(xs + 1, n) if TR[k, j] and O[k, j] > 0), None)
        censored = xo is None
        if censored:
            last = max((k for k in range(i, n) if TR[k, j]), default=i)
            exit_px, exit_i = C[last, j], last
            bench = ewc[last] / ewc[i] - 1 if ewc[i] == ewc[i] else np.nan
        else:
            exit_px, exit_i = O[xo, j], xo
            bench = (ewc[xo - 1] / ewc[i]) * (1 + (ewon[xo] if ewon[xo] == ewon[xo] else 0) / 100) - 1 \
                if ewc[i] == ewc[i] and ewc[xo - 1] == ewc[xo - 1] else np.nan
        gross = (exit_px / entry - 1) * 100
        cost = BUY_COST + SELL_COST + (float(slip_pct(entry) + slip_pct(exit_px)) if slip else 0.0)
        net = gross - cost
        lo_i = max(0, i - 60)
        out.append({"date": d, "code": code, "entry": entry, "exit_date": dates[exit_i], "exit_px": exit_px,
                    "days": exit_i - i, "gross": gross, "net": net, "bench": bench * 100, "ex": net - bench * 100,
                    "lu": bool(LU[i, j]), "censored": censored, "corp_jump": bool(big[lo_i:exit_i + 1, j].any()),
                    "above": ab[i], "above_0050": ab50[i]})
    return pd.DataFrame(out)


def account(P: dict, T: pd.DataFrame, slots: int = 10, frac: float = 0.10, seed: int = 0,
            slip: bool = True, order: str = "random") -> pd.Series:
    """帳戶級：每日權益（起始 1.0）。T 是 trades() 的輸出（要排除的先濾掉，空格自然讓給下一筆）。
    出場在出場日開盤先做、進場在當天收盤；同一天多筆順序隨機（種子＝(seed, 日期)），order='given' 照 T 的順序。"""
    dates = P["dates"]
    di = {d: i for i, d in enumerate(dates)}
    cols = list(P["close"].columns)
    ci = {c: i for i, c in enumerate(cols)}
    CF = P["cf"].to_numpy()
    T = T.reset_index(drop=True)
    by_entry: dict[int, list[int]] = {}
    for k, d in enumerate(T.date):
        by_entry.setdefault(di[d], []).append(k)
    xi = [di[x] for x in T.exit_date]
    cash, held, eq = 1.0, {}, np.empty(len(dates))   # held: trade k → shares
    for t in range(len(dates)):
        for k in [k for k in held if xi[k] == t and not T.censored[k]]:
            px = T.exit_px[k]
            s = float(slip_pct(px)) if slip else 0.0
            cash += held.pop(k) * px * (1 - (SELL_COST + s) / 100)
        cand = by_entry.get(t, [])
        if cand:
            if order == "random":
                rng = np.random.default_rng([seed, int(dates[t].replace("-", ""))])
                cand = list(rng.permutation(cand))
            equity = cash + sum(sh * CF[t, ci[T.code[k]]] for k, sh in held.items())
            for k in cand:
                if len(held) >= slots:
                    break
                amt = frac * equity
                if cash < amt:
                    break
                px = T.entry[k]
                s = float(slip_pct(px)) if slip else 0.0
                held[k] = amt * (1 - BUY_COST / 100) / (px * (1 + s / 100))
                cash -= amt
        eq[t] = cash + sum(sh * CF[t, ci[T.code[k]]] for k, sh in held.items())
    return pd.Series(eq, index=dates)


def _mdd(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min() * 100)


def paired(P: dict, T: pd.DataFrame, keep, seeds: int = 20, **kw) -> pd.DataFrame:
    """兩案配對：base＝T 全部、variant＝T[keep]；同一個種子各跑一次，回傳每個種子的總報酬、回撤、比值。"""
    keep = np.asarray(keep, dtype=bool)
    rows = []
    for s in range(seeds):
        a = account(P, T, seed=s, **kw)
        b = account(P, T[keep], seed=s, **kw)
        rows.append({"seed": s, "base": (a.iloc[-1] - 1) * 100, "variant": (b.iloc[-1] - 1) * 100,
                     "ratio": b.iloc[-1] / a.iloc[-1], "mdd_base": _mdd(a), "mdd_variant": _mdd(b)})
    return pd.DataFrame(rows)


def describe_paired(r: pd.DataFrame) -> str:
    q = lambda s: f"中位 {s.median():+.0f}%（p10 {s.quantile(.1):+.0f}、p90 {s.quantile(.9):+.0f}）"
    return (f"base 總報酬 {q(r.base)}、回撤中位 {r.mdd_base.median():.1f}%\n"
            f"variant 總報酬 {q(r.variant)}、回撤中位 {r.mdd_variant.median():.1f}%\n"
            f"配對比值 中位 {r.ratio.median():.2f}、p10 {r.ratio.quantile(.1):.2f}、p90 {r.ratio.quantile(.9):.2f}、"
            f"variant 贏的種子 {(r.ratio > 1).sum()}/{len(r)}")
