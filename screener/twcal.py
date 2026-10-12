"""台股交易日曆（往後推交易日用）：證交所休市日表，快取在 data/extras/tw_holidays.json。

過去的交易日看 history.csv.gz 就知道；這裡只管「未來」：revdrift 預定出場日、下一批批次日。
證交所 rwd/zh/holidaySchedule 每年一張表，名稱含「交易」的（開始交易日、最後交易日）是有開盤的，其餘是休市。
抓不到就只扣週末，approx=True，頁面要標「約」。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "extras" / "tw_holidays.json"
log = logging.getLogger("twcal")


def parse(j: dict) -> list[str]:
    """證交所休市日表 JSON → 休市日（YYYY-MM-DD，只留平日）。"""
    out = []
    for row in j.get("data") or []:
        name = str(row[0]) if row else ""
        if "交易" in name and "無交易" not in name:   # 開始交易日、最後交易日：有開盤
            continue
        for cell in row[1:3]:
            m = re.search(r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})", str(cell))
            if m:
                y = int(m.group(1))
                y = y + 1911 if y < 1000 else y
                d = dt.date(y, int(m.group(2)), int(m.group(3)))
                if d.weekday() < 5:
                    out.append(d.isoformat())
                break
    return sorted(set(out))


def _fetch(year: int) -> list[str] | None:
    import requests
    try:
        r = requests.get("https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule",
                         params={"date": f"{year}0101", "response": "json"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        days = parse(r.json())
        return days if days else None
    except Exception as e:  # noqa: BLE001
        log.warning("證交所休市日表 %s 抓不到：%s", year, e)
        return None


def holidays(years: list[int], fetch: bool = True) -> tuple[set[str], bool]:
    """回傳 (休市日集合, 是否只是近似)。快取沒有的年份才去抓。"""
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {"years": {}}
    approx, changed = False, False
    for y in years:
        if str(y) in cache["years"]:
            continue
        got = _fetch(y) if fetch else None
        if got is None:
            approx = True
            continue
        cache["years"][str(y)] = got
        changed = True
    if changed:
        cache["fetched"] = dt.date.today().isoformat()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {d for y in years for d in cache["years"].get(str(y), [])}, approx


def future_days(after: str, n: int, fetch: bool = True) -> tuple[list[str], bool]:
    """after 之後的 n 個交易日（平日、扣休市日）。"""
    d = dt.date.fromisoformat(after)
    hol, approx = holidays([d.year, d.year + 1], fetch)
    out = []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in hol:
            out.append(d.isoformat())
    return out, approx
