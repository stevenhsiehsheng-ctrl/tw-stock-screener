"""美股頁（site/us.html）：給台股用的美股資訊，不是美股選股。

- 隔夜總覽：費半、那斯達克、標普、VIX、台積電 ADR 溢價，加上跟台股供應鏈相關的美股
- 美股隔夜 → 台股隔天：用 history 把「前一晚費半漲跌」分組，看台股隔天開盤跳空、全天等權報酬、0050，
  以及我們的爆量突破訊號（收盤進）隔天開盤的表現
資料：Yahoo Finance（yfinance，在 GitHub Actions 上跑；本機連不到）。每天台北 06:20（美股收盤後）更新：
  python -m screener.us            抓資料＋存 data/us/history.csv.gz
  網頁由 report.write_site 產生（pages.yml 會重建）
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("us")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "us"
HIST = DATA / "history.csv.gz"
META = DATA / "meta.json"
IMPACT = DATA / "impact.json"

INDEXES = [("^SOX", "費城半導體"), ("^IXIC", "那斯達克"), ("^GSPC", "標普 500"), ("^VIX", "VIX 恐慌指數"), ("TWD=X", "美元兌台幣")]
LINKED = [  # 跟台股供應鏈連動的美股（客戶、同業、指標）
    ("TSM", "台積電 ADR"), ("NVDA", "輝達"), ("AMD", "超微"), ("AVGO", "博通"), ("MU", "美光"), ("AAPL", "蘋果"),
    ("QCOM", "高通"), ("ASML", "艾司摩爾"), ("AMAT", "應用材料"), ("LRCX", "科林研發"), ("ARM", "安謀"),
    ("INTC", "英特爾"), ("MRVL", "邁威爾"), ("SMCI", "美超微"), ("DELL", "戴爾"), ("MSFT", "微軟"),
    ("GOOGL", "Google"), ("META", "Meta"), ("AMZN", "亞馬遜"), ("TSLA", "特斯拉"),
]
NAMES = dict(INDEXES + LINKED)
SOX_BINS = [-99, -3, -1, 1, 3, 99]
SOX_LABELS = ["跌 3% 以上", "跌 1～3%", "±1% 內", "漲 1～3%", "漲 3% 以上"]


# ------------------------------------------------------------------ 抓資料
def fetch(period: str = "5y") -> pd.DataFrame:
    import yfinance as yf

    out = []
    for sym, _ in INDEXES + LINKED:
        for _ in range(2):
            try:
                h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
                if len(h):
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("%s 下載失敗：%s", sym, e)
                h = pd.DataFrame()
        if h.empty:
            log.warning("%s 沒有資料", sym)
            continue
        h = h.reset_index()
        out.append(pd.DataFrame({"date": pd.to_datetime(h["Date"]).dt.strftime("%Y-%m-%d"), "sym": sym,
                                 "open": h["Open"], "high": h["High"], "low": h["Low"], "close": h["Close"],
                                 "volume": h["Volume"]}))
    if not out:
        raise RuntimeError("美股資料全部抓不到")
    return pd.concat(out, ignore_index=True)


def update() -> dict:
    df = fetch()
    DATA.mkdir(parents=True, exist_ok=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float).round(4)
    df.sort_values(["sym", "date"]).to_csv(HIST, index=False)
    meta = {"rows": len(df), "syms": int(df.sym.nunique()), "last": df.date.max()}
    META.write_text(json.dumps(meta, ensure_ascii=False))
    try:   # 給盤中頁用：前一晚費半漲跌分組 → 台股隔天的歷史平均（live.py 讀）
        tw = pd.read_csv(ROOT / "data" / "history.csv.gz", dtype={"code": str})
        bpath = ROOT / "data" / "extras" / "bench.csv"
        imp = impact(df, tw, pd.read_csv(bpath, dtype={"code": str}) if bpath.exists() else None)
        IMPACT.write_text(json.dumps(_clean(imp), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        log.warning("美股隔夜影響統計失敗：%s", e)
    log.info("美股資料：%s", meta)
    return meta


# ------------------------------------------------------------------ 計算
def _wide(us: pd.DataFrame, col: str = "close") -> pd.DataFrame:
    return us.pivot(index="date", columns="sym", values=col).sort_index()


def overview(us: pd.DataFrame) -> list[dict]:
    c = _wide(us)
    v = _wide(us, "volume")
    rows = []
    for sym, name in INDEXES + LINKED:
        if sym not in c.columns:
            continue
        s = c[sym].dropna()
        if len(s) < 21:
            continue
        vol = v[sym].dropna() if sym in v.columns else pd.Series(dtype=float)
        vr = float(vol.iloc[-1] / vol.iloc[-21:-1].mean()) if len(vol) > 21 and vol.iloc[-21:-1].mean() > 0 else None
        rows.append({"sym": sym, "name": name, "date": s.index[-1], "close": float(s.iloc[-1]),
                     "chg": float(s.iloc[-1] / s.iloc[-2] - 1) * 100, "chg5": float(s.iloc[-1] / s.iloc[-6] - 1) * 100,
                     "chg20": float(s.iloc[-1] / s.iloc[-21] - 1) * 100,
                     "vol_ratio": vr if sym not in ("^VIX", "TWD=X") else None,
                     "hi52": float(s.iloc[-1] / s.iloc[-252:].max() - 1) * 100 if len(s) >= 60 else None,
                     "index": sym in dict(INDEXES)})
    return rows


def adr_premium(us: pd.DataFrame, tw: pd.DataFrame) -> pd.Series:
    """台積電 ADR 溢價（%）：TSM 收盤 × 美元台幣 ÷ 5 ÷ 同一天 2330 收盤 − 1（1 股 ADR = 5 股）。"""
    c = _wide(us)
    if "TSM" not in c or "TWD=X" not in c:
        return pd.Series(dtype=float)
    t = tw[tw.code == "2330"].set_index("date").close.astype(float)
    fx = c["TWD=X"].ffill()
    d = c.index.intersection(t.index)
    return ((c.loc[d, "TSM"] * fx.loc[d] / 5 / t.loc[d] - 1) * 100).dropna()


def _tw_panel(tw: pd.DataFrame):
    tw = tw[tw.code.astype(str).str.fullmatch(r"[1-9]\d{3}")]
    piv = lambda col: tw.pivot(index="date", columns="code", values=col).sort_index()  # noqa: E731
    return piv("open"), piv("high"), piv("close"), piv("volume").fillna(0)


def impact(us: pd.DataFrame, tw: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    """前一晚美股（費半、台積電 ADR）漲跌分組 → 台股隔天。
    台股日 d 對應「d 之前最後一個美股交易日」的收盤漲跌（美股收盤在台北清晨，d 開盤前就知道）。"""
    c = _wide(us)
    if "^SOX" not in c:
        return {}
    sox = (c["^SOX"].pct_change() * 100).dropna()
    tsm = (c["TSM"].pct_change() * 100).dropna() if "TSM" in c else None
    O, H, C, V = _tw_panel(tw)
    days = list(C.index)
    prev = C.shift(1)
    liquid = (V.rolling(20, min_periods=20).mean().shift(1) / 1000) >= 500
    gap = ((O / prev - 1) * 100).where(liquid & (O / prev - 1).abs().lt(0.11))
    day = ((C / prev - 1) * 100).where(liquid & (C / prev - 1).abs().lt(0.11))
    # 我們的爆量突破訊號（收盤進）→ 隔天開盤（跟 tech.surge_frame 同條件；收漲停另外分）
    va5 = V.shift(1).rolling(5, min_periods=5).mean()
    hi60 = H.shift(1).rolling(60, min_periods=60).max()
    chg = C / prev - 1
    surge = (V >= 3 * va5) & (chg >= 0.03) & (C > O) & (C > hi60)
    nxt_gap = (O.shift(-1) / C - 1) * 100
    df = pd.DataFrame({"date": days, "ew_gap": gap.mean(axis=1).values, "ew_day": day.mean(axis=1).values})
    df["sig_gap"] = [float(nxt_gap.iloc[i - 1][surge.iloc[i - 1]].mean()) if i > 0 and surge.iloc[i - 1].any() else np.nan
                     for i in range(len(days))]
    df["sig_n"] = [int(surge.iloc[i - 1].sum()) if i > 0 else 0 for i in range(len(days))]
    if bench is not None and len(bench):
        b = bench[bench.code == "0050"].set_index("date")
        df["b_gap"] = df.date.map((b.open / b.close.shift(1) - 1) * 100)
        df["b_day"] = df.date.map((b.close / b.close.shift(1) - 1) * 100)
    # 對齊：台股 d ← 最後一個 < d 的美股交易日
    us_dates = pd.to_datetime(pd.Series(sox.index))
    m = pd.merge_asof(pd.DataFrame({"t": pd.to_datetime(df.date)}).sort_values("t"),
                      pd.DataFrame({"t": us_dates, "us": sox.index}).sort_values("t"),
                      on="t", allow_exact_matches=False, direction="backward")
    df["us_date"] = m["us"].values
    df["sox"] = df.us_date.map(sox)
    if tsm is not None:
        df["tsm"] = df.us_date.map(tsm)
    df = df.dropna(subset=["sox", "ew_gap"])
    df["bucket"] = pd.cut(df.sox, SOX_BINS, labels=SOX_LABELS)
    rows = []
    for lab in SOX_LABELS:
        g = df[df.bucket == lab]
        if g.empty:
            continue
        s = g.sig_gap.dropna()
        rows.append({"bucket": lab, "n": len(g), "ew_gap": g.ew_gap.mean(), "ew_day": g.ew_day.mean(),
                     "up": (g.ew_day > 0).mean() * 100,
                     "b_gap": g.b_gap.mean() if "b_gap" in g else None, "b_day": g.b_day.mean() if "b_day" in g else None,
                     "sig_days": len(s), "sig_gap": s.mean() if len(s) else None, "sig_gap_med": s.median() if len(s) else None})
    corr = float(df[["sox", "ew_gap"]].corr().iloc[0, 1])
    corr_day = float(df[["sox", "ew_day"]].corr().iloc[0, 1])
    return {"rows": rows, "n": len(df), "start": df.date.min(), "end": df.date.max(), "corr_gap": corr, "corr_day": corr_day,
            "last": df.iloc[-1][["date", "us_date", "sox"]].to_dict() if len(df) else None}


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else round(float(o), 3)
    if isinstance(o, np.integer):
        return int(o)
    return o


def hint(sox_chg: float | None) -> dict | None:
    """盤中頁用：前一晚費半漲跌落在哪一組、那一組過去一年台股隔天的平均。"""
    if sox_chg is None or not IMPACT.exists():
        return None
    imp = json.loads(IMPACT.read_text())
    lab = pd.cut([sox_chg], SOX_BINS, labels=SOX_LABELS)[0]
    row = next((r for r in imp.get("rows", []) if r["bucket"] == lab), None)
    if not row:
        return None
    return {"sox": round(sox_chg, 2), **row, "start": imp.get("start"), "end": imp.get("end")}


# ------------------------------------------------------------------ 網頁
def _f(v, nd=2, pct=False, sign=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    s = f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"
    return s + ("%" if pct else "")


def _cls(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else ("up" if v > 0 else "dn" if v < 0 else "")


def write(site_dir: Path) -> bool:
    if not HIST.exists():
        return False
    try:
        us = pd.read_csv(HIST)
        tw = pd.read_csv(ROOT / "data" / "history.csv.gz", dtype={"code": str})
        bpath = ROOT / "data" / "extras" / "bench.csv"
        bench = pd.read_csv(bpath, dtype={"code": str}) if bpath.exists() else None
        ov = overview(us)
        prem = adr_premium(us, tw)
        imp = impact(us, tw, bench)
    except Exception as e:  # noqa: BLE001
        log.warning("美股頁產生失敗：%s", e)
        return False
    from .weekly import _page

    def tr(r):
        return (f"<tr><td>{html.escape(r['name'])}<div class='meta'>{html.escape(r['sym'])}・{r['date'][5:]}</div></td>"
                f"<td>{_f(r['close'])}</td><td class='{_cls(r['chg'])}'>{_f(r['chg'], pct=True, sign=True)}</td>"
                f"<td class='{_cls(r['chg5'])}'>{_f(r['chg5'], 1, True, True)}</td><td class='{_cls(r['chg20'])}'>{_f(r['chg20'], 1, True, True)}</td>"
                f"<td>{_f(r['vol_ratio'], 1) + '×' if r['vol_ratio'] else '—'}</td><td>{_f(r['hi52'], 1, True, True)}</td></tr>")
    head = "<tr><th>名稱</th><th>收盤</th><th>漲跌</th><th>5 日</th><th>20 日</th><th>量 / 20 日均量</th><th>距 52 週高</th></tr>"
    idx = "".join(tr(r) for r in ov if r["index"])
    lk = "".join(tr(r) for r in sorted([r for r in ov if not r["index"]], key=lambda r: -r["chg"]))
    pm = ""
    if len(prem):
        last = prem.iloc[-1]
        pm = (f"<p>台積電 ADR 溢價（{prem.index[-1]}）：<b>{_f(last, 1, True, True)}</b>　近 20 日平均 {_f(prem.iloc[-20:].mean(), 1, True, True)}、"
              f"近一年 {_f(prem.iloc[-250:].min(), 1, True, True)}～{_f(prem.iloc[-250:].max(), 1, True, True)}。"
              "溢價＝ADR 換算台幣後比台股貴多少；溢價突然擴大，通常是外資隔夜看好、台積電隔天開高。</p>")
    im = ""
    if imp:
        rows = "".join(
            f"<tr><td>{r['bucket']}</td><td>{r['n']}</td><td class='{_cls(r['ew_gap'])}'>{_f(r['ew_gap'], 2, True, True)}</td>"
            f"<td class='{_cls(r['ew_day'])}'>{_f(r['ew_day'], 2, True, True)}</td><td>{_f(r['up'], 0, True)}</td>"
            f"<td class='{_cls(r['b_gap'])}'>{_f(r['b_gap'], 2, True, True)}</td><td class='{_cls(r['b_day'])}'>{_f(r['b_day'], 2, True, True)}</td>"
            f"<td>{r['sig_days']}</td><td class='{_cls(r['sig_gap'])}'>{_f(r['sig_gap'], 2, True, True)}</td>"
            f"<td class='{_cls(r['sig_gap_med'])}'>{_f(r['sig_gap_med'], 2, True, True)}</td></tr>" for r in imp["rows"])
        im = (f"<h2>美股隔夜 → 台股隔天（{imp['start']}～{imp['end']}，{imp['n']} 個交易日）</h2>"
              "<p>依「前一晚費城半導體漲跌」分組，看台股隔天怎麼走。台股＝前 20 日均量 ≥500 張的股票等權平均；"
              "訊號＝我們的爆量突破（前一天收盤符合），隔天開盤相對前一天收盤的跳空。</p>"
              "<div class='tbl'><table><tr><th>前一晚費半</th><th>天數</th><th>台股開盤跳空</th><th>台股全天</th><th>上漲天數</th>"
              "<th>0050 開盤</th><th>0050 全天</th><th>有訊號的天數</th><th>訊號隔天開盤 平均</th><th>中位</th></tr>"
              f"{rows}</table></div>"
              f"<p class='meta'>費半漲跌跟台股開盤跳空的相關係數 {imp['corr_gap']:.2f}、跟台股全天 {imp['corr_day']:.2f}。"
              "只是統計描述，樣本只有約一年；分組天數少的格子（10 天以下）不要當真。</p>")
    body = ("<h1>美股（給台股用）</h1><p class='meta'>每天台北早上 06:20 更新（美股收盤後）。這頁不是美股選股，"
            "是看前一晚美股怎麼走、會怎麼影響今天的台股。</p>"
            f"<h2>指數</h2><div class='tbl'><table>{head}{idx}</table></div>{pm}"
            f"<h2>台股供應鏈相關（依今天漲跌排序）</h2><div class='tbl'><table>{head}{lk}</table></div>{im}")
    css_extra = "<style>.up{color:#c0392b}.dn{color:#1e8449}.meta{color:var(--muted);font-size:13px}</style>"
    (site_dir / "us.html").write_text(_page("美股（給台股用）", css_extra + body, "<a href='index.html'>← 每日報表</a> ・ <a href='live.html'>盤中即時</a>"), "utf-8")
    return True


# ------------------------------------------------------------------ 研究：爆量突破在美股有沒有用
def _universe() -> list[str]:
    """S&P 500＋那斯達克 100 成分股（維基百科），yfinance 代號用「-」取代「.」。"""
    import io

    import requests
    out = set()
    for url, cols in [("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol",)),
                      ("https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Symbol"))]:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (tw-stock-screener research)"}, timeout=30)
            for t in pd.read_html(io.StringIO(r.text)):
                c = next((c for c in cols if c in t.columns), None)
                if c and len(t) > 90:
                    out |= {str(x).strip().replace(".", "-") for x in t[c].dropna()}
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("成分股清單抓不到 %s：%s", url, e)
    return sorted(out)


def research(years: int = 5) -> None:
    """跟台股同一套『爆量突破 60 日新高』（量 ≥ 前 5 日均量 3 倍、漲 ≥3%、收紅、收盤 > 前 60 日最高），
    在美股大型股回測：收盤進、量縮到爆量日一半或滿 20 天隔天開盤出、同檔持有中不重進；
    超額＝減同期成分股等權（同買賣時點），成本假設來回 0.2%（複委託實際更高）。
    另跑配對安慰劑（同進同出、隨機抽同日成分股）。只印在 log。"""
    import yfinance as yf

    from .lottery import report
    syms = _universe()
    log.info("美股研究：%d 檔", len(syms))
    frames = []
    for i in range(0, len(syms), 100):
        chunk = syms[i:i + 100]
        raw = yf.download(chunk, period=f"{years}y", auto_adjust=True, group_by="ticker", threads=True, progress=False)
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            if len(sub) < 100:
                continue
            frames.append(pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "code": t, "open": sub["Open"].values,
                                        "high": sub["High"].values, "close": sub["Close"].values, "volume": sub["Volume"].values}))
    h = pd.concat(frames, ignore_index=True)
    piv = lambda c: h.pivot(index="date", columns="code", values=c).sort_index()  # noqa: E731
    C, O, H, V = piv("close"), piv("open"), piv("high"), piv("volume").fillna(0)
    O, H, V = O.reindex_like(C), H.reindex_like(C), V.reindex_like(C)
    tr = C.notna()
    chg = C / C.shift(1) - 1
    surge = ((V >= 3 * V.shift(1).rolling(5, min_periods=5).mean()) & (chg >= 0.03) & (C > O)
             & (C > H.shift(1).rolling(60, min_periods=60).max()) & tr).fillna(False)
    dates = list(C.index)
    Cv, Ov, Vv, Tv = C.values, O.values, V.values, tr.values
    rows = []
    for j, code in enumerate(C.columns):
        busy = -1
        for i in np.flatnonzero(surge[code].values):
            if i <= busy:
                continue
            x = None
            for k in range(i + 1, min(len(dates) - 1, i + 21)):
                if Tv[k, j] and (Vv[k, j] < 0.5 * Vv[i, j] or k - i >= 20):
                    x = k + 1
                    break
            if x is None or x >= len(dates):
                continue
            busy = x
            r_all = Ov[x] / Cv[i] - 1
            ok = np.isfinite(r_all) & (np.abs(r_all) < 3)
            r = Ov[x, j] / Cv[i, j] - 1
            nxt = Ov[i + 1, j] / Cv[i, j] - 1
            rows.append({"entry": dates[i], "exit": dates[x], "code": code,
                         "ret": (r - r_all[ok].mean()) * 100 - 0.2, "gap": nxt * 100, "hold": x - i})
    t = pd.DataFrame(rows)
    from .lottery import placebo
    pl = placebo(C, t, n=200, cost=0.2, exit_px=O)
    print(f"===== 美股爆量突破（{len(syms)} 檔、{dates[0]}～{dates[-1]}）=====")
    print(report(t, placebo=pl))
    print(f"平均持有 {t.hold.mean():.1f} 天；隔天開盤跳空 平均 {t.gap.mean():+.2f}% 中位 {t.gap.median():+.2f}%")
    for y, g in t.groupby(t.entry.str[:4]):
        print(y, f"N={len(g)} 平均 {g.ret.mean():+.2f} 中位 {g.ret.median():+.2f}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", action="store_true", help="爆量突破在美股大型股的 5 年回測（只印 log）")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if a.research:
        research()
        return 0
    update()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
