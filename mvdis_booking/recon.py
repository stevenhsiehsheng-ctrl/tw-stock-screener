"""偵察模式：把真實網頁的結構記下來（不會填個人資料、不會送出）。

產生 runs/<時間>-recon/summary.txt，內容不含個人資料，可以整份貼給 Claude 來調整程式。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Error as PwError
from playwright.sync_api import sync_playwright

from . import matching as M
from . import pagejs as J
from .browser import Booker
from .config import date_range
from .notify import Notifier
from .runner import wait_exit
from .util import Log, new_run_dir, now_tw

DUMMY_APPLICANT = {"id_no": "A123456789", "birth": dt.date(2000, 1, 2), "birth_format": "auto",
                   "name": "測試", "mobile": "0912345678", "phone": "0912345678",
                   "email": "test@example.com", "extra": {}}


class Report:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def h(self, title: str) -> None:
        self.parts.append(f"\n==== {title} ====")

    def line(self, s: str) -> None:
        self.parts.append(s)

    def json(self, obj) -> None:
        self.parts.append(json.dumps(obj, ensure_ascii=False, indent=1, default=str))

    def text(self) -> str:
        return "\n".join(self.parts) + "\n"


def _brief_field(f: dict) -> dict:
    out = {k: f.get(k) for k in ("order", "tag", "type", "name", "id", "label", "row_label", "before_text",
                                  "placeholder", "col_header", "required", "readonly", "maxlength")}
    if f.get("options") is not None:
        opts = f["options"]
        out["options"] = opts[:40] + ([["…", f"共 {len(opts)} 個"]] if len(opts) > 40 else [])
    return {k: v for k, v in out.items() if v not in (None, "", False)}


def _request_line(r) -> str | None:
    if r.resource_type not in ("document", "xhr", "fetch"):
        return None
    u = urlsplit(r.url)
    line = f"{r.method} {u.path}" + (f"?{u.query[:200]}" if u.query else "") + f"  [{r.resource_type}]"
    if r.method == "POST":
        try:
            body = r.post_data or ""
        except Exception:
            body = ""
        keys = [k for k, _ in parse_qsl(body, keep_blank_values=True)] if "=" in body else []
        line += f"  POST 欄位：{keys}" if keys else (f"  body={body[:120]!r}" if body else "")
    return line


def recon(cfg: dict) -> int:
    run_dir = new_run_dir("recon")
    log = Log(run_dir / "log.txt", verbose=True)
    n = Notifier({**cfg, "notify": {"sound": False, "desktop": False, "email": {"enabled": False}}}, log)
    rep = Report()
    rep.line(f"偵察時間：{now_tw():%Y-%m-%d %H:%M}（台灣時間）")
    requests: list[str] = []
    log.info(f"偵察模式：結果會存在 {run_dir}")
    with sync_playwright() as pw:
        b = Booker(cfg, n, log, run_dir)
        b.start(pw)
        b.ctx.on("request", lambda r: (lambda s: s and requests.append(s))(_request_line(r)))
        try:
            _recon(cfg, b, rep, log)
        except KeyboardInterrupt:
            rep.line("（使用者中斷）")
        except Exception as e:
            rep.h("偵察中途出錯")
            rep.line(repr(e)[:500])
            try:
                b.snapshot(b.page, "error")
            except Exception:
                pass
        rep.h("網路請求（只記網址與欄位名稱）")
        for r in requests[:120]:
            rep.line(r)
        out = run_dir / "summary.txt"
        out.write_text(rep.text(), encoding="utf-8")
        log.info(f"偵察完成：{out}")
        print("\n請把這個檔案的內容整份貼給 Claude：\n  " + str(out) + "\n（裡面沒有你的個人資料）\n")
        wait_exit(log, "看完瀏覽器畫面後，回到這裡按 Enter 結束…")
        b.close()
    return 0


def _recon(cfg: dict, b: Booker, rep: Report, log: Log) -> None:
    t = cfg["target"]
    p = b.page
    rep.h("1. 查詢頁")
    b.goto_locations()
    rep.line(f"網址：{p.url}")
    rep.line(f"標題：{p.title()}")
    b.snapshot(p, "locations")
    rep.line("表單：")
    rep.json(b._eval(p, J.FORMS, default=[]))
    fields = b.fields(p)
    rep.line("欄位：")
    rep.json([_brief_field(f) for f in fields])
    rep.line("按鈕／連結（前 40 個）：")
    rep.json([c["text"] for c in b.clicks(p)][:40])
    try:
        q = b.query_targets(p)
        rep.line("程式認出來的查詢欄位：")
        rep.json({"報考照類": _brief_field(q["license"]) if isinstance(q["license"], dict) else q["license"],
                  "考試地點": [_brief_field(x) if isinstance(x, dict) else x for x in q["places"]],
                  "預計考試日期": _brief_field(q["date"]) if isinstance(q["date"], dict) else q["date"],
                  "僅查周末": _brief_field(q["weekend"]) if isinstance(q.get("weekend"), dict) else q.get("weekend")})
    except M.LocateError as e:
        rep.line(f"⚠ 認不出查詢欄位：{e}")
        return

    rep.h("2. 各監理所底下的監理站")
    try:
        office = q["places"][0]
        offices = [(v, txt) for v, txt in b._target(p, office).evaluate(J.OPTIONS) if v.strip()]

        def station_options():
            q2 = b.query_targets(p)
            return b._target(p, q2["places"][1]).evaluate(J.OPTIONS) if len(q2["places"]) > 1 else []
        prev = station_options()
        for v, txt in offices[:12]:
            b._target(p, office).select_option(value=v)
            stations = prev
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                stations = station_options()
                if stations != prev and any(sv.strip() for sv, _ in stations):
                    break
                p.wait_for_timeout(300)
            prev = stations
            rep.line(f"{txt}（{v}）：" + "、".join(f"{st}（{sv}）" for sv, st in stations if sv.strip()))
    except (PwError, M.LocateError, KeyError, IndexError) as e:
        rep.line(f"⚠ 讀不到監理站清單：{e!r}"[:300])

    if not str(t.get("office_label") or "").strip():
        rep.line("\n（config.yaml 還沒填監理所，略過實際查詢。）")
        return

    rep.h("3. 實際查詢一次")
    start, end = date_range(cfg, now_tw().date())
    b.goto_locations()
    try:
        info = b.fill_query(start)
        rep.line(f"填入：{info}")
    except (M.LocateError, M.OptionError) as e:
        rep.line(f"⚠ 填查詢條件失敗：{e}")
        b.snapshot(p, "query-fill-failed")
        return
    n0 = len(b.dialogs)
    res = b.search()
    p = b.page
    b.snapshot(p, "results")
    rep.line(f"結果：{res.kind} {res.detail}")
    rep.line(f"對話框：{b.dialogs[n0:]}")
    tables = b.tables(p)
    found = M.find_session_table(tables)
    if found:
        table, hi, cols = found
        rep.line(f"場次表欄位對應：{cols}")
        rows = table["rows"][hi:hi + 16]
        rep.json([{"cells": r["cells"], "actions": [a["text"] for a in r.get("actions", [])]} for r in rows])
    else:
        rep.line("⚠ 找不到場次表。頁面上所有表格的前 3 列：")
        rep.json([[r["cells"] for r in tb["rows"][:3]] for tb in tables[:8]])
    sessions = res.sessions
    rep.line("解讀出來的場次：")
    for s in sessions[:30]:
        rep.line("  " + s.describe() + ("" if s.action_id else "（沒有報名按鈕）"))
    matched = M.filter_sessions(sessions, cfg, start, end)
    rep.line(f"符合 config 條件的：{len(matched)} 個")
    rep.line(f"看到的組別：{sorted({s.group for s in sessions if s.group})}")
    rep.line(f"翻頁連結：{M.pick_clickable(b.clicks(p), M.NEXT_PAGE)}")

    target = (matched or [s for s in sessions if s.action_id] or [None])[0]
    if target is None:
        rep.line("\n（目前沒有可以點「報名」的場次，無法偵察報名頁。換個日期範圍或改天再跑一次偵察。）")
        return

    rep.h("4. 報名頁（只看不填、不送出）")
    before = b.text(p)
    n0 = len(b.dialogs)
    rep.line(f"點：{target.describe()}，按鈕文字「{target.action_text}」")
    p.locator(f'[data-mvdis-act="{target.action_id}"]').first.click()
    pg, early = b.wait_signup_form(p, before, n0)
    rep.line(f"對話框：{b.dialogs[n0:]}")
    if early:
        rep.line(f"⚠ 沒有進到填資料頁：{early.kind} {early.detail}")
        b.snapshot(b._newest_page(), "signup-failed")
        pg = b._newest_page()
    else:
        b.snapshot(pg, "signup")
    rep.line(f"網址：{pg.url}")
    rep.line("表單：")
    rep.json(b._eval(pg, J.FORMS, default=[]))
    fs = b.fields(pg)
    rep.line("欄位與程式的判讀：")
    rep.json([{**_brief_field(f), "判讀": M.classify_field(f)} for f in fs])
    imgs = b._eval(pg, J.SCAN_IMAGES, default=[]) or []
    rep.line(f"圖片：{imgs[:10]}")
    cap_img = M.has_captcha_image(imgs)
    plan = M.plan_fill(fs, {**cfg, "applicant": DUMMY_APPLICANT}, cap_img)
    rep.line(f"有驗證碼：{'是' if plan.captcha is not None or cap_img else '否'}")
    rep.line(f"預計填寫：{[(M.describe_field(f), kind) for f, kind, _ in plan.actions]}")
    rep.line(f"問題：{plan.problems}")
    rep.line(f"備註：{plan.notes}")
    clicks = b.clicks(pg)
    rep.line(f"按鈕：{[c['text'] for c in clicks][:40]}")
    rep.line(f"程式會按的送出鈕：{M.pick_clickable(clicks, M.SUBMIT_BUTTONS, M.SUBMIT_EXCLUDE)}")
    rep.line(f"頁面上的日期：{[d.isoformat() for d in M.find_dates(b.text(pg))][:10]}，"
             f"場次日期比對：{M.session_date_check(b.text(pg), target.date)}")
    log.info("偵察完成，沒有填任何個人資料、沒有送出。")
