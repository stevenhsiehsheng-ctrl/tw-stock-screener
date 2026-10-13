"""台股交易日曆（往後推交易日用）：證交所休市日表，快取在 data/extras/tw_holidays.json。

過去的交易日看 history.csv.gz 就知道；這裡只管「未來」：revdrift 預定出場日、下一批批次日。
證交所 rwd/zh/holidaySchedule 每年一張表（抓不到再試 openapi，那邊只有今年），名稱含「交易」的
（開始交易日、最後交易日）是有開盤的，其餘是休市。兩邊格式都吃；解析出來天數不合理就當沒抓到、把前兩列印進 log。
抓不到就扣週末＋FALLBACK 裡推定的休市日，approx=True，頁面要標「約」。
10/13 第一次上線時 Actions 裡沒抓成（tw_holidays.json 沒產生），預定出場日只扣了週末。
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


# 抓不到官方表時用的推定休市日（只列今天以後、有把握的）：2025 年起紀念日逢週六提前到週五、逢週日延到週一放
# （2026-10-09 國慶補假證交所確實沒開，可佐證）。10/25 光復節是週日 → 10/26 週一；12/25 行憲紀念日週五。
FALLBACK = {"2026": ["2026-10-26", "2026-12-25"]}

_DATE = [re.compile(r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})"), re.compile(r"^(\d{3,4})(\d{2})(\d{2})$")]


def _date(cell) -> dt.date | None:
    """2026-01-01、115/01/01、1150101（openapi 民國年）、20260101 都認。"""
    s = str(cell).strip()
    for pat in _DATE:
        m = pat.search(s)
        if m:
            y = int(m.group(1))
            y = y + 1911 if y < 1000 else y
            try:
                return dt.date(y, int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def parse(j) -> list[str]:
    """證交所休市日表 → 休市日（YYYY-MM-DD，只留平日）。
    rwd 版 {"fields": [...], "data": [[...], ...]}、openapi 版 [{"Name": ..., "Date": "1150101", ...}, ...] 都吃。
    名稱含「交易」的（開始交易日、最後交易日）有開盤，不算；「無交易」（只辦結算交割）算休市。"""
    if isinstance(j, dict):
        rows, fields = j.get("data") or [], [str(f) for f in j.get("fields") or []]
    else:
        rows, fields = j or [], []
    iname = next((k for k, f in enumerate(fields) if "名稱" in f), None)
    out = []
    for row in rows:
        if isinstance(row, dict):
            cells = list(row.values())
            name = " ".join(str(v) for k, v in row.items() if "name" in str(k).lower() or "名稱" in str(k))
        else:
            cells = list(row) if isinstance(row, (list, tuple)) else [row]
            name = str(cells[iname]) if iname is not None and iname < len(cells) else " ".join(map(str, cells))
        d = next((x for x in map(_date, cells) if x), None)
        if d is None or ("交易" in name and "無交易" not in name):
            continue
        if d.weekday() < 5:
            out.append(d.isoformat())
    return sorted(set(out))


_MISS: set[int] = set()   # 這次執行已經抓過、沒抓到的年份（同一支程式算好幾次交易日時不重抓）


def _fetch(year: int) -> list[str] | None:
    import requests
    if year in _MISS:
        return None
    srcs = [("https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule", {"date": f"{year}0101", "response": "json"})]
    if year == dt.date.today().year:   # openapi 只有今年的表，問明年只會拿到今年的
        srcs.append(("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule", None))
    for url, params in srcs:
        try:
            j = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            log.warning("證交所休市日表 %s 抓不到（%s）：%s", year, url.split("/")[2], e)
            continue
        if isinstance(j, dict) and "data" in j and not j["data"] and year > dt.date.today().year:
            log.info("證交所 %s 年休市日表還沒公布（通常年底才有），先只扣週末＋推定休市日", year)
            continue
        days = [d for d in parse(j) if d.startswith(str(year))]
        if 3 <= len(days) <= 40:   # 一年平日休市大約 10～20 天；太少太多都是格式沒對上
            return days
        sample = (j.get("data") or [])[:2] if isinstance(j, dict) else (j or [])[:2] if isinstance(j, list) else j
        log.warning("證交所休市日表 %s（%s）解析出 %d 天，格式可能變了：%s", year, url.split("/")[2], len(days), str(sample)[:300])
    _MISS.add(year)
    return None


def holidays(years: list[int], fetch: bool = True) -> tuple[set[str], set[int]]:
    """回傳 (休市日集合, 只是推定的年份)。快取沒有的年份才去抓；抓不到的年份用 FALLBACK。"""
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {"years": {}}
    rough, changed = set(), False
    for y in years:
        if str(y) in cache["years"]:
            continue
        got = _fetch(y) if fetch else None
        if got is None:
            rough.add(y)
            continue
        cache["years"][str(y)] = got
        changed = True
    if changed:
        cache["fetched"] = dt.date.today().isoformat()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", "utf-8")
    hol = set()
    for y in years:
        hol |= set(cache["years"].get(str(y)) or FALLBACK.get(str(y), []))
    return hol, rough


def future_days(after: str, n: int, fetch: bool = True) -> tuple[list[str], bool]:
    """after 之後的 n 個交易日（平日、扣休市日）。第二個值＝這些日子有沒有落在只是推定的年份
    （明年的休市表通常年底才公布，只看今年的日子時不算近似）。"""
    d = dt.date.fromisoformat(after)
    hol, rough = holidays([d.year, d.year + 1], fetch)
    out = []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in hol:
            out.append(d.isoformat())
    return out, any(int(x[:4]) in rough for x in out)
