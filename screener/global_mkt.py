"""美股隔夜行情：費半、那斯達克、台積電 ADR 等。

台股開盤方向和美國科技股前一晚的表現高度相關，尤其是費城半導體指數與台積電 ADR。
資料來源：Yahoo Finance（yfinance），在 GitHub Actions 上執行。
"""
from __future__ import annotations

import logging


log = logging.getLogger(__name__)

SYMBOLS = [
    ("^SOX", "費城半導體"),
    ("^IXIC", "那斯達克"),
    ("^GSPC", "標普500"),
    ("TSM", "台積電ADR"),
    ("NVDA", "輝達"),
    ("AAPL", "蘋果"),
    ("^VIX", "VIX恐慌指數"),
]


def _closes(sym: str):
    """單一代號近 10 日收盤（逐檔抓，避免多檔一起下載時日期對不齊、整欄缺值）。"""
    import yfinance as yf

    for _ in range(2):
        try:
            h = yf.Ticker(sym).history(period="10d", interval="1d", auto_adjust=False)
            s = h["Close"].dropna()
            if len(s) >= 2:
                return s
        except Exception as e:  # noqa: BLE001
            log.warning("%s 下載失敗：%s", sym, e)
    return None


# 抓不到時的替代代號（費半指數抓不到就用費半 ETF）
FALLBACK = {"^SOX": ("SOXX", "費半ETF")}


def _fix_stale(items: list, closes: dict) -> None:
    """個股的日 K 常比指數晚進 Yahoo：早上抓，指數已經是昨晚，個股還停在前一晚。
    落後的改用 data/us/history.csv.gz（us.yml 06:20 存的）補；補不到就標 stale，不要默默拿舊的。"""
    if not items:
        return
    import pandas as pd
    latest = max(pd.Timestamp(closes[i["sym"]].index[-1]).date() for i in items)
    lag = [i for i in items if pd.Timestamp(closes[i["sym"]].index[-1]).date() < latest]
    if not lag:
        return
    try:
        from .us import HIST
        h = pd.read_csv(HIST, usecols=["date", "sym", "close"])
    except Exception:  # noqa: BLE001
        h = None
    for i in lag:
        s = None if h is None else h[h.sym == i["sym"]].set_index("date").close.sort_index()
        if s is not None and len(s) >= 2 and s.index[-1] == latest.isoformat():
            i.update(close=round(float(s.iloc[-1]), 2), chg=round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2),
                     date=latest.strftime("%m/%d"))
            closes[i["sym"]] = pd.Series([float(s.iloc[-2]), float(s.iloc[-1])], index=pd.to_datetime(s.index[-2:]))
            log.info("%s Yahoo 日 K 落後，改用 us 歷史檔 %s", i["name"], latest)
        else:
            i["stale"] = True
            log.warning("%s 只拿到 %s，比指數（%s）舊一天", i["name"], i["date"], latest.strftime("%m/%d"))


def snapshot(tw2330_close: float | None = None) -> dict:
    """回傳 {"items": [{sym,name,close,chg,date}], "adr_premium": %}；失敗回傳空 dict。"""
    items, closes = [], {}
    for sym, name in SYMBOLS + [("TWD=X", "美元台幣")]:
        s = _closes(sym)
        if s is None and sym in FALLBACK:
            sym, name = FALLBACK[sym]
            s = _closes(sym)
        if s is None:
            log.warning("美股隔夜缺 %s", name)
            continue
        closes[sym] = s
        if sym == "TWD=X":
            continue
        items.append({"sym": sym, "name": name, "close": round(float(s.iloc[-1]), 2),
                      "chg": round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2),
                      "date": s.index[-1].strftime("%m/%d")})
    _fix_stale(items, closes)
    out = {"items": items}
    try:
        if tw2330_close and "TSM" in closes and "TWD=X" in closes:
            # 1 股 ADR = 5 股台積電
            out["adr_premium"] = round((float(closes["TSM"].iloc[-1]) * float(closes["TWD=X"].iloc[-1]) / 5
                                        / tw2330_close - 1) * 100, 1)
    except Exception:  # noqa: BLE001
        pass
    log.info("美股隔夜：%s", "、".join(f"{i['name']} {i['chg']:+.2f}%（{i['date']}）" for i in items))
    return out
