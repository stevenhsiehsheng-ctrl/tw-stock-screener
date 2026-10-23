"""模擬監理服務網考照預約流程的小網站（給 tests/test_mvdis_booking_e2e.py 用）。

真實網頁的欄位代碼查不到，這裡故意做好幾種版面（表格／div、驗證碼、注意事項頁、確認頁、
生日拆三格、彈出視窗……），確認程式靠「畫面上的文字」找欄位時都能運作。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import threading
import time
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

# ---- layout="mvdis"：照 2026-10 偵察真實監理服務網看到的結構做的版面
# 選監理所會整頁重新送出（method=dmvOnChange）；查詢前就有空的場次表；查完跳「危險感知」提醒；
# 組別說明裡有 <a> 標示文字；報名頁一打開跳「我已充分知悉相關約定並願接受」；送出後可能要輸入 OTP。
MVDIS_OFFICES = [("20", "臺北市區監理所（含金門馬祖）"), ("40", "臺北區監理所（北宜花）"), ("50", "新竹區監理所（桃竹苗）")]
MVDIS_STATIONS = {
    "20": [("21", "士林監理站(臺北市士林區承德路5段80號)"), ("26", "金門監理站(金門縣金湖鎮黃海路六之一號)")],
    "40": [("40", "臺北區監理所(新北市樹林區中正路248巷7號)"), ("41", "板橋監理站(新北市中和區中山路三段116號)"),
           ("44", "花蓮監理站(花蓮縣吉安鄉中正路二段152號)"), ("45", "玉里監理分站(花蓮縣玉里鎮中華路427號)")],
    "50": [("50", "新竹區監理所(新竹縣新埔鎮文德路三段58號)"), ("52", "桃園監理站(桃園市介壽路416號)"),
           ("53", "中壢監理站(桃園縣中壢市延平路394號)")],
}
G_RETAKE = ("上午場次, 組別 1<br>本場次為<a>重考、吊註銷、持輕機駕照或完成講習</a>之考生<a><br> (初考生勿預約本場次)</a>"
            "<br>請先完成體檢，報到時間8:30-10:00前至61號窗口完成報到繳費,逾時不候。")
G_FIRST = ("上午場次, 組別 2<br>本場次為<a>初考生</a>，請先完成體檢<br><a>於9：00-9：20準時至B棟講習教室繳費報到參加2小時安全講習，"
           "逾時不候</a><br>講習完成後，於下午1：00開始筆路試。")
CAUTION = ("<div>預約考照貼心提醒注意事項：</div><ol><li>同時間可能多人申請預約，剩餘可預約人數請以系統即時訊息為準。</li>"
           "<li>完成畫面中，需顯示預約成功才算成功，請務必以「查詢已報名」功能檢查您的訂單是否成立！</li>"
           "<li>如您考照當天無法應考，請至遲於考照當日上午7:00前於系統「查詢已報名/取消報名」取消預約，以免被停權！</li></ol>"
           "<a href='#' class='std_btn' onclick='unblock();return false;'>我已充分知悉相關約定並願接受</a>")
HPN = ("請點選<span>前往危險感知平台</span>完成危險感知測驗並登錄測驗紀錄後，方能預約考照。<br>"
       "<a href='#'>前往危險感知平台</a> <a href='#'>查詢測驗紀錄</a> "
       "<a href='#' class='std_btn' onclick='unblock();return false;'>選擇場次繼續報名</a>")
# 跟 jQuery blockUI 一樣：一層蓋住整頁的遮罩（點不到底下的東西）＋中間的訊息框
BLOCK_JS = (
    "function unblock(){document.querySelectorAll('#blockOverlay,.blockMsg').forEach(function(e){"
    "var d=e.querySelector('#divOTPDialog');if(d){d.style.display='none';document.body.appendChild(d);}e.remove();});}"
    "function block(msg){unblock();var o=document.createElement('div');o.id='blockOverlay';"
    "o.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;z-index:1000;background:rgba(0,0,0,.3)';"
    "var m=document.createElement('div');m.className='blockUI blockMsg';"
    "m.style.cssText='position:fixed;top:20%;left:25%;width:50%;z-index:1011;background:#fff;padding:10px';"
    "if(typeof msg==='object'){msg.style.display='block';m.appendChild(msg);}else{m.innerHTML=msg||'作業處理中 ，請稍候...';}"
    "document.body.appendChild(o);document.body.appendChild(m);}"
)


# 跟 jQuery UI datepicker 一樣：日期欄位一取得焦點，就在正下方跳出月曆（蓋住「僅查周末」勾選框），
# 按 Esc 後要一點時間才收起來；點到月曆上的日子會改掉日期
DATEPICKER_JS = (
    "(function(){var ed=document.getElementById('expectExamDateStr');var dp=document.createElement('div');"
    "dp.id='ui-datepicker-div';dp.style.cssText='position:absolute;display:none;z-index:9999;background:#eef;width:320px';"
    "var h='';for(var i=1;i<=28;i++){h+='<a href=\"#\" style=\"display:inline-block;width:40px;height:30px\" "
    "onclick=\"pickDay('+i+');return false;\">'+i+'</a>';}dp.innerHTML=h;document.body.appendChild(dp);"
    "window.pickDay=function(d){ed.value='1151231';dp.style.display='none';};"
    "ed.addEventListener('focus',function(){var r=ed.getBoundingClientRect();dp.style.left=(r.left+scrollX)+'px';"
    "dp.style.top=(r.bottom+scrollY)+'px';dp.style.display='block';});"
    "ed.addEventListener('keydown',function(e){if(e.key==='Escape'){setTimeout(function(){dp.style.display='none';},400);}});})();"
)


def roc_text(d: dt.date) -> str:
    return f"{d.year - 1911}年{d.month}月{d.day}日 (星期{'一二三四五六日'[d.weekday()]})"


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
    layout: str = "table"            # table／div／mvdis（照真實監理服務網）
    captcha: bool = False
    captcha_code: str = "4821"
    captcha_wrong_first: bool = False
    wrong_times: int = 0             # 前幾次輸入的驗證碼是錯的
    inline_captcha_error: bool = False   # 驗證碼錯誤時寫在頁面上（不是跳 alert）
    rebind_captcha: bool = False     # 驗證碼錯誤後重新顯示的表單，驗證碼欄位還留著上次打的值
    autotype: str = ""               # 模擬真人輸入驗證碼：""／"full"（打滿）／"enter"（打完按 Enter）
    maintenance_gets: int = 0        # 前幾次打開查詢頁時回「系統維護中」（HTTP 503）
    submit_delay: float = 0          # 送出報名後伺服器過幾秒才回應
    prefilled: bool = False          # 報名頁的身分證／生日已經帶好、唯讀，按鈕是「同意並送出」
    confirm_text: str = "確定要報名嗎？"
    popup: bool = True
    onward: bool = True
    notice_page: bool = False
    notice_inline: bool = False      # 按報名後在同一頁（不換頁）蓋上注意事項，按同意才進報名頁
    confirm_page: bool = False
    birth_split: bool = False
    extra_required: bool = False     # 報名頁多一個程式認不得的必填下拉選單
    open_after: int = 0              # 第幾次查詢之後「初考」那組才有空位（模擬有人取消）
    full_on_click: bool = False      # 按報名時已經被搶走
    hazard_fail: bool = False
    otp: bool = False                # mvdis：送出後要輸入 OTP（寄到手機／信箱）
    otp_code: str = "5678"
    otp_autotype: bool = False       # mvdis：模擬真人收到 OTP 後輸入並按「報名」
    expected: dict = field(default_factory=dict)
    today: dt.date = field(default_factory=lambda: dt.datetime.now(TW).date())
    queries: list = field(default_factory=list)
    submissions: list = field(default_factory=list)
    signup_clicks: int = 0
    _captcha_fails: int = 0

    def __post_init__(self) -> None:
        if self.captcha_wrong_first and not self.wrong_times:
            self.wrong_times = 1
        self._gets = 0
        self._pending: dict = {}
        d = self.today
        if self.layout == "mvdis":
            sat = d + dt.timedelta(days=6 + (5 - (d + dt.timedelta(days=6)).weekday()) % 7)
            self.sessions = sorted([
                Sess("S1", d + dt.timedelta(days=5), G_RETAKE, 12),
                Sess("S2", d + dt.timedelta(days=5), G_FIRST, 0),
                Sess("S3", sat, G_FIRST, 0),          # 週六「初考」那組：有人取消後才有空位
                Sess("S5", sat, G_RETAKE, 0),         # 額滿：「(初考生勿預約本場次)」不能被當成報名鈕
                Sess("S4", d + dt.timedelta(days=13), G_FIRST, 0),
            ], key=lambda s: s.date)
            return
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
        inline = js = ""
        if self.notice_inline:
            inline = (f"<div id='inl' style='display:none'><h2>報名注意事項</h2><p>{NOTICE}</p>"
                      f"<form method='post' action='{P}/signupForm'><input type='hidden' name='secId'>"
                      "<button type='submit'>我已閱讀並同意</button></form></div>")
            js = ("function signUp(id){document.querySelector('table.list').style.display='none';"
                  "var n=document.getElementById('inl');n.style.display='block';n.querySelector('[name=secId]').value=id;}")
        return self.page("考照預約報名", self.query_form(v or {}) + results + inline +
                         f"<form id='sf' method='post' action='{P}/signup'><input type='hidden' name='secId'></form>",
                         self.QUERY_JS + js)

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

    def signup_form(self, sid: str, alert: str = "", inline: str = "", captcha_value: str = "") -> str:
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
               f"<input name='validateStr' id='validateStr' maxlength='4' autocomplete='off' value='{html.escape(captcha_value)}'>"
               "</td></tr>") if self.captcha else ""
        extra = ("<tr><th>考照原因 <span>*</span></th><td><select name='reason'><option value=''>請選擇</option>"
                 "<option value='1'>初考</option><option value='2'>重考</option></select></td></tr>") if self.extra_required else ""
        body = (f"<h2>填寫報考人資料</h2><table class='info'><tr><th>考試日期</th><td>{roc(s.date)}</td></tr>"
                f"<tr><th>場次組別</th><td>{html.escape(s.group)}</td></tr><tr><th>考試地點</th><td>板橋監理站</td></tr></table>"
                f"<p>{NOTICE} 報名成功後如欲取消，請於考照當日07:00前上網取消。</p>"
                f"{'<p class=err>' + html.escape(inline) + '</p>' if inline else ''}"
                f"<form id='f' method='post' action='{P}/submit'><input type='hidden' name='secId' value='{sid}'><table>"
                + ("<tr><th>身分證統一編號 ID No.</th><td><input name='idNo' readonly value='" + self.expected.get("idNo", "") + "'></td></tr>"
                   "<tr><th>出生日期 Date of Birth</th><td><input name='birthdayStr' readonly value='" + self.expected.get("birth", "") + "'></td></tr>"
                   if self.prefilled else
                   "<tr><th>身分證統一編號 ID No.<span>*</span></th><td><input name='idNo' maxlength='10'></td></tr>"
                   f"<tr><th>出生日期 Date of Birth<span>*</span></th><td>{birth}</td></tr>") +
                "<tr><th>姓名</th><td><input name='name'></td><th>聯絡電話</th><td><input name='tel'></td></tr>"
                f"<tr><th>電子郵件 E-mail</th><td><input name='email'></td></tr>{extra}{cap}</table>"
                "<label><input type='checkbox' name='agree' value='Y'> 我已詳閱並同意上述注意事項</label><br>"
                f"<button type='button' onclick='doSubmit()'>{'同意並送出' if self.prefilled else '確認送出'}</button> "
                "<button type='button' onclick='history.back()'>回上一頁</button>"
                "</form>")
        js = ("function doSubmit(){var f=document.getElementById('f');"
              "if(!f.idNo.value){alert('請輸入身分證統一編號');return;}"
              "if(!f.agree.checked){alert('請勾選同意注意事項');return;}"
              "if(confirm(" + json.dumps(self.confirm_text) + ")){f.submit();}}")
        if alert:
            js += f"alert({json.dumps(alert)});"
        if self.captcha and self.autotype:
            js += "setTimeout(" + self._autotype_fn() + ",1500);"
        return self.page("填寫報考人資料", body, js)

    def _autotype_fn(self) -> str:
        """模擬真人一個字一個字打驗證碼（autotype="enter" 時打完按 Enter）。"""
        code = "0000" if self._captcha_fails < self.wrong_times else self.captcha_code
        return ("function(){var el=document.getElementById('validateStr');el.value='';var c=" + json.dumps(code) + ";var i=0;"
                "var t=setInterval(function(){el.value+=c[i++];el.dispatchEvent(new Event('input',{bubbles:true}));"
                "if(i>=c.length){clearInterval(t);" +
                ("el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));" if self.autotype == "enter" else "") +
                "}},150);}")

    # ------------------------------------------------------------ layout="mvdis"
    def mvdis_locations(self, v: dict | None = None, rows: str = "", hpn: bool = False, alert: str = "") -> str:
        v = v or {}

        def opts(items, sel):
            return "<option value=''></option>" + "".join(
                f"<option value='{k}'{' selected' if k == sel else ''}>{html.escape(t)}</option>" for k, t in items)
        lic = opts([("3", "普通重型機車"), ("5", "普通輕型機車 (50cc 以下)"), ("A", "普通小型車")], v.get("licenseTypeCode"))
        off = opts(MVDIS_OFFICES, v.get("dmvNoLv1"))
        sta = opts(MVDIS_STATIONS.get(v.get("dmvNoLv1", ""), []), v.get("dmvNo"))
        wk = " checked" if v.get("onlyWeekend") == "true" else ""
        body = (f"<form id='form1' action='{P}/locations#anchor' method='post'>"
                "<input type='hidden' id='method' name='method' value=''>"
                "<input id='secDateStr' name='secDateStr' type='hidden' value=''><input id='secId' name='secId' type='hidden' value=''>"
                "<input id='divId' name='divId' type='hidden' value=''><table class='form_table2'><tbody>"
                f"<tr><th>報考照類 Type of Test：</th><td><select id='licenseTypeCode' name='licenseTypeCode' "
                f"onchange='trnTableClear();'>{lic}</select></td></tr>"
                "<tr><th>預計考試日期 Date of Test：</th><td><input id='expectExamDateStr' name='expectExamDateStr' type='text' "
                f"value='{html.escape(v.get('expectExamDateStr', ''))}'><br><span class='note'>例：民國78年7月2日，請填0780702</span><br>"
                f"<input id='onlyWeekend1' name='onlyWeekend' type='checkbox' value='true'{wk}>"
                "<input type='hidden' name='_onlyWeekend' value='on'> 僅查周末及假日場次 Test on Holiday</td></tr>"
                f"<tr><th>考試地點 Place of Test：</th><td><select id='dmvNoLv1' name='dmvNoLv1' onchange='dmvOnChange();'>{off}</select> "
                f"<select id='dmvNo' name='dmvNo' onchange='trnTableClear();'>{sta}</select></td></tr></tbody></table>"
                "<div align='center'><a href='#anchor' class='std_btn' onclick='query();'> 查詢場次 Search</a></div></form>"
                "<table id='trnTable'><thead><tr><th>考試日期 (星期) Date of Test</th><th>場次組別說明 Desc.</th>"
                f"<th>可報名人數 Number</th><th>功能 function</th></tr></thead><tbody>{rows}</tbody></table>"
                f"<p>{NOTICE}</p>")
        js = (BLOCK_JS +
              "function trnTableClear(){document.querySelector('#trnTable tbody').innerHTML='';}"
              "function dmvOnChange(){block('查詢監理所監理站');document.getElementById('method').value='dmvOnChange';"
              "document.getElementById('form1').submit();return false;}"
              "function query(){block();document.getElementById('method').value='query';document.getElementById('form1').submit();return false;}"
              "function preAdd(a,b,c){block();var f=document.getElementById('form1');f.secDateStr.value=a;f.secId.value=b;f.divId.value=c;"
              f"document.getElementById('method').value='preAdd';f.action='{P}/signUp';f.submit();return false;}}"
              + DATEPICKER_JS
              + (f"document.addEventListener('DOMContentLoaded',function(){{block({json.dumps(HPN)});}});" if hpn else "")
              + (f"alert({json.dumps(alert)});" if alert else ""))
        return self.page("考照預約報名", body, js)

    def mvdis_rows(self, v: dict) -> str:
        d = parse_roc(v.get("expectExamDateStr", ""))
        weekend = v.get("onlyWeekend") == "true"
        out = []
        for s in self.sessions:
            if not d or s.date < d or (weekend and s.date.weekday() < 5):
                continue
            n = self.seats(s)
            act = (f"<a href='#anchor' class='std_btn' onclick=\"preAdd('{s.date.isoformat()}','{s.sid}','1')\">報名 SignUp</a>"
                   if n > 0 else "")
            out.append(f"<tr><td class='align_c'>{roc_text(s.date)}</td><td class='align_c'>{s.group}</td>"
                       f"<td class='align_c'>{n if n > 0 else '額滿'}</td><td class='align_c'>{act}</td></tr>")
        return "".join(out)

    def mvdis_signup(self, sid: str, v: dict | None = None, otp: bool = False, alert: str = "") -> str:
        s = next(x for x in self.sessions if x.sid == sid)
        v = v or {}

        def val(k):
            return html.escape(v.get(k, ""))
        body = (f"<div class='caption_std'>您所選擇的場次</div><table class='form_table2'>"
                f"<tr><th>考試日期 (星期) Date of Test：</th><td>{roc_text(s.date)}</td></tr>"
                f"<tr><th>場次組別說明 Desc.：</th><td>{html.escape(re.sub(r'<[^>]+>', ' ', s.group))}</td></tr>"
                "<tr><th>考試地點 Place of Test：</th><td>板橋監理站</td></tr></table>"
                f"<form id='form1' action='{P}/signUp#anchor' method='post'><input type='hidden' id='method' name='method' value=''>"
                f"<input id='secDateStr' name='secDateStr' type='hidden' value='{s.date.isoformat()}'>"
                f"<input id='secId' name='secId' type='hidden' value='{sid}'><input id='divId' name='divId' type='hidden' value='1'>"
                "<input id='dmvNo' name='dmvNo' type='hidden' value='41'><input id='licenseTypeCode' name='licenseTypeCode' type='hidden' value='3'>"
                "<input id='otp' name='otp' type='hidden' value=''><div class='caption_std'>報考人資料</div><table class='form_table2'><tbody>"
                f"<tr><th>身分證字號 ID No.：</th><td><input id='idNo' name='idNo' type='text' maxlength='10' value='{val('idNo')}'></td></tr>"
                "<tr><th>出生年月日 Birthday：</th><td><input type='text' name='birthdayStr' id='birthdayStr' maxlength='7' "
                f"value='{val('birthdayStr')}'><br><span class='note'>例：民國78年7月2日，請填0780702</span></td></tr>"
                f"<tr><th>姓名 Name：</th><td><input id='name' name='name' type='text' maxlength='10' value='{val('name')}'></td></tr>"
                "<tr><th>聯絡電話/手機 Phone No.：</th><td><input id='contactTel' name='contactTel' type='text' maxlength='20' "
                f"value='{val('contactTel')}'></td></tr>"
                f"<tr><th>Email：</th><td><input id='email' name='email' type='text' maxlength='50' value='{val('email')}'></td></tr>"
                "<tr><th><label for='txtVerify'>驗證碼：</label></th><td><input type='text' name='validateStr' id='validateStr' size='8'>"
                "<img id='pickimg1' alt='驗證碼' src='/m3-emv-trn/captchaImg.jpg'> <a href='#' onclick='return false;'>換一張</a></td></tr>"
                "<tr><td colspan='2'><a href='#anchor' class='std_btn' onclick='add()'> 報名 SignUp</a> "
                f"<a href='{P}/locations'>回「查詢場次 Reinquiry」</a></td></tr></tbody></table></form>"
                "<div id='divOTPDialog' style='display:none'>請輸入OTP驗證碼完成報名：<input id='otpInput' maxlength='4' type='text'> "
                "<a href='#' class='std_btn' onclick='goOTP();return false;'>報名</a>&nbsp;&nbsp;"
                "<a href='#' onclick='unblock();return false;'>取消此次報名</a></div>")
        js = (BLOCK_JS +
              "function add(){block();document.getElementById('method').value='add';document.getElementById('form1').submit();return false;}"
              "function goOTP(){var f=document.getElementById('form1');f.otp.value=document.getElementById('otpInput').value;"
              "document.getElementById('method').value='addOTP';f.submit();return false;}")
        if otp:
            js += "document.addEventListener('DOMContentLoaded',function(){block(document.getElementById('divOTPDialog'));});"
            if self.otp_autotype:
                js += ("setTimeout(function(){document.getElementById('otpInput').value=" + json.dumps(self.otp_code) +
                       ";goOTP();},3000);")
        else:
            js += f"document.addEventListener('DOMContentLoaded',function(){{block({json.dumps(CAUTION)});}});"
            if self.autotype:
                # 真人是在游標停到驗證碼欄位之後才開始打字
                js += ("document.getElementById('validateStr').addEventListener('focus',function(){setTimeout("
                       + self._autotype_fn() + ",500);},{once:true});")
        if alert:
            js += f"alert({json.dumps(alert)});"
        return self.page("考照預約報名", body, js)

    def mvdis_post_signup(self, v: dict) -> str:
        sid, method = v.get("secId", ""), v.get("method")
        s = next((x for x in self.sessions if x.sid == sid), None)
        if method == "preAdd":
            self.signup_clicks += 1
            if s is None or self.seats(s) <= 0 or self.full_on_click:
                return self.mvdis_locations(alert="此場次名額已滿，請選擇其他場次")
            return self.mvdis_signup(sid)
        keep = {k: v[k] for k in ("idNo", "birthdayStr", "name", "contactTel", "email") if k in v}
        if method == "add":
            if v.get("validateStr", "") != self.captcha_code:
                self._captcha_fails += 1
                return self.mvdis_signup(sid, keep, alert="驗證碼輸入錯誤，請重新輸入")
            err = self.check(v)
            if err:
                return self.page("報名失敗", f"<h2>報名失敗</h2><p>{html.escape(err)}</p>")
            if self.hazard_fail:
                return self.page("報名失敗", "<h2>報名失敗</h2><p>查無有效的(機車)危險感知體驗紀錄，請完成體驗後再報名。</p>")
            if self.otp:
                self._pending = dict(v)
                return self.mvdis_signup(sid, keep, otp=True)
        elif method == "addOTP":
            if v.get("otp") != self.otp_code:
                return self.mvdis_signup(sid, keep, otp=True, alert="OTP驗證碼錯誤")
            v = {**v, **self._pending, "otp": v.get("otp")}     # 驗證碼記第一次送出時的（OTP 那頁欄位已清空）
        else:
            return self.mvdis_signup(sid, keep)
        self.submissions.append(dict(v))
        s.seats = max(0, self.seats(s) - 1)
        return self.page("報名結果", f"<h2>報名成功</h2><p>考試日期：{roc_text(s.date)}</p>"
                         "<p>如欲取消，請於考照當日07:00前至監理服務網取消。</p>")

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
        got = {"idNo": v.get("idNo"), "birth": birth, "name": v.get("name"), "tel": v.get("tel", v.get("contactTel")),
               "email": v.get("email")}
        for k, want in e.items():
            if got.get(k) != want:
                return f"{k} 不對：{got.get(k)!r}"
        if self.layout != "mvdis" and v.get("agree") != "Y":
            return "沒有勾選同意"
        if self.extra_required and not v.get("reason"):
            return "請選擇考照原因"
        return None

    def submit(self, v: dict) -> str:
        sid = v.get("secId", "")
        if self.submit_delay:
            time.sleep(self.submit_delay)
        if self.captcha:
            wrong = v.get("validateStr") != self.captcha_code
            if wrong:
                self._captcha_fails += 1
                msg = "驗證碼輸入錯誤，請重新輸入"
                keep = v.get("validateStr", "") if self.rebind_captcha else ""
                if self.inline_captcha_error:
                    return self.signup_form(sid, inline=msg, captcha_value=keep)
                return self.signup_form(sid, alert=msg, captcha_value=keep)
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
                m._gets += 1
                if m._gets <= m.maintenance_gets:
                    return self._send(m.page("系統維護", "<h1>系統維護中，請稍後再試</h1>"), code=503)
                return self._send(m.mvdis_locations() if m.layout == "mvdis" else m.locations())
            if u.path == f"{P}/stations":
                return self._send(json.dumps(STATIONS.get(q.get("office", ""), []), ensure_ascii=False), "application/json")
            if u.path in ("/m3-emv-trn/captcha.jpg", "/m3-emv-trn/captchaImg.jpg"):
                gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                       b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
                return self._send(gif, "image/gif")
            return self._send("not found", code=404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            v = {k: x[0] for k, x in parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True).items()}
            path = urlsplit(self.path).path
            if m.layout == "mvdis" and path == f"{P}/locations":
                if v.get("method") == "dmvOnChange":
                    time.sleep(0.4)       # 真實網站整頁重新載入要一點時間
                    return self._send(m.mvdis_locations(v))
                if v.get("method") != "query":
                    return self._send(m.mvdis_locations(v))
                m.queries.append(v)
                if not v.get("dmvNo"):
                    return self._send(m.mvdis_locations(v, alert="請選擇考試地點"))
                return self._send(m.mvdis_locations(v, m.mvdis_rows(v), hpn=True))
            if m.layout == "mvdis" and path == f"{P}/signUp":
                return self._send(m.mvdis_post_signup(v))
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
