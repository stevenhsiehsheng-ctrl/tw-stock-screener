"""模擬監理服務網考照預約流程的小網站（給 tests/test_mvdis_booking_e2e.py 用）。

真實網頁的欄位代碼查不到，這裡故意做好幾種版面（表格／div、驗證碼、注意事項頁、確認頁、
生日拆三格、彈出視窗……），確認程式靠「畫面上的文字」找欄位時都能運作。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TW = dt.timezone(dt.timedelta(hours=8))
P = "/m3-emv-trn/exm"

OFFICES = [("20", "臺北市區監理所(含金門馬祖)"), ("40", "臺北區監理所(北宜花)"), ("50", "新竹區監理所(桃竹苗)"),
           ("60", "臺中區監理所(中彰投)"), ("70", "嘉義區監理所(雲嘉南)"), ("80", "高雄區監理所(高屏澎東)"),
           ("30", "高雄市區監理所")]
STATIONS = {"20": [("21", "士林監理站"), ("25", "基隆監理站")], "40": [("41", "板橋監理站"), ("42", "蘆洲監理站"), ("43", "宜蘭監理站")],
            "50": [("51", "新竹市監理站"), ("52", "桃園監理站")], "60": [("61", "彰化監理站")], "70": [("71", "臺南監理站")],
            "80": [("81", "屏東監理站")], "30": [("31", "苓雅監理站")]}
NOTICE = ("請點選【前往危險感知平台】完成危險感知測驗並登錄測驗紀錄後，方能預約考照。未完成危險感知者不得報名。"
          "報名失敗請洽各區監理所。未到考者將停權15日。")


def roc(d: dt.date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def parse_roc(s: str) -> dt.date | None:
    s = (s or "").replace("/", "")
    if len(s) != 7 or not s.isdigit():
        return None
    return dt.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:]))


@dataclass
class Sess:
    sid: str
    date: dt.date
    group: str
    seats: int


@dataclass
class Mock:
    layout: str = "table"            # table／div
    captcha: bool = False
    captcha_code: str = "4821"
    captcha_wrong_first: bool = False
    autotype: str = ""               # 模擬真人輸入驗證碼：""／"full"（打滿）／"enter"（打完按 Enter）
    popup: bool = True
    onward: bool = True
    notice_page: bool = False
    confirm_page: bool = False
    birth_split: bool = False
    extra_required: bool = False     # 報名頁多一個程式認不得的必填下拉選單
    open_after: int = 0              # 第幾次查詢之後「初考」那組才有空位（模擬有人取消）
    full_on_click: bool = False      # 按報名時已經被搶走
    hazard_fail: bool = False
    expected: dict = field(default_factory=dict)
    today: dt.date = field(default_factory=lambda: dt.datetime.now(TW).date())
    queries: list = field(default_factory=list)
    submissions: list = field(default_factory=list)
    signup_clicks: int = 0
    _captcha_fails: int = 0

    def __post_init__(self) -> None:
        d = self.today
        self.sessions = [
            Sess("S1", d + dt.timedelta(days=5), "31 免講習（重考、補考、輕型換重型）", 12),
            Sess("S2", d + dt.timedelta(days=5), "32 初考領，需講習", 0),
            Sess("S3", d + dt.timedelta(days=9), "32 初考領，需講習", 0),
            Sess("S4", d + dt.timedelta(days=12), "32 初考領，需講習", 0),
        ]

    def seats(self, s: Sess) -> int:
        if s.sid == "S3" and len(self.queries) > self.open_after:
            return 3 if s.seats == 0 and not self.submissions else s.seats
        return s.seats

    # ------------------------------------------------------------ 頁面
    def page(self, title: str, body: str, script: str = "") -> str:
        return (f"<!doctype html><html lang='zh-TW'><head><meta charset='utf-8'><title>{title}</title>"
                "<style>.modal{position:fixed;top:20%;left:20%;background:#fff;border:2px solid #333;padding:20px}"
                "</style></head><body><div class='breadcrumb'>首頁 › 考試報名 › 預約報名及查詢 › 考照預約報名</div>"
                f"{body}<script>{script}</script></body></html>")

    def query_form(self, v: dict) -> str:
        def opts(items, sel):
            return "<option value=''>請選擇</option>" + "".join(
                f"<option value='{k}'{' selected' if k == sel else ''}>{html.escape(t)}</option>" for k, t in items)
        lic = opts([("3", "普通重型機車"), ("5", "普通輕型機車"), ("A", "普通小型車")], v.get("licenseTypeCode"))
        off = opts(OFFICES, v.get("dmvNoLv1"))
        sta = opts(STATIONS.get(v.get("dmvNoLv1", ""), []), v.get("dmvNo"))
        date = html.escape(v.get("expectExamDateStr", ""))
        if self.layout == "table":
            rows = (f"<tr><th>報考照類 Type of Test：</th><td><select name='licenseTypeCode' id='licenseTypeCode'>{lic}</select></td></tr>"
                    f"<tr><th>預計考試日期 Date of Test：</th><td><input type='text' name='expectExamDateStr' id='expectExamDateStr' "
                    f"maxlength='7' placeholder='例:1151020' value='{date}'></td></tr>"
                    f"<tr><th>考試地點 Place of Test：</th><td><select name='dmvNoLv1' id='dmvNoLv1' onchange='loadStations()'>{off}</select>"
                    f" <select name='dmvNo' id='dmvNo'>{sta}</select></td></tr>"
                    "<tr><th></th><td><label><input type='checkbox' name='onlyHoliday' value='Y'>僅查周末及假日場次 Test on Holiday</label></td></tr>")
            fields = f"<table class='form'>{rows}</table>"
        else:
            fields = (f"<div class='g'><label for='lt'>報考照類</label><select name='licenseTypeCode' id='lt'>{lic}</select></div>"
                      f"<div class='g'><span>考試地點：</span><select name='dmvNoLv1' id='dmvNoLv1' onchange='loadStations()'>{off}</select>"
                      f"<select name='dmvNo' id='dmvNo'>{sta}</select></div>"
                      f"<div class='g'><label for='ed'>預計考試日期</label><input type='text' readonly name='expectExamDateStr' id='ed' "
                      f"placeholder='115/10/20' value='{date}' onclick='this.value=\"\"'></div>"
                      "<div class='g'><input type='checkbox' id='hol' name='onlyHoliday' value='Y'><label for='hol'>僅查週末及假日場次</label></div>")
        popup = ("<div id='popup' class='modal' style='display:none'><p>提醒您：報名前須完成危險感知體驗並登錄紀錄。</p>"
                 "<button type='button'>前往危險感知平台</button> <button type='button'>查詢測驗登錄紀錄</button> "
                 "<button type='button' onclick='document.getElementById(\"qf\").submit()'>選擇場次繼續報名</button></div>")
        return (f"<p>{NOTICE}</p><form id='qf' method='post' action='{P}/locations'><input type='hidden' name='method' value='query'>"
                f"{fields}<a href='#' class='btn' onclick='doQuery();return false;'>查詢場次 Search</a>"
                f" <a href='{P}/query'>查詢已報名及取消報名</a></form>{popup if self.popup else ''}")

    QUERY_JS = (
        "function loadStations(){var o=document.getElementById('dmvNoLv1').value;"
        "setTimeout(function(){fetch('" + P + "/stations?office='+o).then(r=>r.json()).then(function(list){"
        "var s=document.getElementById('dmvNo');s.innerHTML='<option value=\"\">請選擇</option>'+"
        "list.map(function(x){return '<option value=\"'+x[0]+'\">'+x[1]+'</option>'}).join('');});},300);}"
        "function doQuery(){var f=document.getElementById('qf');"
        "if(!f.dmvNo.value){alert('請選擇考試地點');return;}"
        "if(!f.expectExamDateStr.value){alert('請輸入預計考試日期');return;}"
        "var p=document.getElementById('popup');if(p){p.style.display='block';}else{f.submit();}}"
        "function signUp(id){var f=document.getElementById('sf');f.secId.value=id;f.submit();}"
    )

    def locations(self, v: dict | None = None, results: str = "") -> str:
        return self.page("考照預約報名", self.query_form(v or {}) + results +
                         f"<form id='sf' method='post' action='{P}/signup'><input type='hidden' name='secId'></form>",
                         self.QUERY_JS)

    def results(self, v: dict) -> str:
        d = parse_roc(v.get("expectExamDateStr", ""))
        rows = [s for s in self.sessions if d and (s.date >= d if self.onward else s.date == d)]
        if not rows:
            return "<p class='msg'>查無可報名場次</p>"
        out = []
        by_date: dict = {}
        for s in rows:
            by_date.setdefault(s.date, []).append(s)
        for day, ss in sorted(by_date.items()):
            for i, s in enumerate(ss):
                n = self.seats(s)
                cell = f"<td rowspan='{len(ss)}'>{roc(day)} ({'一二三四五六日'[day.weekday()]})</td>" if i == 0 else ""
                act = (f"<a href='#' onclick=\"signUp('{s.sid}');return false;\">報名</a>" if n > 0 else "")
                out.append(f"<tr>{cell}<td>{html.escape(s.group)}</td><td>{n if n > 0 else '額滿'}</td><td>{act}</td></tr>")
        return ("<table class='list'><thead><tr><th>考試日期 (星期) Date of Test</th><th>場次組別說明 Desc.</th>"
                f"<th>可報名人數</th><th>功能</th></tr></thead><tbody>{''.join(out)}</tbody></table>")

    def signup_form(self, sid: str, alert: str = "") -> str:
        s = next(x for x in self.sessions if x.sid == sid)
        if self.birth_split:
            yo = "".join(f"<option value='{y}'>{y}</option>" for y in range(60, 101))
            mo = "".join(f"<option value='{m:02d}'>{m:02d}</option>" for m in range(1, 13))
            do = "".join(f"<option value='{d:02d}'>{d:02d}</option>" for d in range(1, 32))
            birth = (f"<select name='by'><option value=''>--</option>{yo}</select>年<select name='bm'><option value=''>--</option>{mo}"
                     f"</select>月<select name='bd'><option value=''>--</option>{do}</select>日")
        else:
            birth = "<input name='birthdayStr' maxlength='7' placeholder='例：0780702'>"
        cap = (f"<tr><th>驗證碼</th><td><img src='/m3-emv-trn/captcha.jpg' alt=''> <a href='#'>換一張</a> "
               "<input name='validateStr' id='validateStr' maxlength='4' autocomplete='off'></td></tr>") if self.captcha else ""
        extra = ("<tr><th>考照原因 <span>*</span></th><td><select name='reason'><option value=''>請選擇</option>"
                 "<option value='1'>初考</option><option value='2'>重考</option></select></td></tr>") if self.extra_required else ""
        body = (f"<h2>填寫報考人資料</h2><table class='info'><tr><th>考試日期</th><td>{roc(s.date)}</td></tr>"
                f"<tr><th>場次組別</th><td>{html.escape(s.group)}</td></tr><tr><th>考試地點</th><td>板橋監理站</td></tr></table>"
                f"<p>{NOTICE} 報名成功後如欲取消，請於考照當日07:00前上網取消。</p>"
                f"<form id='f' method='post' action='{P}/submit'><input type='hidden' name='secId' value='{sid}'><table>"
                "<tr><th>身分證統一編號 ID No.<span>*</span></th><td><input name='idNo' maxlength='10'></td></tr>"
                f"<tr><th>出生日期 Date of Birth<span>*</span></th><td>{birth}</td></tr>"
                "<tr><th>姓名</th><td><input name='name'></td><th>聯絡電話</th><td><input name='tel'></td></tr>"
                f"<tr><th>電子郵件 E-mail</th><td><input name='email'></td></tr>{extra}{cap}</table>"
                "<label><input type='checkbox' name='agree' value='Y'> 我已詳閱並同意上述注意事項</label><br>"
                "<button type='button' onclick='doSubmit()'>確認送出</button> <button type='button' onclick='history.back()'>回上一頁</button>"
                "</form>")
        js = ("function doSubmit(){var f=document.getElementById('f');"
              "if(!f.idNo.value){alert('請輸入身分證統一編號');return;}"
              "if(!f.agree.checked){alert('請勾選同意注意事項');return;}"
              "if(confirm('確定要報名嗎？')){f.submit();}}")
        if alert:
            js += f"alert({json.dumps(alert)});"
        if self.captcha and self.autotype:
            code = "0000" if self.captcha_wrong_first and self._captcha_fails == 0 else self.captcha_code
            js += ("setTimeout(function(){var el=document.getElementById('validateStr');var c=" + json.dumps(code) + ";var i=0;"
                   "var t=setInterval(function(){el.value+=c[i++];el.dispatchEvent(new Event('input',{bubbles:true}));"
                   "if(i>=c.length){clearInterval(t);" +
                   ("el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));" if self.autotype == "enter" else "") +
                   "}},150);},1500);")
        return self.page("填寫報考人資料", body, js)

    def notice(self, sid: str) -> str:
        return self.page("注意事項", f"<h2>報名注意事項</h2><p>{NOTICE}</p><form method='post' action='{P}/signupForm'>"
                         f"<input type='hidden' name='secId' value='{sid}'>"
                         "<button type='submit'>我已閱讀並同意</button> <a href='javascript:history.back()'>回上一頁</a></form>")

    def check(self, v: dict) -> str | None:
        e = self.expected
        if self.birth_split:
            birth = f"{int(v.get('by', '0')):03d}{v.get('bm', '')}{v.get('bd', '')}"
        else:
            birth = v.get("birthdayStr", "")
        got = {"idNo": v.get("idNo"), "birth": birth, "name": v.get("name"), "tel": v.get("tel"), "email": v.get("email")}
        for k, want in e.items():
            if got.get(k) != want:
                return f"{k} 不對：{got.get(k)!r}"
        if v.get("agree") != "Y":
            return "沒有勾選同意"
        if self.extra_required and not v.get("reason"):
            return "請選擇考照原因"
        return None

    def submit(self, v: dict) -> str:
        sid = v.get("secId", "")
        if self.captcha:
            wrong = v.get("validateStr") != self.captcha_code
            if wrong:
                self._captcha_fails += 1
                return self.signup_form(sid, "驗證碼輸入錯誤，請重新輸入")
        err = self.check(v)
        if err:
            return self.page("報名失敗", f"<h2>報名失敗</h2><p>{html.escape(err)}</p>")
        if self.hazard_fail:
            return self.page("報名失敗", "<h2>報名失敗</h2><p>查無有效的(機車)危險感知體驗紀錄，請完成體驗後再報名。</p>")
        if self.confirm_page and v.get("step") != "confirm":
            hidden = "".join(f"<input type='hidden' name='{html.escape(k)}' value='{html.escape(x)}'>" for k, x in v.items())
            return self.page("確認報名資料", f"<h2>請確認報名資料</h2><table><tr><th>身分證統一編號</th><td>{html.escape(v.get('idNo', ''))}</td></tr>"
                             f"</table><form method='post' action='{P}/submit'>{hidden}<input type='hidden' name='step' value='confirm'>"
                             "<input type='submit' value='確認送出'> <a href='javascript:history.back()'>回上一頁修改</a></form>")
        s = next(x for x in self.sessions if x.sid == sid)
        self.submissions.append(dict(v))
        s.seats = max(0, self.seats(s) - 1)
        return self.page("報名結果", f"<h2>報名成功</h2><p>考試日期：{roc(s.date)}　{html.escape(s.group)}</p>"
                         "<p>如欲取消，請於考照當日07:00前至監理服務網取消。</p>")


def make_handler(m: Mock):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == f"{P}/locations":
                return self._send(m.locations())
            if u.path == f"{P}/stations":
                return self._send(json.dumps(STATIONS.get(q.get("office", ""), []), ensure_ascii=False), "application/json")
            if u.path == "/m3-emv-trn/captcha.jpg":
                gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                       b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
                return self._send(gif, "image/gif")
            return self._send("not found", code=404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            v = {k: x[0] for k, x in parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True).items()}
            path = urlsplit(self.path).path
            if path == f"{P}/locations":
                m.queries.append(v)
                return self._send(m.locations(v, m.results(v)))
            if path == f"{P}/signup":
                m.signup_clicks += 1
                s = next((x for x in m.sessions if x.sid == v.get("secId")), None)
                if s is None or m.seats(s) <= 0 or m.full_on_click:
                    return self._send(m.page("訊息", "", "alert('此場次名額已滿，請選擇其他場次');"))
                return self._send(m.notice(s.sid) if m.notice_page else m.signup_form(s.sid))
            if path == f"{P}/signupForm":
                return self._send(m.signup_form(v.get("secId", "")))
            if path == f"{P}/submit":
                return self._send(m.submit(v))
            return self._send("not found", code=404)
    return H


class MockServer:
    def __init__(self, mock: Mock) -> None:
        self.mock = mock
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mock))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "MockServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
