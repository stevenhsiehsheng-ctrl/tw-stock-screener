"""民國日期：轉成網站要的格式、從網頁文字裡找日期。"""
from __future__ import annotations

import datetime as dt
import re

FORMATS = ("roc7", "roc_slash", "slash", "iso")

# 115/10/20、115-10-20、115.10.20、115年10月20日、2026/10/20
_SEP_RE = re.compile(r"(?<!\d)(\d{2,4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})(?!\d)")
# 1151020（民國年 3 碼＋月日各 2 碼）
_ROC7_RE = re.compile(r"(?<!\d)(\d{3})(\d{2})(\d{2})(?!\d)")


def to_roc7(d: dt.date) -> str:
    """2026-10-20 → '1151020'；1989-07-02 → '0780702'。"""
    y = d.year - 1911
    if not 1 <= y <= 999:
        raise ValueError(f"民國年超出範圍：{d}")
    return f"{y:03d}{d.month:02d}{d.day:02d}"


def format_date(d: dt.date, fmt: str) -> str:
    if fmt == "roc7":
        return to_roc7(d)
    if fmt == "roc_slash":
        return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"
    if fmt == "slash":
        return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"
    if fmt == "iso":
        return d.isoformat()
    raise ValueError(f"不認得的日期格式：{fmt}（可用 {', '.join(FORMATS)}）")


def _make(y: int, m: int, d: int) -> dt.date | None:
    year = y if y >= 1911 else y + 1911
    if not 1912 <= year <= 2200:
        return None
    try:
        return dt.date(year, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[dt.date]:
    """文字裡所有看起來像日期的東西（依出現順序，不重複）。"""
    found: list[tuple[int, dt.date]] = []
    for m in _SEP_RE.finditer(text or ""):
        d = _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _ROC7_RE.finditer(text or ""):
        y = int(m.group(1))
        if 1 <= y <= 200:
            d = _make(y, int(m.group(2)), int(m.group(3)))
            if d:
                found.append((m.start(), d))
    out: list[dt.date] = []
    for _, d in sorted(found, key=lambda x: x[0]):
        if d not in out:
            out.append(d)
    return out


def parse_date(text: str) -> dt.date | None:
    ds = find_dates(text)
    return ds[0] if ds else None


def guess_input_format(input_type: str, placeholder: str, maxlength: int | None) -> str:
    """依日期欄位的 type／placeholder／maxlength 猜網站要哪種格式。"""
    if (input_type or "").lower() == "date":
        return "iso"
    ph = placeholder or ""
    if re.search(r"(?<!\d)\d{7}(?!\d)", ph):
        return "roc7"
    m = re.search(r"(?<!\d)(\d{2,4})\s*/\s*\d{1,2}\s*/\s*\d{1,2}", ph)
    if m:
        return "slash" if len(m.group(1)) == 4 else "roc_slash"
    if re.search(r"yyyy", ph, re.I):
        return "slash" if "/" in ph else "iso"
    if maxlength == 9:
        return "roc_slash"
    if maxlength == 10:
        return "slash"
    return "roc7"
