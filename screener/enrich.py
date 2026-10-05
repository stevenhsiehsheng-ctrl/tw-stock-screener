"""消息面／籌碼面資料：本益比、三大法人、月營收、重大訊息、注意／處置股、新聞。

每個資料來源各自獨立，抓不到就略過，不影響主流程。
收盤後（main.py）抓一次存到 data/extras/，盤中提醒直接讀取，再即時補抓重大訊息與新聞。
測試：python -m screener.enrich --test
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from .fetch import HEADERS, _num

log = logging.getLogger("enrich")
ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "extras"


def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(s, url, params=None, tries=3):
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r
            log.warning("%s HTTP %s", url, r.status_code)
        except requests.RequestException as e:
            log.warning("%s %s", url, e)
        time.sleep(3 * (i + 1))
    return None


def _json(s, url, params=None):
    r = _get(s, url, params)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        log.warning("%s 不是 JSON：%r", url, r.text[:100])
        return None


def _col(fields, *keys, exclude=()):
    """找欄位位置：欄名同時包含所有 keys，且不含 exclude。"""
    clean = [str(f).replace(" ", "").replace("　", "") for f in fields]
    for i, f in enumerate(clean):
        if all(k in f for k in keys) and not any(x in f for x in exclude):
            return i
    return None


def _foreign_col(fields):
    """外資（不含外資自營商）買賣超欄位。"""
    for i, f in enumerate(str(x).replace(" ", "") for x in fields):
        g = f.replace("不含外資自營商", "")
        if "買賣超" in f and ("外陸資" in f or "外資及陸資" in f) and "外資自營商" not in g:
            return i
    return None


def _tables(j):
    if not j:
        return []
    if j.get("tables"):
        return [t for t in j["tables"] if t.get("data")]
    if j.get("fields") and j.get("data"):
        return [{"fields": j["fields"], "data": j["data"]}]
    return []


# ------------------------------------------------------------ 本益比
def pe(s, d: dt.date) -> pd.DataFrame:
    out = []
    j = _json(s, "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d",
              {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
    for t in _tables(j):
        f = t["fields"]; ic, ip, iy, ib = _col(f, "代號"), _col(f, "本益比"), _col(f, "殖利率"), _col(f, "淨值比")
        out += [{"code": str(r[ic]).strip(), "pe": _num(r[ip]), "yield": _num(r[iy]) if iy is not None else None,
                 "pb": _num(r[ib]) if ib is not None else None} for r in t["data"]]
        break
    time.sleep(3)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate",
              {"date": d.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        f = t["fields"]; ic, ip, iy, ib = _col(f, "代號"), _col(f, "本益比"), _col(f, "殖利率"), _col(f, "淨值比")
        out += [{"code": str(r[ic]).strip(), "pe": _num(r[ip]), "yield": _num(r[iy]) if iy is not None else None,
                 "pb": _num(r[ib]) if ib is not None else None} for r in t["data"]]
        break
    return pd.DataFrame(out)


# ------------------------------------------------------------ 三大法人（股數→張）
def _lots(r, i):
    return (_num(r[i]) or 0) / 1000 if i is not None and i < len(r) else None


def institutional(s, d: dt.date) -> pd.DataFrame:
    out = []
    j = _json(s, "https://www.twse.com.tw/rwd/zh/fund/T86",
              {"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"})
    for t in _tables(j):
        f = t["fields"]
        ic = _col(f, "代號")
        ifo = _foreign_col(f)
        iit = _col(f, "投信", "買賣超")
        idl = _col(f, "自營商買賣超", exclude=("外資", "自行", "避險"))
        itot = _col(f, "三大法人買賣超")
        for r in t["data"]:
            out.append({"code": str(r[ic]).strip(), "foreign": _lots(r, ifo), "trust": _lots(r, iit),
                        "dealer": _lots(r, idl), "inst_total": _lots(r, itot)})
        break
    time.sleep(3)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
              {"type": "Daily", "sect": "EW", "date": d.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        f = t["fields"]
        ic = _col(f, "代號")
        ifo = _foreign_col(f)
        iit = _col(f, "投信", "買賣超")
        idl = _col(f, "自營商", "買賣超", exclude=("外資", "自行", "避險"))
        itot = _col(f, "三大法人", "買賣超")
        if ifo is None and len(f) >= 24 and "買賣超" in str(f[4]):
            # 櫃買新版欄位名稱沒有寫法人別，固定順序：外資(不含自營)、外資自營、外資合計、投信、
            # 自營商(自行)、自營商(避險)、自營商合計（各 買進／賣出／買賣超）、三大法人合計
            ifo, iit, idl, itot = 4, 13, 22, len(f) - 1
        if ic is None:
            continue
        for r in t["data"]:
            out.append({"code": str(r[ic]).strip(), "foreign": _lots(r, ifo), "trust": _lots(r, iit),
                        "dealer": _lots(r, idl), "inst_total": _lots(r, itot)})
        break
    return pd.DataFrame(out)


# ------------------------------------------------------------ 已發行股數（算換手率）
def shares(s) -> pd.DataFrame:
    """上市：openapi t187ap03_L「已發行普通股數或TDR原股發行股數」；上櫃：mopsfin_t187ap03_O「IssueShares」。單位：股。"""
    out = []
    for r in _openapi_rows(s, "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
        out.append({"code": str(r.get("公司代號", "")).strip(), "shares": _num(r.get("已發行普通股數或TDR原股發行股數"))})
    time.sleep(2)
    for r in _openapi_rows(s, "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        out.append({"code": str(r.get("SecuritiesCompanyCode", "")).strip(), "shares": _num(r.get("IssueShares"))})
    df = pd.DataFrame(out, columns=["code", "shares"])
    return df[df.shares.notna() & (df.shares > 0)].drop_duplicates("code")


# ------------------------------------------------------------ 月營收（最新一個月）
def _openapi_rows(s, url):
    j = _json(s, url)
    return j if isinstance(j, list) else []


def _key(row, *keys):
    for k in row:
        kk = k.replace(" ", "")
        if all(x in kk for x in keys):
            return k
    return None


def revenue(s) -> pd.DataFrame:
    rows = _openapi_rows(s, "https://openapi.twse.com.tw/v1/opendata/t187ap05_L")
    rows += _openapi_rows(s, "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")
    out = []
    for r in rows:
        kc = _key(r, "公司代號"); ky = _key(r, "去年同月增減"); km = _key(r, "上月比較增減") or _key(r, "上月增減")
        kd = _key(r, "資料年月"); ka = _key(r, "累計", "前期比較增減") or _key(r, "累計", "增減")
        if not kc or not ky:
            continue
        out.append({"code": str(r[kc]).strip(), "rev_yoy": _num(r[ky]), "rev_mom": _num(r[km]) if km else None,
                    "rev_cum_yoy": _num(r[ka]) if ka else None, "rev_month": r.get(kd)})
    return pd.DataFrame(out)


# ------------------------------------------------------------ 重大訊息（當天）
def announcements(s) -> pd.DataFrame:
    rows = _openapi_rows(s, "https://openapi.twse.com.tw/v1/opendata/t187ap04_L")
    rows += _openapi_rows(s, "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O")
    out = []
    for r in rows:
        kc = _key(r, "公司代號") or _key(r, "SecuritiesCompanyCode"); kt = _key(r, "主旨"); kd = _key(r, "發言日期"); kh = _key(r, "發言時間")
        if not kc or not kt:
            continue
        out.append({"code": str(r[kc]).strip(), "title": str(r[kt]).strip(), "date": r.get(kd), "time": r.get(kh)})
    return pd.DataFrame(out)


# ------------------------------------------------------------ 注意／處置股
def warnings_list(s) -> pd.DataFrame:
    out = []
    for url, kind in [("https://openapi.twse.com.tw/v1/announcement/punish", "處置"),
                      ("https://openapi.twse.com.tw/v1/announcement/notice", "注意"),
                      ("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", "處置"),
                      ("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information", "注意")]:
        for r in _openapi_rows(s, url):
            kc = _key(r, "Code") or _key(r, "證券代號") or _key(r, "SecuritiesCompanyCode") or _key(r, "代號")
            if kc and re.fullmatch(r"\d{4}", str(r[kc]).strip()):
                out.append({"code": str(r[kc]).strip(), "flag": kind})
    df = pd.DataFrame(out, columns=["code", "flag"])
    if df.empty:
        return df
    # 同一檔同時是注意和處置，只留處置
    df["rank"] = df.flag.map({"處置": 0, "注意": 1})
    return df.sort_values("rank").drop_duplicates("code")[["code", "flag"]]


# ------------------------------------------------------------ 新聞（Google News RSS）
def news(s, code: str, name: str, days: int = 3, limit: int = 3) -> list[dict]:
    q = quote(f"{code} {name}")
    try:
        r = s.get(f"https://news.google.com/rss/search?q={q}+when:{days}d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", timeout=10)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if name not in title and code not in title:
            continue
        pub = it.findtext("pubDate")
        try:
            ts = email.utils.parsedate_to_datetime(pub).astimezone(dt.timezone(dt.timedelta(hours=8)))
            when = ts.strftime("%m/%d %H:%M")
        except (TypeError, ValueError):
            when = ""
        items.append({"title": title, "link": it.findtext("link"), "when": when})
        if len(items) >= limit:
            break
    return items


# ------------------------------------------------------------ 收盤後：全部抓一次並存檔
def refresh(d: dt.date, backfill: int = 25) -> dict[str, int]:
    DIR.mkdir(parents=True, exist_ok=True)
    s = _session()
    got = {}
    for name, fn in [("pe", lambda: pe(s, d)), ("inst", lambda: institutional(s, d)),
                     ("revenue", lambda: revenue(s)), ("warnings", lambda: warnings_list(s)),
                     ("shares", lambda: shares(s))]:
        try:
            df = fn()
        except Exception as e:  # noqa: BLE001
            log.warning("%s 失敗：%s", name, e)
            df = pd.DataFrame()
        got[name] = len(df)
        if len(df):
            df.to_csv(DIR / f"{name}.csv", index=False)
            if name == "inst":
                try:
                    got["inst_hist"] = update_inst_history(s, d, df)
                except Exception as e:  # noqa: BLE001
                    log.warning("法人歷史更新失敗：%s", e)
        time.sleep(2)
    from . import chips
    for name, fn in [("margin", lambda: chips.update_margin_history(s, d, backfill)),
                     ("rev_hist", lambda: {"rev_months": chips.update_revenue_history(s, d)}),
                     ("exdiv", lambda: _exdiv().update(d, s))]:
        try:
            got.update(fn())
        except Exception as e:  # noqa: BLE001
            log.warning("%s 歷史更新失敗：%s", name, e)
    (DIR / "updated.json").write_text(json.dumps({"date": d.isoformat(), **got}, ensure_ascii=False))
    log.info("消息面資料：%s", got)
    return got


# ------------------------------------------------------------ 法人歷史：算「連續買超 / 賣超幾天」
INST_HIST = DIR / "inst_hist.csv.gz"
INST_KEEP = 260  # 保留最近一年（算連續天數、5 日累計、法人占成交量比例，也給回測用）
INST_COLS = ["date", "code", "foreign", "trust", "dealer", "inst_total"]


def update_inst_history(s, d: dt.date, today_df: pd.DataFrame, backfill: int = 20) -> int:
    """把今天的法人買賣超加進歷史檔；歷史不夠時往前補抓最多 backfill 個交易日。回傳歷史天數。"""
    cols = INST_COLS
    hist = pd.read_csv(INST_HIST, dtype={"code": str}) if INST_HIST.exists() else pd.DataFrame(columns=cols)
    hist = hist.reindex(columns=cols)
    if len(today_df):
        today = today_df.assign(date=d.isoformat()).reindex(columns=cols)
        hist = pd.concat([hist[hist.date != d.isoformat()], today], ignore_index=True)
    # 要重抓的日子：沒資料、只抓到一半（例如 15:20 時證交所還沒公布、只有櫃買）、或舊資料沒有自營商欄
    g = hist.groupby("date")
    n, no_dealer = g.code.count(), g.dealer.apply(lambda x: x.isna().all())
    bad = set(n[(n < 0.8 * n.median()) | no_dealer].index) if len(n) else set()
    have = set(hist.date) - bad
    if backfill > 0:
        try:
            from . import fetch
            days = sorted(fetch.load_history().date.unique())
        except Exception:  # noqa: BLE001
            days = []
        need = [x for x in days if x < d.isoformat()][-backfill:]
        need = [x for x in need if x not in have]
        for x in reversed(need):
            try:
                df = institutional(s, dt.date.fromisoformat(x))
            except Exception as e:  # noqa: BLE001
                log.warning("補抓法人 %s 失敗：%s", x, e)
                df = pd.DataFrame()
            if len(df):
                hist = pd.concat([hist[hist.date != x], df.assign(date=x).reindex(columns=cols)], ignore_index=True)
                log.info("補抓法人 %s：%d 筆", x, len(df))
            time.sleep(3)
    keep = sorted(set(hist.date))[-INST_KEEP:]
    hist = hist[hist.date.isin(keep)].drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    hist.to_csv(INST_HIST, index=False)
    return len(keep)


def _streak(s: pd.Series) -> int:
    """最近連續同方向的天數：正 = 連續買超，負 = 連續賣超，0 = 最近一天沒進出。"""
    v = [x for x in s.tolist()]
    if not v or pd.isna(v[-1]) or v[-1] == 0:
        return 0
    sign = 1 if v[-1] > 0 else -1
    n = 0
    for x in reversed(v):
        if pd.isna(x) or x == 0 or (x > 0) != (sign > 0):
            break
        n += 1
    return sign * n


def inst_streaks() -> pd.DataFrame:
    """每檔：外資／投信連續買賣超天數、近 5 日累計（張）。"""
    if not INST_HIST.exists():
        return pd.DataFrame()
    h = pd.read_csv(INST_HIST, dtype={"code": str}).sort_values("date")
    days = sorted(h.date.unique())
    out = pd.DataFrame(index=sorted(h.code.unique()))
    piv = {}
    for k in ["foreign", "trust", "dealer"]:
        if k not in h.columns or h[k].isna().all():
            continue
        w = piv[k] = h.pivot(index="date", columns="code", values=k).reindex(days)
        out[f"{k}_streak"] = pd.Series({c: _streak(w[c]) for c in w.columns})
        out[f"{k}_5d"] = w.iloc[-5:].sum(min_count=1)
    out["inst_days"] = len(days)
    # 法人買賣超占當天成交量（%）：最近一天、近 5 日累計
    try:
        from . import fetch
        hv = fetch.load_history()
        vol = hv[hv.date.isin(days[-5:])].pivot(index="date", columns="code", values="volume").reindex(days[-5:]) / 1000
        net = None
        if piv:  # 有任一法人資料才算（全部缺就是 NaN，不當成 0）
            parts = [piv[k].iloc[-5:] for k in piv]
            net = sum(p.fillna(0) for p in parts).where(~pd.concat(parts).isna().groupby(level=0).all())
        if net is not None:
            out["inst_pct"] = (net.iloc[-1] / vol.iloc[-1].where(vol.iloc[-1] > 0) * 100).round(1)
            out["inst_pct_5d"] = (net.sum() / vol.sum(min_count=1).where(lambda v: v > 0) * 100).round(1)
            if "foreign" in piv:
                out["foreign_pct"] = (piv["foreign"].iloc[-1] / vol.iloc[-1].where(vol.iloc[-1] > 0) * 100).round(1)
    except Exception as e:  # noqa: BLE001
        log.warning("法人占成交量計算失敗：%s", e)
    out.index.name = "code"
    return out


def load() -> pd.DataFrame:
    """合併已存的資料，index = 股票代號。"""
    frames = []
    for name in ["pe", "inst", "revenue", "warnings", "shares"]:
        f = DIR / f"{name}.csv"
        if f.exists():
            df = pd.read_csv(f, dtype={"code": str}).drop_duplicates("code").set_index("code")
            frames.append(df)
    from . import chips
    for name, fn in [("法人連續天數", inst_streaks), ("融資融券", chips.margin_metrics), ("月營收歷史", chips.revenue_metrics)]:
        try:
            st = fn()
            if len(st):
                frames.append(st)
        except Exception as e:  # noqa: BLE001
            log.warning("%s計算失敗：%s", name, e)
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def probe(s, d: dt.date) -> None:
    """印出候選資料來源的欄位與前兩列，用來確認 API 格式（在 GitHub Actions 上跑）。"""
    ymd, slash = d.strftime("%Y%m%d"), d.strftime("%Y/%m/%d")
    roc_y, m = d.year - 1911, d.month - 1 or 12
    if d.month == 1:
        roc_y -= 1
    y0 = (d - dt.timedelta(days=365))
    urls = [
        # 已發行股數（算換手率）
        ("TWSE 上市公司基本資料", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", None),
        ("TPEX 上櫃公司基本資料", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", None),
        # 除權息（給虛擬帳戶和 0050 對照組算含息報酬）
        ("TWSE 除權息結果", "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
         {"startDate": y0.strftime("%Y%m%d"), "endDate": ymd, "response": "json"}),
        ("TWSE 除權息預告", "https://www.twse.com.tw/rwd/zh/exRight/TWT48U", {"response": "json"}),
        ("TWSE openapi 預告", "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", None),
        ("TPEX 除權息結果", "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
         {"startDate": y0.strftime("%Y/%m/%d"), "endDate": slash, "response": "json"}),
        ("TPEX 除權息結果(舊)", "https://www.tpex.org.tw/web/stock/exright/dailyquo/exDailyQ_result.php",
         {"l": "zh-tw", "d": f"{y0.year - 1911}/{y0:%m/%d}", "ed": f"{d.year - 1911}/{d:%m/%d}"}),
        ("TPEX openapi 預告", "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost", None),
        ("TWSE 融資融券", "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", {"date": ymd, "selectType": "STOCK", "response": "json"}),
        ("TPEX 融資融券", "https://www.tpex.org.tw/www/zh-tw/margin/balance", {"date": slash, "response": "json"}),
        ("TWSE 當沖", "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U", {"date": ymd, "selectType": "All", "response": "json"}),
        ("TPEX 當沖", "https://www.tpex.org.tw/www/zh-tw/intraday/stat", {"type": "Daily", "date": slash, "response": "json"}),
        ("MOPS 上市營收", f"https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc_y}_{m}_0.html", None),
    ]
    for name, url, params in urls:
        print(f"\n===== {name}  {url}  {params}")
        try:
            r = s.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print("  連線失敗", e)
            continue
        print("  HTTP", r.status_code, "長度", len(r.content), "type", r.headers.get("content-type"))
        if params is None:
            print("  head", r.content[:400])
            if r.content[:1] in (b"[", b"{"):
                j = r.json()
                print("  json", (j[:2] if isinstance(j, list) else j))
                continue
            enc = r.apparent_encoding
            txt = r.content.decode(r.encoding or "utf-8", errors="replace")
            print("  encoding", r.encoding, "apparent", enc)
            i = txt.find("1101")
            print("  RAW", txt[max(0, i - 2500):i + 600])
            try:
                import io
                tbs = pd.read_html(io.StringIO(txt))
                print("  read_html 表格數", len(tbs))
                for t in tbs[:12]:
                    print("   shape", t.shape, "cols", list(t.columns)[:12])
                big = [t for t in tbs if t.shape[1] >= 10]
                if big:
                    print(big[0].head(4).to_string())
            except Exception as e:  # noqa: BLE001
                print("  read_html 失敗", e)
            continue
        try:
            j = r.json()
        except ValueError:
            print("  非 JSON：", r.text[:200])
            continue
        print("  keys", list(j)[:20], "stat", j.get("stat"))
        for i, t in enumerate(j.get("tables") or []):
            print(f"  [table {i}] title={t.get('title')!r} 筆數={len(t.get('data') or [])}")
            print("    groups", t.get("groups"))
            print("    fields", t.get("fields"))
            for row in (t.get("data") or [])[:2]:
                print("    row", row)
        for k in ("fields", "creditFields", "data", "creditList"):
            if k in j:
                v = j[k]
                print(f"  {k}:", v[:2] if isinstance(v, list) else v)
        time.sleep(3)


def _exdiv():
    from . import exdiv
    return exdiv


def _chips():
    from . import chips
    return chips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只印出候選資料來源的欄位格式")
    ap.add_argument("--date")
    ap.add_argument("--backfill-chips", type=int, metavar="N",
                    help="融資融券／當沖往前補 N 個交易日、月營收補到 24 個月（存檔）")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    d = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    s = _session()
    if a.probe:
        probe(s, d)
        return
    if a.backfill_chips:
        from . import chips
        print(chips.update_margin_history(s, d, a.backfill_chips), "月營收", chips.update_revenue_history(s, d), "個月")
        try:
            today = institutional(s, d)
        except Exception as e:  # noqa: BLE001
            log.warning("今天法人抓不到：%s", e)
            today = pd.DataFrame()
        print("法人歷史", update_inst_history(s, d, today, a.backfill_chips), "天")
        return
    for name, fn in [("本益比", lambda: pe(s, d)), ("三大法人", lambda: institutional(s, d)),
                     ("月營收", lambda: revenue(s)), ("重大訊息", lambda: announcements(s)),
                     ("注意處置", lambda: warnings_list(s)), ("融資融券", lambda: _chips().margin(s, d)),
                     ("當沖", lambda: _chips().daytrade(s, d)),
                     ("月營收（上月）", lambda: _chips().revenue_month(s, *_chips()._months_back(d, 1)[0]))]:
        try:
            df = fn()
            print(f"【{name}】{len(df)} 筆")
            print(df.head(3).to_string() if len(df) else "  (無資料)")
        except Exception as e:  # noqa: BLE001
            print(f"【{name}】失敗：{e!r}")
    for code, nm in [("2330", "台積電"), ("2317", "鴻海")]:
        print(f"【新聞 {code}】", news(s, code, nm))


if __name__ == "__main__":
    main()
