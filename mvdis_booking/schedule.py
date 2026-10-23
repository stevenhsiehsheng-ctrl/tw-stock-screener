"""多久查一次：一般時段、08:00 釋出名額前後加快、半夜放慢、網站忙碌時退避。"""
from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass

from . import config

MIN_DELAY_S = 10.0
BACKOFF_S = (60.0, 120.0, 300.0)


@dataclass(frozen=True)
class Window:
    start: int      # 當天第幾分鐘（含）
    end: int        # 當天第幾分鐘（不含）；比 start 小代表跨午夜
    interval: float

    def contains(self, minute: int) -> bool:
        if self.start <= self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end


def parse_window(s: str) -> Window:
    """'07:58-08:20@20' → 07:58～08:20 每 20 秒查一次。"""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*@\s*(\d+(?:\.\d+)?)\s*", str(s))
    if not m:
        raise ValueError("格式要像 07:58-08:20@20（開始-結束@秒數）")
    h1, m1, h2, m2 = (int(m.group(i)) for i in range(1, 5))
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and m1 < 60 and m2 < 60):
        raise ValueError("時間超出範圍")
    interval = float(m.group(5))
    if interval < MIN_DELAY_S:
        raise ValueError(f"秒數不要小於 {MIN_DELAY_S:g}")
    return Window(h1 * 60 + m1, h2 * 60 + m2, interval)


def _minute(now: dt.datetime) -> int:
    return now.hour * 60 + now.minute


def next_delay(now: dt.datetime, poll: dict, backoff_level: int = 0, rng: random.Random | None = None) -> float:
    """下一次查詢前要等幾秒。now 用台灣時間。"""
    rng = rng or random.Random()
    if backoff_level > 0:
        return BACKOFF_S[min(backoff_level, len(BACKOFF_S)) - 1] + rng.uniform(0, 10)
    windows = [parse_window(w) for w in poll.get("windows") or []]
    minute = _minute(now)
    base = float(poll["base_s"])
    jitter = float(poll.get("jitter_s") or 0)
    for w in windows:
        if w.contains(minute):
            base, jitter = w.interval, w.interval * 0.25
            break
    delay = max(MIN_DELAY_S, base + rng.uniform(-jitter, jitter))
    # 快到加速時段（例如 07:58）就不要睡過頭
    for w in windows:
        if w.interval < base and not w.contains(minute):
            start = now.replace(hour=w.start // 60, minute=w.start % 60, second=0, microsecond=0)
            if start <= now:
                start += dt.timedelta(days=1)
            delay = min(delay, max(MIN_DELAY_S, (start - now).total_seconds() + rng.uniform(0, 5)))
    return delay


class DateCursor:
    """決定這一輪「預計考試日期」要填哪一天。

    網站查詢可能只回那一天的場次，也可能回那天以後所有場次（尚未確認）。
    一開始逐日輪流查；只要看到比查詢日更晚的場次，就知道是「那天以後」，之後每次都查範圍第一天就好。
    """

    def __init__(self) -> None:
        self.onward: bool | None = None
        self.idx = 0

    def candidates(self, cfg: dict, today: dt.date) -> list[dt.date]:
        start, end = config.date_range(cfg, today)
        days = []
        d = start
        while d <= end:
            if d.isoweekday() in cfg["target"]["weekdays"]:
                days.append(d)
            d += dt.timedelta(days=1)
        return days

    def next_query_date(self, cfg: dict, today: dt.date) -> dt.date | None:
        start, end = config.date_range(cfg, today)
        if start > end:
            return None
        if self.onward:
            return start
        days = self.candidates(cfg, today)
        if not days:
            return None
        return days[self.idx % len(days)]

    def observe(self, query_date: dt.date, session_dates: list[dt.date]) -> None:
        if any(d > query_date for d in session_dates):
            self.onward = True
        elif not self.onward:
            self.idx += 1
