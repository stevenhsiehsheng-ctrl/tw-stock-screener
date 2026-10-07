"""個股頁：site/stock.html?code=XXXX，任何一檔一頁看完（Dennis 10/7「台玻怎麼沒有被預警」）。

- site/stocks/{code}.json：每檔一個小檔（一年收盤／量、月營收、法人、融資、當沖、估值、除權息、注意處置、
  出現過的每日策略／盤中預警／正式訊號、長期 Top 20 分數），外加 stocks/index.json（搜尋用）。
  這些檔每天重產、不進 git（.gitignore），只隨 Pages 發佈。
- 「明天要怎樣才會觸發盤中預警」逐條算：照 config.yaml live 區的門檻（跟 live.scan 同一套），
  60 日新高用含今天的 60 根 K 最高價、5 日均量用含今天的 5 天；觸發價高過漲停價就直接講「明天漲停也不會預警」。
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

log = logging.getLogger("stockpage")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EX = DATA / "extras"
TEMPLATE = Path(__file__).with_name("stock_page.html")


def _tick(p: float) -> float:
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def limit_up(prev: float) -> float:
    """漲停價（跟 live._lu_price 同一套檔位）。"""
    raw = prev * 1.1
    t = _tick(raw)
    return round(math.floor(raw / t + 1e-6) * t, 2)


def up_tick(p: float) -> float:
    """大於 p 的最小可成交價。"""
    t = _tick(p)
    return round(math.floor(p / t + 1e-6) * t + t, 2)


def ceil_tick(p: float) -> float:
    """不小於 p 的最小可成交價。"""
    t = _tick(p)
    return round(math.ceil(p / t - 1e-6) * t, 2)


def trigger(closes: pd.Series, highs: pd.Series, vols: pd.Series, lc: dict) -> dict:
    """明天觸發盤中早期預警要的價、量（vols 單位：股）。"""
    nh = int(lc.get("new_high_days", 60))
    c = float(closes.iloc[-1])
    out = {"close": c, "lu": limit_up(c)}
    if len(highs) < nh or len(vols) < 5:
        out["note"] = f"上市不到 {nh} 個交易日，算不出 {nh} 日新高"
        return out
    hi = float(highs.iloc[-nh:].max())
    avg5 = float(vols.iloc[-5:].mean()) / 1000
    p_hi = up_tick(hi)                                         # 要「大於」60 日最高
    p_chg = ceil_tick(c * (1 + lc.get("min_change_pct", 3) / 100))
    p_min = float(lc.get("min_price", 10))
    need = max(p_hi, p_chg, p_min)
    out.update({"nh": nh, "hi": hi, "p_hi": p_hi, "p_chg": p_chg, "need": need,
                "need_pct": round((need / c - 1) * 100, 2), "avg5": round(avg5),
                "vol_full": round(max(avg5 * lc.get("volume_multiple", 3), lc.get("min_volume_lots", 300))),
                "vol_now": round(max(avg5 * lc.get("min_actual_multiple", 1), lc.get("min_volume_lots", 300))),
                "reachable": need <= out["lu"] and (need / c - 1) * 100 <= lc.get("max_change_pct", 10.5)})
    return out


def _r(v, nd=2):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), nd)


def _read(p: Path, **kw) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype={"code": str}, **kw) if p.exists() else None
    except Exception as e:  # noqa: BLE001
        log.warning("讀 %s 失敗：%s", p.name, e)
        return None


def _by_code(df: pd.DataFrame | None) -> dict:
    return {} if df is None or df.empty else dict(tuple(df.groupby("code")))


def build(out_dir: Path) -> int:
    """產生 out_dir/stocks/*.json，回傳檔數。"""
    lc = (yaml.safe_load((ROOT / "config.yaml").read_text("utf-8")) or {}).get("live", {})
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str).drop_duplicates("code")
    h = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str}).sort_values(["code", "date"])
    asof = h.date.max()
    hg = _by_code(h)
    days = sorted(h.date.unique())

    rev = _read(EX / "rev_hist.csv.gz")
    inst = _read(EX / "inst_hist.csv.gz")
    # 長期法人：inst_5y 每週累計買賣超（張），個股頁畫外資／投信累計線
    il = _read(EX / "inst_5y.csv.gz", usecols=["date", "code", "foreign", "trust"])
    inst_long = {}
    if il is not None and not il.empty:
        il["wk"] = pd.to_datetime(il.date).dt.to_period("W-FRI")
        wk = il.groupby(["code", "wk"], sort=True).agg(date=("date", "max"), f=("foreign", "sum"), t=("trust", "sum")).reset_index()
        wk[["f", "t"]] = wk.groupby("code")[["f", "t"]].cumsum()
        inst_long = {c: g for c, g in wk.groupby("code")}
    shares = _read(EX / "shares.csv")
    sh = shares.drop_duplicates("code").set_index("code").shares if shares is not None and not shares.empty else pd.Series(dtype=float)
    marg = _read(EX / "margin_hist.csv.gz")
    dtr = _read(EX / "daytrade_hist.csv.gz")
    pe = _read(EX / "pe.csv")
    tech = _read(EX / "tech.csv")
    odd = _read(EX / "oddlot.csv")
    warn = _read(EX / "warnings.csv")
    wh = _read(EX / "warnings_hist.csv.gz")
    exd = _read(EX / "exdiv.csv")
    exu = _read(EX / "exdiv_upcoming.csv")
    pos = _read(DATA / "positions.csv")
    lt_screen = None
    top = {}
    tf = DATA / "longterm" / "top20.json"
    if tf.exists():
        try:
            t = json.loads(tf.read_text("utf-8"))
            top = {p["code"]: p for p in t.get("picks", [])}
            if t.get("screen"):
                lt_screen = _read(DATA / "longterm" / t["screen"])
        except Exception as e:  # noqa: BLE001
            log.warning("長期 Top 20 讀取失敗：%s", e)

    res = pd.concat([pd.read_csv(p, dtype=str) for p in sorted((DATA / "results").glob("*.csv"))], ignore_index=True) \
        if (DATA / "results").exists() else pd.DataFrame(columns=["date", "strategy", "code"])
    al = [pd.read_csv(p, dtype={"code": str}) for p in sorted((DATA / "alerts").glob("*.csv"))] if (DATA / "alerts").exists() else []
    alerts = pd.concat(al, ignore_index=True) if al else pd.DataFrame(columns=["date", "code"])

    G = {k: _by_code(v) for k, v in {"rev": rev, "inst": inst, "marg": marg, "dt": dtr, "wh": wh, "exd": exd,
                                     "exu": exu, "pos": pos, "res": res, "al": alerts}.items()}
    one = {k: (v.drop_duplicates("code").set_index("code") if v is not None and not v.empty else pd.DataFrame())
           for k, v in {"pe": pe, "tech": tech, "odd": odd, "warn": warn, "lt": lt_screen}.items()}
    dt_last = set(dtr[dtr.date == dtr.date.max()].code) if dtr is not None and not dtr.empty else set()
    dt_day = dtr.date.max() if dtr is not None and not dtr.empty else None
    lt_n = len(one["lt"]) if not one["lt"].empty else 0

    sd = out_dir / "stocks"
    sd.mkdir(parents=True, exist_ok=True)
    for old in sd.glob("*.json"):
        old.unlink()
    idx, n = [], 0
    for r in sl.itertuples():
        c = r.code
        g = hg.get(c)
        if g is None or g.empty:
            continue
        g = g.dropna(subset=["close"]).tail(260)
        if g.empty:
            continue
        o = {"code": c, "name": r.name, "market": r.market, "industry": r.industry if isinstance(r.industry, str) else "",
             "asof": asof, "last": g.date.iloc[-1],
             "px": {"d": g.date.tolist(), "c": [_r(x) for x in g.close], "v": [int(x // 1000) for x in g.volume.fillna(0)]}}
        if g.date.iloc[-1] == asof:
            o["trig"] = trigger(g.close, g.high, g.volume, lc)
        hi52, lo52 = g.high.max(), g.low.min()
        o["hi52"], o["lo52"] = _r(hi52), _r(lo52)
        if len(g) >= 2:
            o["chg"] = _r((g.close.iloc[-1] / g.close.iloc[-2] - 1) * 100)
        for k, cols in (("pe", ["pe", "pb", "yield"]), ("tech", ["rs", "tpl", "hi52_dist"]), ("odd", ["price", "shares"])):
            if c in one[k].index:
                row = one[k].loc[c]
                o[k] = {x: (bool(row[x]) if x == "tpl" and pd.notna(row[x]) else _r(row[x]) if pd.notna(row[x]) else None)
                        for x in cols if x in row}
        if c in one["warn"].index:
            o["warn"] = str(one["warn"].loc[c, "flag"])
        if (x := G["wh"].get(c)) is not None:
            o["warn_hist"] = x.sort_values("date").tail(10)[["date", "flag", "start", "end"]].fillna("").values.tolist()
        if (x := G["rev"].get(c)) is not None:
            x = x.sort_values("ym").tail(24)
            o["rev"] = [[ym, _r(v / 1e5), _r(y, 1)] for ym, v, y in zip(x.ym, x.revenue, x.yoy)]
        if (x := G["inst"].get(c)) is not None:
            x = x.sort_values("date").tail(60)
            o["inst"] = [[d, _r(f, 0), _r(t, 0), _r(dd, 0)] for d, f, t, dd in zip(x.date, x.foreign, x.trust, x.dealer)]
        if (x := inst_long.get(c)) is not None and len(x) >= 8:
            o["inst_long"] = {"d": x.date.tolist(), "f": [_r(v, 0) for v in x.f], "t": [_r(v, 0) for v in x.t],
                              "lots": _r(sh.get(c) / 1000, 0) if c in sh.index else None}
        if (x := G["marg"].get(c)) is not None:
            x = x.sort_values("date").tail(60)
            o["margin"] = [[d, _r(m, 0), _r(s, 0)] for d, m, s in zip(x.date, x.margin_bal, x.short_bal)]
        if dt_day is not None:
            o["dt_ok"] = c in dt_last
            if (x := G["dt"].get(c)) is not None:
                x = x.sort_values("date").tail(20)
                o["dt"] = [[d, _r(v, 1)] for d, v in zip(x.date, x.dt_ratio)]
        if (x := G["exd"].get(c)) is not None:
            x = x.sort_values("date").tail(8)
            rows_ex = x[["date", "kind", "cash_div", "stock_ratio"]].fillna(0).values.tolist()
            # 填息天數（history 是未還原收盤，只算得到近一年）：除息當天算第 1 天；0＝還沒填；None＝沒資料
            if g is not None and "prev_close" in x:
                gd, gc = g.date.to_numpy(), g.close.to_numpy()
                for row, pcl in zip(rows_ex, x.prev_close):
                    k = np.searchsorted(gd, row[0])
                    if k >= len(gd) or gd[k] != row[0] or not (pcl > 0):
                        row.append(None)
                        continue
                    hit = np.flatnonzero(gc[k:] >= pcl - 1e-9)
                    row.append(int(hit[0]) + 1 if len(hit) else 0)
            o["exdiv"] = rows_ex
        if (x := G["exu"].get(c)) is not None:
            o["exdiv_next"] = x.sort_values("date")[["date", "kind", "cash_div", "stock_ratio"]].fillna(0).values.tolist()
        if (x := G["res"].get(c)) is not None:
            o["strats"] = x.sort_values("date")[["date", "strategy"]].values.tolist()
        if (x := G["al"].get(c)) is not None:
            o["alerts"] = [[a.date, str(a.first_time), _r(a.first_price), bool(a.official) if "official" in x else False]
                           for a in x.sort_values("date").itertuples()]
        if (x := G["pos"].get(c)) is not None:
            o["pos"] = [{k: (None if pd.isna(v) else v) for k, v in rr.items() if k in
                         ("signal_date", "signal_time", "entry_price", "status", "exit_signal_date", "exit_reason",
                          "days_held", "est_return_pct")} for rr in x.to_dict("records")]
        if c in one["lt"].index:
            row = one["lt"].loc[c]
            o["lt"] = {"rank": int(row["rank"]), "n": lt_n, "score": _r(row["score"], 3), "top": c in top}
        if c in top:
            o.setdefault("lt", {"n": lt_n})["top"] = True
            o["lt"].update({k: top[c].get(k) for k in ("group", "thesis", "risk", "break_rule")})
        (sd / f"{c}.json").write_text(json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str), "utf-8")
        idx.append([c, r.name])
        n += 1
    try:
        n += build_us(sd, idx)
    except Exception as e:  # noqa: BLE001
        log.warning("美股個股頁失敗：%s", e)
    (sd / "index.json").write_text(json.dumps({"asof": asof, "dt_day": dt_day, "s": idx},
                                              ensure_ascii=False, separators=(",", ":")), "utf-8")
    return n


def build_us(sd: Path, idx: list) -> int:
    """美股代號（data/usx）：價量、基本面、美股篩選紀錄。"""
    U = DATA / "usx"
    if not (U / "history.csv.gz").exists() or not (U / "universe.csv").exists():
        return 0
    uni = pd.read_csv(U / "universe.csv", dtype=str).fillna("").drop_duplicates("code")
    h = pd.read_csv(U / "history.csv.gz", dtype={"code": str}).sort_values(["code", "date"])
    asof = h.date.max()
    hg = _by_code(h)
    fund = _read(U / "fund.csv")
    F = fund.drop_duplicates("code").set_index("code") if fund is not None and len(fund) else pd.DataFrame()
    rf = sorted((U / "results").glob("*.csv")) if (U / "results").exists() else []
    res = pd.concat([pd.read_csv(f, dtype=str) for f in rf], ignore_index=True) if rf else pd.DataFrame(columns=["date", "strategy", "code"])
    R = _by_code(res)
    n = 0
    for r in uni.itertuples():
        c = r.code
        g = hg.get(c)
        if g is None or g.empty or (sd / f"{c}.json").exists():
            continue
        g = g.dropna(subset=["close"]).tail(260)
        if g.empty:
            continue
        o = {"code": c, "name": r.name_zh or r.name, "name_en": r.name, "market": "US", "group": r.group,
             "industry": " ／ ".join(x for x in (r.sector, r.industry) if x), "asof": asof, "last": g.date.iloc[-1],
             "px": {"d": g.date.tolist(), "c": [_r(x) for x in g.close], "v": [int(x // 1000) for x in g.volume.fillna(0)]},
             "hi52": _r(g.high.max()), "lo52": _r(g.low.min())}
        if len(g) >= 2:
            o["chg"] = _r((g.close.iloc[-1] / g.close.iloc[-2] - 1) * 100)
        if c in F.index:
            o["fund"] = {k: (None if pd.isna(v) else v) for k, v in F.loc[c].items()}
        if (x := R.get(c)) is not None:
            o["strats"] = x.sort_values("date")[["date", "strategy"]].values.tolist()
        (sd / f"{c}.json").write_text(json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str), "utf-8")
        idx.append([c, o["name"] if o["name"] == r.name else f"{o['name']} {r.name}"])
        n += 1
    return n


def write(site_dir: Path) -> bool:
    if not TEMPLATE.exists():
        return False
    n = build(site_dir)
    (site_dir / "stock.html").write_text(TEMPLATE.read_text("utf-8"), "utf-8")
    log.info("個股頁：%d 檔", n)
    return n > 0
