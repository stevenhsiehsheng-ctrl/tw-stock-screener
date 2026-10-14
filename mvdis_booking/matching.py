"""看網頁內容做判斷的純函式（不碰瀏覽器，方便測試）。

網站的欄位代碼（name/id）查不到可靠資料，所以一律先用「畫面上看得到的文字」找欄位，
name/id 只當備援線索。輸入是 pagejs.py 從網頁抓回來的 dict。
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

from .rocdate import find_dates, format_date, guess_input_format, parse_date, to_roc7


# ---------------------------------------------------------------- 文字正規化
def norm(s: str) -> str:
    """全形轉半形、台→臺、去空白，用來比對選項文字。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("台", "臺")
    return re.sub(r"\s+", "", s)


class OptionError(Exception):
    pass


def _name_part(text: str) -> str:
    """監理服務網的選項後面帶地址／轄區，例如「花蓮監理站(花蓮縣吉安鄉…)」：只取括號前的名稱。"""
    return re.sub(r"\s*[(（].*$", "", text)


def choose_option(options: list, wanted: str) -> str:
    """從 [[value, text], ...] 挑出文字符合 wanted 的那個 value。
    完全相同優先，其次唯一的部分相符；都先比括號前的名稱，再比整段文字（地址裡的「花蓮」不會搶走「花蓮監理站」）。"""
    w = norm(wanted)
    if not w:
        raise OptionError("沒有指定要選什麼")
    texts = [(str(v), str(t)) for v, t in options]
    keys = (lambda t: norm(_name_part(t)), norm)
    for key in keys:
        exact = [v for v, t in texts if key(t) == w]
        if len(exact) == 1:
            return exact[0]
    for key in keys:
        part = [v for v, t in texts if w in key(t)]
        if len(part) == 1:
            return part[0]
    shown = "、".join(t for _, t in texts if t.strip()) or "（沒有選項）"
    if not part:
        raise OptionError(f"找不到「{wanted}」，網頁上可選的有：{shown}")
    raise OptionError(f"「{wanted}」同時符合好幾個選項，請寫得更完整。可選的有：{shown}")


def has_option(options: list, wanted: str) -> bool:
    try:
        choose_option(options, wanted)
        return True
    except OptionError:
        return False


# ---------------------------------------------------------------- 欄位辨識
def field_texts(f: dict) -> list[str]:
    """欄位的說明文字，越可靠的排越前面。

    欄位有自己的標籤（label 或同一列的表頭）時，就不看「前面的文字」和「欄標題」，
    免得上一列的提示文字（例如「請填手機號碼」）被誤當成這一格的名稱。
    """
    strong = [f.get("label") or "", f.get("row_label") or "", f.get("placeholder") or ""]
    if f.get("label") or f.get("row_label"):
        return strong + ["", ""]
    return strong + [f.get("before_text") or "", f.get("col_header") or ""]


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# 查詢頁
QUERY_RX = {
    "license": _rx(r"報考照類|考照類別|照類|Type\s*of\s*Test"),
    "date": _rx(r"預計考試日期|考試日期|Date\s*of\s*Test"),
    "place": _rx(r"考試地點|監理所|監理站|Place\s*of\s*Test"),
    "weekend": _rx(r"[周週]末|假日|Holiday|Weekend"),
}
QUERY_NAME_RX = {
    "license": _rx(r"licen[cs]e"),
    "date": _rx(r"exam\w*date|date\w*str"),
    "place": _rx(r"dmv"),
    "weekend": _rx(r"weekend|holiday"),
}

# 報名資料頁（順序有意義：前面的先比對）
FIELD_RX = [
    # 送出後才出現的 OTP（寄到你手機／信箱的一次性密碼）：不是圖片驗證碼，要你本人輸入
    ("otp", _rx(r"OTP|一次性")),
    ("captcha", _rx(r"驗證碼|認證碼|檢查碼|圖中|圖片中|captcha|verif\w*\s*code|security\s*code")),
    ("id_no", _rx(r"身[分份]證|居留證|統一證號|證號|ID\s*No|Identity")),
    ("birth", _rx(r"出生|生日|Birth")),
    ("email", _rx(r"電子郵件|電子信箱|e-?mail|信箱")),
    ("mobile", _rx(r"手機|行動電話|mobile|cell")),
    ("phone", _rx(r"電話|tel\b|phone")),
    ("name", _rx(r"姓\s*名|^\s*name\b|\bname\s*[:：]?\s*$")),
]
FIELD_NAME_RX = [
    ("otp", _rx(r"otp")),
    ("captcha", _rx(r"captcha|valid(ate)?_?(str|code|num|img)|verif\w*code|verifycode|checkcode|imgcode|kaptcha|rand\w*code|vcode|authcode")),
    ("id_no", _rx(r"^(?:id|idno|id_no|pid|uid|idnum\w*|identity\w*|personid)$|idno")),
    ("birth", _rx(r"birth")),
    ("email", _rx(r"e?mail")),
    ("mobile", _rx(r"mobile|cell")),
    ("phone", _rx(r"tel|phone")),
    ("name", _rx(r"^(?:name|username|cname|applicantname)$")),
]
AGREE_RX = _rx(r"同意|已詳閱|已閱讀|已瞭解|已了解|知悉|I\s*agree")
DISAGREE_RX = _rx(r"不同意")
REQUIRED_MARK_RX = re.compile(r"[*＊]|必填")


def classify_field(f: dict) -> str | None:
    if f.get("type") == "checkbox":
        txt = " ".join(field_texts(f)[:4])
        if AGREE_RX.search(txt) and not DISAGREE_RX.search(f.get("label") or ""):
            return "agree"
        return None
    if f.get("type") == "radio":
        own = f.get("label") or ""
        return "agree" if AGREE_RX.search(own) and not DISAGREE_RX.search(own) else None
    for txt in field_texts(f):
        if not txt:
            continue
        for key, rx in FIELD_RX:
            if rx.search(txt):
                return key
    for attr in (f.get("name") or "", f.get("id") or ""):
        for key, rx in FIELD_NAME_RX:
            if attr and rx.search(attr):
                return key
    return None


class LocateError(Exception):
    pass


def _query_hit(f: dict, key: str) -> bool:
    if any(QUERY_RX[key].search(t) for t in field_texts(f)[:4] if t):
        return True
    return any(QUERY_NAME_RX[key].search(a) for a in (f.get("name") or "", f.get("id") or "") if a)


def locate_query_fields(fields: list[dict]) -> dict:
    """在查詢頁找出：報考照類、考試地點（1～2 個下拉選單）、預計考試日期、僅查周末。"""
    selects = [f for f in fields if f.get("tag") == "select"]
    texts = [f for f in fields if f.get("tag") == "input" and f.get("type") in
             ("text", "date", "tel", "search", "number", "")]
    lic = [f for f in selects if _query_hit(f, "license")]
    if not lic:
        lic = [f for f in selects if any("機車" in t for _, t in f.get("options") or [])]
    if not lic:
        raise LocateError("找不到「報考照類」下拉選單")
    license_f = lic[0]
    places = [f for f in selects if f is not license_f and _query_hit(f, "place")]
    if len(places) == 1:
        # 第二個下拉選單（監理站）前面通常沒有說明文字，就看緊接在後面的下拉選單
        order = sorted(fields, key=lambda f: f["order"])
        i = order.index(places[0])
        nxt = order[i + 1] if i + 1 < len(order) else None
        if nxt is not None and nxt.get("tag") == "select" and nxt is not license_f:
            places.append(nxt)
    if not places:
        raise LocateError("找不到「考試地點」下拉選單")
    places = sorted(places, key=lambda f: f["order"])[:2]
    dates = [f for f in texts if _query_hit(f, "date")]
    if not dates:
        raise LocateError("找不到「預計考試日期」欄位")
    weekend = [f for f in fields if f.get("type") == "checkbox" and _query_hit(f, "weekend")]
    return {"license": license_f, "places": places, "date": dates[0],
            "weekend": weekend[0] if weekend else None}


# ---------------------------------------------------------------- 可點的按鈕
def pick_clickable(clicks: list[dict], patterns: list[str], exclude: str | None = None) -> dict | None:
    """依 patterns 的順序找第一個文字符合的按鈕／連結。"""
    ex = _rx(exclude) if exclude else None
    for p in patterns:
        rx = _rx(p)
        for c in clicks:
            t = c.get("text") or ""
            if c.get("disabled"):
                continue
            if rx.search(t) and not (ex and ex.search(t)):
                return c
    return None


SEARCH_BUTTONS = [r"^查詢場次", r"查詢場次", r"^Search$", r"^查\s*詢$"]
SEARCH_EXCLUDE = r"已報名|紀錄|記錄|取消|危險感知|資格"
CONTINUE_BUTTONS = [r"選擇場次繼續報名", r"繼續報名"]
SUBMIT_BUTTONS = [r"^確認送出$", r"^送\s*出$", r"^確定送出$", r"確認報名|確定報名|送出報名|報名送出",
                  r"^送出", r"送出", r"^確\s*定$|^確\s*認$", r"^報\s*名(\s*sign\s*-?\s*up)?$", r"^submit$"]
SUBMIT_EXCLUDE = r"取消|返回|回上|上一|重新|清除|重填|換一張|關閉|查詢|說明|列印"
CONFIRM_BUTTONS = [r"^確認送出$", r"^確定送出$", r"確認報名|確定報名", r"^確\s*定$|^確\s*認$"]
# 監理服務網報名頁一打開會跳「我已充分知悉相關約定並願接受」蓋住表單
AGREE_BUTTONS = [r"我已(詳細)?閱讀", r"我同意", r"^同意", r"同意.*(繼續|下一步)", r"我已.{0,6}知悉", r"並願接受"]
NEXT_BUTTONS = [r"^下一步$", r"^進入報名", r"^繼續$"]
# 注意事項頁只按「同意／下一步」；看起來像送出報名的按鈕一律不按
AGREE_EXCLUDE = r"不同意|取消|返回|送出|確定|確認|完成|submit"
# 確認頁上真正送出報名的按鈕（單獨的「確定」只有在頁面寫著請確認時才算）
STRONG_CONFIRM_BUTTONS = [r"^確認送出$", r"^確定送出$", r"確認報名|確定報名|送出報名"]
CONFIRM_PAGE_RX = re.compile(r"請確認|確認.{0,6}(資料|內容|資訊)|核對")
NEXT_PAGE = [r"^下一頁$", r"^下頁$", r"^Next$", r"^[>›»]$"]


# ---------------------------------------------------------------- 場次表
HEADER_RX = [
    ("date", _rx(r"考試日期|日期|Date")),
    ("seats", _rx(r"可報名人數|可報名|剩餘|名額|人數|Quota|Available")),
    ("group", _rx(r"場次組別|組別|場次|說明|Desc")),
    ("action", _rx(r"功能|報名|操作|Function|Action|Sign")),
]
SIGNUP_RX = _rx(r"報名|sign\s*-?\s*up|預約|選擇")
SIGNUP_EXCLUDE = _rx(r"取消|查詢|說明|勿|不得|請勿")


def header_map(cells: list[str]) -> dict:
    m: dict = {}
    for i, c in enumerate(cells):
        for key, rx in HEADER_RX:
            if key not in m and rx.search(c or ""):
                m[key] = i
                break
    return m


def find_session_table(tables: list[dict]) -> tuple[dict, int, dict] | None:
    """在所有表格裡找場次表，回傳 (表格, 表頭列序號, 欄位對應)。"""
    best = None
    for t in tables:
        for ri, row in enumerate(t.get("rows", [])[:4]):
            cells = row.get("cells") or []
            # 整列合併成一格（同一段文字重複）的不是表頭
            if len(set(cells)) < 2:
                continue
            m = header_map(cells)
            if len(m) >= 3 and ("date" in m or "group" in m):
                score = len(m)
                if best is None or score > best[0]:
                    best = (score, t, ri, m)
                break
    return None if best is None else (best[1], best[2], best[3])


@dataclass
class Session:
    date: dt.date | None
    group: str
    seats: int | None
    action_id: str | None
    action_text: str
    raw: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.date, self.group)

    def describe(self) -> str:
        d = f"{self.date.isoformat()}（{'一二三四五六日'[self.date.weekday()]}）" if self.date else "日期不明"
        seats = "?" if self.seats is None else str(self.seats)
        return f"{d} {self.group or '（無組別說明）'}｜可報名 {seats}"


def parse_seats(text: str) -> int | None:
    t = text or ""
    if re.search(r"額滿|已滿|名額已滿|無名額|不開放|停止", t):
        return 0
    if re.search(r"\d+\s*/\s*\d+", t):
        return None     # 「已報名/總名額」之類的格式，看不出剩幾個
    m = re.search(r"\d+", t)
    return int(m.group()) if m else None


def parse_sessions(found: tuple[dict, int, dict]) -> list[Session]:
    table, hdr_i, m = found
    out: list[Session] = []
    cur_date: dt.date | None = None
    for row in table["rows"][hdr_i + 1:]:
        cells = row.get("cells") or []
        if not cells or len(header_map(cells)) >= 3:
            continue
        uniq = {c for c in cells if c}
        if len(uniq) == 1:
            # 整列合併成一格的分隔列，可能只寫日期
            d = parse_date(next(iter(uniq)))
            if d:
                cur_date = d
            continue

        def cell(key: str) -> str:
            i = m.get(key)
            return cells[i] if i is not None and i < len(cells) else ""
        d = parse_date(cell("date")) if "date" in m else None
        if d:
            cur_date = d
        group = cell("group") if "group" in m else " ".join(c for i, c in enumerate(cells) if i != m.get("date"))
        acts = row.get("actions") or []
        col = m.get("action")
        in_col = [a for a in acts if col is not None and a.get("col") == col]
        # 有「功能」欄就只看那一欄：監理服務網的組別說明裡有「(初考生勿預約本場次)」這種 <a> 標示文字，不是報名鈕
        pool = in_col if col is not None else acts
        good = [a for a in pool if SIGNUP_RX.search(a.get("text") or "") and not SIGNUP_EXCLUDE.search(a.get("text") or "")]
        if not good and len(in_col) == 1 and not SIGNUP_EXCLUDE.search(in_col[0].get("text") or ""):
            good = in_col
        if not good and col is not None:
            # 「功能」欄對錯了（例如前面多一欄「已報名人數」被當成功能欄）：退回整列找，但不看組別說明那一欄
            gcol = m.get("group")
            good = [a for a in acts if a.get("col") != gcol and SIGNUP_RX.search(a.get("text") or "")
                    and not SIGNUP_EXCLUDE.search(a.get("text") or "")]
        act = good[0] if good else None
        seats = parse_seats(cell("seats")) if "seats" in m else None
        if not (cur_date or group.strip() or act):
            continue
        out.append(Session(cur_date, group.strip(), seats, act.get("aid") if act else None,
                           act.get("text", "") if act else "", list(cells)))
    return out


# 組別說明裡「(初考生勿預約本場次)」這種「誰不能報」的句子：比對 group_include 前先拿掉，
# 不然「初考」會在「初考生勿預約」裡被找到，幫第一次考的人約到重考專用的場次
NEGATED_RX = re.compile(r"[(（][^)）]*(?:請?勿|不得|不可|不能)[^)）]*[)）]"
                        r"|[^，,。、()（）\s]*(?:請?勿|不得|不可|不能)(?:預約|報名|報考)[^，,。()（）]*")


def positive_part(group: str) -> str:
    return NEGATED_RX.sub(" ", group or "")


def filter_sessions(sessions: list[Session], cfg: dict, start: dt.date, end: dt.date) -> list[Session]:
    t = cfg["target"]
    inc = _rx(t["group_include"]) if t.get("group_include") else None
    exc = _rx(t["group_exclude"]) if t.get("group_exclude") else None
    out = []
    for s in sessions:
        if not s.action_id or s.date is None:
            continue
        if not start <= s.date <= end or s.date.isoweekday() not in t["weekdays"]:
            continue
        if s.seats is not None and s.seats < 1:
            continue
        if inc and not inc.search(positive_part(s.group)):
            continue
        if exc and exc.search(s.group):
            continue
        out.append(s)
    return sorted(out, key=lambda s: s.date)


# ---------------------------------------------------------------- 填報名資料
@dataclass
class FillPlan:
    actions: list = field(default_factory=list)     # (field, kind, value)
    captcha: dict | None = None
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    mapping: list = field(default_factory=list)     # (欄位說明, 辨識結果) 給偵察報告用


def describe_field(f: dict) -> str:
    for t in field_texts(f):
        if t:
            return t[:30]
    return f.get("name") or f.get("id") or f"第 {f.get('order')} 個欄位"


def _is_required(f: dict) -> bool:
    return bool(f.get("required")) or any(REQUIRED_MARK_RX.search(t or "") for t in field_texts(f)[:2])


def _birth_parts(birth: dt.date, year_field: dict) -> list[str]:
    """出生日期分成年／月／日三格時各填什麼（年份看選項或長度判斷西元或民國）。"""
    opts = year_field.get("options") or []
    if opts:
        gregorian = any(re.search(r"(?<!\d)(19|20)\d\d(?!\d)", f"{v} {t}") for v, t in opts)
    else:
        gregorian = year_field.get("maxlength") == 4
    return [str(birth.year) if gregorian else str(birth.year - 1911), str(birth.month), str(birth.day)]


def _select_value_for_number(options: list, num: str) -> str | None:
    """年／月／日下拉選單：比對數字（不管前面有沒有 0、有沒有「年」字）。"""
    want = int(num)
    for v, t in options:
        for s in (str(v), str(t)):
            m = re.search(r"\d+", s)
            if m and int(m.group()) == want:
                return str(v)
    return None


def plan_fill(fields: list[dict], cfg: dict, has_captcha_image: bool = False) -> FillPlan:
    """決定報名頁每個欄位要填什麼。不認得又必填的欄位會列在 problems（程式會停下來請你處理）。"""
    a = cfg["applicant"]
    extra = {norm(k): v for k, v in (a.get("extra") or {}).items()}
    values = {
        "id_no": a["id_no"],
        "name": a.get("name") or "",
        "mobile": a.get("mobile") or a.get("phone") or "",
        "phone": a.get("phone") or a.get("mobile") or "",
        "email": a.get("email") or "",
    }
    plan = FillPlan()
    order = sorted(fields, key=lambda f: f["order"])
    used: set = set()
    radios_done: set = set()
    for i, f in enumerate(order):
        if id(f) in used:
            continue
        desc = describe_field(f)
        typ = f.get("type")
        # 使用者在 applicant.extra 指定的欄位
        ex_val = None
        for t in field_texts(f)[:4]:
            if t and any(k and k in norm(t) for k in extra):
                ex_val = next(v for k, v in extra.items() if k and k in norm(t))
                break
        if typ == "radio":
            name = f.get("name") or desc
            if name in radios_done:
                continue
            radios_done.add(name)
            group = [g for g in order if g.get("type") == "radio" and (g.get("name") or describe_field(g)) == name]
            desc = (f.get("row_label") or f.get("before_text") or f.get("col_header") or name)[:30]
            if ex_val is not None:
                want = norm(ex_val)
                target = next((g for g in group if want and want in norm(g.get("label") or "")), None)
                if target is None:
                    plan.problems.append(f"單選題「{desc}」找不到選項「{ex_val}」")
                elif not target.get("checked"):
                    plan.actions.append((target, "check", True))
                continue
            agree = next((g for g in group if classify_field(g) == "agree"), None)
            if agree is not None:
                # 「同意／不同意」單選：就算預設勾在不同意，也要改選同意
                if not agree.get("checked"):
                    if cfg.get("auto_agree"):
                        plan.actions.append((agree, "check", True))
                        plan.notes.append(f"選擇「{describe_field(agree)}」")
                    else:
                        plan.problems.append(f"需要選擇「{describe_field(agree)}」（auto_agree 是關的）")
                continue
            if any(g.get("checked") for g in group):
                continue
            if any(_is_required(g) for g in group):
                plan.problems.append(f"單選題「{desc}」不知道要選哪個（可在 applicant.extra 指定）")
            continue
        if f.get("disabled"):
            continue
        key = classify_field(f)
        plan.mapping.append((desc, key or ("（自訂）" if ex_val is not None else "？")))
        if ex_val is not None and key != "captcha":
            if f.get("tag") == "select":
                plan.actions.append((f, "select", ex_val))
            elif typ == "checkbox":
                if str(ex_val).lower() in ("1", "true", "yes", "y", "是", "勾選", "on"):
                    plan.actions.append((f, "check", True))
            else:
                plan.actions.append((f, "fill", ex_val))
            continue
        if key == "captcha":
            if plan.captcha is None:
                plan.captcha = f
            continue
        if key == "agree":
            if not f.get("checked"):
                if cfg.get("auto_agree"):
                    plan.actions.append((f, "check", True))
                    plan.notes.append(f"勾選「{desc}」")
                elif _is_required(f):
                    plan.problems.append(f"需要勾選「{desc}」（auto_agree 是關的）")
            continue
        if typ == "checkbox":
            continue
        if f.get("readonly") and (key is None or (f.get("value_len") or 0) > 0):
            continue     # 唯讀欄位：通常是網站已經帶好的資料（例如場次）
        if key == "birth":
            parts = [f]
            j = i + 1
            while len(parts) < 3 and j < len(order):
                g = order[j]
                if g.get("tag") != f.get("tag") or g.get("type") in ("checkbox", "radio"):
                    break
                gk = classify_field(g)
                if not (gk == "birth" or (gk is None and re.fullmatch(r"\s*[年月日/\-.]?\s*", g.get("before_text") or ""))):
                    break
                parts.append(g)
                j += 1
            if len(parts) == 3:
                for g, v in zip(parts, _birth_parts(a["birth"], parts[0])):
                    used.add(id(g))
                    if g.get("tag") == "select":
                        sv = _select_value_for_number(g.get("options") or [], v)
                        if sv is None:
                            plan.problems.append(f"出生日期下拉選單找不到 {v}")
                        else:
                            plan.actions.append((g, "select_value", sv))
                    else:
                        plan.actions.append((g, "fill", v.zfill(3) if g is parts[0] and g.get("maxlength") == 3 else v))
                continue
            fmt = a.get("birth_format") or "auto"
            if fmt == "auto":
                fmt = guess_input_format(typ, f.get("placeholder") or "", f.get("maxlength"))
            val = format_date(a["birth"], fmt)
            plan.actions.append((f, "js_set" if f.get("readonly") else "fill", val))
            continue
        if key in values:
            if f.get("tag") == "select":
                plan.problems.append(f"「{desc}」是下拉選單，請在 applicant.extra 指定要選什麼")
            elif values[key]:
                plan.actions.append((f, "js_set" if f.get("readonly") else "fill", values[key]))
            elif (f.get("value_len") or 0) == 0:
                plan.problems.append(f"網頁要填「{desc}」，但 config.yaml 的 applicant 沒有填這一項")
            continue
        # 認不得的欄位
        if f.get("tag") == "select":
            if not (f.get("selected_value") or "").strip() and len(f.get("options") or []) > 1:
                plan.problems.append(f"不認得的下拉選單「{desc}」（可在 applicant.extra 指定）")
            continue
        if (f.get("value_len") or 0) == 0:
            if _is_required(f):
                plan.problems.append(f"不認得的必填欄位「{desc}」（可在 applicant.extra 指定）")
            else:
                plan.notes.append(f"略過不認得的欄位「{desc}」")
    if plan.captcha is None and has_captcha_image:
        empties = [f for f in order if f.get("tag") == "input" and f.get("type") in ("text", "tel", "number", "")
                   and classify_field(f) is None and (f.get("value_len") or 0) == 0 and not f.get("readonly")]
        if len(empties) == 1:
            plan.captcha = empties[0]
            plan.problems = [p for p in plan.problems if describe_field(empties[0]) not in p]
            plan.notes = [n for n in plan.notes if describe_field(empties[0]) not in n]
        else:
            plan.problems.append("網頁上有驗證碼圖片，但找不到要輸入驗證碼的欄位")
    if not any(classify_field(f) == "id_no" for f in fields):
        plan.problems.append("報名頁上找不到身分證字號欄位")
    return plan


def date_variants(d: dt.date) -> list[str]:
    return [to_roc7(d), format_date(d, "roc_slash"), format_date(d, "slash"), d.isoformat()]


def session_date_check(page_text: str, d: dt.date | None) -> str:
    """報名頁上顯示的場次日期跟我們點的是否一致：'ok'／'mismatch'／'unknown'。"""
    if d is None:
        return "unknown"
    found = find_dates(page_text)
    if d in found or re.search(rf"(?<!\d)0?{d.month}\s*月\s*0?{d.day}\s*日", page_text or ""):
        return "ok"
    if any(abs((x - d).days) <= 45 for x in found):
        return "mismatch"
    return "unknown"


# ---------------------------------------------------------------- 結果判斷
# 注意：只拿「動作之後才新出現的文字」和網頁對話框來判斷（見 new_lines），
# 否則頁面上本來就有的注意事項（例如「未完成危險感知者不得報名」）會被誤判。
CAPTCHA_WRONG_RX = _rx(r"驗證碼.{0,6}(錯誤|不正確|有誤|不符|失效|過期)|(錯誤|請重新輸入).{0,4}驗證碼")
# 「如報名失敗請洽監理所」「報名成功者請準時到考」「是否報名成功」這類條件句、說明文字不算結果。
# 監理服務網報名頁的注意事項：「完成畫面中，需顯示預約成功才算成功」也不算
FAILED_RX = _rx(r"(?<![如若倘有])(報名|預約)(失敗|未成功)(?!請洽|者|或|時|的話|，?請洽)")
SUCCESS_RX = _rx(r"(?<![未不法如若倘否是將示需須])(報名|預約)成功(?![後前時者與才]|的話|即為|與否)"
                 r"|(?<![如若倘將])已完成(報名|預約)(?![後前時者])"
                 r"|(?<![如若倘將即])(報名|預約)完成(?![後前時者與])")
# 明確的錯誤訊息（單獨出現也採信）
REASON_RX = [
    ("hazard", _rx(r"查無.{0,20}危險感知|無.{0,6}有效.{0,10}危險感知|危險感知.{0,10}(紀錄|記錄).{0,6}(不存在|無效|過期|查無)")),
    ("banned", _rx(r"停權(中|期間)|(您|目前|仍在).{0,12}(暫停|停止|禁止)預約|不得預約至")),
    ("duplicate", _rx(r"已有.{0,6}(報名|預約)(紀錄|記錄|資料)|已(報名|預約)過|已報名此")),
    ("full", _rx(r"額滿|名額已滿|已無名額|名額不足|無剩餘名額")),
    ("busy", _rx(r"系統忙碌|系統發生錯誤|系統錯誤|請稍後再試|連線逾時|系統維護|維護中|暫停服務|"
                 r"Service Unavailable|Internal Server Error|Bad Gateway|Gateway Time-?out")),
]
# 注意事項裡也常出現的字眼：只有同時寫了「報名失敗」才拿來判斷原因
WEAK_REASON_RX = [
    ("hazard", _rx(r"危險感知")),
    ("banned", _rx(r"停權|暫停預約|禁止預約|停止預約")),
    ("duplicate", _rx(r"重複(報名|預約)")),
]
INPUT_ERROR_RX = _rx(r"請輸入|必須輸入|不可空白|格式(錯誤|不正確|有誤)|資格不符|不得報名|無法(報名|預約)"
                     r"|請(選擇|點選|勾選)|請先(點選|勾選|選擇|同意)")
# 查詢條件被網站擋下來（通常是設定或日期格式的問題，一直重試也沒用）
QUERY_REJECT_RX = _rx(r"請(輸入|選擇|填寫).{0,12}(日期|地點|監理|照類|考試|場次)|日期.{0,8}(格式|錯誤|不正確|有誤|超過|不得|須|需)"
                      r"|格式(錯誤|不正確|有誤)|不可空白|必須(輸入|選擇)")
# 監理服務網：「該條件查詢不到符合的考試場次，敬請重新選擇」
QUERY_EMPTY_RX = _rx(r"查無.{0,8}(場次|資料)|無可(報名|預約).{0,4}場次|目前無.{0,6}場次|沒有.{0,4}場次|查詢不到.{0,8}場次")


def classify_page_change(text: str) -> str | None:
    """網頁上新出現的文字（不是對話框）。注意事項常常寫著「未完成危險感知者不得報名」之類的話，
    所以只有明確寫了「報名失敗」才採信危險感知／停權／重複報名這類嚴重判斷。"""
    k = classify_result(text)
    if k in ("hazard", "banned", "duplicate", "failed") and not FAILED_RX.search(text or ""):
        return None
    return k


def classify_result(text: str) -> str | None:
    """'success'／'captcha_wrong'／'hazard'／'banned'／'duplicate'／'full'／'busy'／'failed'／None。"""
    t = text or ""
    if CAPTCHA_WRONG_RX.search(t):
        return "captcha_wrong"
    if FAILED_RX.search(t):
        if SUCCESS_RX.search(t):
            return None     # 成功、失敗字樣都有：看不出來，交給人判斷
        for kind, rx in REASON_RX + WEAK_REASON_RX:
            if rx.search(t):
                return kind
        return "failed"
    if SUCCESS_RX.search(t):
        return "success"
    for kind, rx in REASON_RX:
        if rx.search(t):
            return kind
    if INPUT_ERROR_RX.search(t):
        return "failed"
    return None


def new_lines(before: str, after: str) -> str:
    """after 裡面 before 沒有的那些行（避免把網頁上本來就有的注意事項當成結果）。"""
    old = {ln.strip() for ln in (before or "").splitlines() if ln.strip()}
    return "\n".join(ln for ln in (after or "").splitlines() if ln.strip() and ln.strip() not in old)


def has_captcha_image(images: list[dict]) -> bool:
    rx = _rx(r"captcha|valid|verif|checkcode|imgcode|kaptcha|randcode|vcode|code\.(jpg|png|gif)|驗證|^data:image|imgservlet")
    return any(rx.search(i.get("src") or "") or rx.search(i.get("alt") or "") or i.get("near_refresh") for i in images)
