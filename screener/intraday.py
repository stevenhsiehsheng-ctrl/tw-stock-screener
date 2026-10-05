"""盤中進場提醒：約 13:12 掃描全市場，找出「爆量＋上漲＋突破 60 日新高」的股票。

回測（2021~2026）：爆量當天收盤前買進、成交量縮到爆量日一半以下隔天賣出，
加上「創 60 日新高」條件後，平均每筆跑贏大盤約 0.8%，6 個年度都是正的。
資料來源：證交所 MIS 即時報價（mis.twse.com.tw）。
用法：python -m screener.intraday [--wait-until 13:12] [--test]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

from . import corpact, enrich, fetch, notify, positions, sentiment

log = logging.getLogger("intraday")
TZ = ZoneInfo("Asia/Taipei")
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
QUOTES: tuple[pd.DataFrame, str | None] | None = None  # 盤中監控傳進來的報價


def _f(x):
    try:
        v = float(str(x).split("_")[0])
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def fetch_quotes(stocks: pd.DataFrame, batch: int = 50) -> tuple[pd.DataFrame, str | None]:
    s = requests.Session()
    s.headers.update(fetch.HEADERS | {"Referer": "https://mis.twse.com.tw/stock/index.jsp"})
    try:
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=20)
    except requests.RequestException as e:
        log.warning("MIS 首頁連線失敗：%s", e)
    keys = [f"{'tse' if m == 'TWSE' else 'otc'}_{c}.tw" for c, m in zip(stocks.code, stocks.market)]
    got, day = {}, None

    def pull(key_list):
        nonlocal day
        for i in range(0, len(key_list), batch):
            chunk = "|".join(key_list[i : i + batch])
            for attempt in range(3):
                try:
                    r = s.get(MIS, params={"ex_ch": chunk, "json": "1", "delay": "0", "_": int(time.time() * 1000)},
                              timeout=20)
                    j = r.json()
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("MIS 失敗（%d）：%s", attempt + 1, e)
                    time.sleep(3)
            else:
                continue
            for q in j.get("msgArray", []):
                row = {"code": q.get("c"), "name": q.get("n"), "price": _price(q), "open": _f(q.get("o")),
                       "high": _f(q.get("h")), "low": _f(q.get("l")), "yclose": _f(q.get("y")), "vol_lots": _f(q.get("v")) or 0,
                       "time": q.get("t"), "date": q.get("d"),
                       # 五檔第一檔：價格與張數（漲停鎖住時委賣是 "-"，委買第一檔就是排隊買漲停的張數）
                       "bid1": _f(q.get("b")), "ask1": _f(q.get("a")), "bid1_lots": _f(q.get("g")), "ask1_lots": _f(q.get("f"))}
                old = got.get(row["code"])
                if old is None or row["price"] is not None:
                    got[row["code"]] = row
                day = day or q.get("d")
            time.sleep(0.8)

    pull(keys)
    # MIS 偶爾在兩筆成交之間回傳 "-"（沒有最新成交價），對這些股票再抓一次
    key_of = dict(zip(stocks.code.astype(str), keys))
    miss = [key_of[c] for c, r in got.items() if r["price"] is None and c in key_of]
    if miss:
        time.sleep(2)
        pull(miss)
        log.info("報價缺漏 %d 檔，重抓後仍缺 %d 檔", len(miss), sum(r["price"] is None for r in got.values()))
    log.info("取得 %d 檔即時報價", len(got))
    df = pd.DataFrame(list(got.values()))
    return df, day


def _price(q: dict) -> float | None:
    """最新成交價；沒有就用最近一筆成交（pz），再沒有就用最佳買賣價的中間價。"""
    p = _f(q.get("z")) or _f(q.get("pz"))
    if p:
        return p
    b, a = _f(q.get("b")), _f(q.get("a"))
    if b and a:
        return round((b + a) / 2, 2)
    return b or a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-until", help="台北時間 HH:MM，等到這個時間才掃描")
    ap.add_argument("--test", action="store_true", help="測試模式：不檢查日期、不記錄持倉")
    ap.add_argument("--no-notify", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    root = fetch.ROOT
    cfg = yaml.safe_load((root / "config.yaml").read_text("utf-8"))
    ic = cfg.get("intraday", {})
    if not ic.get("enabled", True) and not a.test:
        log.info("盤中提醒已關閉")
        return 0

    now = dt.datetime.now(TZ)
    if now.weekday() >= 5 and not a.test:
        log.info("週末不掃描")
        return 0
    state_f = root / "data" / "intraday_state.json"
    today_s = now.date().isoformat()
    if not a.test and state_f.exists() and json.loads(state_f.read_text()).get("last_run") == today_s:
        log.info("今天已經掃描過，略過（排程有多個備援時間）")
        return 0
    if a.wait_until and not a.test:
        hh, mm = map(int, a.wait_until.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        wait = (target - now).total_seconds()
        if wait > 0:
            log.info("等待 %.0f 秒到 %s", wait, a.wait_until)
            time.sleep(wait)

    late = dt.datetime.now(TZ).replace(hour=13, minute=28, second=0, microsecond=0)
    if dt.datetime.now(TZ) > late and not a.test:
        log.warning("已超過 13:28，來不及在收盤前下單，今天略過盤中提醒")
        return 0

    markets = cfg.get("settings", {}).get("markets", ["TWSE", "TPEX"])
    stocks = fetch.load_stock_list(markets)
    hist = fetch.load_history()
    today = dt.datetime.now(TZ).date().isoformat()
    hist = corpact.adjust(hist[hist.date < today], upto=today)  # 減資／變更面額：舊價接起來，今天恢復買賣的也算
    g = hist.sort_values("date").groupby("code")
    nh = int(ic.get("new_high_days", 60))
    ref = pd.DataFrame({
        "avg5": g.volume.apply(lambda s: s.iloc[-5:].mean() if len(s) >= 5 else None),
        "hi": g.high.apply(lambda s: s.iloc[-nh:].max() if len(s) >= nh else None),
        "last_close": g.close.last(),
    })

    # 盤中監控呼叫時直接用同一輪的報價（已沿用上一輪價格補洞）；單獨重抓的話，
    # 鎖漲停一直沒成交的股票偶爾抓不到價格就被漏掉（10/5 聯策）
    q, day = QUOTES if QUOTES is not None else fetch_quotes(stocks)
    if q.empty:
        log.error("抓不到即時報價")
        return 1
    if day and day != today.replace("-", "") and not a.test:
        log.info("今天（%s）沒有交易資料（MIS 日期 %s），可能休市", today, day)
        return 0

    q = q.merge(ref, left_on="code", right_index=True, how="left").merge(
        stocks[["code", "industry"]], on="code", how="left")
    q["industry"] = q.industry.fillna("")
    q["chg"] = (q.price / q.yclose - 1) * 100
    q["vol_x_now"] = q.vol_lots * 1000 / q.avg5
    # 回測用的是「全天」成交量；盤中量還沒出完，改用依時間推估的全日量（config 可關）
    frac = 1.0
    if ic.get("use_projected_volume", True):
        from .live import vol_fraction
        frac = vol_fraction(dt.datetime.now(TZ))
    q["vol_x"] = q.vol_x_now / frac
    cond = (
        (q.vol_x >= ic.get("volume_multiple", 3))
        & (q.vol_lots >= ic.get("min_volume_lots", 500))
        & (q.chg >= ic.get("min_change_pct", 3)) & (q.chg <= ic.get("max_change_pct", 10.5))
        & (q.price > q.open)
        & (q.price > q.hi)
        & (q.price >= ic.get("min_price", 10))
    )
    ex_ind = set(ic.get("exclude_industries") or [])
    if ex_ind:
        cond &= ~q.industry.isin(ex_ind)
    hits = q[cond].sort_values("chg", ascending=False)
    log.info("符合 %d 檔：%s", len(hits), " ".join(hits.code))

    # 大盤環境：過去 20 天等權報酬（回測顯示大盤跌超過 3% 時此策略表現差）
    closes = hist.pivot(index="date", columns="code", values="close").sort_index()
    ew = closes.pct_change().clip(-0.11, 0.11).mean(axis=1).iloc[-20:]
    mkt20 = ((1 + ew).prod() - 1) * 100
    warn = mkt20 < ic.get("weak_market_pct", -3)

    scan_time = dt.datetime.now(TZ).strftime("%H:%M")
    if not a.test:
        state_f.write_text(json.dumps({"last_run": today, "time": scan_time, "hits": len(hits)}))
    extras = enrich.load()

    def ex(code, k):
        if extras.empty or code not in extras.index or k not in extras.columns:
            return None
        v = extras.at[code, k]
        return None if pd.isna(v) else v

    if not a.test and len(hits):
        positions.add_signals([{
            "code": r.code, "name": r.name, "industry": r.industry, "signal_date": today, "signal_time": scan_time,
            "alert_price": r.price, "surge_volume": r.vol_lots * 1000,
            "rev_yoy": None if ex(r.code, "rev_yoy") is None else round(float(ex(r.code, "rev_yoy")), 2),
            "bid1_lots_1312": r.bid1_lots, "ask1_lots_1312": r.ask1_lots, "vol_lots_1312": r.vol_lots,
        } for r in hits.itertuples()])

    if a.no_notify or (hits.empty and not ic.get("notify_when_empty", False)):
        return 0

    # 情緒與消息面
    try:
        senti = sentiment.from_quotes(q, hist)
    except Exception as e:  # noqa: BLE001
        log.warning("盤中情緒失敗：%s", e)
        senti = None
    sess = enrich._session()
    ann_map, news_map = {}, {}
    try:
        ann = enrich.announcements(sess) if len(hits) else pd.DataFrame(columns=["code", "title"])
        for code, g in ann[ann.code.isin(hits.code)].groupby("code"):
            ann_map[code] = list(dict.fromkeys(g.title))[:3]
    except Exception as e:  # noqa: BLE001
        log.warning("重大訊息失敗：%s", e)
    deadline = time.time() + 45  # 消息最多花 45 秒，避免拖延提醒
    for r in hits.head(15).itertuples():
        if time.time() > deadline:
            log.warning("抓新聞超過時間，略過其餘")
            break
        try:
            items = enrich.news(sess, r.code, r.name)
            if items:
                news_map[r.code] = items
        except Exception as e:  # noqa: BLE001
            log.warning("新聞失敗：%s", e)

    title = f"【盤中進場提醒】{today} {scan_time}｜{len(hits)} 檔"
    lines = [f"## 盤中進場提醒 {today} {scan_time}", ""]
    if senti:
        lines += [sentiment.md_line(senti, intraday=True), ""]
    if warn:
        lines += [f"> ⚠️ 大盤過去 20 天下跌 {mkt20:.1f}%。回測顯示大盤弱勢時此策略平均是虧損的，今天建議降低部位或觀望。", ""]
    if hits.empty:
        lines.append("今天沒有符合條件的股票。")
    else:
        lines += ["| 股票 | 產業 | 現價 | 漲幅 | 目前量(張) | 預估全日量 / 5日均量 | 本益比 | 投信昨日(張) | 月營收年增 | 備註 |",
                  "|---|---|--:|--:|--:|--:|--:|--:|--:|---|"]
        for r in hits.itertuples():
            mk = "TW" if stocks.set_index("code").at[r.code, "market"] == "TWSE" else "TWO"
            pe, tr, rv, fl = ex(r.code, "pe"), ex(r.code, "trust"), ex(r.code, "rev_yoy"), ex(r.code, "flag")
            note = []
            if fl:
                note.append(f"⛔{fl}股")
            if pe is not None and pe < 10:
                note.append("低本益比（歷史較弱）")
            if rv is not None and rv <= 0:
                note.append("營收衰退（回測無超額）")
            if r.code in ann_map:
                note.append("📢今日有重大訊息")
            lines.append(f"| [{r.code} {r.name}](https://tw.stock.yahoo.com/quote/{r.code}.{mk}) | {r.industry or ''} | "
                         f"{r.price:.2f} | {r.chg:+.2f}% | {r.vol_lots:,.0f} | {r.vol_x:.1f}× | "
                         f"{f'{pe:.1f}' if pe else '—'} | {f'{tr:+,.0f}' if tr is not None else '—'} | "
                         f"{f'{rv:+.0f}%' if rv is not None else '—'} | {'、'.join(note)} |")
        extra = []
        for r in hits.itertuples():
            items = [f"📢 {t}" for t in ann_map.get(r.code, [])] + [
                f"[{n['title']}]({n['link']}) {n['when']}" for n in news_map.get(r.code, [])]
            if items:
                extra += [f"**{r.code} {r.name}**", ""] + [f"- {x}" for x in items] + [""]
        if extra:
            lines += ["", "### 相關消息（近 3 天）", ""] + extra
    lines += ["", "<sub>條件：預估全日成交量 ≥ 前 5 日均量 3 倍、漲 3% 以上、現價 > 開盤價、突破前 60 日最高價。"
              "回測做法：收盤前買進，之後收盤量低於爆量日一半時，隔天開盤賣出（收盤後會另外通知出場）。"
              "量比為依時間推估的全日量（13:12 約已成交全天的 9 成），最終以收盤量為準。僅供參考，不構成投資建議。</sub>"]
    body = "\n".join(lines)
    try:
        notify.send(title, body)
    except Exception as e:  # noqa: BLE001
        log.error("通知失敗：%s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
