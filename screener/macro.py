"""大環境長歷史：加權指數、0050、費半、標普、VIX、美債殖利率、美元、台幣、銅、油（Yahoo，盡量抓最長）。

用途：
- 「大盤風險燈號」研究：台股 20 多年有好幾次空頭（2000、2008、2011、2015、2018、2020、2022），
  5 年回測只碰到 2022 一次，要判斷「能不能提前警報」得用長歷史。
- 大環境頁的走勢圖。
存 data/macro/long.csv.gz（date, sym, close, close_adj）。在 GitHub Actions 跑（本機連不到 Yahoo）：
  python -m screener.macro
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("macro")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "macro"
LONG = DATA / "long.csv.gz"

SYMS = [("^TWII", "加權指數"), ("0050.TW", "0050"), ("^SOX", "費城半導體"), ("^GSPC", "標普 500"),
        ("^VIX", "VIX 恐慌指數"), ("^TNX", "美國 10 年債殖利率"), ("^IRX", "美國 3 個月國庫券殖利率"),
        ("DX-Y.NYB", "美元指數"), ("TWD=X", "美元兌台幣"), ("HG=F", "銅"), ("CL=F", "原油")]


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
    df = pd.concat(out, ignore_index=True).dropna(subset=["close"])
    DATA.mkdir(parents=True, exist_ok=True)
    df.sort_values(["sym", "date"]).to_csv(LONG, index=False, compression="gzip")
    return len(df)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print("大環境長歷史", update(), "筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
