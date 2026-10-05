"""股東會紀念品：抓各家股代與整理網站的紀念品清單，給網站「股東紀念品」頁用。

用法：
  python -m screener.gifts --probe   只印出候選資料來源的格式（在 GitHub Actions 上跑，雲端環境連不到台灣網站）
"""
from __future__ import annotations

import argparse
import io
import re
import sys

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CANDIDATES = [
    ("HiStock 紀念品", "https://histock.tw/stock/gift.aspx"),
    ("股代網", "https://www.gooddie.tw/stock/meeting"),
    ("元大股代", "https://www.yuanta.com.tw/eYuanta/agent/AgentAPI/ShareHolderMeeting"),
    ("中信股代", "https://ecorp.ctbcbank.com/cts/static/ag_gift.jsp"),
    ("中信股代（列印版）", "https://ecorp.ctbcbank.com/cts/static/ag_gift_printer.jsp"),
    ("宏遠股代", "https://srd.honsec.com.tw/stock/souvenir.aspx"),
    ("玩股網", "https://www.wantgoo.com/stock/calendar/shareholders-meeting-souvenirs"),
    ("換換零股", "https://www.stockbox.com.tw/meetings?year=2026"),
    ("永豐", "https://www.sinotrade.com.tw/richclub/tools/gifts"),
    ("口袋", "https://events.pocket.tw/pocketsmlist-34839"),
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
    return s


def _text(r: requests.Response) -> str:
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def probe() -> None:
    """印出每個候選來源的狀態、標題、API 線索與前幾列表格。"""
    s = _session()
    for name, url in CANDIDATES:
        print(f"\n===== {name}  {url}")
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException as e:
            print("  連線失敗", e)
            continue
        t = _text(r)
        print("  HTTP", r.status_code, "長度", len(r.content), "type", r.headers.get("content-type"), "最後網址", r.url)
        m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
        print("  title", (m.group(1).strip() if m else None))
        hints = sorted(set(re.findall(r"""["']([^"'\s]*(?:api|ajax|json|ashx|Handler|GetData|query)[^"'\s]*)["']""", t, re.I)))
        print("  API 線索", hints[:20])
        try:
            tables = pd.read_html(io.StringIO(t))
        except (ValueError, ImportError) as e:
            tables = []
            print("  沒有表格", str(e)[:80])
        print("  表格數", len(tables))
        for i, df in enumerate(tables[:3]):
            print(f"  -- 表 {i} {df.shape}")
            print(df.head(8).to_string()[:3000])
        if not tables:
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
            print("  內文", body[:1500])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只印出候選資料來源的格式")
    a = ap.parse_args()
    if a.probe:
        probe()
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
