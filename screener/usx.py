"""美股選股（Dennis 10/7「把進軍美股的裝備準備好」）：S&P 500＋那斯達克 100＋常用 ETF／ADR。

跟 us.py（美股隔夜→台股）分開：這支是「選美股」本身。資料只能在 GitHub Actions 上抓（yfinance、維基百科；本機連不到）。
  python -m screener.usx                    每天（us.yml 06:20）：成分股清單、近 13 個月日 K（還原價）、基本面輪流補、跑篩選
  python -m screener.usx --backtest 5 OUT   5 年日 K 存 OUT（us.yml 手動參數，推到 us-backtest 分支，研究用）
產出：
  data/usx/universe.csv   code, name, name_zh, sector, industry, group（SP500／NDX／ETF／ADR）
  data/usx/history.csv.gz date, code, open, high, low, close, volume（還原價，股數）
  data/usx/fund.csv       基本面：市值、本益比、預估本益比、殖利率、營收／獲利成長、毛利率、下次財報日、目標價…（每天補最舊的一批）
  data/usx/results/YYYY-MM-DD.csv  date, strategy, code
  data/usx/meta.json
網頁：site/usx.html（美股篩選）＋個股頁（stockpage 也產美股代號）。
"""
from __future__ import annotations

import datetime as dt
import html
import io
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

log = logging.getLogger("usx")
ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "usx"
UNIV = DIR / "universe.csv"
HIST = DIR / "history.csv.gz"
FUND = DIR / "fund.csv"
META = DIR / "meta.json"
RES = DIR / "results"
BT5 = DIR / "strat_5y.json"

ETFS = [("SPY", "標普 500 ETF"), ("QQQ", "那斯達克 100 ETF"), ("DIA", "道瓊 ETF"), ("IWM", "羅素 2000 小型股 ETF"),
        ("VTI", "美國全市場 ETF"), ("VOO", "Vanguard 標普 500"), ("SMH", "半導體 ETF（VanEck）"), ("SOXX", "半導體 ETF（iShares）"),
        ("XLK", "科技類股 ETF"), ("XLF", "金融類股 ETF"), ("XLE", "能源類股 ETF"), ("XLV", "醫療類股 ETF"),
        ("XLY", "非必需消費 ETF"), ("XLP", "必需消費 ETF"), ("XLI", "工業類股 ETF"), ("XLB", "原物料 ETF"),
        ("XLU", "公用事業 ETF"), ("XLRE", "房地產 ETF"), ("XLC", "通訊服務 ETF"), ("TLT", "20 年以上美債 ETF"),
        ("IEF", "7-10 年美債 ETF"), ("SHY", "1-3 年美債 ETF"), ("GLD", "黃金 ETF"), ("SLV", "白銀 ETF"),
        ("USO", "原油 ETF"), ("VNQ", "不動產 REIT ETF"), ("SCHD", "高股息 ETF（Schwab）"), ("VYM", "高股息 ETF（Vanguard）"),
        ("ARKK", "ARK 創新 ETF"), ("EEM", "新興市場 ETF"), ("EWT", "台灣 ETF（iShares）"), ("EWJ", "日本 ETF"),
        ("FXI", "中國大型股 ETF"), ("IBIT", "比特幣現貨 ETF"), ("TQQQ", "3 倍做多那斯達克"), ("SOXL", "3 倍做多半導體")]
ADRS = [("TSM", "台積電 ADR"), ("ASML", "艾司摩爾"), ("BABA", "阿里巴巴"), ("SONY", "索尼"), ("TM", "豐田"),
        ("SAP", "SAP"), ("NVO", "諾和諾德"), ("UMC", "聯電 ADR"), ("ASX", "日月光 ADR"), ("CHT", "中華電信 ADR"),
        ("HIMX", "奇景光電"), ("SHOP", "Shopify"), ("SE", "Sea"), ("NU", "Nu Holdings"), ("PDD", "拼多多"), ("ARM", "安謀")]
ZH = {"AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "GOOGL": "Google A", "GOOG": "Google C", "AMZN": "亞馬遜",
      "META": "Meta", "TSLA": "特斯拉", "AVGO": "博通", "AMD": "超微", "INTC": "英特爾", "QCOM": "高通", "MU": "美光",
      "AMAT": "應用材料", "LRCX": "科林研發", "KLAC": "科磊", "MRVL": "邁威爾", "TXN": "德州儀器", "ADI": "亞德諾",
      "NXPI": "恩智浦", "ON": "安森美", "MCHP": "微芯", "SMCI": "美超微", "DELL": "戴爾", "HPQ": "惠普", "HPE": "慧與",
      "ORCL": "甲骨文", "CRM": "Salesforce", "ADBE": "Adobe", "NFLX": "網飛", "CSCO": "思科", "IBM": "IBM",
      "PLTR": "Palantir", "NOW": "ServiceNow", "UBER": "Uber", "ABNB": "Airbnb", "PYPL": "PayPal", "SBUX": "星巴克",
      "COST": "好市多", "WMT": "沃爾瑪", "HD": "家得寶", "MCD": "麥當勞", "KO": "可口可樂", "PEP": "百事",
      "PG": "寶僑", "JNJ": "嬌生", "LLY": "禮來", "PFE": "輝瑞", "MRK": "默克", "ABBV": "艾伯維", "UNH": "聯合健康",
      "JPM": "摩根大通", "BAC": "美國銀行", "WFC": "富國銀行", "GS": "高盛", "MS": "摩根士丹利", "V": "Visa",
      "MA": "萬事達", "BRK-B": "波克夏 B", "XOM": "埃克森美孚", "CVX": "雪佛龍", "BA": "波音", "CAT": "開拓重工",
      "GE": "奇異", "DIS": "迪士尼", "NKE": "Nike", "T": "AT&T", "VZ": "Verizon", "ANET": "Arista", "CDNS": "益華",
      "SNPS": "新思", "INTU": "Intuit", "PANW": "Palo Alto", "CRWD": "CrowdStrike", "ISRG": "直覺手術", "COIN": "Coinbase",
      "MSTR": "微策略", "APP": "AppLovin", "VRT": "維諦技術", "GEV": "GE Vernova", "CEG": "Constellation 能源"}
ZH.update(dict(ETFS + ADRS))


# ------------------------------------------------------------------ 抓資料（只在 Actions 上跑）
def fetch_universe() -> pd.DataFrame:
    """維基百科 S&P 500、那斯達克 100 成分股（含 GICS 產業），加 ETF／ADR。抓不到就沿用舊檔。"""
    import requests
    rows = {}
    for url, grp, want in [("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "SP500", ("symbol", "security")),
                           ("https://en.wikipedia.org/wiki/Nasdaq-100", "NDX", ("ticker", "company"))]:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (tw-stock-screener research)"}, timeout=30)
            for t in pd.read_html(io.StringIO(r.text)):
                cols = {str(c[-1] if isinstance(c, tuple) else c).split("[")[0].strip().lower(): c for c in t.columns}
                ks = next((cols[k] for k in ("symbol", "ticker") if k in cols), None)
                kn = next((cols[k] for k in ("security", "company") if k in cols), None)
                if ks is None or kn is None or len(t) < 90:
                    continue
                ksec, kind = cols.get("gics sector"), cols.get("gics sub-industry")
                for _, x in t.iterrows():
                    sym = str(x[ks]).strip().replace(".", "-")
                    if not sym.isascii() or not sym:
                        continue
                    old = rows.get(sym, {})
                    rows[sym] = {"code": sym, "name": str(x[kn]).strip(),
                                 "sector": str(x[ksec]).strip() if ksec is not None and pd.notna(x[ksec]) else old.get("sector", ""),
                                 "industry": str(x[kind]).strip() if kind is not None and pd.notna(x[kind]) else old.get("industry", ""),
                                 "group": "+".join(sorted(set(filter(None, [old.get("group", ""), grp]))))}
                log.info("成分股 %s：累計 %d 檔", grp, len(rows))
                break
        except Exception as e:  # noqa: BLE001
            log.warning("成分股抓不到 %s：%s", url, e)
    if len(rows) < 450:
        if UNIV.exists():
            log.warning("成分股只抓到 %d 檔，沿用舊清單", len(rows))
            return pd.read_csv(UNIV, dtype=str).fillna("")
        raise RuntimeError(f"成分股只抓到 {len(rows)} 檔，也沒有舊清單")
    for grp, lst in (("ETF", ETFS), ("ADR", ADRS)):
        for sym, zh in lst:
            if sym in rows:
                rows[sym]["group"] = "+".join(sorted({rows[sym]["group"], grp}))
            else:
                rows[sym] = {"code": sym, "name": zh, "sector": "ETF" if grp == "ETF" else "", "industry": "", "group": grp}
    df = pd.DataFrame(rows.values())
    df["name_zh"] = df.code.map(ZH).fillna("")
    return df[["code", "name", "name_zh", "sector", "industry", "group"]].sort_values("code")


def fetch_history(syms: list[str], period: str = "13mo") -> pd.DataFrame:
    import yfinance as yf
    frames = []
    for i in range(0, len(syms), 80):
        chunk = syms[i:i + 80]
        raw = None
        for k in range(3):
            try:
                raw = yf.download(chunk, period=period, auto_adjust=True, group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("下載 %d～ 失敗（%d）：%s", i, k + 1, e)
                time.sleep(10)
        if raw is None or raw.empty:
            continue
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            if sub.empty:
                continue
            frames.append(pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "code": t,
                                        "open": sub["Open"].round(4).values, "high": sub["High"].round(4).values,
                                        "low": sub["Low"].round(4).values, "close": sub["Close"].round(4).values,
                                        "volume": sub["Volume"].fillna(0).astype("int64").values}))
        time.sleep(1)
    if not frames:
        raise RuntimeError("美股日 K 全部抓不到")
    h = pd.concat(frames, ignore_index=True)
    # 美股還沒收完（美東 16:30 前）就切掉當天那根半根 K；再以 SPY 最後一天為準
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    if now.hour * 60 + now.minute < 16 * 60 + 30:
        h = h[h.date < now.date().isoformat()]
    last = h.loc[h.code == "SPY", "date"].max()
    return h[h.date <= last] if isinstance(last, str) else h


FUND_COLS = ["code", "updated", "mcap", "pe", "fpe", "pb", "yield", "rev_g", "eps_g", "gross_m", "op_m", "roe",
             "beta", "short_pct", "target", "rec", "n_analyst", "next_earn", "last_earn", "employees", "summary"]


def _ts(v) -> str:
    try:
        return dt.datetime.utcfromtimestamp(int(v)).strftime("%Y-%m-%d") if v else ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_fund(syms: list[str], budget_min: float = 12, max_n: int = 200) -> int:
    """基本面輪流補：沒有的先補，再補最舊的；一次最多 max_n 檔或 budget_min 分鐘（Yahoo 會擋太密的請求）。"""
    import yfinance as yf
    old = pd.read_csv(FUND, dtype={"code": str}) if FUND.exists() else pd.DataFrame(columns=FUND_COLS)
    have = dict(zip(old.code, old.updated))
    order = sorted(syms, key=lambda s: (s in have, have.get(s, "")))[:max_n]
    t0, rows = time.time(), []
    for s in order:
        if time.time() - t0 > budget_min * 60:
            break
        try:
            i = yf.Ticker(s).info or {}
        except Exception as e:  # noqa: BLE001
            log.warning("%s 基本面失敗：%s", s, e)
            time.sleep(2)
            continue
        g = lambda k, m=1, nd=2: round(float(i[k]) * m, nd) if isinstance(i.get(k), (int, float)) else None  # noqa: E731
        y = i.get("dividendYield")
        rows.append({"code": s, "updated": dt.date.today().isoformat(), "mcap": g("marketCap", 1e-9, 1),
                     "pe": g("trailingPE", 1, 1), "fpe": g("forwardPE", 1, 1), "pb": g("priceToBook", 1, 2),
                     # yfinance 新版 dividendYield 已經是 %（0.45＝0.45%），舊版是小數（0.0045）
                     "yield": None if not isinstance(y, (int, float)) else round(y if y > 0.3 else y * 100, 2),
                     "rev_g": g("revenueGrowth", 100, 1), "eps_g": g("earningsGrowth", 100, 1),
                     "gross_m": g("grossMargins", 100, 1), "op_m": g("operatingMargins", 100, 1), "roe": g("returnOnEquity", 100, 1),
                     "beta": g("beta"), "short_pct": g("shortPercentOfFloat", 100, 1), "target": g("targetMeanPrice"),
                     "rec": i.get("recommendationKey") or "", "n_analyst": i.get("numberOfAnalystOpinions"),
                     "next_earn": _ts(i.get("earningsTimestampStart") or i.get("earningsTimestamp")),
                     "last_earn": _ts(i.get("mostRecentQuarter")), "employees": i.get("fullTimeEmployees"),
                     "summary": (i.get("longBusinessSummary") or "")[:400]})
        time.sleep(0.4)
    if rows:
        new = pd.DataFrame(rows, columns=FUND_COLS)
        out = pd.concat([old[~old.code.isin(new.code)], new], ignore_index=True).sort_values("code")
        out.to_csv(FUND, index=False)
    log.info("基本面補了 %d 檔（%.0f 秒）", len(rows), time.time() - t0)
    return len(rows)


# ------------------------------------------------------------------ 篩選
def _cfg() -> dict:
    return (yaml.safe_load((ROOT / "config.yaml").read_text("utf-8")) or {}).get("us_screener", {})


def signal_matrix(h: pd.DataFrame, cfg: dict | None = None) -> tuple:
    """回傳 (panel, {策略: 布林寬表})——每天篩選跟 5 年回測共用。"""
    from . import rules
    cfg = cfg or _cfg()
    p = rules.Panel(h)

    def ev(cond):
        m = rules.CONDITIONS[cond["type"]][0](p, cond).fillna(False).astype(bool)
        if cond.get("days_ago"):
            m = m.shift(int(cond["days_ago"]), fill_value=False)
        if cond.get("within"):
            m = m.astype(int).rolling(int(cond["within"]), min_periods=1).max().astype(bool)
        return ~m if cond.get("not") else m

    base = p.traded.copy()
    for c in cfg.get("base_filter", []):
        base &= ev(c)
    out = {}
    for s in cfg.get("strategies", []):
        if not s.get("enabled", True):
            continue
        m = base.copy()
        for c in s.get("conditions", []):
            m &= ev(c)
        out[s["name"]] = m
    return p, out


def screen(h: pd.DataFrame) -> pd.DataFrame:
    p, sig = signal_matrix(h)
    d = p.close.index[-1]
    rows = [{"date": d, "strategy": k, "code": c} for k, m in sig.items() for c in m.columns[m.iloc[-1].to_numpy()]]
    return pd.DataFrame(rows, columns=["date", "strategy", "code"])


def update() -> dict:
    DIR.mkdir(parents=True, exist_ok=True)
    RES.mkdir(parents=True, exist_ok=True)
    uni = fetch_universe()
    uni.to_csv(UNIV, index=False)
    h = fetch_history(list(uni.code))
    h.sort_values(["code", "date"]).to_csv(HIST, index=False)
    res = screen(h)
    d = h.date.max()
    res.to_csv(RES / f"{d}.csv", index=False)
    for old in sorted(RES.glob("*.csv"))[:-120]:   # 留近 120 個交易日
        old.unlink()
    try:
        fetch_fund(list(uni.code))
    except Exception as e:  # noqa: BLE001
        log.warning("基本面失敗：%s", e)
    meta = {"last": d, "codes": int(h.code.nunique()), "rows": len(h), "universe": len(uni),
            "results": res.strategy.value_counts().to_dict(), "updated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    META.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    log.info("美股：%s", meta)
    return meta


# ------------------------------------------------------------------ 網頁
def _load():
    if not (UNIV.exists() and HIST.exists()):
        return None
    uni = pd.read_csv(UNIV, dtype=str).fillna("")
    h = pd.read_csv(HIST, dtype={"code": str})
    fund = pd.read_csv(FUND, dtype={"code": str}) if FUND.exists() else pd.DataFrame(columns=FUND_COLS)
    res = pd.concat([pd.read_csv(f, dtype=str) for f in sorted(RES.glob("*.csv"))], ignore_index=True) \
        if RES.exists() and any(RES.glob("*.csv")) else pd.DataFrame(columns=["date", "strategy", "code"])
    return uni, h, fund, res


def rows_for_page(uni, h, fund, res) -> tuple[str, list[dict], list[dict]]:
    h = h.sort_values(["code", "date"])
    last = h.date.max()
    today = res[res.date == last]
    g = h.groupby("code")
    close = g.close.last()
    prev = g.close.apply(lambda s: s.iloc[-2] if len(s) >= 2 else np.nan)
    v = g.volume.last()
    v20 = g.volume.apply(lambda s: s.iloc[-21:-1].mean() if len(s) >= 21 else np.nan)
    hi52 = g.high.apply(lambda s: s.iloc[-252:].max())
    r20 = g.close.apply(lambda s: s.iloc[-1] / s.iloc[-21] - 1 if len(s) >= 21 else np.nan)
    dv = (h.assign(dv=h.close * h.volume).groupby("code").dv.apply(lambda s: s.iloc[-20:].mean()) / 1e6)
    U = uni.set_index("code")
    F = fund.set_index("code") if len(fund) else pd.DataFrame()
    tags = today.groupby("code").strategy.apply(list).to_dict()
    cfg = _cfg()
    strats = []
    for s in cfg.get("strategies", []):
        from . import rules
        strats.append({"name": s["name"], "desc": "、".join(rules.describe(c) for c in s.get("conditions", [])),
                       "codes": sorted(today[today.strategy == s["name"]].code)})
    out = []
    for c in sorted(tags):
        if c not in close.index:
            continue
        u = U.loc[c] if c in U.index else {}
        f = F.loc[c] if len(F) and c in F.index else {}
        fv = lambda k: (None if k not in f or pd.isna(f[k]) else f[k])  # noqa: E731
        out.append({"code": c, "name": u.get("name", "") if len(u) else "", "zh": u.get("name_zh", "") if len(u) else "",
                    "sector": u.get("sector", "") if len(u) else "", "close": round(float(close[c]), 2),
                    "chg": round(float((close[c] / prev[c] - 1) * 100), 2) if prev[c] == prev[c] else None,
                    "vx": round(float(v[c] / v20[c]), 2) if v20[c] and v20[c] == v20[c] else None,
                    "hi52": round(float((close[c] / hi52[c] - 1) * 100), 1), "r20": None if r20[c] != r20[c] else round(float(r20[c] * 100), 1),
                    "dv": round(float(dv[c]), 0), "mcap": fv("mcap"), "pe": fv("pe"), "fpe": fv("fpe"),
                    "next_earn": fv("next_earn") or "", "tags": tags[c]})
    return last, strats, out


PAGE_CSS = """:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--line:#ddddd6;--link:#2f55c4;--card:#fff;--up:#0b8a3a;--down:#d03b3b;--accent:#2f5bd3}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--line:#34342f;--link:#8fa8f5;--card:#1d1d1b;--up:#3fbf6a;--down:#f06b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}a{color:var(--link)}
h1{font-size:23px;margin:8px 0 4px}.meta{color:var(--muted);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:12px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;text-align:left;font:inherit;color:inherit;cursor:pointer}
.tile.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.tile .n{font-size:24px;font-weight:650}.tile .t{font-weight:600}.tile .d{color:var(--muted);font-size:12px}
.tile .bt{font-size:11.5px;margin-top:4px;padding-top:4px;border-top:1px dashed var(--line);color:var(--muted)}
.note{color:var(--muted);font-size:12.5px;margin:4px 0 10px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:14px}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;cursor:pointer;position:sticky;top:0;background:var(--card)}td.l,th.l{text-align:left}
.up{color:var(--up)}.down{color:var(--down)}.tag{display:inline-block;font-size:11.5px;padding:0 7px;margin:1px 2px;border-radius:999px;background:rgba(128,128,128,.14)}
input{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:220px;max-width:100%}"""


def write(site_dir: Path) -> bool:
    got = _load()
    if got is None:
        return False
    last, strats, rows = rows_for_page(*got)
    uni, _, fund, _ = got
    cal = ""
    if len(fund) and "next_earn" in fund:
        f = fund.merge(uni[["code", "name", "name_zh", "group"]], on="code", how="left")
        f = f[f.next_earn.fillna("").str.len() == 10]
        end = (dt.date.fromisoformat(last) + dt.timedelta(days=15)).isoformat()
        f = f[(f.next_earn > last) & (f.next_earn <= end) & ~f.group.fillna("").str.contains("ETF")]
        f = f.sort_values(["next_earn", "mcap"], ascending=[True, False])
        if len(f):
            items = []
            for d, g in f.groupby("next_earn", sort=True):
                wd = "一二三四五六日"[dt.date.fromisoformat(d).weekday()]
                names = "、".join(f"<a href='stock.html?code={html.escape(r.code)}'>{html.escape(r.name_zh if isinstance(r.name_zh, str) and r.name_zh else r.code)}</a>"
                                 for r in g.head(12).itertuples())
                more = f" 等 {len(g)} 家" if len(g) > 12 else ""
                items.append(f"<li><b>{d[5:]}（{wd}）</b> {names}{more}</li>")
            cal = (f"<details class='box' open><summary><b>📅 未來兩週財報</b>（{len(f)} 家，照市值排；日期是美東時間，"
                   f"盤後公布的台北隔天早上才看得到反應）</summary><ul style='margin:6px 0;padding-left:20px'>{''.join(items)}</ul>"
                   f"<div class='meta'>財報日來自 Yahoo，每天輪流更新一批，可能有幾天誤差；還沒補到基本面的公司不會出現。</div></details>")
    bt = json.loads(BT5.read_text("utf-8")) if BT5.exists() else {}
    cost = _cfg().get("cost_pct", 0.3)
    js = lambda o: json.dumps(o, ensure_ascii=False, default=str).replace("</", "<\\/")  # noqa: E731
    body = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>美股篩選</title><style>{PAGE_CSS}</style></head><body><main><!--SITENAV:usx-->
<h1>🇺🇸 美股篩選</h1><div class="meta">{html.escape(last)} 美股收盤（台北隔天早上 06:20 更新）・S&amp;P 500＋那斯達克 100＋常用 ETF／ADR，{len(got[0])} 檔・價格為還原價（美元）</div>
<div class="tiles" id="tiles"></div><p class="note" id="btnote"></p>
{cal}
<div class="box">💵 <b>台灣人買美股要知道的</b>：國泰複委託網路下單，<b>個股買賣各 0.08%、不設最低收費</b>（優惠到 2026/12/31）；ETF 每筆 3 美元（小額買 ETF 反而貴）。
賣出另有美國 SEC 規費（約 0.003%）。換匯有價差，錢留在美元帳戶就只付一次。現金股利先扣 30% 美國稅。美股沒有漲跌停、一股就能買，交割 T+1。
交易時間台北 21:30～04:00（夏令）／22:30～05:00（冬令），所以這裡的名單是「早上看、當晚開盤買」。回測成本抓來回 {cost}%（手續費 0.16%＋買賣價差）。</div>
<p><input id="q" placeholder="搜尋代號、名稱、產業"> <span class="meta" id="cnt"></span></p>
<div class="tbl"><table><thead><tr><th class="l" data-k="code">代號</th><th class="l" data-k="name">名稱</th><th class="l" data-k="sector">產業</th>
<th data-k="close">收盤</th><th data-k="chg">漲跌%</th><th data-k="vx">量/20日均</th><th data-k="r20">20日%</th><th data-k="hi52">距52週高%</th>
<th data-k="mcap">市值(十億)</th><th data-k="pe">本益比</th><th data-k="fpe">預估本益比</th><th data-k="next_earn">下次財報</th><th class="l">符合策略</th></tr></thead><tbody id="bd"></tbody></table></div>
<p class="meta">不構成投資建議。股票池是「現在」的成分股，回測有存活者偏差（被踢出指數的爛股不在裡面），數字偏樂觀。</p>
</main><script>
const STRATS={js(strats)},DATA={js(rows)},BT5={js(bt)};
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
let sel=null,sk='chg',sd=-1;const pc=v=>(v>0?'+':'')+v.toFixed(2)+'%';
function bt(n){{const b=(BT5.strategies||[]).find(x=>x.name===n);if(!b)return'';const a=b.next_open_5,c=b.next_open_20;
 return `<div class="bt">5 年・隔天開盤買：5 日 <b>${{pc(a.ex)}}</b>｜20 日 <b>${{pc(c.ex)}}</b>｜勝率 ${{a.win.toFixed(0)}}%</div>`}}
function tiles(){{$('tiles').innerHTML=STRATS.map((s,i)=>`<button class="tile${{sel===i?' on':''}}" data-i="${{i}}"><div class="n">${{s.codes.length}}</div><div class="t">${{esc(s.name)}}</div><div class="d">${{esc(s.desc)}}</div>${{bt(s.name)}}</button>`).join('');
 document.querySelectorAll('.tile').forEach(b=>b.onclick=()=>{{const i=+b.dataset.i;sel=sel===i?null:i;tiles();render()}})}}
function f(v,d=2){{return v==null||v===''?'—':typeof v==='number'?v.toLocaleString('en-US',{{maximumFractionDigits:d,minimumFractionDigits:d}}):esc(v)}}
function render(){{const q=$('q').value.trim().toLowerCase();let R=DATA.filter(r=>(sel===null||r.tags.includes(STRATS[sel].name))&&(!q||(r.code+r.name+r.zh+r.sector).toLowerCase().includes(q)));
 R.sort((a,b)=>{{const x=a[sk],y=b[sk];if(x==null||x==='')return 1;if(y==null||y==='')return -1;return(x>y?1:x<y?-1:0)*sd}});$('cnt').textContent=`${{R.length}} 檔`;
 $('bd').innerHTML=R.map(r=>`<tr><td class="l"><a href="stock.html?code=${{encodeURIComponent(r.code)}}">${{esc(r.code)}}</a></td><td class="l">${{esc(r.zh||r.name)}}${{r.zh?`<div class="meta">${{esc(r.name)}}</div>`:''}}</td><td class="l meta">${{esc(r.sector)}}</td>
 <td>${{f(r.close)}}</td><td class="${{r.chg>0?'up':r.chg<0?'down':''}}">${{r.chg==null?'—':pc(r.chg)}}</td><td>${{f(r.vx)}}</td><td class="${{r.r20>0?'up':r.r20<0?'down':''}}">${{f(r.r20,1)}}</td><td>${{f(r.hi52,1)}}</td>
 <td>${{f(r.mcap,1)}}</td><td>${{f(r.pe,1)}}</td><td>${{f(r.fpe,1)}}</td><td>${{esc(r.next_earn||'—')}}</td><td class="l">${{r.tags.map(t=>`<span class="tag">${{esc(t)}}</span>`).join('')}}</td></tr>`).join('')}}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{{const k=th.dataset.k;if(sk===k)sd*=-1;else{{sk=k;sd=(k==='code'||k==='name'||k==='sector'||k==='next_earn')?1:-1}}render()}});
$('q').oninput=render;tiles();render();
if(BT5.strategies){{const L=BT5.strategies,lose=L.filter(x=>x.next_open_5.ex<=0).length;const w20=L.filter(x=>x.next_open_20.ex>0.5&&x.next_open_20.t>=2).map(x=>`${{x.name}}（${{pc(x.next_open_20.ex)}}）`);
 $('btnote').innerHTML=`📏 5 年回測（${{(BT5.period||[]).join('～')}}，現成分股、扣來回 ${{BT5.cost}}%、比同期成分股等權）：隔天開盤買持有 5 天，${{lose}}/${{L.length}} 招輸平均；`+
 (w20.length?`持有 20 天站得住的是 <b>${{w20.join('、')}}</b>。`:'持有 20 天也沒有一招站得住。')+`股票池是現在的成分股，有存活者偏差，但比較基準是同一批股票，偏差大半互相抵掉。`}}
</script></body></html>"""
    (site_dir / "usx.html").write_text(body, "utf-8")
    return True


def backtest_download(years: int, out: str) -> None:
    uni = fetch_universe()
    h = fetch_history(list(uni.code), period=f"{years}y")
    h.to_csv(out, index=False)
    uni.to_csv(Path(out).with_name("universe.csv"), index=False)
    log.info("美股 %d 年日 K：%d 列、%d 檔 → %s", years, len(h), h.code.nunique(), out)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", nargs=2, metavar=("YEARS", "OUT"), help="下載 N 年日 K 到 OUT（研究用）")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if a.backtest:
        backtest_download(int(a.backtest[0]), a.backtest[1])
        return 0
    update()
    return 0


if __name__ == "__main__":
    sys.exit(main())
