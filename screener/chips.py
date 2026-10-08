"""籌碼與營收歷史：融資融券、當沖、月營收（存成歷史檔，算連續天數／月數與變化率）。

資料來源（2026/10 用 GitHub Actions 實測過格式）：
- 融資融券：證交所 MI_MARGN（selectType=STOCK，第 2 張表）、櫃買 margin/balance，單位都是張
- 當沖：證交所 TWTB4U、櫃買 intraday/stat（type=Daily），個股當沖成交股數
- 月營收：公開資訊觀測站 mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_{民國年}_{月}_0.html（Big5），單位千元

歷史檔：
- data/extras/margin_hist.csv.gz：date, code, margin_bal, margin_chg, short_bal, util
- data/extras/daytrade_hist.csv.gz：date, code, dt_vol（張）, dt_ratio（當沖股數 ÷ 成交股數，%）
- data/extras/rev_hist.csv.gz：code, ym, revenue（千元）, yoy, mom（%）
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

import numpy as np
import pandas as pd

from .enrich import DIR, _json, _num, _streak, _tables

log = logging.getLogger("chips")

MARGIN_HIST = DIR / "margin_hist.csv.gz"
DT_HIST = DIR / "daytrade_hist.csv.gz"
REV_HIST = DIR / "rev_hist.csv.gz"
MARGIN_COLS = ["date", "code", "margin_bal", "margin_chg", "short_bal", "util"]
DT_COLS = ["date", "code", "dt_vol", "dt_ratio"]
REV_COLS = ["code", "ym", "revenue", "yoy", "mom"]
MARGIN_KEEP = 260   # 約一年
REV_MONTHS = 24
CODE_RE = re.compile(r"[1-9]\d{3}")


# ------------------------------------------------------------ 融資融券（張）
def margin(s, d: dt.date) -> pd.DataFrame:
    out = []
    j = _json(s, "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
              {"date": d.strftime("%Y%m%d"), "selectType": "STOCK", "response": "json"})
    for t in _tables(j):
        f = [str(x).strip() for x in t.get("fields") or []]
        # 欄位：代號 名稱｜融資 買進 賣出 現金償還 前日餘額 今日餘額 限額｜融券 買進 賣出 現券償還 前日餘額 今日餘額 限額｜…
        if len(f) < 13 or f[0] != "代號" or f[5] != "前日餘額" or f[6] != "今日餘額" or f[12] != "今日餘額":
            continue
        for r in t["data"]:
            code = str(r[0]).strip()
            if not CODE_RE.fullmatch(code):
                continue
            bal, prev, lim = _num(r[6]), _num(r[5]), _num(r[7])
            out.append({"code": code, "margin_bal": bal, "margin_chg": None if bal is None or prev is None else bal - prev,
                        "short_bal": _num(r[12]), "util": round(bal / lim * 100, 2) if bal is not None and lim else None})
        break
    time.sleep(3)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/margin/balance", {"date": d.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        f = [str(x).replace(" ", "") for x in t.get("fields") or []]
        ix = {k: next((i for i, x in enumerate(f) if x.startswith(k)), None)
              for k in ["代號", "前資餘額", "資餘額", "資使用率", "券餘額"]}
        if None in ix.values():
            continue
        for r in t["data"]:
            code = str(r[ix["代號"]]).strip()
            if not CODE_RE.fullmatch(code):
                continue
            bal, prev = _num(r[ix["資餘額"]]), _num(r[ix["前資餘額"]])
            out.append({"code": code, "margin_bal": bal, "margin_chg": None if bal is None or prev is None else bal - prev,
                        "short_bal": _num(r[ix["券餘額"]]), "util": _num(r[ix["資使用率"]])})
        break
    return pd.DataFrame(out, columns=MARGIN_COLS[1:])


# ------------------------------------------------------------ 當沖（股數 → 張）
def daytrade(s, d: dt.date) -> pd.DataFrame:
    out = []
    for url, params in [("https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U",
                         {"date": d.strftime("%Y%m%d"), "selectType": "All", "response": "json"}),
                        ("https://www.tpex.org.tw/www/zh-tw/intraday/stat",
                         {"type": "Daily", "date": d.strftime("%Y/%m/%d"), "response": "json"})]:
        for t in _tables(_json(s, url, params)):
            f = [str(x).replace(" ", "") for x in t.get("fields") or []]
            ic = next((i for i, x in enumerate(f) if x in ("證券代號", "代號")), None)
            iv = next((i for i, x in enumerate(f) if "當日沖銷交易成交股數" in x), None)
            if ic is None or iv is None:
                continue
            for r in t["data"]:
                code = str(r[ic]).strip()
                v = _num(r[iv])
                if CODE_RE.fullmatch(code) and v is not None:
                    out.append({"code": code, "dt_vol": v / 1000})
            break
        time.sleep(3)
    return pd.DataFrame(out, columns=["code", "dt_vol"])


_HIST_CACHE: list = []


def _volume_map(date: str) -> pd.Series:
    """某天各股成交股數（從 history.csv.gz，讀一次後快取）。"""
    from . import fetch
    if not _HIST_CACHE:
        _HIST_CACHE.append(fetch.load_history()[["date", "code", "volume"]])
    h = _HIST_CACHE[0]
    return h[h.date == date].drop_duplicates("code").set_index("code").volume


def _trading_days() -> list[str]:
    from . import fetch
    try:
        return sorted(fetch.load_history().date.unique())
    except Exception:  # noqa: BLE001
        return []


def update_margin_history(s, d: dt.date, backfill: int = 25) -> dict:
    """把今天的融資融券、當沖加進歷史；再往前補最多 backfill 個缺漏的交易日（新的先補）。"""
    mh = pd.read_csv(MARGIN_HIST, dtype={"code": str}) if MARGIN_HIST.exists() else pd.DataFrame(columns=MARGIN_COLS)
    th = pd.read_csv(DT_HIST, dtype={"code": str}) if DT_HIST.exists() else pd.DataFrame(columns=DT_COLS)
    days = [x for x in _trading_days() if x <= d.isoformat()][-MARGIN_KEEP:]
    if d.isoformat() not in days:
        days.append(d.isoformat())
    todo = [x for x in reversed(days) if x not in set(mh.date)][: backfill + 1]

    def save():
        nonlocal mh, th
        keep = sorted(set(mh.date))[-MARGIN_KEEP:]
        mh = mh[mh.date.isin(keep)].drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        th = th[th.date.isin(keep)].drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        DIR.mkdir(parents=True, exist_ok=True)
        mh.to_csv(MARGIN_HIST, index=False)
        th.to_csv(DT_HIST, index=False)
        return keep

    for i, x in enumerate(todo, 1):
        day = dt.date.fromisoformat(x)
        try:
            m = margin(s, day)
            t = daytrade(s, day)
        except Exception as e:  # noqa: BLE001
            log.warning("融資融券／當沖 %s 失敗：%s", x, e)
            continue
        if m.empty:
            log.info("融資融券 %s 沒有資料", x)
            continue
        mh = pd.concat([mh[mh.date != x], m.assign(date=x)[MARGIN_COLS]], ignore_index=True)
        if len(t):
            vol = _volume_map(x)
            t["dt_ratio"] = (t.dt_vol * 1000 / t.code.map(vol).where(lambda v: v > 0) * 100).round(2)
            th = pd.concat([th[th.date != x], t.assign(date=x)[DT_COLS]], ignore_index=True)
        log.info("[%d/%d] 融資融券 %s：%d 檔；當沖 %d 檔", i, len(todo), x, len(m), len(t))
        if i % 20 == 0:
            save()  # 補很多天時分段存檔，中途中斷也不會全部白抓
    keep = save()
    return {"margin_days": len(keep), "daytrade_days": th.date.nunique()}


DT_LONG = DIR / "daytrade_5y.csv.gz"


def backfill_daytrade_long(s, d: dt.date, years: float, budget_min: float = 25, bt_path: str | None = None) -> int:
    """當沖往回補 years 年（研究用，存 daytrade_5y.csv.gz；每天用的 daytrade_hist 仍只留一年）。分身 0215-cc-ac、Cowork 0226／0255：
    前日當沖 >36% 警示要做 2021～2025 樣本外驗證。一天打證交所 TWTB4U＋櫃買 intraday/stat 各一次（約 8 秒），
    每次只跑 budget_min 分鐘、從最近往回補，已經有的日子跳過，重跑會接著補。
    dt_ratio＝當沖股數 ÷ 當天成交股數；成交股數有 history.csv.gz（官方）就用它，沒有的舊年份用回測檔（bt_path，Yahoo 量）。"""
    if DT_LONG.exists():
        hist = pd.read_csv(DT_LONG, dtype={"code": str})
    elif DT_HIST.exists():
        hist = pd.read_csv(DT_HIST, dtype={"code": str}).reindex(columns=DT_COLS)
    else:
        hist = pd.DataFrame(columns=DT_COLS)
    have = set(hist.date)
    from .enrich import ROOT
    tw = pd.read_csv(ROOT / "data" / "macro" / "long.csv.gz").query("sym == '^TWII'").date
    start = (d - dt.timedelta(days=int(years * 365.25))).isoformat()
    need = sorted((x for x in tw if start <= x < d.isoformat() and x not in have), reverse=True)
    bt = None
    if bt_path:
        try:
            b = pd.read_csv(bt_path, dtype={"code": str}, usecols=["date", "code", "volume"])
            bt = {k: g.drop_duplicates("code").set_index("code").volume for k, g in b[b.date.isin(set(need))].groupby("date")}
            log.info("回測檔成交量：%d 天", len(bt))
        except Exception as e:  # noqa: BLE001
            log.warning("回測檔讀不到，舊年份 dt_ratio 會是空的：%s", e)
    t0, done, parts = time.time(), 0, [hist]
    for x in need:
        if time.time() - t0 > budget_min * 60:
            break
        try:
            t = daytrade(s, dt.date.fromisoformat(x))
        except Exception as e:  # noqa: BLE001
            log.warning("當沖 %s 失敗：%s", x, e)
            continue
        if len(t):
            vol = _volume_map(x)
            if vol.empty and bt is not None:
                vol = bt.get(x, pd.Series(dtype=float))
            t["dt_ratio"] = (t.dt_vol * 1000 / t.code.map(vol).where(lambda v: v > 0) * 100).round(2)
            parts.append(t.assign(date=x)[DT_COLS])
            done += 1
        if done and done % 20 == 0:
            pd.concat(parts, ignore_index=True).to_csv(DT_LONG, index=False, compression="gzip")
    out = pd.concat(parts, ignore_index=True).drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    out.to_csv(DT_LONG, index=False, compression="gzip")
    log.info("當沖長歷史：這次補 %d 天，還差 %d 天（%s 起），共 %d 天", done, len(need) - done, start, out.date.nunique())
    return out.date.nunique()


def margin_metrics() -> pd.DataFrame:
    """每檔：融資增減、使用率、券資比、融資連續增減天數、融資 5 日增減 %、當沖比率。"""
    if not MARGIN_HIST.exists():
        return pd.DataFrame()
    h = pd.read_csv(MARGIN_HIST, dtype={"code": str}).sort_values("date")
    days = sorted(h.date.unique())
    bal = h.pivot(index="date", columns="code", values="margin_bal").reindex(days)
    chg = h.pivot(index="date", columns="code", values="margin_chg").reindex(days)
    last = h[h.date == days[-1]].set_index("code")
    out = pd.DataFrame(index=last.index)
    out["margin_bal"] = last.margin_bal
    out["margin_chg"] = last.margin_chg
    out["margin_util"] = last.util
    out["short_ratio"] = (last.short_bal / last.margin_bal.where(last.margin_bal > 0) * 100).round(2)
    out["margin_streak"] = pd.Series({c: _streak(chg[c]) for c in chg.columns})
    if len(days) >= 6:
        b0 = bal.iloc[-6]
        out["margin_5d_pct"] = ((bal.iloc[-1] / b0.where(b0 > 0) - 1) * 100).round(2)
    out["margin_days"] = len(days)
    out["margin_date"] = days[-1]
    if DT_HIST.exists():
        t = pd.read_csv(DT_HIST, dtype={"code": str})
        if len(t):
            t = t[t.date == t.date.max()].drop_duplicates("code").set_index("code")
            out = out.join(t.dt_ratio, how="outer")
    out.index.name = "code"
    return out


# ------------------------------------------------------------ 月營收歷史（公開資訊觀測站）
_ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>\s*([1-9]\d{3})\s*</td>\s*<td[^>]*>.*?</td>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)


def parse_mops_revenue(html: str) -> pd.DataFrame:
    """解析月營收統計表：代號、當月營收、上月營收、去年當月營收、上月比較%、去年同月%…"""
    out = []
    for code, rest in _ROW.findall(html):
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in _CELL.findall(rest)]
        if len(cells) < 5:
            continue
        rev, yoy, mom = _num(cells[0]), _num(cells[4]), _num(cells[3])
        if rev is None:
            continue
        out.append({"code": code, "revenue": rev, "yoy": yoy, "mom": mom})
    return pd.DataFrame(out, columns=["code", "revenue", "yoy", "mom"]).drop_duplicates("code")


def revenue_month(s, y: int, m: int) -> pd.DataFrame:
    frames = []
    for mk in ("sii", "otc"):
        url = f"https://mopsov.twse.com.tw/nas/t21/{mk}/t21sc03_{y - 1911}_{m}_0.html"
        try:
            r = s.get(url, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.warning("月營收 %s 失敗：%s", url, e)
            continue
        if r.status_code != 200:
            continue
        frames.append(parse_mops_revenue(r.content.decode("cp950", errors="replace")))
        time.sleep(3)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["code", "revenue", "yoy", "mom"])
    return df.assign(ym=f"{y}-{m:02d}")


def _months_back(today: dt.date, n: int) -> list[tuple[int, int]]:
    """上個月往前 n 個月（新的在前）。"""
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append((y, m))
    return out


def _upsert_month(h: pd.DataFrame, ym: str, df: pd.DataFrame) -> pd.DataFrame:
    """把新抓的某月營收『按公司』換進去：新抓到的公司才換，沒抓到的保留舊列。
    10/7 00:42 上櫃那邊只回 1 列，舊寫法整月替換，把 2026-08 上櫃 863 家洗掉（Cowork 0346）。
    新抓列數 < 舊的 90% 時另外警告（多半是某個市場沒抓到）。"""
    old = h[h.ym == ym]
    if len(old) and len(df) < 0.9 * len(old):
        log.warning("月營收 %s 這次只抓到 %d 家（原本 %d 家），沒抓到的保留舊資料", ym, len(df), len(old))
    keep_old = old[~old.code.isin(df.code)]
    return pd.concat([h[h.ym != ym], keep_old, df], ignore_index=True)


def update_revenue_history(s, today: dt.date, max_fetch: int = 30) -> int:
    """最近兩個月每天重抓（公司陸續公布、可能更正），其餘缺的月份補齊到 24 個月。"""
    h = pd.read_csv(REV_HIST, dtype={"code": str}) if REV_HIST.exists() else pd.DataFrame(columns=REV_COLS)
    h = h.reindex(columns=REV_COLS + ["ann_date"])
    h["ann_date"] = h["ann_date"].astype(object)
    months = _months_back(today, REV_MONTHS)
    have = set(h.ym)
    todo = months[:2] + [x for x in months[2:] if f"{x[0]}-{x[1]:02d}" not in have]
    for y, m in todo[:max_fetch]:
        df = revenue_month(s, y, m)
        if df.empty:
            log.info("月營收 %d-%02d 尚無資料", y, m)
            continue
        ym = f"{y}-{m:02d}"
        # ann_date＝我們第一次看到這家這個月營收的日期（公告日的上界；收盤後才抓，所以通常是公告當天或隔天）。
        # 已經有的保留舊值；往回補的舊月份不知道公告日，留空。
        seen = h[h.ym == ym].set_index("code")["ann_date"]
        df = df[REV_COLS].copy()
        df["ann_date"] = df.code.map(seen).astype(object)
        if (y, m) in months[:2]:
            df.loc[~df.code.isin(seen.index), "ann_date"] = today.isoformat()
        h = _upsert_month(h, ym, df)
        log.info("月營收 %d-%02d：%d 家", y, m, len(df))
    keep = {f"{y}-{m:02d}" for y, m in months}
    h = h[h.ym.isin(keep)].drop_duplicates(["code", "ym"], keep="last").sort_values(["code", "ym"])
    DIR.mkdir(parents=True, exist_ok=True)
    h.to_csv(REV_HIST, index=False)
    return h.ym.nunique()


REV_LONG = DIR / "rev_5y.csv.gz"


def backfill_revenue_long(s, today: dt.date, months: int = 69) -> int:
    """月營收長歷史（研究用，MOPS 每月彙總，上市＋上櫃）→ data/extras/rev_5y.csv.gz。
    rev_hist 只留 24 個月給每日用，這份另外放；已有的月份不重抓（最近 2 個月照樣重抓）。回傳月份數。"""
    h = pd.read_csv(REV_LONG, dtype={"code": str}) if REV_LONG.exists() else pd.DataFrame(columns=REV_COLS)
    have = set(h.ym)
    want = _months_back(today, months)
    todo = want[:2] + [x for x in want[2:] if f"{x[0]}-{x[1]:02d}" not in have]
    for y, m in todo:
        df = revenue_month(s, y, m)
        if df.empty:
            log.info("月營收 %d-%02d 沒資料", y, m)
            continue
        h = _upsert_month(h, f"{y}-{m:02d}", df[REV_COLS])
        log.info("月營收 %d-%02d：%d 家", y, m, len(df))
    h = h.drop_duplicates(["code", "ym"], keep="last").sort_values(["code", "ym"])
    h.to_csv(REV_LONG, index=False, compression="gzip")
    return int(h.ym.nunique())


def _ym_shift(ym: str, k: int) -> str:
    y, m = map(int, ym.split("-"))
    t = y * 12 + (m - 1) + k
    return f"{t // 12}-{t % 12 + 1:02d}"


def revenue_metrics() -> pd.DataFrame:
    """每檔（以該公司最新公布月份為準）：
    - rev_streak：年增率 > 0 連續幾個月（負數 = 年增率 ≤ 0 連續幾個月）
    - rev_yoy_3m：近 3 個月平均年增率（%）；rev_yoy_3m_prev：再前 3 個月的平均；rev_accel：兩者相減（> 0 = 加速）
    - rev_high12：最新月營收是近 12 個月最高（需有 12 個月資料）
    - rev_mom_last：最新月的月增率（只顯示）
    """
    if not REV_HIST.exists():
        return pd.DataFrame()
    h = pd.read_csv(REV_HIST, dtype={"code": str}).sort_values(["code", "ym"])
    rows = {}
    for code, g in h.groupby("code"):
        g = g.set_index("ym")
        last = g.index[-1]
        # 只用連續月份（中間缺月就停）
        seq = [last]
        while _ym_shift(seq[-1], -1) in g.index and len(seq) < REV_MONTHS:
            seq.append(_ym_shift(seq[-1], -1))
        yoy = g.yoy.reindex(seq)  # 新 → 舊
        pos = yoy > 0
        n = 0
        for v, p in zip(yoy, pos):
            if pd.isna(v) or p != pos.iloc[0]:
                break
            n += 1
        streak = n if pos.iloc[0] else -n
        if pd.isna(yoy.iloc[0]):
            streak = None
        r3 = yoy.iloc[:3].mean() if yoy.iloc[:3].notna().sum() == 3 else np.nan
        p3 = yoy.iloc[3:6].mean() if len(yoy) >= 6 and yoy.iloc[3:6].notna().sum() == 3 else np.nan
        rev = g.revenue.reindex(seq)
        high12 = bool(rev.iloc[0] >= rev.iloc[:12].max()) if rev.iloc[:12].notna().sum() == 12 else None
        rows[code] = {"rev_ym": last, "rev_streak": streak, "rev_yoy_3m": r3, "rev_yoy_3m_prev": p3,
                      "rev_accel": r3 - p3 if pd.notna(r3) and pd.notna(p3) else np.nan,
                      "rev_high12": high12, "rev_mom_last": g.mom.iloc[-1]}
    out = pd.DataFrame.from_dict(rows, orient="index")
    for k in ["rev_yoy_3m", "rev_yoy_3m_prev", "rev_accel"]:
        out[k] = out[k].astype(float).round(2)
    out.index.name = "code"
    return out
