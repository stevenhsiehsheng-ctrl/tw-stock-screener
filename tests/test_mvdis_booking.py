"""考照預約小幫手（mvdis_booking）不需要瀏覽器的部分。PR 時 CI 會跑。"""
from __future__ import annotations

import datetime as dt
import random

import pytest

from mvdis_booking import config as C
from mvdis_booking import matching as M
from mvdis_booking import rocdate as R
from mvdis_booking import schedule as S

TW = C.TW


def cfg_of(**over):
    raw = {"target": {"license_label": "普通重型機車", "office_label": "臺北區監理所", "station_label": "板橋監理站",
                      "group_include": "初考", "group_exclude": "免講習|重考"},
           "applicant": {"id_no": "f131104093", "birth": "2005-07-02", "name": "王小明", "mobile": "0912-345-678",
                         "email": "a@b.com"}}
    for k, v in over.items():
        raw[k] = {**raw.get(k, {}), **v} if isinstance(v, dict) else v
    return C.validate(C._merge(C.DEFAULTS, raw))


def fld(order, tag="input", type="text", **kw):
    return {"mid": str(order), "order": order, "tag": tag, "type": type, "name": "", "id": "", "label": "",
            "row_label": "", "col_header": "", "before_text": "", "placeholder": "", "required": False,
            "readonly": False, "disabled": False, "maxlength": None, "value_len": 0, "checked": False,
            "options": None, "selected_value": None, **kw}


# ---------------------------------------------------------------- 日期
def test_roc_dates():
    assert R.to_roc7(dt.date(2026, 10, 20)) == "1151020"
    assert R.to_roc7(dt.date(1989, 7, 2)) == "0780702"
    assert R.format_date(dt.date(2026, 10, 20), "roc_slash") == "115/10/20"
    assert R.parse_date("115/10/20 (二)") == dt.date(2026, 10, 20)
    assert R.parse_date("考試日期 1151020") == dt.date(2026, 10, 20)
    assert R.parse_date("2026-10-20") == dt.date(2026, 10, 20)
    assert R.find_dates("電話 0912345678，身分證 A123456789") == []
    assert R.guess_input_format("text", "例:1151020", 7) == "roc7"
    assert R.guess_input_format("text", "115/10/20", None) == "roc_slash"
    assert R.guess_input_format("date", "", None) == "iso"


# ---------------------------------------------------------------- 設定檔
def test_tw_id_checksum():
    assert C.valid_tw_id("A123456789") and C.valid_tw_id("F131104093")
    assert not C.valid_tw_id("A123456788")
    assert C.valid_tw_id("AB12345678")         # 舊式居留證只檢查格式
    assert C.mask_id("F131104093") == "F13****093"


def test_config_validation():
    cfg = cfg_of()
    assert cfg["applicant"]["id_no"] == "F131104093"
    assert cfg["applicant"]["mobile"] == "0912345678"
    assert cfg["applicant"]["birth"] == dt.date(2005, 7, 2)
    with pytest.raises(C.ConfigError, match="範例"):
        cfg_of(applicant={"id_no": "A123456789"})
    with pytest.raises(C.ConfigError, match="檢查碼"):
        cfg_of(applicant={"id_no": "F131104094"})
    with pytest.raises(C.ConfigError, match="mode"):
        cfg_of(mode="yolo")
    with pytest.raises(C.ConfigError, match="30"):
        cfg_of(poll={"base_s": 5})
    with pytest.raises(C.ConfigError, match="windows"):
        cfg_of(poll={"windows": ["8點"]})
    assert cfg_of(applicant={"mobile": 912345678})["applicant"]["mobile"] == "0912345678"


def test_recon_config_does_not_need_applicant(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("target:\n  office_label: 臺北區監理所\n  station_label: 板橋監理站\n"
                 "applicant:\n  id_no: \"\"\n  name: \"\"\n", encoding="utf-8")
    cfg = C.load_config(p, require_applicant=False)
    assert cfg["target"]["office_label"] == "臺北區監理所"
    with pytest.raises(C.ConfigError, match="身分證"):
        C.load_config(p)


def test_date_range_clamped_to_booking_window():
    today = dt.date(2026, 10, 13)
    cfg = cfg_of(target={"date_from": "2026-10-01", "date_to": "2026-12-31"})
    assert C.date_range(cfg, today) == (dt.date(2026, 10, 16), dt.date(2026, 11, 12))


# ---------------------------------------------------------------- 查詢頻率
def test_next_delay_windows():
    poll = C.DEFAULTS["poll"]
    rng = random.Random(1)
    at = lambda h, m: dt.datetime(2026, 10, 13, h, m, tzinfo=TW)   # noqa: E731
    assert 60 <= S.next_delay(at(14, 0), poll, rng=rng) <= 120
    assert 15 <= S.next_delay(at(8, 5), poll, rng=rng) <= 25
    assert S.next_delay(at(3, 0), poll, rng=rng) >= 450
    assert S.next_delay(at(7, 57), poll, rng=rng) <= 70              # 快到 07:58 不要睡過頭
    assert S.next_delay(at(14, 0), poll, backoff_level=2, rng=rng) >= 120
    w = S.parse_window("23:50-00:10@30")
    assert w.contains(23 * 60 + 55) and w.contains(5) and not w.contains(60)


def test_date_cursor_rotates_until_onward_seen():
    cfg = cfg_of(target={"weekdays": [6, 7]})
    today = dt.date(2026, 10, 13)
    cur = S.DateCursor()
    first = cur.next_query_date(cfg, today)
    assert first.isoweekday() in (6, 7)
    cur.observe(first, [first])
    second = cur.next_query_date(cfg, today)
    assert second > first
    cur.observe(second, [second, second + dt.timedelta(days=3)])
    assert cur.onward and cur.next_query_date(cfg, today) == C.date_range(cfg, today)[0]


# ---------------------------------------------------------------- 選項與欄位
def test_choose_option():
    opts = [["", "請選擇"], ["20", "臺北市區監理所(含金門馬祖)"], ["40", "臺北區監理所(北宜花)"]]
    assert M.choose_option(opts, "台北區監理所") == "40"
    assert M.choose_option(opts, "臺北市區") == "20"
    with pytest.raises(M.OptionError, match="同時符合"):
        M.choose_option(opts, "臺北")
    with pytest.raises(M.OptionError, match="找不到"):
        M.choose_option(opts, "花蓮")


def test_locate_query_fields_table_and_div():
    lic_opts = [["3", "普通重型機車"], ["5", "普通輕型機車"]]
    table = [
        fld(0, "select", "select", name="licenseTypeCode", row_label="報考照類 Type of Test：",
            before_text="……報名失敗請洽各區監理所。報考照類 Type of Test：", options=lic_opts),
        fld(1, row_label="預計考試日期 Date of Test：", name="expectExamDateStr"),
        fld(2, "select", "select", row_label="考試地點 Place of Test：", name="dmvNoLv1"),
        fld(3, "select", "select", row_label="考試地點 Place of Test：", name="dmvNo"),
        fld(4, type="checkbox", label="僅查周末及假日場次 Test on Holiday"),
    ]
    q = M.locate_query_fields(table)
    assert q["license"]["order"] == 0 and [p["order"] for p in q["places"]] == [2, 3]
    assert q["date"]["order"] == 1 and q["weekend"]["order"] == 4
    div = [
        fld(0, "select", "select", label="報考照類", options=lic_opts),
        fld(1, "select", "select", before_text="考試地點："),
        fld(2, "select", "select"),
        fld(3, label="預計考試日期", readonly=True),
    ]
    q = M.locate_query_fields(div)
    assert [p["order"] for p in q["places"]] == [1, 2] and q["date"]["order"] == 3 and q["weekend"] is None
    with pytest.raises(M.LocateError):
        M.locate_query_fields([fld(0)])


def test_classify_field():
    assert M.classify_field(fld(0, row_label="身分證統一編號 ID No.*")) == "id_no"
    assert M.classify_field(fld(0, row_label="出生日期 Date of Birth")) == "birth"
    assert M.classify_field(fld(0, row_label="行動電話")) == "mobile"
    assert M.classify_field(fld(0, row_label="聯絡電話")) == "phone"
    assert M.classify_field(fld(0, label="姓名")) == "name"
    assert M.classify_field(fld(0, row_label="驗證碼")) == "captcha"
    assert M.classify_field(fld(0, placeholder="請輸入圖中文字")) == "captcha"
    assert M.classify_field(fld(0, name="validateStr")) == "captcha"
    assert M.classify_field(fld(0, type="checkbox", label="我已詳閱並同意上述注意事項")) == "agree"
    assert M.classify_field(fld(0, type="checkbox", label="不同意")) is None
    assert M.classify_field(fld(0, row_label="考照原因")) is None


# ---------------------------------------------------------------- 場次表
def _table(rows):
    return {"tid": 0, "rows": rows}


def test_parse_sessions_with_rowspan_and_filter():
    hdr = {"cells": ["考試日期 (星期) Date of Test", "場次組別說明 Desc.", "可報名人數", "功能"], "actions": []}
    rows = [
        {"cells": ["查詢條件", "查詢條件", "查詢條件", "查詢條件"], "actions": []},
        hdr,
        {"cells": ["115/10/20 (二)", "31 免講習（重考）", "12", "報名"], "actions": [{"aid": "a1", "text": "報名", "col": 3}]},
        {"cells": ["115/10/20 (二)", "32 初考領，需講習", "額滿", ""], "actions": []},
        {"cells": ["115/10/24 (六)", "32 初考領，需講習", "3", "報名"], "actions": [{"aid": "a3", "text": "報名", "col": 3}]},
        {"cells": ["115/10/27 (二)", "32 初考領，需講習", "5", "報名"],
         "actions": [{"aid": "a4", "text": "取消報名", "col": 3}]},
    ]
    found = M.find_session_table([_table([{"cells": ["首頁"], "actions": []}]), _table(rows)])
    assert found and found[1] == 1 and found[2] == {"date": 0, "group": 1, "seats": 2, "action": 3}
    ss = M.parse_sessions(found)
    assert [(s.date.day, s.seats, s.action_id) for s in ss] == [(20, 12, "a1"), (20, 0, None), (24, 3, "a3"), (27, 5, None)]
    cfg = cfg_of()
    got = M.filter_sessions(ss, cfg, dt.date(2026, 10, 16), dt.date(2026, 11, 12))
    assert [s.action_id for s in got] == ["a3"]
    cfg["target"]["weekdays"] = [1, 2, 3, 4, 5]
    assert M.filter_sessions(ss, cfg, dt.date(2026, 10, 16), dt.date(2026, 11, 12)) == []


def test_parse_sessions_date_separator_rows():
    rows = [
        {"cells": ["日期", "組別", "名額", "功能"], "actions": []},
        {"cells": ["115/11/02（一）"] * 4, "actions": []},
        {"cells": ["", "上午組", "2", "報名"], "actions": [{"aid": "x", "text": "SIGN UP", "col": 3}]},
    ]
    ss = M.parse_sessions(M.find_session_table([_table(rows)]))
    assert len(ss) == 1 and ss[0].date == dt.date(2026, 11, 2) and ss[0].action_id == "x"
    assert M.parse_seats("38/40") is None and M.parse_seats("剩 7 名") == 7


# ---------------------------------------------------------------- 填報名資料
def _form(*extra, birth=None):
    base = [
        fld(0, row_label="身分證統一編號 ID No.*", maxlength=10),
        *(birth or [fld(1, row_label="出生日期", maxlength=7, placeholder="例：0780702")]),
        fld(5, row_label="姓名"),
        fld(6, row_label="聯絡電話"),
        fld(7, row_label="電子郵件 E-mail"),
        fld(8, type="checkbox", label="我已詳閱並同意上述注意事項"),
    ]
    return base + list(extra)


def _values(plan):
    return {f["order"]: v for f, kind, v in plan.actions}


def test_plan_fill_basic():
    plan = M.plan_fill(_form(), cfg_of())
    assert plan.problems == [] and plan.captcha is None
    assert _values(plan) == {0: "F131104093", 1: "0940702", 5: "王小明", 6: "0912345678", 7: "a@b.com", 8: True}


def test_plan_fill_split_birth_and_captcha():
    years = [[str(y), str(y)] for y in range(60, 101)]
    months = [[f"{m:02d}", f"{m:02d}"] for m in range(1, 13)]
    birth = [fld(1, "select", "select", row_label="出生日期", options=years),
             fld(2, "select", "select", row_label="出生日期", before_text="年", options=months),
             fld(3, "select", "select", row_label="出生日期", before_text="月", options=months + [["31", "31"]])]
    plan = M.plan_fill(_form(fld(9, row_label="驗證碼", maxlength=4), birth=birth), cfg_of())
    v = _values(plan)
    assert (v[1], v[2], v[3]) == ("94", "07", "02") and plan.captcha["order"] == 9 and not plan.problems
    plan = M.plan_fill(_form(fld(9, row_label="")), cfg_of(), has_captcha_image=True)
    assert plan.captcha["order"] == 9 and not plan.problems


def test_plan_fill_unknown_fields():
    sel = fld(9, "select", "select", row_label="考照原因 *", options=[["", "請選擇"], ["1", "初考"], ["2", "重考"]],
              selected_value="")
    plan = M.plan_fill(_form(sel), cfg_of())
    assert any("考照原因" in p for p in plan.problems)
    plan = M.plan_fill(_form(sel), cfg_of(applicant={"extra": {"考照原因": "初考"}}))
    assert not plan.problems and (sel, "select", "初考") in plan.actions
    radios = [fld(10, type="radio", name="sex", row_label="性別", label="男", required=True),
              fld(11, type="radio", name="sex", row_label="性別", label="女", required=True)]
    plan = M.plan_fill(_form(*radios), cfg_of())
    assert any("性別" in p for p in plan.problems)
    plan = M.plan_fill(_form(*radios), cfg_of(applicant={"extra": {"性別": "女"}}))
    assert not plan.problems and (radios[1], "check", True) in plan.actions
    plan = M.plan_fill(_form(fld(9, row_label="備註")), cfg_of())
    assert not plan.problems and any("備註" in n for n in plan.notes)
    plan = M.plan_fill(_form(), cfg_of(applicant={"name": ""}))
    assert any("姓名" in p for p in plan.problems)
    assert any("身分證" in p for p in M.plan_fill([fld(0, row_label="姓名")], cfg_of()).problems)


def test_plan_fill_agree_off():
    plan = M.plan_fill(_form(), cfg_of(auto_agree=False))
    assert 8 not in _values(plan)


# ---------------------------------------------------------------- 結果判斷
NOTICE = "未完成危險感知者不得報名。如報名失敗請洽各區監理所。未到考者將停權15日，同一人不可重複報名。報名成功後如欲取消，請於07:00前取消。"


def test_classify_result():
    assert M.classify_result("報名成功\n考試日期 115/10/24") == "success"
    assert M.classify_result("報名失敗\n查無有效的(機車)危險感知體驗紀錄") == "hazard"
    assert M.classify_result("驗證碼輸入錯誤，請重新輸入") == "captcha_wrong"
    assert M.classify_result("此場次名額已滿，請選擇其他場次") == "full"
    assert M.classify_result("您已有報名紀錄，不可重複報名") == "duplicate"
    assert M.classify_result("系統忙碌中，請稍後再試") == "busy"
    assert M.classify_result("您尚未報名成功") != "success"
    assert M.classify_result("請輸入身分證統一編號") == "failed"
    # 注意事項本身不算結果
    assert M.classify_page_change(NOTICE) is None
    assert M.classify_page_change("報名成功\n" + NOTICE) == "success"
    assert M.classify_page_change("報名失敗\n" + NOTICE) == "hazard"
    assert M.classify_result("提醒您：請先完成危險感知體驗並登錄紀錄，未到考者將暫停預約15日") is None


def test_new_lines_ignores_text_already_on_page():
    before = "填寫報考人資料\n" + NOTICE
    after = "報名結果\n報名成功\n" + NOTICE
    assert M.new_lines(before, after) == "報名結果\n報名成功"


def test_session_date_check():
    d = dt.date(2026, 10, 24)
    assert M.session_date_check("考試日期：115/10/24", d) == "ok"
    assert M.session_date_check("考試日期：10月24日", d) == "ok"
    assert M.session_date_check("考試日期：115/10/27", d) == "mismatch"
    assert M.session_date_check("例：0780702", d) == "unknown"


def test_pick_clickable():
    clicks = [{"cid": "1", "text": "查詢已報名及取消報名"}, {"cid": "2", "text": "查詢場次 Search"},
              {"cid": "3", "text": "回上一頁"}, {"cid": "4", "text": "確認送出"}, {"cid": "5", "text": "送出", "disabled": True}]
    assert M.pick_clickable(clicks, M.SEARCH_BUTTONS, M.SEARCH_EXCLUDE)["cid"] == "2"
    assert M.pick_clickable(clicks, M.SUBMIT_BUTTONS, M.SUBMIT_EXCLUDE)["cid"] == "4"
    assert M.pick_clickable(clicks[:3], M.SUBMIT_BUTTONS, M.SUBMIT_EXCLUDE) is None
