"""大環境長歷史：加權指數、0050、費半、標普、VIX、美債殖利率、美元、台幣、銅、油（Yahoo，盡量抓最長）。

用途：
- 「大盤風險燈號」研究：台股 20 多年有好幾次空頭（2000、2008、2011、2015、2018、2020、2022），
  5 年回測只碰到 2022 一次，要判斷「能不能提前警報」得用長歷史。
- 大環境頁 site/macro.html（report.write_site 產生）：每項的最新值、一年變化、在過去 10／20 年排第幾（分位）。
  只描述「現在在歷史上的位置」，不是買賣訊號。
存 data/macro/long.csv.gz（date, sym, close, close_adj）。在 GitHub Actions 跑（本機連不到 Yahoo）：
  python -m screener.macro
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("macro")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "macro"
LONG = DATA / "long.csv.gz"

SYMS = [("^TWII", "加權指數"), ("0050.TW", "0050"), ("^SOX", "費城半導體"), ("^GSPC", "標普 500"),
        ("^VIX", "VIX 恐慌指數"), ("^TNX", "美國 10 年債殖利率"), ("^IRX", "美國 3 個月國庫券殖利率"),
        ("DX-Y.NYB", "美元指數"), ("TWD=X", "美元兌台幣"), ("HG=F", "銅"), ("CL=F", "原油")]


TW_SYMS = {"^TWII", "0050.TW"}


def _drop_unfinished(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """盤中抓會多一根還沒收完的當天 K（例如台北 23:53 抓，美股才開盤兩小時）：
    台股代號台北 14:30 前、其他（美股、期貨、匯率）美東 17:00 前，當天那列切掉。"""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    tpe, ny = now.tz_convert("Asia/Taipei"), now.tz_convert("America/New_York")
    cut_tw = tpe.strftime("%Y-%m-%d") if (tpe.hour, tpe.minute) >= (14, 30) else (tpe - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    cut_us = ny.strftime("%Y-%m-%d") if ny.hour >= 17 else (ny - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    tw = df.sym.isin(TW_SYMS)
    return df[(tw & (df.date <= cut_tw)) | (~tw & (df.date <= cut_us))]


def update() -> int:
    import yfinance as yf

    out = []
    for sym, _ in SYMS:
        try:
            h = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 下載失敗：%s", sym, e)
            continue
        if h.empty:
            log.warning("%s 沒有資料", sym)
            continue
        h = h.reset_index()
        adj = h["Adj Close"] if "Adj Close" in h else h["Close"]
        out.append(pd.DataFrame({"date": pd.to_datetime(h["Date"]).dt.strftime("%Y-%m-%d"), "sym": sym,
                                 "close": h["Close"].round(4), "close_adj": adj.round(4)}))
        log.info("%s：%s～%s，%d 筆", sym, out[-1].date.min(), out[-1].date.max(), len(out[-1]))
    if not out:
        return 0
    df = _drop_unfinished(pd.concat(out, ignore_index=True).dropna(subset=["close"]))
    DATA.mkdir(parents=True, exist_ok=True)
    df.sort_values(["sym", "date"]).to_csv(LONG, index=False, compression="gzip")
    return len(df)


# (代號, 名稱, 種類, 白話)；種類 idx＝看離 200 日線、rate＝殖利率（變化用百分點）、lvl＝看水準
ROWS = [("^TWII", "加權指數", "idx", "台股大盤"), ("^SOX", "費城半導體", "idx", "台股電子權值的風向球"),
        ("^GSPC", "標普 500", "idx", "美股大盤"),
        ("^VIX", "VIX 恐慌指數", "lvl", "越高越恐慌；20 以下算平靜"),
        ("^TNX", "美國 10 年債殖利率", "rate", "越高，股票的估值壓力越大"),
        ("^IRX", "美國 3 個月國庫券殖利率", "rate", "大致等於美國短期利率"),
        ("SPREAD", "長短利差（10 年−3 個月）", "rate", "負的＝倒掛，過去常出現在景氣衰退前（但領先時間很不固定）"),
        ("DX-Y.NYB", "美元指數", "lvl", "美元強，資金通常比較不愛新興市場"),
        ("TWD=X", "美元兌台幣", "lvl", "數字越大＝台幣越弱；台幣急貶常伴隨外資賣超"),
        ("HG=F", "銅", "lvl", "「銅博士」：反映全球工業需求"),
        ("CL=F", "原油", "lvl", "油價急漲＝通膨壓力")]


def _pct_rank(s: pd.Series, years: int) -> float:
    """最新值在過去 years 年（日資料）裡的分位（0～100，越大越高）；資料不夠 years 年就不給。"""
    s = s.dropna()
    if s.empty or s.index[0] > s.index[-1] - pd.DateOffset(years=years) + pd.Timedelta(days=7):
        return np.nan
    w = s[s.index > s.index[-1] - pd.DateOffset(years=years)]
    return float((w <= w.iloc[-1]).mean() * 100)


def _ago(s: pd.Series, days: int = 365) -> float:
    s = s.dropna()
    w = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return float(w.iloc[-1]) if len(w) else np.nan


def table(long: pd.DataFrame) -> list[dict]:
    px = long.pivot(index="date", columns="sym", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    if {"^TNX", "^IRX"} <= set(px.columns):
        px["SPREAD"] = px["^TNX"] - px["^IRX"]
    out = []
    for sym, name, kind, note in ROWS:
        if sym not in px:
            continue
        s = px[sym].dropna()
        if len(s) < 260:
            continue
        v, a = float(s.iloc[-1]), _ago(s)
        r = {"sym": sym, "name": name, "kind": kind, "note": note, "date": s.index[-1].strftime("%Y-%m-%d"), "value": v,
             "spark": [round(float(x), 4) for x in s.iloc[-250:]]}
        if kind == "rate":
            r["chg1y"] = v - a
            r["p10"], r["p20"] = _pct_rank(s, 10), _pct_rank(s, 20)
        else:
            r["chg1y"] = (v / a - 1) * 100 if a else np.nan
            if kind == "idx":
                dev = (s / s.rolling(200).mean() - 1) * 100
                r["dev200"] = float(dev.iloc[-1])
                r["dd52"] = float((v / s.iloc[-250:].max() - 1) * 100)
                r["p10"], r["p20"] = _pct_rank(dev, 10), _pct_rank(dev, 20)
            else:
                r["p10"], r["p20"] = _pct_rank(s, 10), _pct_rank(s, 20)
        out.append(r)
    return out


def tw_flows(root: Path = ROOT) -> list[dict]:
    """台股資金面（資料只有一年左右，分位只算這段）：外資近 20 日買賣超、融資餘額 20 日變化、站上 20 日線比例。"""
    ex = root / "data" / "extras"
    out = []
    try:
        h = pd.read_csv(root / "data" / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
        px = h.pivot(index="date", columns="code", values="close")
        for f, col, name, note in [("inst_hist.csv.gz", "foreign", "外資近 20 日買賣超（億元）", "個股買賣超張數×收盤價加總，估算"),
                                   ("margin_hist.csv.gz", "margin_bal", "融資餘額近 20 日變化", "散戶槓桿；快速增加＝追價的人變多")]:
            d = pd.read_csv(ex / f, dtype={"code": str}, usecols=["date", "code", col])
            v = d.pivot_table(index="date", columns="code", values=col, aggfunc="sum")
            amt = (v * px.reindex(index=v.index, columns=v.columns) * 1000).sum(axis=1, min_count=1) / 1e8
            amt = amt.sort_index()
            amt.index = pd.to_datetime(amt.index)
            if col == "foreign":
                s = amt.rolling(20).sum().dropna()
                unit = "億"
            else:
                s = (amt / amt.shift(20) - 1).mul(100).dropna()
                unit = "%"
            if len(s) < 20:
                continue
            out.append({"name": name, "note": note, "date": s.index[-1].strftime("%Y-%m-%d"), "value": float(s.iloc[-1]),
                        "unit": unit, "pct": float((s <= s.iloc[-1]).mean() * 100), "since": s.index[0].strftime("%Y-%m-%d"),
                        "spark": [round(float(x), 2) for x in s.iloc[-250:]]})
    except Exception as e:  # noqa: BLE001
        log.warning("台股資金面失敗：%s", e)
    try:
        b = pd.read_csv(ex / "breadth.csv").dropna(subset=["above_ma20"]).sort_values("date")
        s = b.set_index("date").above_ma20
        out.append({"name": "站上 20 日線的股票比例", "note": "市場廣度；80% 以上＝普漲過熱、20% 以下＝普跌", "date": s.index[-1],
                    "value": float(s.iloc[-1]), "unit": "%", "pct": float((s <= s.iloc[-1]).mean() * 100),
                    "since": s.index[0], "spark": [float(x) for x in s.iloc[-250:]]})
    except Exception as e:  # noqa: BLE001
        log.warning("市場廣度失敗：%s", e)
    return out


def _spark(v: list[float]) -> str:
    v = [x for x in v if x == x]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * 100 / (len(v) - 1):.1f},{22 - (x - lo) / rng * 20:.1f}" for i, x in enumerate(v))
    return (f"<svg viewBox='0 0 100 24' width='100' height='24' preserveAspectRatio='none'>"
            f"<polyline points='{pts}' fill='none' stroke='currentColor' stroke-width='1.2' vector-effect='non-scaling-stroke'/></svg>")


def _lvl(p: float) -> str:
    if p != p:
        return ""
    return "歷史偏高" if p >= 90 else "偏高" if p >= 75 else "歷史偏低" if p <= 10 else "偏低" if p <= 25 else "中間"


def write(site_dir: Path) -> bool:
    if not LONG.exists():
        return False
    try:
        rows = table(pd.read_csv(LONG))
        flows = tw_flows()
    except Exception as e:  # noqa: BLE001
        log.warning("大環境頁產生失敗：%s", e)
        return False
    if not rows:
        return False
    from .weekly import _page
    f = lambda v, nd=1, suf="", sign=False: "—" if v is None or v != v else (f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}") + suf
    p = lambda v: "—" if v != v else f"{v:.0f}"
    trs = []
    for r in rows:
        if r["kind"] == "idx":
            now = f(r["value"], 0)
            r["note"] = f"離 200 日線 {f(r['dev200'], 1, '%', True)}、離一年高點 {f(r['dd52'], 1, '%', True)}；{r['note']}"
        elif r["kind"] == "rate":
            now = f(r["value"], 2, "%")
        else:
            now = f(r["value"], 2)
        chg = f(r["chg1y"], 2, " 點", True) if r["kind"] == "rate" else f(r["chg1y"], 1, "%", True)
        trs.append(f"<tr><td>{html.escape(r['name'])}<div class='meta'>{html.escape(r['note'])}・{r['date']}</div></td>"
                   f"<td>{now}</td><td>{chg}</td><td>{p(r['p10'])}<div class='meta'>{_lvl(r['p10'])}</div></td>"
                   f"<td>{p(r['p20'])}<div class='meta'>{_lvl(r['p20'])}</div></td><td class='sp'>{_spark(r['spark'])}</td></tr>")
    ftr = "".join(f"<tr><td>{html.escape(r['name'])}<div class='meta'>{html.escape(r['note'])}・{r['date']}（{r['since']} 起算）</div></td>"
                  f"<td>{f(r['value'], 1, ' ' + r['unit'] if r['unit'] == '億' else r['unit'], r['unit'] != '%' or r['name'].startswith('融資'))}</td>"
                  f"<td>{p(r['pct'])}<div class='meta'>{_lvl(r['pct'])}</div></td><td class='sp'>{_spark(r['spark'])}</td></tr>"
                  for r in flows)
    css = ("<style>.meta{font-size:12px;color:var(--muted)}td:first-child .meta{white-space:normal;max-width:240px;min-width:150px}td{vertical-align:top}"
           ".sp{color:var(--link)}@media(max-width:560px){.sp{display:none}td:first-child .meta{min-width:0;max-width:140px}th,td{padding:6px 5px}}</style>")
    body = (css + "<h1>大環境</h1>"
            "<blockquote>這頁只描述「現在在歷史上排在哪裡」，不是買賣訊號。我們用 1998 年以來的資料測過："
            "這些數字拿來<b>預測</b>台股空頭幾乎都沒用（假警報太多），唯一站得住的是「大盤已經從一年高點跌 10%」"
            "——那是確認、不是預知，會放在盤中頁的風險燈。</blockquote>"
            "<p>分位＝最新值在過去 10／20 年每天的值裡排第幾（0＝最低、100＝最高）。指數類看的是「離 200 日線多遠」，"
            "因為指數本身長期一直往上，比水準沒有意義。利率的一年變化單位是百分點。走勢＝近一年。</p>"
            "<h2>全球</h2><div class='tbl'><table><tr><th>項目</th><th>最新</th><th>一年變化</th><th>10 年<br>分位</th><th>20 年<br>分位</th><th class='sp'>近一年</th></tr>"
            + "".join(trs) + "</table></div>"
            + ("<h2>台股資金面</h2><p>這幾項我們只有一年左右的資料，分位只跟這一年比，參考就好。</p>"
               "<div class='tbl'><table><tr><th>項目</th><th>最新</th><th>一年內<br>分位</th><th class='sp'>近一年</th></tr>" + ftr + "</table></div>"
               if ftr else "")
            + "<p class='meta'>資料：Yahoo Finance（每天台北 06:20 更新）、證交所／櫃買（每天 15:20）。</p>")
    (site_dir / "macro.html").write_text(_page("大環境", body, "<!--SITENAV:macro-->"), "utf-8")
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print("大環境長歷史", update(), "筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
