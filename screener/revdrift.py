"""月營收公告漂移（revdrift）每批成績表：site/revdrift.html＋data/extras/revdrift_batches.csv。

口徑（協作板 0657／0715／0955 定案）：
- 籃子：營收月份 M 的年增率 ≥30% 且營收是近 12 個月最高（要有 12 個月資料），4 碼普通股
- 批次日：M 的下個月 11 日之後第一個交易日（11 日當作公告日），和「法定期限 10 日（遇假日順延到下一個交易日）之後第一個交易日」取較晚者（Cowork 0052）；批次日開盤進、第 20 個交易日收盤出
- 超額：個股（出場收盤 ÷ 進場開盤 − 1）減同期全市場等權（同買賣時點），再扣來回 0.38%
- 配對安慰劑：籃子每一檔換成「同一天、同流動性五分位」隨機一檔，算籃子平均；300 次取中位。
  比的是「籃子平均」對「安慰劑籃子平均的中位」（同一個尺）
- 下架判準（Cowork 0955）：連 3 批平均 < 安慰劑中位，或累計 6 批平均 ≤0
- Cowork 抽 3 檔：numpy default_rng(批次日 YYYYMMDD) 從籃子不放回抽 3 檔（本尊的抽法，供對照）
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .lottery import COST, liquidity_quintile

log = logging.getLogger("revdrift")
ROOT = Path(__file__).resolve().parent.parent
REV = ROOT / "data" / "extras" / "rev_hist.csv.gz"
HIST = ROOT / "data" / "history.csv.gz"
OUT = ROOT / "data" / "extras" / "revdrift_batches.csv"
HOLD = 20
YOY_MIN = 30.0
N_PLACEBO = 300


def baskets(rev: pd.DataFrame) -> dict[str, list[str]]:
    """{營收月份: [符合的代號]}"""
    rev = rev[rev.code.astype(str).str.fullmatch(r"[1-9]\d{3}")].copy()
    piv = rev.pivot_table(index="ym", columns="code", values="revenue").sort_index()
    yoy = rev.pivot_table(index="ym", columns="code", values="yoy").reindex_like(piv)
    hi12 = piv.rolling(12, min_periods=12).max()
    ok = (yoy >= YOY_MIN) & (piv >= hi12 - 1e-9) & hi12.notna()
    return {ym: list(ok.columns[ok.loc[ym].fillna(False).to_numpy()]) for ym in ok.index}


def _batch_day(ym: str, days: list[str]) -> int | None:
    y, m = map(int, ym.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    ann = f"{y}-{m:02d}-11"
    i = next((k for k, d in enumerate(days) if d > ann), None)
    # 法定期限 10 日遇假日順延到下一個上班日（用交易日近似），期限當天公告的要隔天開盤才進得去
    dl = next((k for k, d in enumerate(days) if d >= f"{y}-{m:02d}-10"), None)
    j = dl + 1 if dl is not None and dl + 1 < len(days) else None
    return None if i is None or j is None else max(i, j)


def compute(hist: pd.DataFrame | None = None, rev: pd.DataFrame | None = None, n: int = N_PLACEBO) -> pd.DataFrame:
    hist = hist if hist is not None else pd.read_csv(HIST, dtype={"code": str})
    rev = rev if rev is not None else pd.read_csv(REV, dtype={"code": str})
    hist = hist[hist.code.str.fullmatch(r"[1-9]\d{3}")]
    piv = lambda c: hist.pivot(index="date", columns="code", values=c).sort_index()
    close, opn, vol = piv("close"), piv("open"), piv("volume").fillna(0)
    opn = opn.reindex_like(close)
    vol = vol.reindex_like(close)
    days = list(close.index)
    q = liquidity_quintile(close.ffill(), vol)
    rows = []
    for ym, codes in sorted(baskets(rev).items()):
        i = _batch_day(ym, days)
        if i is None or not codes:
            continue
        e = days[i]
        done = i + HOLD < len(days)
        x = days[min(i + HOLD, len(days) - 1)]
        r = close.loc[x] / opn.loc[e] - 1
        valid = r.notna() & (r.abs() < 3)
        ex = (r - r[valid].mean()) * 100 - COST
        b = [c for c in codes if c in ex.index and valid.get(c, False)]
        if not b:
            continue
        avg = float(ex[b].mean())
        rng = np.random.default_rng(int(e.replace("-", "")))
        pick = list(rng.choice(sorted(b), size=min(3, len(b)), replace=False))
        # 配對安慰劑：同日同流動性五分位
        qe = q.loc[e]
        pools, pos = {}, []
        for c in b:
            g = qe.get(c)
            if pd.isna(g):
                continue
            if g not in pools:
                pools[g] = ex[valid & (qe == g)].to_numpy()
            pos.append(g)
        prng = np.random.default_rng(0)
        sims = [np.mean([pools[g][prng.integers(len(pools[g]))] for g in pos]) for _ in range(n)] if pos else []
        pm = float(np.median(sims)) if sims else np.nan
        rows.append({"rev_month": ym, "batch_date": e, "exit_date": x, "done": done, "n": len(b),
                     "avg": round(avg, 2), "median": round(float(ex[b].median()), 2),
                     "placebo_avg_median": round(pm, 2), "beat": bool(avg > pm) if not np.isnan(pm) else None,
                     "pick3": " ".join(pick), "pick3_avg": round(float(ex[pick].mean()), 2)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 連輸批數、累計 6 批平均（只算已滿 20 日的）
    streak, run = [], 0
    for d, beat in zip(df.done, df.beat):
        if d and beat is not None:
            run = 0 if beat else run + 1
        streak.append(run)
    df["lose_streak"] = streak
    df["avg6"] = df["avg"].where(df.done).rolling(6, min_periods=6).mean().round(2)
    df["delist"] = (df.lose_streak >= 3) | (df.avg6 <= 0)
    return df


def write(site_dir: Path) -> bool:
    try:
        df = compute()
    except Exception as e:  # noqa: BLE001
        log.warning("revdrift 成績表失敗：%s", e)
        return False
    if df.empty:
        return False
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    from .weekly import _page
    f = lambda v: "" if pd.isna(v) else f"{v:+.2f}%"
    trs = "".join(
        f"<tr><td>{r.rev_month}</td><td>{r.batch_date}</td><td>{r.exit_date}{'' if r.done else '（未滿）'}</td><td>{r.n}</td>"
        f"<td>{f(r.avg)}</td><td>{f(r.median)}</td><td>{f(r.placebo_avg_median)}</td>"
        f"<td>{'' if r.beat is None or not r.done else ('贏' if r.beat else '輸')}</td><td>{r.lose_streak}</td><td>{f(r.avg6)}</td>"
        f"<td>{html.escape(r.pick3)}</td><td>{f(r.pick3_avg)}</td><td>{'⚠️ 下架' if r.delist and r.done else ''}</td></tr>"
        for r in df.iloc[::-1].itertuples())
    body = ("<h1>月營收漂移（revdrift）每批成績</h1>"
            "<p>籃子＝年增 ≥30% 且營收創 12 個月新高；11 日後第一個交易日（10 日期限遇假日順延時再往後）開盤進、第 20 個交易日收盤出；"
            "超額＝減同期全市場等權、扣 0.38%。安慰劑＝每檔換成同日同流動性五分位的隨機股票，籃子平均 300 次取中位。"
            "下架：連 3 批輸安慰劑，或最近 6 批平均 ≤0。抽 3 檔＝numpy default_rng(批次日) 不放回抽（本尊算法）。</p>"
            "<div class='tbl'><table><tr><th>營收月</th><th>批次日</th><th>出場日</th><th>檔數</th><th>籃子平均</th><th>籃子中位</th>"
            "<th>安慰劑平均中位</th><th>輸贏</th><th>連輸</th><th>近 6 批平均</th><th>抽 3 檔</th><th>3 檔平均</th><th></th></tr>"
            f"{trs}</table></div>")
    (site_dir / "revdrift.html").write_text(_page("月營收漂移每批成績", body, "<a href='index.html'>← 每日報表</a>"), "utf-8")
    return True
