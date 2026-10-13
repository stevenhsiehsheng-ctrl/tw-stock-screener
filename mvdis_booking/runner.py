"""主流程：一直查場次，有符合的空位就報名。"""
from __future__ import annotations

import random
import re
import time

from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from . import matching as M
from .browser import CANCEL_PATH, Booker, Busy, Fatal, Outcome, Rejected
from .config import date_range, mask_id
from .schedule import MIN_DELAY_S, DateCursor, next_delay
from .util import Log, load_state, new_run_dir, now_tw, save_state
from .notify import Notifier

MAX_LOCATE_FAILS = 3
SESSION_COOLDOWN_S = {"full": 600, "gone": 300, "signup_timeout": 300, "captcha_timeout": 180,
                      "captcha_wrong": 120, "busy": 120}
STOP_KINDS = {"hazard", "banned", "duplicate", "failed", "result_unknown", "human_timeout"}


def secrets_of(cfg: dict) -> list[str]:
    a = cfg["applicant"]
    return [a.get("id_no") or "", a.get("mobile") or "", a.get("phone") or "", a.get("email") or ""]


def wait_exit(log: Log, msg: str = "瀏覽器會保持開啟。處理完之後，回到這個視窗按 Enter 結束程式…") -> None:
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def describe_target(cfg: dict) -> str:
    t, a = cfg["target"], cfg["applicant"]
    start, end = date_range(cfg, now_tw().date())
    wd = "" if len(t["weekdays"]) == 7 else "，只要星期" + "、".join("一二三四五六日"[d - 1] for d in t["weekdays"])
    lines = [
        f"模式：{'自動送出報名（book）' if cfg['mode'] == 'book' else '測試模式，只填不送出（dry_run）'}",
        f"照類：{t['license_label']}｜地點：{t['office_label']} {t.get('station_label') or ''}",
        f"日期：{start} ～ {end}{wd}{'，只查週末假日' if t.get('weekend_only') else ''}",
        f"組別：包含「{t.get('group_include') or '不限'}」，排除「{t.get('group_exclude') or '無'}」",
        f"報名人：{mask_id(a['id_no'])}，生日 {a['birth']}",
    ]
    return "\n".join(lines)


def run(cfg: dict, verbose: bool = False) -> int:
    state = load_state()
    if cfg["mode"] == "book" and state.get("booked"):
        print(f"state.json 記錄你已經在 {state.get('at')} 預約成功（{state.get('session')}），為了避免重複報名，程式不會再送出。")
        print("如果確定要再預約一次，請先刪除 mvdis_booking/state.json。")
        return 1
    run_dir = new_run_dir(cfg["mode"])
    log = Log(run_dir / "log.txt", secrets_of(cfg), verbose)
    n = Notifier(cfg, log)
    log.info("監理服務網考照預約小幫手啟動\n" + describe_target(cfg))
    log.info(f"紀錄與截圖存在 {run_dir}")
    with sync_playwright() as pw:
        b = Booker(cfg, n, log, run_dir)
        if not start_browser(b, pw):
            return 2
        try:
            return _loop(cfg, b, n, log)
        except KeyboardInterrupt:
            log.info("已停止（Ctrl+C）")
            return 0
        finally:
            b.close()


def start_browser(b: Booker, pw) -> bool:
    """開瀏覽器；失敗時用中文說明原因（最常見：另一個小幫手視窗還開著）。"""
    try:
        b.start(pw)
        return True
    except PwError as e:
        msg = str(e)
        if re.search(r"ProcessSingleton|existing browser session|user data directory is already in use|SingletonLock", msg, re.I):
            print("另一個小幫手（run 或 recon）的視窗還開著。請先回到那個黑色視窗按 Enter 或 Ctrl+C 關掉，再重新執行。")
        elif "Executable doesn't exist" in msg or "playwright install" in msg:
            print("找不到瀏覽器，請再執行一次 install_windows.bat（Mac：python3 -m playwright install chromium）。")
        else:
            print(f"瀏覽器開不起來：{msg.strip().splitlines()[0][:200] if msg.strip() else repr(e)}")
        return False


def _loop(cfg: dict, b: Booker, n: Notifier, log: Log) -> int:
    cursor = DateCursor()
    rng = random.Random()
    backoff = 0
    locate_fails = rejects = timeouts = pw_errors = 0
    warned: set = set()
    cycle = 0
    seen_groups: set = set()
    cooldown: dict = {}

    def warn_once(key: str, title: str, body: str) -> None:
        if key not in warned:
            warned.add(key)
            n.alert(title, body, email=True, key=key)

    while True:
        cycle += 1
        today = now_tw().date()
        start, end = date_range(cfg, today)
        qdate = cursor.next_query_date(cfg, today)
        if qdate is None:
            n.alert("日期範圍已經過了", "config.yaml 的 date_to 已經過了（或範圍內沒有可預約的日子），請修改後重新執行。", email=True)
            return 1
        sessions: list = []
        matched: list = []
        status, detail = None, ""
        delay_override = None
        try:
            if pw_errors and pw_errors % 3 == 0 and b.page is not None:
                b.crashed.add(b.page)      # 連續出錯好幾次：換一個新分頁試試
            b.ensure_page()
            b.close_extra_pages()
            b.goto_locations()
            b.fill_query(qdate)
            res = b.search()
            status, detail = res.kind, res.detail
            pages = 1
            while res is not None and res.kind == "ok":
                sessions += res.sessions
                now = time.monotonic()
                matched = [s for s in M.filter_sessions(res.sessions, cfg, start, end)
                           if cooldown.get(s.key, 0) < now]
                if matched or pages >= int(cfg["poll"].get("max_pages") or 1):
                    break
                res = b.next_page()
                pages += 1
            locate_fails = pw_errors = 0
            if status == "rejected":
                raise Rejected(detail)
            rejects = 0
        except M.OptionError as e:
            b.snapshot(b.page, "option-not-found")
            n.alert("設定的名稱在網頁上找不到，程式停止",
                    f"{e}\n請照上面列出的名稱修改 config.yaml 後重新執行。", email=True, key="option")
            wait_exit(log)
            return 2
        except Rejected as e:
            rejects += 1
            status = None
            b.snapshot(b.page, "rejected")
            if rejects >= 2:
                n.alert("網站不接受查詢條件，程式停止",
                        f"網頁訊息：{e}\n請檢查 config.yaml 的監理站、日期設定（或 target.date_format），"
                        "或執行偵察模式（recon）把結果給 Claude 看。", email=True, key="rejected")
                wait_exit(log)
                return 2
            log.info(f"網站不接受查詢條件（{e}），再試一次")
            backoff = min(backoff + 1, 3)
        except Busy as e:
            log.info(f"網站維護中或忙碌，放慢速度：{e}")
            backoff = min(backoff + 1, 3)
        except M.LocateError as e:
            locate_fails += 1
            b.snapshot(b.page, "not-matched")
            if locate_fails >= MAX_LOCATE_FAILS:
                warn_once("locate", "網頁內容跟預期不同",
                          f"{e}\n程式會繼續慢慢重試。如果一直這樣，請先在這個黑色視窗按 Ctrl+C 關掉程式，"
                          "再執行偵察模式（recon）並把結果貼給 Claude。")
            log.info(f"網頁內容跟預期不同（{e}），稍後重試")
            backoff = 3 if locate_fails >= MAX_LOCATE_FAILS else min(backoff + 1, 3)
        except Fatal as e:
            n.alert("程式停止", str(e), email=True)
            wait_exit(log)
            return 2
        except PwError as e:
            if b.ctx is None or not _browser_alive(b):
                log.info("瀏覽器被關掉了，程式結束。")
                return 1
            pw_errors += 1
            log.info(f"連線或網頁錯誤：{str(e).strip().splitlines()[0][:150] if str(e).strip() else repr(e)}")
            b.snapshot(b.page, "error")
            backoff = min(backoff + 1, 3)
            if pw_errors >= 10:
                warn_once("pwerror", "連線一直失敗",
                          "已經連續 10 次連不上監理服務網或網頁出錯，程式會繼續慢慢重試。請確認電腦網路正常。")

        if status is not None:
            if status in ("busy", "timeout"):
                backoff = min(backoff + 1, 3)
                log.info("網站忙碌或沒有回應，放慢速度" + (f"：{detail}" if detail else ""))
            else:
                backoff = 0
            timeouts = timeouts + 1 if status == "timeout" else 0
            if timeouts >= 5:
                warn_once("timeout", "一直讀不到場次表",
                          "已經連續 5 次查詢後讀不到場次表，程式會繼續重試。如果一直這樣，請執行偵察模式（recon）並把結果貼給 Claude。")
            cursor.observe(qdate, [s.date for s in sessions if s.date])
            for s in sessions:
                if s.group and s.group not in seen_groups:
                    seen_groups.add(s.group)
                    log.info(f"看到場次組別：{s.group}")
            if cycle == 1 and sessions:
                log.info("目前場次：\n" + "\n".join("  " + s.describe() for s in sessions[:30]))
            when = f"{qdate:%m/%d}{'起' if cursor.onward else ''}"
            log.info(f"第 {cycle} 次查詢（{when}）：{len(sessions)} 個場次，有空位又符合條件的 {len(matched)} 個")

        if matched:
            s = matched[0]
            log.info(f"找到空位：{s.describe()}")
            n.sound(2)
            try:
                out = b.try_book(s)
            except PwError as e:
                log.info(f"報名過程出錯：{e}")
                # 已經按過送出就不能當成「網站忙碌」去報別的場次：停下來請你確認
                out = Outcome("result_unknown" if b.submitted else "busy", str(e)[:200], s)
            except Exception as e:
                log.info(f"報名過程發生未預期的錯誤：{e!r}")
                n.alert("程式出錯，停在目前畫面", f"{s.describe()}\n錯誤：{e!r}\n請看瀏覽器畫面，必要時自己完成報名。",
                        email=True, key="crash")
                wait_exit(log)
                return 3
            code = _handle(cfg, b, n, log, out, s)
            if code is not None:
                return code
            cooldown[s.key] = time.monotonic() + SESSION_COOLDOWN_S.get(out.kind, 120)
            if out.kind in ("busy", "signup_timeout"):
                backoff = min(backoff + 1, 3)
            elif out.kind in ("full", "gone"):
                delay_override = MIN_DELAY_S      # 被搶走了：馬上看看還有沒有別的場次

        delay = delay_override if delay_override is not None else next_delay(now_tw(), cfg["poll"], backoff, rng)
        log.debug(f"{delay:.0f} 秒後再查")
        b.idle(delay)


def _browser_alive(b: Booker) -> bool:
    try:
        return bool(b.ctx.pages) or b.ctx.new_page() is not None
    except PwError:
        return False


def _handle(cfg: dict, b: Booker, n: Notifier, log: Log, out: Outcome, s: M.Session) -> int | None:
    """處理報名結果。回傳 None 代表繼續盯，數字代表結束程式。"""
    s = out.session or s
    where = f"{cfg['target']['office_label']} {cfg['target'].get('station_label') or ''}".strip()
    if out.kind == "success":
        save_state({"booked": True, "at": now_tw().isoformat(timespec="seconds"),
                    "session": s.describe(), "where": where})
        n.alert("預約成功！",
                f"{s.describe()}\n地點：{where}\n\n請記得：\n"
                f"・要取消請在考照當天 07:00 前到 {b.base}{CANCEL_PATH} 線上取消\n"
                "・沒取消又沒到考，會被停止預約 15～60 天\n"
                "・可以到上面的網址用身分證字號＋生日查詢，確認真的有報名成功",
                email=True, key="success")
        wait_exit(log)
        return 0
    if out.kind == "dry_run_ready":
        extra = f"\n要注意：{out.detail}" if out.detail else ""
        n.alert("測試模式：資料已經幫你填好（沒有送出）",
                f"{s.describe()}\n地點：{where}\n瀏覽器停在填好的報名頁。要報名就自己檢查後按送出；"
                f"想讓程式全自動送出，把 config.yaml 的 mode 改成 book。{extra}",
                email=True, key="dry_run")
        wait_exit(log)
        return 0
    if out.kind in STOP_KINDS:
        titles = {
            "hazard": "報名失敗：查不到危險感知紀錄",
            "banned": "報名失敗：目前被停止預約",
            "duplicate": "報名失敗：你可能已經有預約了",
            "failed": "報名失敗",
            "result_unknown": "送出了，但看不出有沒有成功",
            "human_timeout": "等你接手等太久，程式停止",
        }
        tips = {
            "hazard": "請先到監理服務網的危險感知平台完成測驗並登錄紀錄（身分證字號、生日要跟 config.yaml 一樣）。",
            "duplicate": f"請到 {b.base}{CANCEL_PATH} 查詢目前的預約。",
            "result_unknown": f"請看瀏覽器畫面，並到 {b.base}{CANCEL_PATH} 查詢是否報名成功。",
        }
        n.alert(titles[out.kind], f"{s.describe()}\n網頁訊息：{out.detail or '（無）'}\n{tips.get(out.kind, '請看瀏覽器畫面。')}",
                email=True, key=out.kind)
        wait_exit(log)
        return 3
    msgs = {
        "full": "按下去時已經被搶走了，繼續盯",
        "gone": "場次已經不在表上，繼續盯",
        "captcha_timeout": "等驗證碼等太久，繼續盯（下次有空位會再叫你）",
        "captcha_wrong": "驗證碼一直錯，繼續盯",
        "signup_timeout": "點了報名但等不到填資料頁面，繼續盯",
        "busy": "網站忙碌，繼續盯",
    }
    log.info(f"{msgs.get(out.kind, out.kind)}" + (f"（{out.detail}）" if out.detail else ""))
    return None
