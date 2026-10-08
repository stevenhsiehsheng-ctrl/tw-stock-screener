"""回測共用摘要：以後所有回測回覆都帶「兩段＋盤勢」，不用每題重講。

口徑（Cowork 1658-cw-talk-halves、1756-cw-halves-ack、1759-cw-talk-regimesplit、1857-cw-regime-lose 定案）：
- 每列：N、平均、中位、勝率、月 t、月數。月 t＝同一個月進場的先平均，再對「月平均序列」做 t（月份聚類）
- 兩段（日曆）：2021-01～2023-12／2024-01～最新（回測檔 2020-10 起，之前的不算進兩段）
- 盤勢（data/extras/ew_index.csv，進場日那列的判定＝前一日收盤 vs 200 日線，資料洞日沿用前一個判定）：
  等權、0050 同在線上／同在線下／分歧日（分歧單列，不併進任何一段）；另印等權線下、0050 線下（各含分歧）參考；
  200 日線還沒有值的日子印「無判定」
- 判定：
  - 樣本不足：該段 N<300 或月數<18 → 不判同號、不准拿它降級也不准拿它背書
  - 同號：兩段的 平均 和 月 t 都同號（兩段都樣本夠才判）
  - 單段撐場：同號、但有一段 |月 t|<1
  - 全天候：同在線上、同在線下都 平均>0 且 月 t>1，且 0050 線下（含分歧）平均>0（0050 線下樣本不足就只看等權）
  - 只限線上：同在線下樣本夠，而且 平均≤0 或 月 t<0 → 原則要加註「只限等權在 200 日線上」

用法：
  from tools.btstats import summarize
  print(summarize(df, ret="ex5"))      # df：date（進場日 YYYY-MM-DD）＋報酬欄（%，已扣成本、已減對照組）
  python -m tools.btstats events.csv --ret ex5 [--date date]
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EW = ROOT / "data" / "extras" / "ew_index.csv"
HALVES = (("2021-01～2023-12", "2021-01-01", "2023-12-31"), ("2024-01～最新", "2024-01-01", "9999-12-31"))
MIN_N, MIN_M = 300, 18


def stats(r: pd.Series, d: pd.Series) -> dict:
    """r：報酬（%），d：進場日字串。"""
    r = pd.to_numeric(r, errors="coerce")
    ok = r.notna()
    r, d = r[ok], d[ok]
    n = len(r)
    if not n:
        return {"N": 0, "平均": np.nan, "中位": np.nan, "勝率": np.nan, "月t": np.nan, "月數": 0}
    m = r.groupby(d.str[:7]).mean()
    k = len(m)
    sd = m.std(ddof=1) if k > 1 else np.nan
    t = m.mean() / (sd / math.sqrt(k)) if k > 1 and sd and sd == sd and sd > 0 else np.nan
    return {"N": n, "平均": r.mean(), "中位": r.median(), "勝率": (r > 0).mean() * 100, "月t": t, "月數": k}


def _enough(s: dict) -> bool:
    return s["N"] >= MIN_N and s["月數"] >= MIN_M


def _regime(dates: pd.Series, ew_path: Path = EW) -> pd.DataFrame:
    e = pd.read_csv(ew_path, usecols=["date", "above", "above_0050"])
    return pd.DataFrame({"date": dates.values}).merge(e, on="date", how="left")


def table(df: pd.DataFrame, ret: str = "ret", date: str = "date", ew_path: Path = EW) -> tuple[pd.DataFrame, list[str]]:
    d = df[date].astype(str).str[:10].reset_index(drop=True)
    r = pd.to_numeric(df[ret], errors="coerce").reset_index(drop=True)
    rows = [("全部", stats(r, d))]
    halves = []
    for name, lo, hi in HALVES:
        m = (d >= lo) & (d <= hi)
        s = stats(r[m], d[m])
        rows.append((name, s))
        halves.append(s)
    reg = {}
    if Path(ew_path).exists():
        g = _regime(d, ew_path)
        a, b = g.above, g.above_0050
        segs = [("同在線上", (a == 1) & (b == 1)), ("同在線下", (a == 0) & (b == 0)),
                ("分歧日（單列）", a.notna() & b.notna() & (a != b)),
                ("等權線下（含分歧）", a == 0), ("0050 線下（含分歧）", b == 0), ("無判定（200 日線前）", a.isna())]
        for name, m in segs:
            m = m.fillna(False).values
            s = stats(r[m], d[m])
            rows.append((name, s))
            reg[name] = s
    out = pd.DataFrame([{"段": k, **v} for k, v in rows])
    out["註"] = ["" if k == "全部" else ("樣本不足" if not _enough(v) and v["N"] else "") for k, v in rows]
    verdict = []
    h1, h2 = halves
    if not (_enough(h1) and _enough(h2)):
        verdict.append("兩段：樣本不足，不判同號")
    else:
        same = np.sign(h1["平均"]) == np.sign(h2["平均"]) and np.sign(h1["月t"]) == np.sign(h2["月t"])
        if not same:
            verdict.append("兩段：不同號（平均或月 t 方向打架）")
        elif min(abs(h1["月t"]), abs(h2["月t"])) < 1:
            verdict.append("兩段：同號、單段撐場（有一段 |月 t|<1）")
        else:
            verdict.append("兩段：同號")
    if reg:
        up, dn, d50 = reg["同在線上"], reg["同在線下"], reg["0050 線下（含分歧）"]
        if not _enough(dn):
            verdict.append(f"盤勢：同在線下 N={dn['N']}、{dn['月數']} 個月，樣本不足，不判全天候也不判只限線上")
        elif dn["平均"] <= 0 or dn["月t"] < 0:
            verdict.append("盤勢：同在線下 平均≤0 或月 t<0 → 只限等權在 200 日線上")
        else:
            ok = up["平均"] > 0 and up["月t"] > 1 and dn["平均"] > 0 and dn["月t"] > 1
            if ok and _enough(d50) and d50["平均"] <= 0:
                ok = False
            verdict.append("盤勢：全天候" if ok else "盤勢：線下沒翻負，但不夠全天候（平均>0 且月 t>1 沒有兩段都過）")
    return out, verdict


def summarize(df: pd.DataFrame, ret: str = "ret", date: str = "date", title: str = "", ew_path: Path = EW) -> str:
    t, v = table(df, ret, date, ew_path)
    f = lambda x, nd=2: "—" if x != x else f"{x:+.{nd}f}"
    lines = [title] if title else []
    lines.append(f"{'段':<16}{'N':>7}{'平均':>8}{'中位':>8}{'勝率':>7}{'月t':>7}{'月數':>5}  註")
    for r in t.itertuples(index=False):
        lines.append(f"{r.段:<16}{r.N:>7,}{f(r.平均):>8}{f(r.中位):>8}{(f'{r.勝率:.0f}%' if r.勝率 == r.勝率 else '—'):>7}"
                     f"{f(r.月t):>7}{r.月數:>5}  {r.註}")
    lines += ["→ " + x for x in v]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--ret", default="ret")
    ap.add_argument("--date", default="date")
    a = ap.parse_args()
    print(summarize(pd.read_csv(a.csv), a.ret, a.date))


if __name__ == "__main__":
    main()
