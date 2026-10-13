"""讀取、檢查 config.yaml。"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
from pathlib import Path

import yaml

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PKG_DIR / "config.yaml"
TW = dt.timezone(dt.timedelta(hours=8))   # 台灣沒有日光節約時間，固定 UTC+8（Windows 不必另裝 tzdata）

MODES = ("dry_run", "book")
EARLIEST_DAYS_AHEAD = 3      # 考照日前 3 日 24 時截止預約
LATEST_DAYS_AHEAD = 30       # 開放 30 天內的場次

DEFAULTS: dict = {
    "mode": "dry_run",
    "target": {
        "license_label": "普通重型機車",
        "office_label": "",
        "station_label": "",
        "date_from": None,
        "date_to": None,
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
        "weekend_only": False,
        "group_include": "",
        "group_exclude": "",
        "date_format": "auto",
    },
    "applicant": {
        "id_no": "",
        "birth": None,
        "birth_format": "auto",
        "name": "",
        "mobile": "",
        "phone": "",
        "email": "",
        "extra": {},
    },
    "auto_agree": True,
    "captcha": {
        "timeout_s": 180,
        "submit_when_full": True,
        "length": None,
        "max_retries": 3,
    },
    "human_timeout_s": 900,
    "poll": {
        "base_s": 90,
        "jitter_s": 30,
        "windows": ["07:58-08:20@20", "01:00-06:30@600"],
        "max_pages": 5,
    },
    "notify": {
        "sound": True,
        "desktop": True,
        "email": {
            "enabled": False,
            "smtp_user": "",
            "smtp_password": "",
            "mail_to": "",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 465,
        },
    },
    "browser": {
        "channel": None,
        "headless": False,
    },
    "site": {
        "base_url": "https://www.mvdis.gov.tw",
    },
    "selectors": {},
}


class ConfigError(Exception):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- 身分證字號
_LETTER = {c: n for c, n in zip("ABCDEFGHJKLMNPQRSTUVXYWZIO", range(10, 36))}


def valid_tw_id(s: str) -> bool:
    """身分證字號／新式居留證（第 2 碼 8、9）檢查碼。舊式居留證（第 2 碼 A–D）只檢查格式。"""
    s = (s or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-D]\d{8}", s):
        return True
    if not re.fullmatch(r"[A-Z][1289]\d{8}", s):
        return False
    n = _LETTER[s[0]]
    total = n // 10 + (n % 10) * 9
    for w, ch in zip((8, 7, 6, 5, 4, 3, 2, 1), s[1:9]):
        total += w * int(ch)
    total += int(s[9])
    return total % 10 == 0


def mask_id(s: str) -> str:
    s = s or ""
    return s[:3] + "*" * max(0, len(s) - 6) + s[-3:] if len(s) > 6 else "*" * len(s)


def _to_date(v, what: str) -> dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip())
    except ValueError:
        raise ConfigError(f"{what} 要寫成 西元年-月-日，例如 2026-10-20（現在是 {v!r}）") from None


def _check_regex(s: str, what: str) -> None:
    try:
        re.compile(s or "")
    except re.error as e:
        raise ConfigError(f"{what} 不是正確的規則：{e}") from None


def validate(cfg: dict, require_applicant: bool = True) -> dict:
    """require_applicant=False：偵察模式用，不檢查（也用不到）applicant 的個人資料。"""
    if cfg["mode"] not in MODES:
        raise ConfigError(f"mode 只能是 {' 或 '.join(MODES)}（現在是 {cfg['mode']!r}）")
    t = cfg["target"]
    if not str(t.get("license_label") or "").strip():
        raise ConfigError("target.license_label（報考照類）沒有填，例如 普通重型機車")
    if not str(t.get("office_label") or "").strip():
        raise ConfigError("target.office_label（監理所）沒有填，例如 臺北區監理所")
    t["date_from"] = _to_date(t.get("date_from"), "target.date_from")
    t["date_to"] = _to_date(t.get("date_to"), "target.date_to")
    if t["date_from"] and t["date_to"] and t["date_from"] > t["date_to"]:
        raise ConfigError("target.date_from 比 date_to 還晚")
    wd = t.get("weekdays") or [1, 2, 3, 4, 5, 6, 7]
    if not all(isinstance(x, int) and 1 <= x <= 7 for x in wd):
        raise ConfigError("target.weekdays 只能填 1～7（1＝星期一，7＝星期日）")
    t["weekdays"] = sorted(set(wd))
    _check_regex(t.get("group_include"), "target.group_include")
    _check_regex(t.get("group_exclude"), "target.group_exclude")
    if require_applicant:
        _validate_applicant(cfg["applicant"])

    from . import schedule   # 避免循環 import
    for w in cfg["poll"].get("windows") or []:
        try:
            schedule.parse_window(w)
        except ValueError as e:
            raise ConfigError(f"poll.windows 的 {w!r} 寫錯了：{e}") from None
    if float(cfg["poll"]["base_s"]) < 30:
        raise ConfigError("poll.base_s 不要小於 30 秒，太頻繁可能被網站封鎖")

    e = cfg["notify"]["email"]
    if e.get("enabled"):
        e["smtp_password"] = e.get("smtp_password") or os.environ.get("SMTP_PASSWORD", "")
        e["smtp_user"] = e.get("smtp_user") or os.environ.get("SMTP_USER", "")
        e["mail_to"] = e.get("mail_to") or os.environ.get("MAIL_TO", "") or e["smtp_user"]
        if not (e["smtp_user"] and e["smtp_password"]):
            raise ConfigError("notify.email 開了，但 smtp_user／smtp_password（Gmail 應用程式密碼）沒有填")
    return cfg


def _validate_applicant(a: dict) -> None:
    a["id_no"] = str(a.get("id_no") or "").strip().upper()
    if a["id_no"] == "A123456789":
        raise ConfigError("applicant.id_no 還是範例號碼 A123456789，請改成你自己的身分證字號")
    if not valid_tw_id(a["id_no"]):
        raise ConfigError("applicant.id_no（身分證字號）格式或檢查碼不對，請再確認一次")
    a["birth"] = _to_date(a.get("birth"), "applicant.birth")
    if not a["birth"]:
        raise ConfigError("applicant.birth（出生日期）沒有填，要寫成 西元年-月-日，例如 2005-07-02")
    for k in ("name", "mobile", "phone", "email"):
        v = a.get(k)
        if k in ("mobile", "phone") and isinstance(v, int):
            v = f"{v:010d}" if k == "mobile" else f"0{v}"     # 沒加引號被 YAML 當成數字，補回開頭的 0
        a[k] = str(v or "").strip()
    a["mobile"] = re.sub(r"[\s\-()]", "", a["mobile"])
    a["phone"] = re.sub(r"\s", "", a["phone"])
    if a["mobile"] and not re.fullmatch(r"09\d{8}", a["mobile"]):
        raise ConfigError("applicant.mobile（手機）要是 09 開頭的 10 碼數字")
    if a["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a["email"]):
        raise ConfigError("applicant.email 格式不對")
    if not isinstance(a.get("extra") or {}, dict):
        raise ConfigError("applicant.extra 要寫成「欄位名稱: 值」的形式")
    a["extra"] = {str(k): str(v) for k, v in (a.get("extra") or {}).items()}


def load_config(path: Path | str | None = None, require_applicant: bool = True) -> dict:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(f"找不到設定檔 {path}。請先把 config.example.yaml 複製一份改名成 config.yaml，再填入你的資料。")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml 格式有誤（縮排要用空格、冒號後面要空一格）：{e}") from None
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml 內容格式不對")
    return validate(_merge(DEFAULTS, raw), require_applicant)


def date_range(cfg: dict, today: dt.date) -> tuple[dt.date, dt.date]:
    """實際要盯的日期範圍（夾在網站可預約的範圍內）。"""
    t = cfg["target"]
    lo = today + dt.timedelta(days=EARLIEST_DAYS_AHEAD)
    hi = today + dt.timedelta(days=LATEST_DAYS_AHEAD)
    start = max(lo, t["date_from"]) if t["date_from"] else lo
    end = min(hi, t["date_to"]) if t["date_to"] else hi
    return start, end
