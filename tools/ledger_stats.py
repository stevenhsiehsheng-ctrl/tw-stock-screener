"""系統訊號累計＋虛擬帳戶摘要：可重現版（Cowork 0426-cw-repro-ok、0427-cw-total-first）。
以前 15:47 收盤摘要的『系統訊號累計①②③』是在 session 裡臨時算、算完就蒸發；改成 clone repo 跑這支，
第一行的 provenance 抄進 ledger_daily 的 calc 欄，誰都能用同一個 commit 重現。

口徑（跟首頁「今天重點」💼 那行同一套，screener/today.py _sys_stats）：
- 來源 data/positions.csv（盤中 13:12 訊號＋收盤確認，positions.update 每天收盤更新）
- ① 已出場：status=closed 且 exit_open 有值（隔天開盤真的賣掉），報酬＝est_return_pct（用出場開盤價）
  ② 持有中：status=open，或出場訊號已出、明天開盤才賣（exit_open 空）；報酬＝est_return_pct（最新收盤估）；
     今天才出的訊號 est_return_pct 還沒有 → 0（進場價＝今天收盤）
  ③ 合計＝①＋②（主數字）
- 全部扣來回 0.38%；同期 0050 含息＝data/extras/bench.csv 的 tr，訊號日收盤 → 出場訊號日收盤（已出場、待賣）或最新收盤（持有中）
- 另外照訊號日收盤是否鎖漲停（close_lu）拆兩組；N<30 不印勝率（Cowork 0427）
- 虛擬帳戶：data/ledger/ 最新一檔（只有虛擬帳戶）的 nav、累計、同期 0050

用法：python -m tools.ledger_stats [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
POS = ROOT / "data" / "positions.csv"
BENCH = ROOT / "data" / "extras" / "bench.csv"
LEDGER = ROOT / "data" / "ledger"
COST = 0.38


def provenance(**params) -> str:
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "tools", "screener", "data/positions.csv"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        h, dirty = "", "?"
    kv = "、".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"ledger_stats {h or 'nogit'} {'DIRTY' if dirty else 'clean'}" + (f"｜{kv}" if kv else "")


def _tr() -> pd.Series:
    if not BENCH.exists():
        return pd.Series(dtype=float)
    b = pd.read_csv(BENCH, dtype={"code": str})
    return b[b.code.isin(["0050", "50"]) & b.tr.notna()].set_index("date").tr.astype(float).sort_index()


def frame(pos: pd.DataFrame | None = None, tr: pd.Series | None = None) -> pd.DataFrame:
    """每筆訊號一列：group（closed／holding）、ret（扣 0.38）、bench（同期 0050 含息）、ex、lu。"""
    pos = pd.read_csv(POS, dtype={"code": str}) if pos is None else pos.copy()
    tr = _tr() if tr is None else tr
    est = pd.to_numeric(pos.est_return_pct, errors="coerce")
    est = est.where(est.notna() | (pos.status != "open"), 0.0)
    closed = (pos.status == "closed") & pos.exit_open.notna()
    p = pos.assign(est=est, group=np.where(closed, "closed", "holding"))
    p = p[p.est.notna()].copy()
    p["ret"] = p.est - COST
    idx = list(tr.index)

    def at(d):
        if not idx or not isinstance(d, str) or d < idx[0]:
            return np.nan
        return float(tr.iloc[np.searchsorted(idx, d, side="right") - 1])
    end = p.exit_signal_date.where(p.exit_signal_date.notna(), idx[-1] if idx else np.nan)
    p["bench"] = (end.map(at) / p.signal_date.map(at) - 1) * 100
    p["ex"] = p.ret - p.bench
    p["lu"] = pd.to_numeric(p.get("close_lu"), errors="coerce")
    return p[["code", "name", "signal_date", "status", "group", "exit_signal_date", "ret", "bench", "ex", "lu"]]


def table(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, m in [("① 已出場", p.group == "closed"), ("② 持有中（收盤估）", p.group == "holding"), ("③ 合計", p.index == p.index),
                    ("　收盤鎖漲停", p.lu == 1), ("　收盤沒鎖", p.lu == 0)]:
        x = p[m]
        rows.append({"段": name, "N": len(x), "平均": x.ret.mean(), "中位": x.ret.median(),
                     "勝率": (x.ret > 0).mean() * 100 if len(x) >= 30 else np.nan,
                     "同期0050": x.bench.mean(), "超額": x.ex.mean()})
    return pd.DataFrame(rows)


def ledger_latest() -> dict | None:
    fs = sorted(LEDGER.glob("20??-??-??.json")) if LEDGER.exists() else []
    if not fs:
        return None
    g = json.loads(fs[-1].read_text("utf-8"))
    return {k: g.get(k) for k in ("date", "nav", "equity", "ret_today", "ret_cum", "bench_tr_today", "bench_tr_cum",
                                  "bench_base_date", "core_ret_cum")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="印 JSON（給 15:47 寫進 ledger_daily）")
    a = ap.parse_args()
    p = frame()
    t = table(p)
    prov = provenance(positions=f"{len(p)}筆", since=str(p.signal_date.min()), last=str(p.signal_date.max()))
    if a.json:
        out = {"calc": prov, "system": json.loads(t.round(2).to_json(orient="records", force_ascii=False)),
               "ledger": ledger_latest()}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(prov)
    f = lambda v: "—" if v != v else f"{v:+.2f}%"
    print(f"系統訊號（非虛擬帳戶，{p.signal_date.min()} 起；扣 0.38%；同期 0050 含息＝訊號日收盤 → 出場訊號日／最新收盤）")
    for r in t.itertuples(index=False):
        print(f"{r.段:<14} N {r.N:>3}  平均 {f(r.平均):>8}  中位 {f(r.中位):>8}  "
              f"勝率 {'—' if r.勝率 != r.勝率 else f'{r.勝率:.0f}%':>4}  0050 {f(r.同期0050):>8}  超額 {f(r.超額):>8}")
    print("註：① 早出場先天偏負（btsim 6 年：1～3 天就出場的平均 −2.8%），主數字看 ③；N<30 不印勝率。")
    g = ledger_latest()
    if g:
        print(f"虛擬帳戶 {g['date']}：nav {g['nav']}、今日 {f(g['ret_today'])}、累計 {f(g['ret_cum'])}"
              f"｜0050 含息 今日 {f(g['bench_tr_today'])}、累計 {f(g['bench_tr_cum'])}"
              + (f"（{g['bench_base_date']} 收盤起算）" if g.get("bench_base_date") else ""))


if __name__ == "__main__":
    main()
