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
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("macro")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "macro"
LONG = DATA / "long.csv.gz"
REGIME = DATA / "regime.json"
SOURCES = DATA / "sources.json"   # 每個代號 Yahoo 最近一次實際給到哪天

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


def _save_sources(yahoo_last: dict) -> None:
    """記下 Yahoo 這次實際給到每個代號的哪一天（sources.json）；這次整個沒抓到的代號沿用上次記的。
    regime 拿它跟 long.csv.gz 的最後一天比，就知道最後那幾根是 Yahoo 給的還是從舊檔沿用的（carried）。"""
    try:
        prev = json.loads(SOURCES.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        prev = {}
    prev.update({k: str(v) for k, v in yahoo_last.items()})
    DATA.mkdir(parents=True, exist_ok=True)
    SOURCES.write_text(json.dumps(prev, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")


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
    last = df.groupby("sym").date.max()
    _save_sources(last.to_dict())
    if LONG.exists():
        # Yahoo 偶爾會把最新一根暫時吃掉（10/7 07:16 抓，^TWII 少了 10/6），或整個代號下載失敗：
        # 舊檔裡比這次最後一天還新的列、這次沒抓到的代號，都留著，不讓檔案倒退。
        old = pd.read_csv(LONG)
        keep = old[old.date > old.sym.map(last).fillna("")]
        if len(keep):
            log.warning("Yahoo 這次少了 %d 列，沿用舊檔：%s", len(keep),
                        keep.groupby("sym").date.max().to_dict())
            df = pd.concat([df, keep], ignore_index=True)
    # Yahoo 有時到早上還沒給台股加權指數昨天那根（10/8 06:45 抓，^TWII 停在 10/6）→ 用證交所 FMTQIK 補收盤
    tw_done = _json_get(ROOT / "data" / "state.json", "last_done") or ""
    tw_last = df.loc[df.sym == "^TWII", "date"].max() if (df.sym == "^TWII").any() else ""
    if tw_done and tw_last and tw_done > tw_last:
        try:
            add = _twii_from_twse(tw_last, tw_done)
            if len(add):
                log.warning("^TWII Yahoo 只到 %s，用證交所補 %s", tw_last, add.date.tolist())
                df = pd.concat([df, add], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            log.warning("證交所補加權指數失敗：%s", e)
    DATA.mkdir(parents=True, exist_ok=True)
    df.sort_values(["sym", "date"]).to_csv(LONG, index=False, compression="gzip")
    return len(df)


def _parse_fmtqik(j: dict, after: str, until: str) -> pd.DataFrame:
    """證交所 FMTQIK（每日市場成交資訊）→ 加權指數收盤；日期是民國年（115/10/07）。"""
    fields = j.get("fields") or []
    col = next((i for i, f in enumerate(fields) if "加權" in str(f)), None)
    rows = []
    if col is None:
        return pd.DataFrame(columns=["date", "sym", "close", "close_adj"])
    for r in j.get("data") or []:
        try:
            y, m, d = str(r[0]).strip().split("/")
            date = f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"
            v = float(str(r[col]).replace(",", ""))
        except (ValueError, IndexError):
            continue
        if after < date <= until:
            rows.append({"date": date, "sym": "^TWII", "close": round(v, 4), "close_adj": round(v, 4)})
    return pd.DataFrame(rows, columns=["date", "sym", "close", "close_adj"])


def _twii_from_twse(after: str, until: str) -> pd.DataFrame:
    import requests

    from .fetch import _get_json
    s = requests.Session()
    months = sorted({after[:7], until[:7]})
    parts = [_parse_fmtqik(_get_json(s, "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                                     {"date": m.replace("-", "") + "01", "response": "json"}), after, until) for m in months]
    return pd.concat(parts, ignore_index=True).drop_duplicates("date")


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
        for f, col, name, note in [("inst_hist.csv.gz", "foreign", "外資近 20 日買賣超（億元）", "個股買賣超張數×收盤價加總，估算；不含 ETF，20 日加總會被單日大額綁架（詳見法人籌碼頁）"),
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
                  f"<td class='short'>{p(r['pct'])}<div class='meta'>{_lvl(r['pct'])}</div></td><td class='sp'>{_spark(r['spark'])}</td></tr>"
                  for r in flows)
    css = ("<style>.meta{font-size:12px;color:var(--muted)}td:first-child .meta{white-space:normal;max-width:240px;min-width:150px}td{vertical-align:top}"
           "td.sp{color:var(--link)}.short{color:var(--muted)}.light{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}@media(max-width:560px){.sp{display:none}td:first-child .meta{min-width:0;max-width:140px}th,td{padding:6px 5px}}</style>")
    light = ""
    if REGIME.exists():
        g = json.loads(REGIME.read_text("utf-8"))
        red = g.get("light") == "red"
        aux = "、".join(t for t, k in (("加權跌破 200 日線連 3 日（R1）", "r1"), ("費半跌破 200 日線（R3，輔助、假警報多）", "r3")) if g.get(k))
        light = ("<div class='light'>" + ("🔴 <b>風險燈：紅</b>" if red else "🟢 <b>風險燈：綠</b>")
                 + f"　加權距一年高點 {g['dd52']:+.1f}%（−10% 亮紅，R2）・{g['date']}"
                 + f"<div class='meta'>R2＝已經跌 10% 才亮，不是預知。綠燈不等於安全：加權現在離 200 日線 {g['dev200']:+.1f}%，"
                 f"在 {g['dev200_since'][:4]} 年以來排第 {g['dev200_pct_all']:.0f} 百分位。"
                 + (f"另外亮著：{aux}。" if aux else "R1、R3 都沒亮。") + "燈亮只提醒、不自動賣。</div>"
                 + ("".join(f"<div class='meta'>⚠️ {html.escape(x['sym'])} "
                            + (f"已經連 {x.get('carried_days')} 個交易日沒從 Yahoo 抓到新資料、沿用舊檔" if x.get("reason") == "carried"
                               else f"停在 {x['last']}（應該到 {x['ref']}）")
                            + ("，<b>燈號用的就是它，燈可能是舊的</b>" if x["sym"] in CORE else "") + "</div>"
                            for x in g.get("stale", [])))
                 + "</div>")
    body = (css + "<h1>大環境</h1>" + light +
            "<blockquote>這頁只描述「現在在歷史上排在哪裡」，不是買賣訊號。我們用 1998 年以來的資料測過："
            "這些數字拿來<b>預測</b>台股空頭幾乎都沒用（假警報太多），唯一站得住的是「大盤已經從一年高點跌 10%」"
            "——那是確認、不是預知，就是上面的風險燈。</blockquote>"
            "<p>分位＝最新值在過去 10／20 年每天的值裡排第幾（0＝最低、100＝最高）。指數類看的是「離 200 日線多遠」，"
            "因為指數本身長期一直往上，比水準沒有意義。利率的一年變化單位是百分點。走勢＝近一年。</p>"
            "<h2>全球</h2><div class='tbl'><table><tr><th>項目</th><th>最新</th><th>一年變化</th><th>10 年<br>分位</th><th>20 年<br>分位</th><th class='sp'>近一年</th></tr>"
            + "".join(trs) + "</table></div>"
            + ("<h2>台股資金面</h2><p>這幾項我們只有一年左右的資料，分位只跟這一年比（灰字），<b>不能跟上面 10／20 年的分位放在一起比</b>：一年裡的 80 分跟 20 年裡的 80 分不是同一把尺。</p>"
               "<div class='tbl'><table><tr><th>項目</th><th>最新</th><th class='short'>一年分位<br>（樣本短，參考）</th><th class='sp'>近一年</th></tr>" + ftr + "</table></div>"
               if ftr else "")
            + "<p class='meta'>資料：Yahoo Finance（每天台北 06:20 更新）、證交所／櫃買（每天 15:20）。</p>")
    (site_dir / "macro.html").write_text(_page("大環境", body, "<!--SITENAV:macro-->"), "utf-8")
    return True


def _dev200(s: pd.Series) -> pd.Series:
    return (s / s.rolling(200).mean() - 1) * 100


# 落後檢查（Cowork 0726、0755、0825）：同一組代號應該停在同一個交易日。每組的參考日＝組內最新的一天，
# 台股另外比 daily 的 state.json、美股指數另外比 us/meta.json（整組一起沒更新也抓得到）。
# 美股、匯率期貨兩組數落後天數時跳過美國股市＋債市假日（債市多放哥倫布日、退伍軍人節，美債殖利率那兩天沒資料），
# 不會因為放假誤報；台股組靠 state.json，不用假日表。燈號用的是加權、費半（CORE）。
STALE_GROUPS = {"tw": ("^TWII", "0050.TW"), "us": ("^SOX", "^GSPC", "^VIX", "^TNX", "^IRX"),
                "fx": ("DX-Y.NYB", "TWD=X", "HG=F", "CL=F")}
CORE = ("^TWII", "^SOX")
# NYSE 休市＋SIFMA 債市休市（2026～2027；過了要補）
US_HOLIDAYS = ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
               "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25",
               "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
               "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25", "2027-12-24"]
CARRIED_MAX = 3   # 連續沿用舊檔幾個交易日以上就算落後


def _json_get(path: Path, key: str) -> str:
    try:
        return str(json.loads(path.read_text("utf-8")).get(key) or "")
    except Exception:  # noqa: BLE001
        return ""


def _lag(d: str, ref: str, hol: list[str]) -> int:
    """d 之後到 ref（含）之間有幾個交易日（平日、扣掉 hol）。"""
    one = np.timedelta64(1, "D")
    return int(np.busday_count(np.datetime64(d) + one, np.datetime64(ref) + one, holidays=hol))


def staleness(long: pd.DataFrame) -> tuple[dict, dict, list]:
    """回傳（每個代號最後一天, 每個代號來源, 落後清單）。
    來源：yahoo＝最後一根是 Yahoo 這次給的；carried＝Yahoo 少給、沿用舊檔（carried_days＝沿用了幾個交易日）。
    落後：比參考日少 ≥1 個交易日（behind），或沿用舊檔 ≥CARRIED_MAX 個交易日（carried）。"""
    last = long.groupby("sym").date.max().to_dict()
    ext = {"tw": _json_get(ROOT / "data" / "state.json", "last_done"), "us": _json_get(ROOT / "data" / "us" / "meta.json", "last")}
    try:
        ylast = json.loads(SOURCES.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        ylast = {}
    src, stale = {}, []
    for g, syms in STALE_GROUPS.items():
        hol = [] if g == "tw" else US_HOLIDAYS
        ref = max([last[s] for s in syms if s in last] + [ext.get(g, "")])
        for s in syms:
            d = last.get(s)
            y = ylast.get(s)
            cd = _lag(y, d, hol) if d and y and y < d else 0
            src[s] = {"source": "carried" if cd else "yahoo", "yahoo_last": y, "carried_days": cd}
            lag = _lag(d, ref, hol) if d else None
            if d is None or lag >= 1:
                stale.append({"sym": s, "last": d, "ref": ref, "lag": lag, "reason": "behind"})
            elif cd >= CARRIED_MAX:
                stale.append({"sym": s, "last": d, "ref": ref, "lag": 0, "reason": "carried", "carried_days": cd})
    return last, src, stale


def regime(long: pd.DataFrame) -> dict:
    """大盤風險燈（協作板 2358 回測、Cowork 0026 條件）：
    R2 主燈＝加權收盤距 52 週（250 交易日）高 ≤ −10%；R1＝加權跌破 200 日線連 3 日；R3＝費半收盤 < 200 日線（輔助、假警報多）。
    R2 是『已經跌 10% 才亮』，不是預知；綠燈不等於安全，所以並列偏離 200 日線的歷史分位。"""
    long = _drop_unfinished(long)  # 手動在盤中跑也不會拿到還沒收完的半根（Cowork 0349）
    px = long.pivot(index="date", columns="sym", values="close").sort_index()
    tw = px["^TWII"].dropna()
    dd = (tw / tw.rolling(250, min_periods=200).max() - 1) * 100
    r2 = dd <= -10
    below = tw < tw.rolling(200).mean()
    r1 = below & below.shift(1, fill_value=False) & below.shift(2, fill_value=False)
    dev = _dev200(tw)
    since = r2.ne(r2.shift()).cumsum()
    on_since = r2[since == since.iloc[-1]].index[0]
    out = {"date": tw.index[-1], "close": round(float(tw.iloc[-1]), 2), "light": "red" if r2.iloc[-1] else "green",
           "light_since": on_since, "r2": bool(r2.iloc[-1]), "dd52": round(float(dd.iloc[-1]), 2),
           "r1": bool(r1.iloc[-1]), "dev200": round(float(dev.iloc[-1]), 2),
           "dev200_pct20y": round(_pct_rank(dev.set_axis(pd.to_datetime(dev.index)), 20), 1),
           "dev200_pct_all": round(float((dev.dropna() <= dev.iloc[-1]).mean() * 100), 1),
           "dev200_since": dev.dropna().index[0]}
    if "^SOX" in px:
        sox = px["^SOX"].dropna()
        out.update({"r3": bool(sox.iloc[-1] < sox.rolling(200).mean().iloc[-1]), "sox_date": sox.index[-1],
                    "sox_dev200": round(float(_dev200(sox).iloc[-1]), 2)})
    last, src, stale = staleness(long)
    out.update({"last_dates": last, "sources": src, "stale": stale, "stale_core": [x["sym"] for x in stale if x["sym"] in CORE]})
    if stale:
        log.warning("有代號沒更新到最新交易日：%s", stale)
    return out


def update_regime(notify: bool = True) -> dict | None:
    """算 regime.json；主燈（R2）跟上一次存的不一樣就通知 Dennis（第一次產生不通知）。"""
    if not LONG.exists():
        return None
    r = regime(pd.read_csv(LONG))
    prev = json.loads(REGIME.read_text("utf-8")) if REGIME.exists() else None
    r["changed"] = bool(prev and prev.get("light") != r["light"])
    r["prev_light"] = prev.get("light") if prev else None
    if r["changed"]:
        r["prev_light_since"] = prev.get("light_since")
        tw = pd.read_csv(LONG).query("sym == '^TWII'").date
        r["prev_light_days"] = int(((tw >= prev.get("light_since", r["date"])) & (tw < r["light_since"])).sum())
    REGIME.write_text(json.dumps(r, ensure_ascii=False, indent=1), "utf-8")
    log.info("風險燈 %s（距一年高點 %+.1f%%，離 200 日線 %+.1f%%）", r["light"], r["dd52"], r["dev200"])
    if r["changed"] and notify:
        from . import notify as nt
        if r["light"] == "red":
            title = f"⚠️ 大盤風險燈轉紅（{r['date']}）"
            body = (f"加權 {r['close']:,.0f}，已從一年高點下跌 {-r['dd52']:.1f}%（R2）。\n\n"
                    "這是『已經跌了 10%』的確認，不是預知；1998 年以來 12 次大空頭每次都會經過這裡，"
                    "但每年也約有 1.3 次只是一般回檔。帳本規則：只提醒，不自動賣；這時候不要加碼，檢查自己承受得了。")
        else:
            title = f"✅ 大盤風險燈轉綠（{r['date']}）"
            body = f"加權 {r['close']:,.0f}，距一年高點 {r['dd52']:+.1f}%，回到 −10% 以內。綠燈不等於安全。"
        prev_txt = {"red": "紅燈", "green": "綠燈"}.get(r["prev_light"], r["prev_light"])
        body += (f"\n\n上一段{prev_txt}從 {r['prev_light_since']} 起維持了 {r['prev_light_days']} 個交易日"
                 + ("（很短，可能只是抖一下）。" if r["prev_light_days"] < 5 else "。"))
        nt.send(title, body + "\n\n<sub>自動產生，僅供參考，不構成投資建議。</sub>")
    return r


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print("大環境長歷史", update(), "筆")
    print("風險燈", update_regime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
