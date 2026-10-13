"""每天自動跑的流程裡幾個容易出錯的小函式（PR 時 .github/workflows/ci.yml 會跑）。不連網、不讀 data/ 的真資料。"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from screener import health, ledger_nav, revdrift, twcal


# ---------------------------------------------------------------- 交易日曆
def test_twcal_parse_formats():
    rwd = {"fields": ["名稱", "日期", "星期", "說明"], "data": [
        ["國曆新年開始交易日", "115/01/02", "五", "國曆新年開始交易"],
        ["中華民國開國紀念日", "115/01/01", "四", "依規定放假1日"],
        ["市場無交易，僅辦理結算交割作業", "115/02/11", "三", ""],
        ["國慶日", "115/10/10", "六", ""]]}                       # 週六不列
    assert twcal.parse(rwd) == ["2026-01-01", "2026-02-11"]
    iso = {"fields": ["日期", "名稱", "說明"], "data": [["2026-10-09", "國慶日補假", ""], ["2026-01-02", "開始交易日", ""]]}
    assert twcal.parse(iso) == ["2026-10-09"]
    # 10/14 用 probe.yml 抓到的真格式（rwd）：日期在第一欄、名稱第二欄
    real = {"stat": "ok", "fields": ["日期", "名稱", "說明"], "data": [
        ["2026-02-11", "農曆春節前最後交易日", "農曆春節前最後交易。\r\n"], ["2026-02-12", "市場無交易，僅辦理結算交割作業", ""],
        ["2026-02-15", "農曆除夕及春節", "依規定於2月15日至2月19日放假5日。"], ["2026-02-16", "農曆除夕及春節", ""],
        ["2026-10-25", "臺灣光復暨金門古寧頭大捷紀念日", "依規定放假1日。"],
        ["2026-10-26", "臺灣光復暨金門古寧頭大捷紀念日", "10月25日適逢星期日，於10月26日（星期一）補假。"]]}
    assert twcal.parse(real) == ["2026-02-12", "2026-02-16", "2026-10-26"]
    assert twcal.parse({"stat": "ok", "fields": ["日期", "名稱", "說明"], "data": []}) == []   # 明年的表還沒出
    openapi = [{"Name": "國慶日", "Date": "1151009", "Weekday": "五", "Description": ""},
               {"Name": "農曆春節前最後交易日", "Date": "1150211", "Weekday": "三", "Description": ""}]
    assert twcal.parse(openapi) == ["2026-10-09"]


def test_twcal_future_days_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(twcal, "CACHE", tmp_path / "tw_holidays.json")
    days, approx = twcal.future_days("2026-10-13", 20, fetch=False)
    assert approx and "2026-10-26" not in days and days[-1] == "2026-11-11"   # 10/26 光復節補假（推定）
    (tmp_path / "tw_holidays.json").write_text(json.dumps({"years": {"2026": ["2026-10-26"], "2027": []}}), "utf-8")
    days, approx = twcal.future_days("2026-10-13", 20, fetch=False)
    assert not approx and days[-1] == "2026-11-11"


def test_twcal_fetch_next_year_not_published(monkeypatch):
    """明年的表還沒公布：只問 rwd 一次（openapi 只有今年、不問），同一次執行不重抓。"""
    import requests
    calls = []

    class R:
        def __init__(self, j):
            self.j = j

        def json(self):
            return self.j

    def get(url, params=None, **kw):
        calls.append(url)
        return R({"stat": "ok", "fields": ["日期", "名稱", "說明"], "data": []})

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(twcal, "_MISS", set())
    nxt = twcal.dt.date.today().year + 1
    assert twcal._fetch(nxt) is None and len(calls) == 1 and "openapi" not in calls[0]
    assert twcal._fetch(nxt) is None and len(calls) == 1
    # 今年的表：rwd 抓不到才換 openapi
    assert twcal._fetch(nxt - 1) is None and len(calls) == 3 and "openapi" in calls[2]


# ---------------------------------------------------------------- revdrift 批次日／申報期限
def test_revdrift_deadline_and_batch_day():
    days = ["2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14",
            "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    # 10/10 週六 → 期限順延到 10/12，期限隔天 10/13 進場
    assert days[revdrift._deadline("2026-09", days)] == "2026-10-12"
    assert days[revdrift._batch_day("2026-09", days)] == "2026-10-13"
    # 11/10 週二 → 期限 11/10；11 日之後第一個交易日 11/12 比較晚
    assert days[revdrift._deadline("2026-10", days)] == "2026-11-10"
    assert days[revdrift._batch_day("2026-10", days)] == "2026-11-12"
    # 資料還沒到批次日 → None
    assert revdrift._batch_day("2026-09", days[:3]) is None


# ---------------------------------------------------------------- 系統體檢
def test_health_last_us_day():
    assert health.last_us_day(dt.date(2026, 10, 13)) == "2026-10-12"   # 週二早上看週一（哥倫布日美股有開）
    assert health.last_us_day(dt.date(2026, 10, 12)) == "2026-10-09"   # 週一早上看上週五
    assert health.last_us_day(dt.date(2026, 11, 27)) == "2026-11-25"   # 感恩節休市


def test_health_checks_reads_files(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DATA", tmp_path)
    monkeypatch.setattr(health, "REF", None)
    (tmp_path / "extras").mkdir()
    (tmp_path / "us").mkdir()
    (tmp_path / "macro").mkdir()
    pd.DataFrame({"date": ["2026-10-08", "2026-10-12"], "code": ["2330", "2330"]}).to_csv(tmp_path / "history.csv.gz", index=False)
    pd.DataFrame({"date": ["2026-10-12", "2026-10-12"], "market": ["TWSE", "TPEX"]}).to_csv(
        tmp_path / "extras" / "oddlot_hist.csv.gz", index=False)
    (tmp_path / "us" / "meta.json").write_text(json.dumps({"sox_date": "2026-10-09"}), "utf-8")
    items = {i["name"]: i for i in health.checks("2026-10-12", "2026-10-12")}
    assert items["每日資料（收盤價）"]["ok"]
    assert items["盤後零股（上市）"]["ok"] and items["盤後零股（上櫃）"]["ok"]
    assert not items["美股隔夜（費半）"]["ok"] and items["美股隔夜（費半）"]["fix"] == "us"
    assert not items["三大法人"]["ok"] and items["三大法人"]["have"] == "—"       # 檔案不存在
    assert not items["虛擬帳戶帳本"]["ok"] and items["虛擬帳戶帳本"]["fix"] is None
    (tmp_path / "ledger").mkdir()
    (tmp_path / "ledger" / "2026-10-12.json").write_text("{}", "utf-8")
    items = {i["name"]: i for i in health.checks("2026-10-12", "2026-10-12")}
    assert items["虛擬帳戶帳本"]["ok"]
    # 日期到了、但只抓到一半（平常 100 筆，那天 40 筆）→ 不算到齊，交給傍晚補抓重抓
    rows = [(d, f"{1000 + i}") for d in ["2026-10-06", "2026-10-07", "2026-10-08"] for i in range(100)]
    rows += [("2026-10-12", f"{1000 + i}") for i in range(40)]
    pd.DataFrame(rows, columns=["date", "code"]).to_csv(tmp_path / "extras" / "inst_hist.csv.gz", index=False)
    pd.DataFrame(rows[:300] + [("2026-10-12", f"{1000 + i}") for i in range(95)], columns=["date", "code"]).to_csv(
        tmp_path / "extras" / "margin_hist.csv.gz", index=False)
    items = {i["name"]: i for i in health.checks("2026-10-12", "2026-10-12")}
    assert not items["三大法人"]["ok"] and "只有 40 筆" in items["三大法人"]["have"] and items["三大法人"]["fix"] == "extras"
    assert items["融資融券"]["ok"]                                                   # 95 筆＝平常的 95%，算正常


# ---------------------------------------------------------------- 虛擬帳戶拆桶
def _lg(date, nav, positions, cash, trades=(), cbb=None, bench=0.0):
    eq = sum(p["shares"] * p["close"] for p in positions) + cash
    return {"date": date, "nav": nav, "equity": eq, "cash": cash, "cash_by_bucket": cbb, "bench_tr_cum": bench,
            "positions": [dict(p, mv=p["shares"] * p["close"]) for p in positions], "trades": list(trades)}


def test_ledger_nav_buckets_and_transfer():
    a0 = {"code": "2330", "role": "core", "shares": 10, "close": 100.0}
    e0 = {"code": "0050", "role": "etf", "shares": 100, "close": 50.0}
    L = [
        # 第 1 天：10 萬現金，收盤買 2330 10 股＠100（費 1）、0050 100 股＠50（費 1）
        _lg("2026-10-01", 0.99998, [a0, e0], 100000 - 1001 - 5001,
            trades=[{"bucket": "core", "code": "2330", "side": "buy", "shares": 10, "price": 100, "fee": 1, "tax": 0},
                    {"bucket": "etf", "code": "0050", "side": "buy", "shares": 100, "price": 50, "fee": 1, "tax": 0}]),
        # 第 2 天：2330 漲 10%、0050 不動；剩下的現金分給各桶
        _lg("2026-10-02", None, [dict(a0, close=110.0), e0], 93998,
            cbb={"core": 0, "etf": 93998}, bench=1.0),
        # 第 3 天：賣 0050 40 股＠50（費 1 稅 2）撥給 revdrift 現金
        _lg("2026-10-05", None, [dict(a0, close=110.0), dict(e0, shares=60)], 93998 + 1997,
            trades=[{"bucket": "etf", "code": "0050", "side": "sell", "shares": 40, "price": 50, "fee": 1, "tax": 2}],
            cbb={"core": 0, "etf": 93998, "revdrift": 1997}, bench=1.0),
    ]
    r = ledger_nav.compute(L)
    assert r["ok"] and r["resid_max"] < 0.01
    b = r["buckets"]
    assert abs(b["core"]["ret"] - ((1100 - 1001) / 1001 * 100)) < 0.01      # 含買進手續費：+9.89%
    assert abs(b["etf"]["pnl"] - (-1 - 3)) < 0.01                            # 只有費稅
    assert b["revdrift"]["start"] == "2026-10-05" and abs(b["revdrift"]["value"] - 1997) < 0.01
    assert abs(b["revdrift"]["ret"]) < 1e-9                                   # 撥款不算損益


def test_ledger_nav_flags_missing_trade():
    a0 = {"code": "2330", "role": "core", "shares": 10, "close": 100.0}
    L = [_lg("2026-10-01", 1.0, [], 100000),
         _lg("2026-10-02", None, [a0], 100000 - 1001)]          # 買了卻沒記 trades
    r = ledger_nav.compute(L)
    assert any("股數對不上" in w for w in r["warnings"])


# ---------------------------------------------------------------- 盤中零股五檔（MIS odd=1，10/14 probe 的真資料）
def test_live_odd_rows(tmp_path, monkeypatch):
    """盤中零股：要打 getOddInfo.jsp（getStockInfo 加 odd=1 回的是整股，#269 用錯）。樣本是 10/13 收盤台積電的真回應。"""
    from screener import live
    odd = {"c": "2330", "t": "13:30:00", "z": "2500.0000", "pz": "2515.0000", "v": "6128251",
           "a": "2505.0000_2510.0000_2515.0000_2520.0000_2525.0000_", "b": "2500.0000_2495.0000_2490.0000_2485.0000_2480.0000_",
           "f": "11610_37827_27194_11709_6758_", "g": "14632_606122_997638_261606_467088_"}
    msg = [odd, {"c": "6789", "t": "13:12:00", "z": "-", "pz": "88.5", "v": "0", "a": "-", "b": "88.5000_", "f": "-", "g": "12000_"}]
    r = live.odd_rows(msg)
    assert r[0]["odd_price"] == 2500 and r[0]["odd_vol_sh"] == 6128251 and r[0]["odd_bid1"] == 2500 and r[0]["odd_bid1_sh"] == 14632
    assert r[0]["odd_bid5_sh"] == 14632 + 606122 + 997638 + 261606 + 467088 and r[0]["odd_ask5_sh"] == 11610 + 37827 + 27194 + 11709 + 6758
    assert r[1]["odd_ask1"] is None and r[1]["odd_ask5_sh"] == 0 and r[1]["odd_bid1_sh"] == 12000 and r[1]["odd_price"] == 88.5   # 鎖漲停：沒人賣

    # save_odd_book 打的是零股端點、照 data/book/日期.csv 那批股票
    monkeypatch.setattr(live, "ROOT", tmp_path)
    (tmp_path / "data" / "book").mkdir(parents=True)
    pd.DataFrame({"code": ["2330"]}).to_csv(tmp_path / "data" / "book" / "2026-10-14.csv", index=False)
    seen = []

    class S:
        headers = {}

        def get(self, url, params=None, timeout=None):
            seen.append((url, params))
            return type("R", (), {"json": lambda self: {"msgArray": [odd]}})()

    monkeypatch.setattr(live.requests, "Session", S)
    live.save_odd_book("2026-10-14", "1312", pd.DataFrame({"code": ["2330", "2317"], "market": ["TWSE", "TWSE"]}))
    assert seen[0][0].endswith("/getOddInfo.jsp") and seen[0][1]["ex_ch"] == "tse_2330.tw" and "odd" not in seen[0][1]
    out = pd.read_csv(tmp_path / "data" / "book" / "odd_2026-10-14.csv", dtype={"code": str})
    assert list(out.code) == ["2330"] and out.odd_vol_sh[0] == 6128251 and out.when[0] == 1312


# ---------------------------------------------------------------- 每日信的聯一光型段落
def test_momo_today_md(tmp_path, monkeypatch):
    from screener import momo
    f = tmp_path / "momo_recent.csv"
    pd.DataFrame({"rule": ["base", "cont", "cont"], "date": ["2026-10-13", "2026-10-13", "2026-10-12"],
                  "code": ["1312", "3406", "6834"], "sig_close": [17.65, 1175, 145]}).to_csv(f, index=False)
    monkeypatch.setattr(momo, "RECENT", f)
    md = momo.today_md("2026-10-13", {"3406": "玉晶光", "1312": "國喬"})
    assert md.index("強勢續攻：3406 玉晶光") < md.index("低檔爆量突破：1312 國喬") and "6834" not in md
    assert momo.today_md("2026-10-14") == ""


# ---------------------------------------------------------------- revdrift 預告：申報期限過後營收補抓過才算「明天開盤前名單」
def test_revdrift_preview_settled(monkeypatch, tmp_path):
    monkeypatch.setattr(twcal, "CACHE", tmp_path / "tw_holidays.json")
    yms = [f"{y}-{m:02d}" for y, m in [(2025, 9), (2025, 10), (2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 10)]]
    rows = [{"code": c, "ym": ym, "revenue": 100 + i * 10, "yoy": 50.0, "mom": 1.0, "ann_date": None}
            for c in ("1101", "1102") for i, ym in enumerate(yms)]
    rev = pd.DataFrame(rows)
    days = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12"]
    hist = pd.DataFrame([{"date": d, "code": c, "close": 50.0} for d in days for c in ("1101", "1102")])

    def run(seen):
        rev.loc[rev.ym == "2026-09", "ann_date"] = seen
        return revdrift.preview(hist, rev, fetch_cal=False)
    early = run("2026-10-12")          # 10/12 晚上：期限當天才看到 → 還會有人公布
    assert early["batch_date"] == "2026-10-13" and early["deadline"] == "2026-10-12"
    assert early["next_day"] and not early["settled"] and not early["exact"]
    late = run("2026-10-13")           # 10/13 半夜補抓：期限隔天看到的 → 齊了
    assert late["settled"] and late["exact"]


# ---------------------------------------------------------------- 漲停價：四個地方各算一次，要一模一樣
def test_limit_up_price_consistent():
    import numpy as np
    from screener import live, positions, rules, stockpage
    prevs = np.round(np.concatenate([np.arange(1, 12, 0.01), np.arange(12, 120, 0.05), np.arange(120, 1100, 0.5),
                                     np.arange(1100, 3000, 1.0), [9.09, 9.1, 45.45, 45.5, 90.9, 91.0, 454.5, 455.0, 909.0, 910.0]]), 2)
    r = rules.limit_price(pd.DataFrame({"x": prevs}))["x"].round(2).to_numpy()
    lv = live._lu_price(pd.Series(prevs)).to_numpy()
    sp = np.array([stockpage.limit_up(p) for p in prevs])
    assert np.allclose(r, lv) and np.allclose(r, sp)
    assert all(positions._limit_up(lu, p) and not positions._limit_up(lu - stockpage._tick(lu), p) for p, lu in zip(prevs[::37], r[::37]))
    assert stockpage.limit_up(100) == 110 and stockpage.limit_up(45.5) == 50.0 and stockpage.limit_up(9.1) == 10.0
    assert stockpage.limit_up(2490) == 2735 and stockpage.limit_up(25.55) == 28.1


# ---------------------------------------------------------------- .gz 檔內容沒變就要一模一樣（git 才不會白存一份）
def test_gzip_csv_deterministic(tmp_path):
    import time
    import screener  # noqa: F401  （import 時裝好 to_csv 的固定檔頭）
    df = pd.DataFrame({"date": ["2026-10-13"] * 3, "code": ["2330", "2317", "3041"], "close": [2490, 240, 25.55]})
    p = tmp_path / "h.csv.gz"
    for kw in ({}, {"compression": "gzip"}, {"compression": {"method": "gzip", "compresslevel": 9}}):
        df.to_csv(p, index=False, **kw)
        a = p.read_bytes()
        time.sleep(1.05)
        df.to_csv(str(p), index=False, **kw)
        assert p.read_bytes() == a, kw
    assert pd.read_csv(p, dtype={"code": str}).equals(df.astype({"code": str}))
    s = tmp_path / "s.csv.gz"
    df.set_index("code")["close"].to_csv(s)                   # Series 也一樣
    assert s.read_bytes()[4:8] == b"\0\0\0\0"                 # gzip 檔頭第 4～7 byte 是時間
    # 預設不壓縮（git 才能存差異）：原文直接看得到；自己指定 compresslevel 的照它的
    df.to_csv(p, index=False)
    assert b"2330,2490" in p.read_bytes()
    df.to_csv(p, index=False, compression={"method": "gzip", "compresslevel": 9})
    assert b"2330,2490" not in p.read_bytes()
    # 寫完太大的（5 年檔）改回一般壓縮
    big = pd.concat([df] * 2000, ignore_index=True)
    big.to_csv(p, index=False)
    raw = p.stat().st_size
    import screener as sc
    old = sc.BIG
    try:
        sc.BIG = raw // 2
        big.to_csv(p, index=False)
        assert p.stat().st_size < raw // 5
        assert pd.read_csv(p).shape == big.shape
    finally:
        sc.BIG = old
    q = tmp_path / "plain.csv"
    df.to_csv(q, index=False)
    assert q.read_text().startswith("date,code,close")       # 一般 CSV 不受影響


# ---------------------------------------------------------------- 系統訊號出場：量縮到爆量日一半以下隔天開盤賣，漲停那天不算
def test_positions_exit_rules(tmp_path, monkeypatch):
    from screener import positions
    monkeypatch.setattr(positions, "FILE", tmp_path / "positions.csv")
    positions.add_signals([{"code": "1111", "name": "甲", "signal_date": "2026-10-01", "alert_price": 100.0},
                           {"code": "2222", "name": "乙", "signal_date": "2026-10-01", "alert_price": 50.0}])
    rows = []
    #            日期          1111 收／量／開          2222 收／量／開
    for d, a, b in [("2026-09-30", (95, 3000, 95), (45.5, 2000, 45)),
                    ("2026-10-01", (100, 10000, 96), (50.0, 8000, 46)),
                    ("2026-10-02", (102, 6000, 101), (55.0, 3000, 51)),     # 乙量縮到 37.5% 但收漲停（50→55）→ 續抱
                    ("2026-10-05", (105, 4000, 104), (54.0, 3000, 55)),     # 甲 40% → 出場；乙 37.5% → 出場
                    ("2026-10-06", (99, 5000, 103), (53.0, 3000, 53.5))]:
        rows += [{"date": d, "code": "1111", "close": a[0], "volume": a[1], "open": a[2]},
                 {"date": d, "code": "2222", "close": b[0], "volume": b[1], "open": b[2]}]
    hist = pd.DataFrame(rows)
    for d in ["2026-10-01", "2026-10-02"]:
        ex, hold = positions.update(hist, d)
        assert ex == [] and len(hold) == 2
    ex, hold = positions.update(hist, "2026-10-05")
    assert sorted(e["code"] for e in ex) == ["1111", "2222"] and hold == []
    assert all(e["exit_reason"].startswith("量縮至爆量日") for e in ex)
    positions.update(hist, "2026-10-06")                                   # 隔天補開盤價、報酬改用開盤價算
    p = positions.load().set_index("code")
    assert p.at["1111", "exit_open"] == 103 and p.at["1111", "est_return_pct"] == 3.0
    assert p.at["2222", "exit_open"] == 53.5 and p.at["2222", "est_return_pct"] == 7.0
    assert p.at["1111", "close_lu"] == 0 and p.at["2222", "close_lu"] == 1          # 50 是前收 45.5 的漲停價


def test_health_run_heal_and_notify(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo
    from screener import notify
    monkeypatch.setattr(health, "DATA", tmp_path)
    monkeypatch.setattr(health, "OUT", tmp_path / "health" / "latest.json")
    monkeypatch.setattr(health, "HIST", tmp_path / "health" / "history.jsonl")
    monkeypatch.setattr(health, "REF", None)
    monkeypatch.setattr(health, "twse_days", lambda t: ["2026-10-08", "2026-10-12", "2026-10-13"])
    monkeypatch.setattr(health, "failed_runs", lambda hours=20: [])
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)   # 不在 Actions 裡 → 不看 cron-job.org
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)   # 也不查 repo 大小
    sent, mails = [], []
    monkeypatch.setattr(health, "dispatch", lambda f, D: sent.append((f, D)) or True)
    monkeypatch.setattr(notify, "send", lambda s, b: mails.append(s))
    (tmp_path / "extras").mkdir()
    (tmp_path / "ledger").mkdir()
    pd.DataFrame({"date": ["2026-10-12"], "code": ["2330"]}).to_csv(tmp_path / "history.csv.gz", index=False)
    am = dt.datetime(2026, 10, 13, 8, 40, tzinfo=ZoneInfo("Asia/Taipei"))
    r = health.run("am", heal=True, notify=True, now=am)
    assert r["trading_day"] == "2026-10-12"
    assert sorted(f for f, _ in sent) == ["daily", "extras", "us"] and all(d == "2026-10-12" for _, d in sent)
    assert len(mails) == 1 and "要看" in mails[0]                # 鎖漲停家數、帳本補跑救不了 → 寄一封
    again = health.run("am", heal=True, notify=True, now=am)    # 同一班不重跑
    assert again["checked_at"] == r["checked_at"] and len(sent) == 3 and len(mails) == 1
    hist = [json.loads(x) for x in (tmp_path / "health" / "history.jsonl").read_text("utf-8").splitlines()]
    assert len(hist) == 1 and hist[0]["slot"] == "am" and "13:12 鎖漲停家數" in hist[0]["bad"]   # 跳過的那次不寫


def test_health_wait_dispatched(tmp_path, monkeypatch):
    """早上補跑的等跑完再開始盤中監控：跑完就不等、剛派出去還沒出現在列表的先等一下、最晚等到 08:55。"""
    import datetime as dt
    from zoneinfo import ZoneInfo
    out = tmp_path / "latest.json"
    monkeypatch.setattr(health, "OUT", out)
    t = [dt.datetime(2026, 10, 14, 8, 41, tzinfo=ZoneInfo("Asia/Taipei"))]
    clock, nap = (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + dt.timedelta(seconds=s)))
    out.write_text(json.dumps({"date": "2026-10-14", "dispatched": ["extras", "us"]}), "utf-8")
    # 美股 1 分鐘後才出現在列表、3 分鐘跑完；傍晚補抓 5 分鐘跑完
    busy = {"us.yml": lambda m: 1 <= m < 3, "extras_evening.yml": lambda m: m < 5}
    monkeypatch.setattr(health, "_busy", lambda w: busy[w]((t[0] - dt.datetime(2026, 10, 14, 8, 41, tzinfo=ZoneInfo("Asia/Taipei"))).total_seconds() / 60))
    assert health.wait_dispatched("08:55", now=clock, sleep=nap) == []
    assert dt.time(8, 46) <= t[0].time() < dt.time(8, 47)
    # 一直沒跑完 → 等到 08:55 就放棄
    t[0] = dt.datetime(2026, 10, 14, 8, 41, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(health, "_busy", lambda w: True)
    assert health.wait_dispatched("08:55", now=clock, sleep=nap) == ["extras_evening.yml", "us.yml"]
    assert t[0].time() >= dt.time(8, 55)
    # 不是今天的體檢、沒派工 → 不等
    t[0] = dt.datetime(2026, 10, 15, 8, 41, tzinfo=ZoneInfo("Asia/Taipei"))
    assert health.wait_dispatched("08:55", now=clock, sleep=nap) == [] and t[0].time() == dt.time(8, 41)


def test_health_cron_check(tmp_path, monkeypatch):
    """cron-job.org 沒準時叫：今天 08:40 沒有 workflow_dispatch 的盤中監控、上一個交易日 15:20 沒有 workflow_dispatch 的每日資料；
    連兩天才寄信。08:40 沒叫到的日子沒有早上體檢，收工後體檢（GitHub 備援班 14 點後）自己看。"""
    import datetime as dt
    import subprocess
    from zoneinfo import ZoneInfo
    from screener import notify

    runs = {"daily.yml": [{"event": "workflow_dispatch", "createdAt": "2026-10-13T07:20:31Z"},   # 台北 15:20 → 有叫
                          {"event": "schedule", "createdAt": "2026-10-13T09:58:00Z"},
                          {"event": "workflow_dispatch", "createdAt": "2026-10-12T00:41:00Z"}],  # 早上補跑的，不算
            "live.yml": [{"event": "workflow_dispatch", "createdAt": "2026-10-14T00:40:05Z"},    # 台北 10/14 08:40
                         {"event": "schedule", "createdAt": "2026-10-13T06:14:00Z"}]}
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, json.dumps(runs[cmd[4]]), ""))
    now = dt.datetime(2026, 10, 14, 8, 41, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    assert health.cron_check("2026-10-13", now) is None                     # 不在 Actions 裡不判斷
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert health.cron_check("2026-10-13", now) == []
    assert health.cron_check("2026-10-12", now) == ["每日資料 10-12 15:20 那班沒叫到"]
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    pm = dt.datetime(2026, 10, 15, 14, 14, tzinfo=ZoneInfo("Asia/Taipei"))  # 10/15 08:40 沒叫到
    assert health.cron_check("2026-10-14", pm) == ["盤中監控今天 08:40 沒叫到（由 GitHub 備援排程 14:14 啟動）",
                                                   "每日資料 10-14 15:20 那班沒叫到"]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("gh 壞了")))
    assert health.cron_check("2026-10-13", now) == []                       # 讀不到執行紀錄不亂報

    # 寄信：第一次沒叫到不寄、連兩天才寄
    monkeypatch.setattr(health, "DATA", tmp_path)
    monkeypatch.setattr(health, "OUT", tmp_path / "health" / "latest.json")
    monkeypatch.setattr(health, "HIST", tmp_path / "health" / "history.jsonl")
    monkeypatch.setattr(health, "REF", None)
    monkeypatch.setattr(health, "checks", lambda D, u: [])
    monkeypatch.setattr(health, "failed_runs", lambda hours=20: [])
    mails = []
    monkeypatch.setattr(notify, "send", lambda s, b: mails.append((s, b)))
    calls = []
    monkeypatch.setattr(health, "cron_check", lambda D, n: calls.append(n) or ["每日資料 X 15:20 那班沒叫到"])
    monkeypatch.setattr(health, "repo_mb", lambda: 520.8)
    monkeypatch.setattr(health, "twse_days", lambda t: ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"])
    health.run("am", heal=False, notify=True, now=now)
    assert mails == []
    r = health.run("pm", heal=False, notify=True, now=now.replace(hour=13, minute=31))
    assert r["cron"] == ["每日資料 X 15:20 那班沒叫到"] and mails == [] and len(calls) == 1   # 下午沿用早上的結果、不寄
    assert r["repo_mb"] == 520.8
    health.run("am", heal=False, notify=True, now=now + dt.timedelta(days=1))
    assert len(mails) == 1 and "token" in mails[0][1] and "1 項" in mails[0][0]
    # token 過期：沒有早上體檢，只有 GitHub 備援班的收工後體檢 → 下午自己看，連兩天就寄
    mails.clear()
    monkeypatch.setattr(health, "cron_check", lambda D, n: ["盤中監控今天 08:40 沒叫到"])
    r = health.run("pm", heal=False, notify=True, now=pm.replace(day=16))
    assert r["cron"] == ["盤中監控今天 08:40 沒叫到"] and len(mails) == 1   # 前一次（10/15 早上）也沒叫到 → 寄


def test_margin_history_refetch_partial(tmp_path, monkeypatch):
    """融資／當沖：當沖沒有、或只抓到一半（筆數不到平常八成）的日子要重抓；抓到的比舊的少不換。"""
    from screener import chips
    monkeypatch.setattr(chips, "MARGIN_HIST", tmp_path / "m.csv.gz")
    monkeypatch.setattr(chips, "DT_HIST", tmp_path / "t.csv.gz")
    monkeypatch.setattr(chips, "DIR", tmp_path)
    days = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"]
    monkeypatch.setattr(chips, "_trading_days", lambda: days)
    monkeypatch.setattr(chips, "_volume_map", lambda x: pd.Series(dtype=float))
    monkeypatch.setattr(chips.time, "sleep", lambda s: None)
    codes = [f"{1000 + i}" for i in range(100)]
    full_m = pd.DataFrame({"code": codes, "margin_bal": 1.0, "margin_chg": 0.0, "short_bal": 0.0, "util": 1.0})
    full_t = pd.DataFrame({"code": codes, "dt_vol": 1.0})
    mh = pd.concat([full_m.assign(date=d) for d in days[:4]] + [full_m.head(40).assign(date="2026-10-13")])   # 10/13 只有一半
    th = pd.concat([full_t.assign(date=d) for d in days[:3]])                                                 # 10/12、10/13 沒當沖
    mh[chips.MARGIN_COLS].to_csv(chips.MARGIN_HIST, index=False)
    th.assign(dt_ratio=None)[chips.DT_COLS].to_csv(chips.DT_HIST, index=False)
    asked = []
    monkeypatch.setattr(chips, "margin", lambda s, d: asked.append(d.isoformat()) or (full_m.head(70) if d.isoformat() == "2026-10-12" else full_m))
    monkeypatch.setattr(chips, "daytrade", lambda s, d: full_t)
    chips.update_margin_history(None, chips.dt.date(2026, 10, 13))
    assert sorted(asked) == ["2026-10-12", "2026-10-13"]                       # 完整的 10/6～10/8 不重抓
    m2 = pd.read_csv(chips.MARGIN_HIST, dtype={"code": str})
    t2 = pd.read_csv(chips.DT_HIST, dtype={"code": str})
    assert m2.groupby("date").size().to_dict() == {d: 100 for d in days}       # 10/13 補齊；10/12 抓到 70 筆比舊的少、不換
    assert t2.groupby("date").size().to_dict() == {d: 100 for d in days}       # 當沖 10/12、10/13 補上


# ---------------------------------------------------------------- 篩選規則引擎（每天選股的核心）
def test_rules_engine_basic_strategies():
    """假資料 70 天：1111 一路跌到季線下、最後一天爆量長紅站上季線；2222 平盤；3333 一路在季線上、最後一天收漲停（20 → 22）。"""
    from screener import rules
    days = pd.bdate_range("2026-07-01", periods=70).strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(days):
        last = i == len(days) - 1
        c1 = 60 - i * 0.05 if not last else round((60 - (i - 1) * 0.05) * 1.08, 2)   # 最後一天 +8%（沒到漲停）
        rows.append((d, "1111", c1 * (0.97 if last else 1), c1 * 1.01, c1 * 0.96, c1, 3_000_000 if last else 800_000))
        rows.append((d, "2222", 30, 30.2, 29.8, 30, 900_000))
        c3 = 22.0 if last else round(18 + i * 2 / 68, 2)        # 一路在季線上（不是「突破」），最後一天 20 → 22 漲停
        rows.append((d, "3333", 20.5 if last else c3, c3, c3 * 0.99, c3, 1_500_000 if last else 700_000))
    hist = pd.DataFrame(rows, columns=["date", "code", "open", "high", "low", "close", "volume"])
    p = rules.Panel(hist)
    strategies = [
        {"name": "爆量突破季線", "conditions": [{"type": "volume_vs_prev", "min": 2}, {"type": "cross_above_ma", "period": 60}]},
        {"name": "漲停", "conditions": [{"type": "limit_up"}]},
        {"name": "爆量長紅", "conditions": [{"type": "volume_vs_avg", "days": 5, "min": 3}, {"type": "change_pct", "min": 5},
                                          {"type": "red_candle"}]},
        {"name": "停用的", "enabled": False, "conditions": []},
    ]
    hits = rules.run_strategies(p, strategies, [{"type": "volume", "min": 500}, {"type": "price", "min": 10}])
    assert hits["爆量突破季線"] == ["1111"]
    assert hits["漲停"] == ["3333"]
    assert hits["爆量長紅"] == ["1111"]
    assert "停用的" not in hits
    # 門檻：量 < 500 張的被 base_filter 擋掉
    hits = rules.run_strategies(p, strategies, [{"type": "volume", "min": 2000}])
    assert hits["爆量突破季線"] == ["1111"] and hits["漲停"] == []
    # not／days_ago：昨天沒有漲停、今天有
    assert bool(rules.evaluate(p, {"type": "limit_up", "days_ago": 1, "not": True})["3333"])


# ---------------------------------------------------------------- 虛擬帳戶帳本存檔前檢查（16:25）
def test_ledger_save_check():
    from screener import ledger_save
    days = ["2026-10-12", "2026-10-13"]
    base = {"date": "2026-10-13", "equity": 1100.0, "cash": 100.0, "nav": 1.1, "note": "只含虛擬帳戶", "trades": [],
            "cash_by_bucket": {"core": 60, "etf": 40},
            "positions": [{"code": "2330", "shares": 2, "close": 250.0, "mv": 500.0},
                          {"code": "0050", "shares": 5, "close": 100.0, "mv": 500.0}]}
    closes = {"2330": 250.0, "0050": 100.0}
    led, err, warn = ledger_save.check({"id": "2026-10-13", "data": base}, days, closes)   # 協作板整份也吃
    assert led["equity"] == 1100.0 and err == [] and warn == []
    _, err, warn = ledger_save.check(dict(base, equity=1151.4), days, {"2330": 251.0, "0050": 100.0})
    assert err == [] and any("差 +51.4" in w for w in warn) and any("2330 收盤寫 250.0" in w for w in warn)
    _, err, _ = ledger_save.check(dict(base, note="媽媽的持股"), days, closes)            # 真實持股字眼 → 擋
    assert any("真實持股" in e for e in err) and any("虛擬" in e for e in err)
    _, err, _ = ledger_save.check(dict(base, date="2026-10-10"), days, closes)            # 休市日
    assert any("不是交易日" in e for e in err)
    _, err, _ = ledger_save.check({k: v for k, v in base.items() if k != "positions"}, days, closes)
    assert err == ["缺欄位：positions"]


# ---------------------------------------------------------------- workflow 檔：語法、輸入、cron-job.org 相容
def test_workflows_valid():
    """GitHub 要等觸發時才發現 workflow 寫錯（每日流程整個不跑）。這裡先擋：YAML 讀得進來、每段 run 過 bash -n、
    用到的 inputs.X 都有宣告、needs 指到存在的 job；live.yml、daily.yml 一定要有 test 輸入
    （cron-job.org 叫的 body 帶 test，少了 GitHub 回 422、整天不跑）。"""
    import glob
    import re
    import subprocess

    import yaml
    files = sorted(glob.glob(".github/workflows/*.yml"))
    assert len(files) >= 8
    for f in files:
        txt = open(f, encoding="utf-8").read()
        d = yaml.safe_load(txt)
        on = d.get(True, d.get("on"))
        wd = on.get("workflow_dispatch") if isinstance(on, dict) else None
        declared = set((wd or {}).get("inputs") or {}) if isinstance(wd, dict) else set()
        assert set(re.findall(r"inputs\.([A-Za-z_][\w-]*)", txt)) <= declared, f
        jobs = d["jobs"]
        for jn, j in jobs.items():
            assert "runs-on" in j, (f, jn)
            needs = [j["needs"]] if isinstance(j.get("needs"), str) else j.get("needs") or []
            assert set(needs) <= set(jobs), (f, jn, needs)
            for st in j.get("steps") or []:
                if "run" in st:
                    r = subprocess.run(["bash", "-n"], input=re.sub(r"\$\{\{.*?\}\}", "X", st["run"]),
                                       text=True, capture_output=True)
                    assert r.returncode == 0, (f, jn, st.get("name"), r.stderr)
        if f.endswith(("/live.yml", "/daily.yml")):
            assert "test" in declared, f"{f} 少了 test 輸入，cron-job.org 叫會被拒"
        # 有排程、會推資料的要自己叫網站重畫（GITHUB_TOKEN 推的 commit 不會觸發 pages.yml，#296／#297 的坑）
        # 例外：intraday.yml（盤中進場提醒，排程都晚到 13:28 以後、一跑就略過，不推網站要用的資料）
        if isinstance(on, dict) and "schedule" in on and "git push" in txt and not f.endswith("/intraday.yml"):
            assert "pages.yml" in txt or "deploy-pages" in txt, f"{f} 推完資料沒叫網站重畫"


# ---------------------------------------------------------------- 回測檔：檔尾盤中抓的半天資料要丟掉
def test_btsim_drop_partial_tail():
    from tools.btsim import drop_partial_tail
    days = pd.bdate_range("2026-08-03", periods=25).strftime("%Y-%m-%d")
    vol = {d: 1_000_000 for d in days}
    vol[days[10]] = 600_000            # 中間某天量少（颱風假前）不動
    vol[days[-1]] = 730_000            # 最後一天只有 73%：盤中抓的
    df = pd.DataFrame([(d, c, vol[d]) for d in days for c in ("1111", "2222")], columns=["date", "code", "volume"])
    out, cut = drop_partial_tail(df)
    assert cut == [days[-1]] and out.date.max() == days[-2] and days[10] in set(out.date)
    vol[days[-1]] = 990_000            # 正常的最後一天不丟
    df = pd.DataFrame([(d, c, vol[d]) for d in days for c in ("1111", "2222")], columns=["date", "code", "volume"])
    out, cut = drop_partial_tail(df)
    assert cut == [] and len(out) == len(df)


# ---------------------------------------------------------------- 盤中監控：單輪出錯不要整天停掉
def test_live_main_supervised(monkeypatch):
    from screener import live
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("MIS 回了怪格式")
        return 0

    monkeypatch.setattr(live, "main", flaky)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    monkeypatch.setattr(live.sys, "argv", ["live"])
    assert live.main_supervised() == 0 and len(calls) == 3          # 錯兩次、第三次正常跑完
    calls.clear()
    monkeypatch.setattr(live, "main", lambda: (_ for _ in ()).throw(RuntimeError("一直壞")))
    try:
        live.main_supervised(retries=2)
        raise AssertionError("應該要丟出來")
    except RuntimeError:
        pass                                                           # 重啟用完照舊丟出去（工作流程變紅）
    monkeypatch.setattr(live.sys, "argv", ["live", "--test"])
    monkeypatch.setattr(live, "main", lambda: (_ for _ in ()).throw(KeyError("x")))
    try:
        live.main_supervised()
        raise AssertionError("測試模式不包")
    except KeyError:
        pass


def test_live_restore_state(tmp_path, monkeypatch):
    """重啟（main_supervised、備援班）接續今天的預警紀錄，不重複通知；昨天的紀錄不能沿用。"""
    from screener import live
    f = tmp_path / "live_state.json"
    monkeypatch.setattr(live, "STATE_F", f)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)   # 不去抓 live 分支
    assert live.restore_state("2026-10-14")["alerts"] == {}            # 沒有檔：空白
    f.write_text(json.dumps({"date": "2026-10-14", "alerts": {"2330": {"first_time": "09:31"}},
                             "hold_alerted": ["3041"], "last_notify": 123}), "utf-8")
    s = live.restore_state("2026-10-14")
    assert s["alerts"] == {"2330": {"first_time": "09:31"}} and s["hold_alerted"] == ["3041"] and s["last_notify"] == 123
    assert s["pending"] == [] and s["official"] is None                # 舊檔沒有的欄位補預設
    f.write_text(json.dumps({"state": {"date": "2026-10-14", "alerts": {"2454": {}}}}), "utf-8")   # live.json 包一層 state
    assert list(live.restore_state("2026-10-14")["alerts"]) == ["2454"]
    assert live.restore_state("2026-10-15")["alerts"] == {}            # 隔天不沿用
    f.write_text("{壞掉", "utf-8")
    assert live.restore_state("2026-10-14")["alerts"] == {}            # 檔案壞掉也不會讓盤中監控起不來


def test_intraday_fetch_quotes_retries_failed_batch(monkeypatch):
    """MIS 某一批連錯 3 次（回非 JSON）不能就少掉那 50 檔：最後整批再抓一次（10/13 12:40 那輪少了 50 檔）。"""
    from screener import intraday
    stocks = pd.DataFrame({"code": [f"{1100 + i}" for i in range(7)], "market": ["TWSE"] * 4 + ["TPEX"] * 3})
    bad = {"n": 0}

    class R:
        def __init__(self, keys):
            self.keys = keys

        def json(self):
            if "tse_1102.tw" in self.keys and bad["n"] < 3:       # 第二批前 3 次壞掉
                bad["n"] += 1
                raise ValueError("Expecting value: line 22 column 1 (char 21)")
            return {"msgArray": [{"c": k.split("_")[1][:-3], "n": "x", "z": "10.5", "y": "10", "v": "5", "d": "20261014",
                                  "t": "13:12:00"} for k in self.keys]}

    class S:
        headers = {}

        def get(self, url, params=None, timeout=None):
            return R((params or {}).get("ex_ch", "").split("|")) if params else R([])

    monkeypatch.setattr(intraday.requests, "Session", S)
    monkeypatch.setattr(intraday.time, "sleep", lambda s: None)
    df, day = intraday.fetch_quotes(stocks, batch=2)
    assert sorted(df.code) == sorted(stocks.code) and day == "20261014" and bad["n"] == 3
    # 一直壞：照舊回其他的，不會卡住
    monkeypatch.setattr(R, "json", lambda self: (_ for _ in ()).throw(ValueError("x")) if "tse_1102.tw" in self.keys
                        else {"msgArray": [{"c": k.split("_")[1][:-3], "z": "1", "d": "20261014"} for k in self.keys]})
    df, _ = intraday.fetch_quotes(stocks, batch=2)
    assert sorted(df.code) == ["1100", "1101", "1104", "1105", "1106"]


def test_daily_evening_dispatch(tmp_path):
    """daily.yml 的 evening1／evening2（#286、#307）：把 shell 抽出來，用假時鐘、假 gh 跑。
    今天跑出今天資料才叫、18:05／21:50 準時叫、晚於 18:00 跑完馬上叫、那個時段已經有一班就不重複、
    隔天半夜才到的備援班不叫、21:50 那段過午夜才開始不等到隔天。"""
    import datetime as dt
    import os
    import subprocess

    import yaml
    w = yaml.safe_load(open(".github/workflows/daily.yml", encoding="utf-8"))["jobs"]
    tz = dt.timezone(dt.timedelta(hours=8))
    clock = tmp_path / "clock"
    b = tmp_path / "bin"
    b.mkdir()
    (b / "date").write_text("#!/usr/bin/env python3\nimport sys, os, datetime as d\n"
                            "t = d.datetime.fromtimestamp(float(open(os.environ['CLOCK']).read()), d.timezone(d.timedelta(hours=8)))\n"
                            "print(t.strftime(sys.argv[-1][1:]))\n")
    (b / "sleep").write_text("#!/usr/bin/env python3\nimport sys, os\nf = os.environ['CLOCK']\n"
                             "t = float(open(f).read()) + float(sys.argv[1])\nopen(f, 'w').write(str(t))\n")
    (b / "gh").write_text('#!/usr/bin/env bash\necho "$*" >> "$GHLOG"\ncase "$1 $2" in\n'
                          '  "api repos/"*) printf \'{"last_done": "%s"}\' "$LAST_DONE" | base64 ;;\n'
                          '  "run list") echo "$RUNS" ;;\nesac\n')
    for f in b.iterdir():
        f.chmod(0o755)
    # SINCE 裡的「現在」改讀假時鐘
    since = w["evening1"]["env"]["SINCE"].replace(
        "d.datetime.now(tz)", "d.datetime.fromtimestamp(float(open(__import__('os').environ['CLOCK']).read()), tz)")
    assert since != w["evening1"]["env"]["SINCE"] and w["evening1"]["env"]["SINCE"] == w["evening2"]["env"]["SINCE"]

    def iso(s):
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=tz).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def run(job, start, last_done, runs=()):
        clock.write_text(str(dt.datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp()))
        out, log = tmp_path / "out", tmp_path / "gh.log"
        out.write_text("")
        log.write_text("")
        env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", CLOCK=str(clock), GHLOG=str(log), LAST_DONE=last_done,
                   RUNS=json.dumps([{"createdAt": iso(r)} for r in runs]), GITHUB_OUTPUT=str(out), GH_REPO="x/y",
                   D=last_done, SINCE=since)
        r = subprocess.run(["bash", "-e", "-c", w[job]["steps"][0]["run"]], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        end = dt.datetime.fromtimestamp(float(clock.read_text()), tz).strftime("%H:%M")
        return end, out.read_text().strip(), sum("workflow run" in x for x in log.read_text().splitlines())

    T = "2026-10-14"
    end, o, n = run("evening1", f"{T} 15:31", T)
    assert end in ("18:05", "18:06") and o == f"D={T}" and n == 1                     # 正常：等到 18:05 叫
    assert run("evening1", f"{T} 17:48", T, [f"{T} 18:05"])[2] == 0                  # 17:40 備援班：已有一班就不叫
    assert run("evening1", f"{T} 18:30", T, [f"{T} 08:41"])[1:] == (f"D={T}", 1)     # 晚到：馬上叫（早上補跑的不算）
    assert run("evening1", f"{T} 19:30", T, [f"{T} 18:05"])[1:] == (f"D={T}", 0)
    assert run("evening1", f"{T} 21:40", T)[1:] == (f"D={T}", 0)                     # 過 21:30 只留 21:50 那班
    assert run("evening1", "2026-10-15 01:07", T)[1:] == ("", 0)                     # 隔天半夜才到的備援班
    assert run("evening1", f"{T} 15:31", "2026-10-13")[1:] == ("", 0)               # 今天資料還沒出來
    end, _, n = run("evening2", f"{T} 18:07", T, [f"{T} 18:05"])
    assert end in ("21:50", "21:51") and n == 1                                      # 21:50 再叫一次
    assert run("evening2", f"{T} 23:06", T, [f"{T} 21:50"])[2] == 0                  # 已有一班
    assert run("evening2", f"{T} 21:40", T, [f"{T} 21:47"])[2] == 0                  # GitHub 21:47 排程準時跑了
    assert run("evening2", "2026-10-15 00:01", T) == ("00:01", "", 0)                # 過午夜不等到隔天


def test_oddlot_mis_fallback(monkeypatch):
    """櫃買盤後零股官方 0 列時用 MIS 的 oz／ov／ob／oa 補（10/13 樣本：環球晶 1020／9544 股，跟官方一樣）；
    MIS 已經是別天的資料就不收；官方有資料就不碰 MIS。"""
    from screener import enrich, fetch

    def q(code, d, oz, ov, ob, oa):
        return {"c": code, "d": d, "oz": oz, "ov": ov, "ob": ob, "oa": oa, "z": "999"}

    pages = [[q("6488", "20261013", "1020.0000", "9544", "1015.0000", "1020.0000"),
              q("8069", "20261013", "-", "0", "200.5000", "201.0000")],              # 沒成交、有掛價
             [q("5347", "20261013", "-", "0", "-", "-"),                              # 什麼都沒有 → 不收
              q("3105", "20261009", "300.0000", "50", "299.0000", "300.0000")]]       # 停牌、別天 → 不收
    calls = []

    class S:
        headers = {}

        def get(self, url, params=None, timeout=None):
            if not params:
                return None
            calls.append(params["ex_ch"])
            return type("R", (), {"json": lambda self, m=pages[len(calls) - 1]: {"msgArray": m}})()

    monkeypatch.setattr(enrich.requests, "Session", S)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch, "load_stock_list", lambda m: pd.DataFrame({"code": ["6488", "8069", "5347", "3105"]}))
    rows = enrich._oddlot_mis(dt.date(2026, 10, 13), "TPEX", batch=2)
    assert calls == ["otc_6488.tw|otc_8069.tw", "otc_5347.tw|otc_3105.tw"]
    assert rows == [{"code": "6488", "market": "TPEX", "price": 1020.0, "shares": 9544.0, "amount": 9734880, "bid": 1015.0, "ask": 1020.0},
                    {"code": "8069", "market": "TPEX", "price": None, "shares": 0, "amount": 0, "bid": 200.5, "ask": 201.0}]
    calls.clear()
    assert enrich._oddlot_mis(dt.date(2026, 10, 14), "TPEX", batch=2) == [] and len(calls) == 1   # MIS 還是 10/13 → 第一批就放棄

    # oddlot()：上市官方有、上櫃官方 0 列 → 上櫃用 MIS
    twse = {"fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "成交價", "最後揭示買價", "最後揭示賣價"],
            "data": [["2330", "台積電", "92,010", "1", "230,025,000", "2,500.00", "2,500.00", "2,505.00"]]}
    monkeypatch.setattr(enrich, "_json", lambda s, url, params: {"tables": [twse]} if "twse" in url else {"tables": [{"fields": [], "data": []}]})
    monkeypatch.setattr(enrich, "_tables", lambda j: j.get("tables") or [])
    monkeypatch.setattr(enrich, "_oddlot_mis", lambda d, m: [{"code": "6488", "market": m, "price": 1020.0, "shares": 9544.0,
                                                              "amount": 9734880, "bid": 1015.0, "ask": 1020.0}] if m == "TPEX" else [])
    df = enrich.oddlot(None, dt.date(2026, 10, 13))
    assert sorted(zip(df.code, df.market)) == [("2330", "TWSE"), ("6488", "TPEX")] and df.set_index("code").price["2330"] == 2500


def test_probe_split_arg():
    """probe.yml：『網址|關鍵字』，但 MIS 多檔的 ex_ch=a|b 是網址的一部分（10/14 踩過，查錯成單檔）。"""
    from tools.probe import split_arg
    assert split_arg("https://x/y?a=1|成交價") == ("https://x/y?a=1", "成交價")
    assert split_arg("https://x/y") == ("https://x/y", "")
    mis = "https://mis.twse.com.tw/stock/api/getOddInfo.jsp?ex_ch=tse_2330.tw|otc_6488.tw&json=1"
    assert split_arg(mis) == (mis, "")
    assert split_arg(mis + "|台積電") == (mis, "台積電")
    assert split_arg("https://m/api?ex_ch=tse_2330.tw|tse_2317.tw") == ("https://m/api?ex_ch=tse_2330.tw|tse_2317.tw", "")
