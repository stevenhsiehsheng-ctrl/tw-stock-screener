"""用 Playwright 操作監理服務網：查場次、點報名、填資料、送出。"""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PwError

from . import matching as M
from . import pagejs as J
from .config import PKG_DIR
from .rocdate import format_date, guess_input_format

LOCATIONS_PATH = "/m3-emv-trn/exm/locations"
CANCEL_PATH = "/m3-emv-trn/exm/query"
LOGIN_MARK = "/m3-emv-cas/login"
PROFILE_DIR = PKG_DIR / ".browser-profile"


class Fatal(Exception):
    """需要停下來請你處理的狀況。"""


class Busy(Exception):
    """網站維護中／忙碌（例如回了錯誤頁）：放慢速度再試，不是程式看不懂網頁。"""


class Rejected(Exception):
    """網站不接受查詢條件（例如日期格式不對、沒選到考試地點）。"""


@dataclass
class QueryResult:
    kind: str                     # ok／empty／busy／timeout／rejected
    sessions: list = field(default_factory=list)
    detail: str = ""


@dataclass
class Outcome:
    kind: str
    detail: str = ""
    session: M.Session | None = None


def _short(e: BaseException) -> str:
    return str(e).strip().splitlines()[0][:200] if str(e).strip() else repr(e)


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z@.]", "", s or "").upper()


def _real_options(opts: list) -> list:
    """下拉選單裡真正的選項（排除空值和「請選擇」）。"""
    return [(v, t) for v, t in opts if str(v).strip() and not re.search(r"請選擇|請先選|^-+$|^\s*$", str(t))]


class Booker:
    def __init__(self, cfg: dict, notifier, log, run_dir: Path) -> None:
        self.cfg = cfg
        self.n = notifier
        self.log = log
        self.run_dir = run_dir
        self.base = cfg["site"]["base_url"].rstrip("/")
        self.sel = cfg.get("selectors") or {}
        self.dialogs: list[tuple[str, str]] = []     # (type, message)
        self.crashed: set = set()
        self.submitted = False                       # 這次報名是否已經按過送出（之後出錯不能當成「網站忙碌」）
        self.ctx = None
        self.page = None
        self._shot = 0
        self._status = None

    # ================================================================ 瀏覽器
    def start(self, pw) -> None:
        b = self.cfg["browser"]
        profile = Path(b.get("profile_dir") or PROFILE_DIR)
        profile.mkdir(parents=True, exist_ok=True)
        kw = {"headless": bool(b.get("headless")), "locale": "zh-TW", "timezone_id": "Asia/Taipei",
              "viewport": {"width": 1280, "height": 900}}
        if b.get("channel"):
            kw["channel"] = b["channel"]
        self.ctx = pw.chromium.launch_persistent_context(str(profile), **kw)
        self.ctx.set_default_timeout(20000)
        self.ctx.on("page", self._hook)
        for p in self.ctx.pages:
            self._hook(p)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def close(self) -> None:
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:     # 關閉時的錯誤不重要
            pass

    def _hook(self, p) -> None:
        p.on("dialog", self._on_dialog)
        p.on("crash", lambda pg: self.crashed.add(pg))

    def _on_dialog(self, d) -> None:
        msg = (d.message or "").strip()
        self.dialogs.append((d.type, msg))
        self.log.info(f"網頁跳出訊息（{d.type}）：{msg[:200]}")
        try:
            d.accept()
        except Exception:     # 對話框可能已經被關掉
            pass

    def alerts(self, n0: int) -> list[str]:
        """n0 之後網頁跳出的「通知」訊息。confirm()（例如「確定要報名嗎？」）是問句，不拿來判斷結果。"""
        return [m for t, m in self.dialogs[n0:] if t == "alert" and m]

    def ensure_page(self) -> None:
        """分頁被關掉或當掉（Aw, Snap!）就換一個新的。"""
        if self.page is not None and not self.page.is_closed() and self.page not in self.crashed:
            return
        if self.page is not None and not self.page.is_closed():
            try:
                self.page.close()
            except PwError:
                pass
        self.log.info("瀏覽器分頁關掉或當掉了，開一個新的")
        self.page = self.ctx.new_page()

    def close_extra_pages(self) -> None:
        for p in list(self.ctx.pages):
            if p is not self.page and not p.is_closed():
                try:
                    p.close()
                except PwError:
                    pass

    def idle(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            try:
                self.page.wait_for_timeout(min(1000.0, left * 1000))
            except PwError:      # 分頁被關掉：照樣等完，下一輪會換新分頁
                time.sleep(max(0.0, end - time.monotonic()))
                return

    def bring(self, p) -> None:
        try:
            p.bring_to_front()
        except PwError:
            pass

    # ================================================================ 小工具
    def _settle(self, p, ms: int = 5000) -> None:
        for state in ("domcontentloaded", "networkidle"):
            try:
                p.wait_for_load_state(state, timeout=ms)
            except PwError:
                pass

    def _eval(self, p, js: str, arg=None, default=None):
        try:
            return p.evaluate(js) if arg is None else p.evaluate(js, arg)
        except PwError:
            return default

    def fields(self, p) -> list:
        return self._eval(p, J.SCAN_FIELDS, default=[]) or []

    def clicks(self, p) -> list:
        return self._eval(p, J.SCAN_CLICKABLES, default=[]) or []

    def tables(self, p) -> list:
        return self._eval(p, J.SCAN_TABLES, default=[]) or []

    def text(self, p) -> str:
        try:
            return p.inner_text("body", timeout=5000)
        except PwError:
            return ""

    def _target(self, p, f):
        if isinstance(f, str):
            return p.locator(f).first
        return p.locator(f'[data-mvdis-id="{f["mid"]}"]').first

    def click(self, p, c: dict) -> None:
        p.locator(f'[data-mvdis-click="{c["cid"]}"]').first.click()

    def press_submit(self, loc) -> None:
        """按下送出／確認送出。先記下「已送出」，之後就算逾時也不會當成網站忙碌而改報別的場次。"""
        self.submitted = True
        try:
            loc.click(no_wait_after=True)
        except PwError as e:
            self.log.info(f"按送出時出錯（可能已經送出）：{_short(e)}")

    def snapshot(self, p, name: str) -> None:
        self._shot += 1
        stem = self.run_dir / f"{self._shot:02d}-{name}"
        try:
            p.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
            stem.with_suffix(".html").write_text(p.content(), encoding="utf-8")
        except Exception as e:     # 截圖失敗不影響主流程
            self.log.debug(f"存截圖失敗：{e!r}")

    def _is_form(self, fs: list) -> bool:
        return any(M.classify_field(f) in ("id_no", "birth") for f in fs)

    # ================================================================ 查詢場次
    def goto_locations(self) -> None:
        p = self.page
        resp = p.goto(self.base + LOCATIONS_PATH, wait_until="domcontentloaded", timeout=30000)
        self._status = resp.status if resp else None
        self._settle(p)
        self._ensure_not_login(p)

    def _ensure_not_login(self, p) -> None:
        if LOGIN_MARK not in p.url:
            return
        self.bring(p)
        self.n.alert("需要登入監理服務網", "請在程式打開的瀏覽器裡登入會員，登入後程式會自動繼續。",
                     email=True, key="login")
        deadline = time.monotonic() + float(self.cfg["human_timeout_s"])
        while LOGIN_MARK in p.url:
            if time.monotonic() > deadline:
                raise Fatal("等太久沒有登入")
            p.wait_for_timeout(1000)
        resp = p.goto(self.base + LOCATIONS_PATH, wait_until="domcontentloaded", timeout=30000)
        self._status = resp.status if resp else None
        self._settle(p)

    def query_targets(self, p) -> dict:
        err = None
        try:
            q = M.locate_query_fields(self.fields(p))
        except M.LocateError as e:
            err, q = e, {}
        s = self.sel
        if s.get("license"):
            q["license"] = s["license"]
        if s.get("office"):
            q["places"] = [s["office"]] + ([s["station"]] if s.get("station") else [])
        if s.get("date"):
            q["date"] = s["date"]
        if s.get("weekend"):
            q["weekend"] = s["weekend"]
        missing = [k for k in ("license", "places", "date") if k not in q]
        if missing:
            raise err or M.LocateError(f"找不到查詢欄位：{missing}")
        q.setdefault("weekend", None)
        return q

    def select(self, p, target, wanted: str) -> str:
        loc = self._target(p, target)
        value = M.choose_option(loc.evaluate(J.OPTIONS), wanted)
        loc.select_option(value=value)
        return value

    def fill_query(self, qdate: dt.date) -> dict:
        """填好查詢條件，回傳實際填了什麼（偵察模式用）。"""
        p = self.page
        t = self.cfg["target"]
        info: dict = {}
        try:
            q = self.query_targets(p)
        except M.LocateError:
            # 找不到查詢表單：先看是不是網站維護／錯誤頁，是的話只是放慢速度再試
            txt = self.text(p)
            if (self._status or 0) >= 500 or M.classify_result(txt) == "busy" or len(txt.strip()) < 30:
                raise Busy(f"網站回應異常（HTTP {self._status}）：{txt.strip()[:80]}") from None
            raise
        info["license"] = self.select(p, q["license"], t["license_label"])
        self._settle(p, 3000)
        q = self.query_targets(p)
        info["place"] = self._select_place(p, q)
        q = self.query_targets(p)
        info["date_sent"], info["date_shown"] = self._fill_date(p, q["date"], qdate)
        if q.get("weekend") is not None:
            loc = self._target(p, q["weekend"])
            want = bool(t.get("weekend_only"))
            if loc.is_checked() != want:
                loc.set_checked(want, force=True)
        return info

    def _select_place(self, p, q: dict) -> list:
        t = self.cfg["target"]
        office, station = t["office_label"], (t.get("station_label") or "").strip()
        places = q["places"]
        if len(places) == 1:
            opts = self._target(p, places[0]).evaluate(J.OPTIONS)
            if not station or M.has_option(opts, station):
                return [self.select(p, places[0], station or office)]
        chosen = [self.select(p, places[0], office)]
        if not station:
            return chosen
        # 監理站清單通常是選了監理所之後才載入（也可能原本藏起來）：等它出現
        deadline = time.monotonic() + 15
        last: Exception | None = None
        loaded = False
        while True:
            q2 = self.query_targets(p)
            st = q2["places"][1] if len(q2["places"]) > 1 else None
            if st is not None:
                opts = self._target(p, st).evaluate(J.OPTIONS)
                loaded = bool(_real_options(opts))
                try:
                    chosen.append(self.select(p, st, station))
                    return chosen
                except M.OptionError as e:
                    last = e
            if time.monotonic() > deadline:
                if st is None:
                    raise M.OptionError(f"網頁上找不到監理站「{station}」可以選（只有一個考試地點選單）")
                if not loaded:
                    raise M.LocateError("監理站選單一直沒有載入")     # 暫時的，下一輪再試
                raise last or M.OptionError(f"找不到監理站「{station}」")
            p.wait_for_timeout(300)

    def _fill_date(self, p, target, d: dt.date) -> tuple[str, str]:
        loc = self._target(p, target)
        info = loc.evaluate("el => ({type: el.type || '', ro: !!el.readOnly, "
                            "ph: el.getAttribute('placeholder') || '', ml: el.maxLength > 0 ? el.maxLength : null})")
        fmt = self.cfg["target"].get("date_format") or "auto"
        if fmt == "auto":
            fmt = guess_input_format(info["type"], info["ph"], info["ml"])
        val = format_date(d, fmt)
        if info["ro"]:
            loc.evaluate(J.SET_VALUE, val)
        else:
            loc.fill(val)
            try:
                loc.press("Escape", timeout=2000)
            except PwError:
                pass
            loc.evaluate(J.FIRE_CHANGE)
        got = loc.input_value()
        if re.sub(r"\D", "", got) != re.sub(r"\D", "", val):
            if not re.search(r"\d", got):
                raise Rejected(f"日期欄位填了 {val} 之後被網站清空，可能格式不對（可在 config.yaml 設定 target.date_format）")
            self.log.info(f"注意：日期欄位填了 {val}，網頁上顯示 {got}")
        return val, got

    def search(self) -> QueryResult:
        p = self.page
        before = self.text(p)
        n0 = len(self.dialogs)
        if self.sel.get("search_button"):
            p.locator(self.sel["search_button"]).first.click()
        else:
            c = M.pick_clickable(self.clicks(p), M.SEARCH_BUTTONS, M.SEARCH_EXCLUDE)
            if not c:
                raise M.LocateError("找不到「查詢場次」按鈕")
            self.click(p, c)
        return self.wait_results(before, n0)

    def wait_results(self, before: str, n0: int, timeout: float = 30, stale: list | None = None) -> QueryResult:
        """等場次表出現。stale：翻頁前的表格內容，表格沒變就繼續等（AJAX 翻頁）。"""
        deadline = time.monotonic() + timeout
        popup = 0
        while time.monotonic() < deadline:
            p = self._newest_page()
            msgs = "\n".join(self.alerts(n0))
            if M.classify_result(msgs) == "busy":
                return QueryResult("busy", detail=msgs[:200])
            if M.QUERY_REJECT_RX.search(msgs):
                return QueryResult("rejected", detail=msgs[:200])
            c = M.pick_clickable(self.clicks(p), M.CONTINUE_BUTTONS)
            if c and popup < 3:
                popup += 1
                self.log.debug(f"點「{c['text']}」")
                try:
                    self.click(p, c)
                except PwError as e:
                    self.log.debug(f"點不到：{_short(e)}")
                self._settle(p, 5000)
                continue
            found = M.find_session_table(self.tables(p))
            if found:
                sessions = M.parse_sessions(found)
                if stale is None or [s.raw for s in sessions] != stale:
                    self.page = p
                    return QueryResult("ok", sessions)
            new = M.new_lines(before, self.text(p)) + "\n" + msgs
            if M.QUERY_EMPTY_RX.search(new):
                return QueryResult("empty", detail=new.strip()[:200])
            if M.classify_result(new) == "busy":
                return QueryResult("busy", detail=new.strip()[:200])
            p.wait_for_timeout(500)
        return QueryResult("timeout")

    def _newest_page(self):
        pages = [p for p in self.ctx.pages if not p.is_closed() and p not in self.crashed]
        return pages[-1] if pages else self.page

    def next_page(self) -> QueryResult | None:
        """場次表有分頁時翻到下一頁；沒有下一頁（或翻不過去）回傳 None。"""
        p = self.page
        c = M.pick_clickable(self.clicks(p), M.NEXT_PAGE)
        if not c:
            return None
        found = M.find_session_table(self.tables(p))
        stale = [s.raw for s in M.parse_sessions(found)] if found else None
        before = self.text(p)
        n0 = len(self.dialogs)
        try:
            self.click(p, c)
        except PwError:
            return None
        self._settle(p, 5000)
        res = self.wait_results(before, n0, timeout=20, stale=stale)
        return None if res.kind == "timeout" else res

    # ================================================================ 報名
    def try_book(self, s: M.Session) -> Outcome:
        self.submitted = False
        p = self.page
        found = M.find_session_table(self.tables(p))
        fresh = [x for x in M.parse_sessions(found) if x.key == s.key and x.action_id] if found else []
        if not fresh:
            return Outcome("gone", "那個場次已經不在表上", s)
        s = fresh[0]
        before = self.text(p)
        n0 = len(self.dialogs)
        self.log.info(f"點「{s.action_text or '報名'}」：{s.describe()}")
        p.locator(f'[data-mvdis-act="{s.action_id}"]').first.click()
        pg, early = self.wait_signup_form(p, before, n0)
        if early:
            early.session = s
            return early
        return self._fill_and_submit(pg, s)

    def wait_signup_form(self, p0, before: str, n0: int, timeout: float = 30):
        """點了報名之後，等填資料的頁面出現（中間如果有注意事項／同意頁就幫你按）。"""
        deadline = time.monotonic() + timeout
        steps = 0
        while time.monotonic() < deadline:
            for m in reversed(self.alerts(n0)):
                k = M.classify_result(m)
                if k:
                    return None, Outcome(k, m[:300])
            for pg in reversed([x for x in self.ctx.pages if not x.is_closed()]):
                fs = self.fields(pg)
                if self._is_form(fs):
                    self._settle(pg, 3000)
                    return pg, None
                new = M.new_lines(before, self.text(pg))
                k = M.classify_page_change(new)
                if k == "success":
                    # 不該在這一步就成功；照實回報並停下來，避免再去報別的場次
                    return None, Outcome("success", new.strip()[:300])
                if steps < 4 and new.strip() and not M.find_session_table(self.tables(pg)):
                    acted = False
                    c = None
                    if self.cfg.get("auto_agree"):
                        for f in fs:
                            if M.classify_field(f) == "agree" and not f.get("checked"):
                                self.log.info(f"勾選「{M.describe_field(f)}」")
                                self._target(pg, f).set_checked(True, force=True)
                                acted = True
                        c = M.pick_clickable(self.clicks(pg), M.AGREE_BUTTONS, M.AGREE_EXCLUDE)
                    c = c or M.pick_clickable(self.clicks(pg), M.NEXT_BUTTONS, M.AGREE_EXCLUDE)
                    if c:
                        self.log.info(f"點「{c['text']}」")
                        self.click(pg, c)
                        acted = True
                    if acted:
                        steps += 1
                        self._settle(pg, 5000)
                        break
                if k:
                    return None, Outcome(k, new.strip()[:300])
            p0.wait_for_timeout(500)
        return None, Outcome("signup_timeout", "點了報名之後，等不到填資料的頁面")

    def plan(self, pg) -> M.FillPlan:
        imgs = self._eval(pg, J.SCAN_IMAGES, default=[]) or []
        return M.plan_fill(self.fields(pg), self.cfg, M.has_captcha_image(imgs))

    def apply(self, pg, plan: M.FillPlan) -> list[str]:
        problems = []
        for f, kind, val in plan.actions:
            loc = self._target(pg, f)
            try:
                if kind == "fill":
                    loc.fill(str(val))
                    loc.evaluate(J.FIRE_CHANGE)
                elif kind == "js_set":
                    loc.evaluate(J.SET_VALUE, str(val))
                elif kind == "select":
                    loc.select_option(value=M.choose_option(loc.evaluate(J.OPTIONS), str(val)))
                elif kind == "select_value":
                    loc.select_option(value=str(val))
                elif kind == "check":
                    loc.set_checked(True, force=True)
            except (PwError, M.OptionError) as e:
                problems.append(f"「{M.describe_field(f)}」填不進去：{_short(e)}")
        for f, kind, val in plan.actions:
            if kind not in ("fill", "js_set"):
                continue
            try:
                got = self._target(pg, f).input_value()
            except PwError:
                continue
            if _alnum(got) != _alnum(str(val)) or (not _alnum(val) and M.norm(got) != M.norm(str(val))):
                problems.append(f"「{M.describe_field(f)}」填完後顯示的內容不一樣（{got}）")
        return problems

    def _submit_button(self, pg):
        if self.sel.get("submit_button"):
            loc = pg.locator(self.sel["submit_button"]).first
            return loc if loc.count() else None
        c = M.pick_clickable(self.clicks(pg), M.SUBMIT_BUTTONS, M.SUBMIT_EXCLUDE)
        return pg.locator(f'[data-mvdis-click="{c["cid"]}"]').first if c else None

    def _baseline(self, pg) -> str:
        """送出前的頁面文字（不含舊的「驗證碼錯誤」訊息，這樣同一句話再出現時才看得到）。"""
        return "\n".join(ln for ln in self.text(pg).splitlines() if not M.CAPTCHA_WRONG_RX.search(ln))

    def _refill(self, pg):
        """表單重新載入後（驗證碼錯誤、換一張）重新填一次。回傳 (plan, problems)。"""
        self._settle(pg, 5000)
        plan = self.plan(pg)
        problems = list(plan.problems) + self.apply(pg, plan)
        if plan.captcha is not None:
            try:
                self._target(pg, plan.captcha).fill("")
            except PwError:
                pass
        # 把舊的「驗證碼錯誤」字樣藏起來，避免送出後還沒換頁就被當成新的錯誤
        self._eval(pg, J.HIDE_TEXT, M.CAPTCHA_WRONG_RX.pattern)
        return plan, problems

    def _fill_and_submit(self, pg, s: M.Session) -> Outcome:
        form_text = self.text(pg)
        plan = self.plan(pg)
        for desc, key in plan.mapping:
            self.log.debug(f"欄位「{desc}」→ {key}")
        for note in plan.notes:
            self.log.info(note)
        problems = list(plan.problems) + self.apply(pg, plan)
        if M.session_date_check(form_text, s.date) == "mismatch":
            problems.append(f"報名頁上的日期跟點的場次（{s.date}）對不起來")
        if self._submit_button(pg) is None:
            problems.append("找不到送出按鈕")
        self.snapshot(pg, "signup")
        if self.cfg["mode"] != "book":
            return Outcome("dry_run_ready", "；".join(problems), s)
        if problems:
            return self.human_finish(pg, s, "；".join(problems), form_text)
        retries = int(self.cfg["captcha"].get("max_retries") or 0)
        attempt = reloads = 0
        while True:
            before = self._baseline(pg)
            n0 = len(self.dialogs)
            pages0 = set(self.ctx.pages)
            if plan.captcha is not None:
                r = self.captcha_assist(pg, plan.captcha, s)
                if r == "timeout":
                    return Outcome("captcha_timeout", "等驗證碼等太久", s)
                if r == "reloaded":
                    reloads += 1
                    plan, problems = self._refill(pg)
                    if problems or plan.captcha is None or reloads > 5:
                        return self.human_finish(pg, s, "；".join(problems) or "表單重新載入了", self.text(pg))
                    continue
                if r == "user_submitted":
                    self.submitted = True
                    return self._finish(self.wait_result(pg, before, n0, pages0), s)
            btn = self._submit_button(pg)
            if btn is None:
                return self.human_finish(pg, s, "找不到送出按鈕", form_text)
            self.log.info("送出報名")
            self.press_submit(btn)
            out = self.wait_result(pg, before, n0, pages0)
            if out.kind == "captcha_wrong" and plan.captcha is None:
                self._settle(pg, 5000)
                return self.human_finish(pg, s, "網站說驗證碼錯誤，但程式沒認出驗證碼欄位：請輸入驗證碼後自己按送出",
                                         self._baseline(pg))
            if out.kind == "captcha_wrong" and attempt < retries:
                attempt += 1
                self.log.info("驗證碼錯誤，請再輸入一次")
                plan, problems = self._refill(pg)
                if problems or plan.captcha is None:
                    return self.human_finish(pg, s, "；".join(problems) or "驗證碼錯誤", self.text(pg))
                continue
            return self._finish(out, s)

    def _finish(self, out: Outcome, s: M.Session) -> Outcome:
        out.session = s
        self.snapshot(self._newest_page(), f"result-{out.kind}")
        return out

    def captcha_assist(self, pg, cap: dict, s: M.Session | None) -> str:
        """請你輸入驗證碼：'ready'（可以送出）／'user_submitted'（你自己按了送出）／'reloaded'（表單重新載入）／'timeout'。"""
        sel = f'[data-mvdis-id="{cap["mid"]}"]'
        loc = pg.locator(sel).first
        n0 = len(self.dialogs)
        self.bring(pg)
        baseline = ""
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            baseline = loc.input_value()
            loc.click(timeout=3000)
            loc.evaluate(J.WATCH_ENTER)
        except PwError:
            pass
        c = self.cfg["captcha"]
        need = cap.get("maxlength") or c.get("length")
        need = int(need) if need and int(need) <= 8 and c.get("submit_when_full") else None
        how = f"打完 {need} 個字會自動送出（也可以按 Enter）" if need else "打完按 Enter 就會送出"
        what = s.describe() if s else "確認頁"
        self.n.alert("有空位！請輸入驗證碼", f"{what}\n資料都填好了，請到瀏覽器輸入驗證碼，{how}。",
                     email=True, key="captcha")
        deadline = time.monotonic() + float(c.get("timeout_s") or 180)
        beep = time.monotonic() + 30
        while time.monotonic() < deadline:
            st = self._eval(pg, J.CAPTCHA_STATE, sel, default="gone")
            if st in ("gone", None):
                return self._after_captcha_gone(pg, n0)
            if st["enter"] and st["len"] > 0:
                return "ready"
            if need and st["len"] >= need and st["val"] != baseline:
                return "ready"
            if time.monotonic() > beep:
                self.n.sound(2)
                beep = time.monotonic() + 30
            pg.wait_for_timeout(250)
        return "timeout"

    def _after_captcha_gone(self, pg, n0: int) -> str:
        """驗證碼欄位不見了：是你按了送出，還是只是重新整理／換一張？"""
        self._settle(pg, 5000)
        if pg.is_closed():
            return "user_submitted"
        fs = self.fields(pg)
        has_cap = any(M.classify_field(f) == "captcha" for f in fs)
        if self._is_form(fs) and has_cap and not self.alerts(n0):
            return "reloaded"
        return "user_submitted"

    def _is_confirm_page(self, pg, before: str) -> dict | None:
        """送出後的「請確認報名資料」頁：沒有可以改的身分證／生日欄位，只有確認送出的按鈕。"""
        if self._eval(pg, "() => document.readyState") != "complete":
            return None          # 還在換頁：先不要判斷
        fs = self._eval(pg, J.SCAN_FIELDS, default=None)
        if fs is None:
            return None
        editable = [f for f in fs if not f.get("readonly") and not f.get("disabled")]
        if any(M.classify_field(f) in ("id_no", "birth") for f in editable):
            return None
        new = M.new_lines(before, self.text(pg))
        if not new.strip():
            return None
        clicks = self.clicks(pg)
        c = M.pick_clickable(clicks, M.STRONG_CONFIRM_BUTTONS, M.SUBMIT_EXCLUDE)
        if c is None and M.CONFIRM_PAGE_RX.search(new):
            c = M.pick_clickable(clicks, M.CONFIRM_BUTTONS, M.SUBMIT_EXCLUDE)
        if c is None:
            return None
        cap = next((f for f in editable if M.classify_field(f) == "captcha"), None)
        return {"button": c, "captcha": cap}

    def wait_result(self, pg, before: str, n0: int, pages0: set | None = None,
                    timeout: float = 45, human: bool = False) -> Outcome:
        """送出後判斷結果。中間如果有「確認送出」頁就再按一次確認（human=True 時讓你自己按）。"""
        deadline = time.monotonic() + timeout
        pages0 = pages0 or set()
        confirms = 0
        while time.monotonic() < deadline:
            for m in reversed(self.alerts(n0)):      # 一則一則看，新的優先
                k = M.classify_result(m)
                if k and not (human and k in ("failed", "captcha_wrong")):
                    return Outcome(k, m[:300])
            watch = [pg] + [x for x in self.ctx.pages if x not in pages0 and x is not pg]
            found = []
            for q in watch:
                if q.is_closed():
                    continue
                new = M.new_lines(before, self.text(q))
                k = M.classify_page_change(new)
                if k and not (human and k in ("failed", "captcha_wrong")):
                    found.append((k, new))
            # 錯誤訊息先處理；「成功」要等確認過不是「請確認資料」頁才算
            bad = [(k, t) for k, t in found if k != "success"]
            if bad:
                return Outcome(bad[0][0], bad[0][1].strip()[:300])
            if not human and confirms < 2 and not pg.is_closed():
                cp = self._is_confirm_page(pg, before)
                if cp is not None:
                    confirms += 1
                    if cp["captcha"] is not None:
                        r = self.captcha_assist(pg, cp["captcha"], None)
                        if r == "timeout":
                            return Outcome("result_unknown", "確認頁的驗證碼沒有輸入")
                        if r != "ready":
                            continue      # 你自己按了確認，或頁面重新載入：繼續看結果
                    self.log.info(f"確認頁：按「{cp['button']['text']}」")
                    before = self.text(pg)
                    self.press_submit(pg.locator(f'[data-mvdis-click="{cp["button"]["cid"]}"]').first)
                    self._settle(pg, 5000)
                    continue
            if found:
                return Outcome("success", found[0][1].strip()[:300])
            pg.wait_for_timeout(500)
        return Outcome("result_unknown", "送出後看不出結果")

    def human_finish(self, pg, s: M.Session, reason: str, before: str) -> Outcome:
        self.bring(pg)
        self.n.alert("需要你接手完成報名",
                     f"{s.describe()}\n原因：{reason}\n能填的都填好了，請到瀏覽器檢查、補齊後自己按送出。",
                     email=True, key="human")
        n0 = len(self.dialogs)
        out = self.wait_result(pg, before, n0, set(self.ctx.pages),
                               timeout=float(self.cfg["human_timeout_s"]), human=True)
        if out.kind == "result_unknown":
            return Outcome("human_timeout", reason, s)
        self.submitted = True
        return self._finish(out, s)
