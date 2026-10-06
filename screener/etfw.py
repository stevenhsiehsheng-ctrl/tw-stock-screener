"""0050 成分股權重（MoneyDJ 每月持股）＋每天「0050 除台積電」報酬。

- data/extras/etf0050_w.csv：asof（MoneyDJ 資料日期，通常是月底）、code、name、weight（%）。每月一份，一直累積。
- data/extras/ex_tsmc.csv：date、r0050（含息日報酬 %，bench.csv 的 tr）、r2330（含息日報酬 %）、w2330（前一天收盤時的權重 %）、
  ex_tsmc（%）＝ (r0050 − w×r2330) ÷ (1 − w)。
  w 是拿最近一份月持股權重，再用台積電與 0050 從那天起的含息漲幅調整（權重會跟著股價飄）。
  比第一份權重還早的日子，用第一份往回推，src 標 back（比較不準）。
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

from .enrich import DIR, _get

log = logging.getLogger("etfw")
ROOT = DIR.parent.parent
W_FILE = DIR / "etf0050_w.csv"
EX_FILE = DIR / "ex_tsmc.csv"
URL = "https://www.moneydj.com/ETF/X/Basic/Basic0007a.xdjhtm?etfid=0050.TW"


def parse(html: str, names: dict[str, str]) -> pd.DataFrame:
    m = re.search(r"資料日期：\s*(\d{4})/(\d{2})/(\d{2})", html)
    if not m:
        return pd.DataFrame()
    asof = f"{m[1]}-{m[2]}-{m[3]}"
    rows = re.findall(r'class="col05">\s*([^<]+?)\s*</td>\s*<td class="col06">[^<]*</td>\s*<td class="col07">\s*([\d.]+)', html)
    out = [{"asof": asof, "code": names.get(n.strip(), ""), "name": n.strip(), "weight": float(w)} for n, w in rows]
    return pd.DataFrame(out, columns=["asof", "code", "name", "weight"])


def fetch_weights(s) -> pd.DataFrame:
    names = pd.read_csv(ROOT / "data" / "stock_list.csv", dtype=str).set_index("name")["code"].to_dict()
    r = _get(s, URL)
    if r is None:
        return pd.DataFrame()
    r.encoding = "utf-8" if "utf" in (r.headers.get("content-type") or "").lower() else r.apparent_encoding
    df = parse(r.text, names)
    if len(df) and (df.code == "").any():
        log.warning("0050 成分有 %d 檔名稱對不到代號：%s", (df.code == "").sum(), list(df[df.code == ""].name)[:5])
    return df


def update_weights(s) -> int:
    new = fetch_weights(s)
    if new.empty or new.weight.sum() < 80:   # 抓到殘缺的頁面就不存
        log.warning("0050 權重抓不到或不完整（%d 檔、合計 %.1f%%）", len(new), new.weight.sum() if len(new) else 0)
        return 0
    old = pd.read_csv(W_FILE, dtype=str) if W_FILE.exists() else pd.DataFrame(columns=new.columns)
    df = pd.concat([old[old["asof"] != new["asof"].iloc[0]], new.astype(str)], ignore_index=True)
    df.sort_values(["asof", "weight"], ascending=[True, False], key=lambda c: c if c.name == "asof" else c.astype(float)).to_csv(W_FILE, index=False)
    return int(df["asof"].nunique())


def _tr(close: pd.Series, code: str) -> pd.Series:
    """含息指數：收盤 × 除權息 factor（跟 exdiv.bench_tr 同一套）。"""
    ex = pd.read_csv(DIR / "exdiv.csv", dtype={"code": str})
    f = ex[ex.code == code].groupby("date").factor.prod()
    step = f.reindex(close.index).fillna(1.0)
    return (close / close.shift(1) * step).fillna(1.0).cumprod() * close.iloc[0]


def update_ex_tsmc() -> int:
    if not W_FILE.exists():
        return 0
    w = pd.read_csv(W_FILE, dtype={"code": str})
    w = w[w.code == "2330"].set_index("asof").weight.astype(float).sort_index()
    if w.empty:
        return 0
    b = pd.read_csv(DIR / "bench.csv", dtype={"code": str})
    b = b[b.code == "0050"].set_index("date").sort_index()
    h = pd.read_csv(ROOT / "data" / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
    c = h[h.code == "2330"].set_index("date").close.sort_index()
    idx = b.index.intersection(c.index)
    t50 = b.loc[idx, "tr"].astype(float)
    t30 = _tr(c.loc[idx].astype(float), "2330")
    rel = t30 / t50   # 台積電相對 0050 的含息走勢；權重 ∝ rel
    out = []
    for i in range(1, len(idx)):
        d, p = idx[i], idx[i - 1]
        snaps = [a for a in w.index if a <= p]
        a = snaps[-1] if snaps else w.index[0]
        base = rel.loc[rel.index <= a].iloc[-1] if (rel.index <= a).any() else rel.iloc[0]
        wt = w[a] / 100 * rel.loc[p] / base   # 權重＝快照權重 ×（台積電含息漲幅 ÷ 0050 含息漲幅）
        r50 = t50.loc[d] / t50.loc[p] - 1
        r30 = t30.loc[d] / t30.loc[p] - 1
        out.append({"date": d, "r0050": round(r50 * 100, 4), "r2330": round(r30 * 100, 4), "w2330": round(wt * 100, 2),
                    "ex_tsmc": round((r50 - wt * r30) / (1 - wt) * 100, 4), "src": "snap" if snaps else "back"})
    pd.DataFrame(out).to_csv(EX_FILE, index=False)
    return len(out)


def refresh(s) -> dict:
    got = {}
    try:
        got["etf0050_w"] = update_weights(s)
    except Exception as e:  # noqa: BLE001
        log.warning("0050 權重更新失敗：%s", e)
    try:
        got["ex_tsmc_days"] = update_ex_tsmc()
    except Exception as e:  # noqa: BLE001
        log.warning("0050 除台積電報酬失敗：%s", e)
    return got
