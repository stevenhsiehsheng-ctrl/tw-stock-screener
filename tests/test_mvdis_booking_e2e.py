"""考照預約小幫手的端對端測試：用 Playwright 操作 tests/mvdis_mock.py 的模擬網站。

CI 沒有裝 Playwright，會自動略過；本機要跑：pip install -r mvdis_booking/requirements.txt && python -m playwright install chromium
"""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from mvdis_booking import config as C  # noqa: E402
from mvdis_booking import matching as M  # noqa: E402
from mvdis_booking import runner, util  # noqa: E402
from mvdis_booking.browser import Booker  # noqa: E402
from mvdis_booking.recon import Report, _recon  # noqa: E402
from mvdis_mock import Mock, MockServer  # noqa: E402

TODAY = dt.datetime.now(C.TW).date()
EXPECTED = {"idNo": "F131104093", "birth": "0940702", "name": "王小明", "tel": "0912345678", "email": "a@b.com"}


class FakeNotifier:
    def __init__(self):
        self.alerts = []

    def alert(self, title, body, **kw):
        self.alerts.append((title, body))

    def sound(self, *a, **k):
        pass

    def email(self, *a, **k):
        return False


@pytest.fixture(scope="module")
def pw():
    p = sync_playwright().start()
    try:
        b = p.chromium.launch()
        b.close()
    except Exception as e:     # 有裝套件但沒裝瀏覽器
        p.stop()
        pytest.skip(f"Chromium 無法啟動：{e}")
    yield p
    p.stop()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(util, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "wait_exit", lambda *a, **k: None)
    monkeypatch.setattr(runner, "next_delay", lambda *a, **k: 0.2)
    return tmp_path


def make_cfg(url, tmp_path, mode="dry_run", target=None, **over):
    raw = {
        "mode": mode,
        "target": {"license_label": "普通重型機車", "office_label": "台北區監理所", "station_label": "板橋",
                   "group_include": "初考", "group_exclude": "免講習|補考|重考", **(target or {})},
        "applicant": {"id_no": "F131104093", "birth": "2005-07-02", "name": "王小明", "mobile": "0912-345-678",
                      "email": "a@b.com"},
        "notify": {"sound": False, "desktop": False},
        "browser": {"headless": True, "profile_dir": str(tmp_path / "profile")},
        "site": {"base_url": url},
        "captcha": {"timeout_s": 20},
        "human_timeout_s": 3,
    }
    for k, v in over.items():
        raw[k] = {**raw[k], **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v
    return C.validate(C._merge(C.DEFAULTS, raw))


def start(pw, cfg, tmp_path):
    n = FakeNotifier()
    b = Booker(cfg, n, util.Log(tmp_path / "log.txt"), tmp_path)
    b.start(pw)
    return b, n


def query(b, cfg, qdate):
    b.goto_locations()
    info = b.fill_query(qdate)
    res = b.search()
    s, e = C.date_range(cfg, TODAY)
    return info, res, M.filter_sessions(res.sessions, cfg, s, e)


# ---------------------------------------------------------------- 測試模式：只填不送出
@pytest.mark.parametrize("layout", ["table", "div"])
def test_dry_run_fills_but_does_not_submit(pw, env, layout):
    mock = Mock(layout=layout, expected=EXPECTED, captcha=True)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env)
        b, n = start(pw, cfg, env)
        try:
            info, res, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            assert res.kind == "ok"
            q = mock.queries[-1]
            assert (q["licenseTypeCode"], q["dmvNoLv1"], q["dmvNo"]) == ("3", "40", "41")
            want_date = C.date_range(cfg, TODAY)[0]
            assert M.parse_date(q["expectExamDateStr"]) == want_date
            # 31 免講習那組被排除；32 初考 只有 S3 有空位
            assert [(s.date, s.seats) for s in matched] == [(TODAY + dt.timedelta(days=9), 3)]
            groups = {s.group for s in res.sessions}
            assert "31 免講習（重考、補考、輕型換重型）" in groups
            out = b.try_book(matched[0])
            assert out.kind == "dry_run_ready", out.detail
            pg = b._newest_page()
            assert pg.input_value("[name=idNo]") == "F131104093"
            assert pg.input_value("[name=birthdayStr]") == "0940702"
            assert pg.input_value("[name=name]") == "王小明"
            assert pg.input_value("[name=tel]") == "0912345678"
            assert pg.input_value("[name=email]") == "a@b.com"
            assert pg.is_checked("[name=agree]")
            assert pg.input_value("[name=validateStr]") == ""      # 驗證碼留給人
            assert mock.submissions == []
        finally:
            b.close()


# ---------------------------------------------------------------- 全自動：等到有人取消才有空位
def test_book_waits_for_cancellation_then_books(pw, env):
    mock = Mock(layout="div", onward=False, open_after=2, notice_page=True, confirm_page=True,
                birth_split=True, popup=False, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            code = runner._loop(cfg, b, n, b.log)
        finally:
            b.close()
    assert code == 0, n.alerts
    assert len(mock.submissions) == 1
    assert mock.submissions[0]["secId"] == "S3"
    assert len(mock.queries) > 3
    assert util.load_state()["booked"] is True
    assert n.alerts[-1][0] == "預約成功！"


@pytest.mark.parametrize("autotype", ["full", "enter"])
def test_book_with_captcha_typed_by_human(pw, env, autotype):
    mock = Mock(captcha=True, autotype=autotype, captcha_wrong_first=(autotype == "full"), expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            _, res, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "success", (out, n.alerts)
    assert len(mock.submissions) == 1 and mock.submissions[0]["validateStr"] == "4821"
    assert any("驗證碼" in t for t, _ in n.alerts)


def test_captcha_timeout_does_not_submit(pw, env):
    mock = Mock(captcha=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book", captcha={"timeout_s": 2})
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "captcha_timeout"
    assert mock.submissions == []


def test_hazard_failure_stops_program(pw, env):
    mock = Mock(hazard_fail=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            code = runner._loop(cfg, b, n, b.log)
        finally:
            b.close()
    assert code == 3
    assert "危險感知" in n.alerts[-1][0]
    assert not util.load_state()


def test_full_when_clicked(pw, env):
    mock = Mock(full_on_click=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "full"
    assert mock.submissions == []


def test_unknown_required_field_hands_over_to_human(pw, env):
    mock = Mock(extra_required=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "human_timeout"
    assert "考照原因" in out.detail
    assert mock.submissions == []


def test_extra_mapping_fills_unknown_field(pw, env):
    mock = Mock(extra_required=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book", applicant={"extra": {"考照原因": "初考"}})
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "success", out
    assert mock.submissions[0]["reason"] == "1"


def test_recon_report(pw, env):
    mock = Mock(captcha=True, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env)
        b, n = start(pw, cfg, env)
        rep = Report()
        try:
            _recon(cfg, b, rep, b.log)
        finally:
            b.close()
    text = rep.text()
    assert "板橋監理站（41）" in text
    assert "有驗證碼：是" in text
    assert "'id_no'" not in text or "判讀" in text
    assert mock.submissions == []
    assert "F131104093" not in text     # 偵察報告不含個人資料


# ---------------------------------------------------------------- 審查發現的情境
def test_maintenance_pages_do_not_stop_the_loop(pw, env):
    mock = Mock(maintenance_gets=4, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            code = runner._loop(cfg, b, n, b.log)
        finally:
            b.close()
    assert code == 0, n.alerts
    assert len(mock.submissions) == 1
    assert not any("停止" in t for t, _ in n.alerts)


def test_slow_submit_response_is_not_treated_as_busy(pw, env):
    mock = Mock(submit_delay=25, expected=EXPECTED)
    mock.sessions[3].seats = 2          # S4 也有空位：送出變慢時不可以改去報 S4
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            code = runner._loop(cfg, b, n, b.log)
        finally:
            b.close()
    assert code == 0, n.alerts
    assert [x["secId"] for x in mock.submissions] == ["S3"]


def test_dry_run_never_presses_agree_and_submit(pw, env):
    mock = Mock(prefilled=True, expected=EXPECTED)
    mock.sessions[3].seats = 2
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env)
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "dry_run_ready", out
    assert mock.submissions == []


def test_conditional_success_wording_in_confirm_is_not_success(pw, env):
    mock = Mock(hazard_fail=True, confirm_text="確定要報名嗎？報名成功者請準時到考", expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "hazard", out


@pytest.mark.parametrize("inline,rebind", [(True, False), (False, True)])
def test_captcha_wrong_twice_then_right(pw, env, inline, rebind):
    mock = Mock(captcha=True, autotype="full", wrong_times=2, inline_captcha_error=inline,
                rebind_captcha=rebind, expected=EXPECTED)
    with MockServer(mock) as srv:
        cfg = make_cfg(srv.url, env, mode="book")
        b, n = start(pw, cfg, env)
        try:
            _, _, matched = query(b, cfg, TODAY + dt.timedelta(days=3))
            out = b.try_book(matched[0])
        finally:
            b.close()
    assert out.kind == "success", (out, n.alerts)
    assert mock._captcha_fails == 2 and len(mock.submissions) == 1
