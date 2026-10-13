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


def test_twcal_is_trading_day(tmp_path, monkeypatch):
    """16:25 排程先看今天是不是交易日：休市日（補假、週末）不做體檢、不叫每日資料、不叫 Dennis 換 token。"""
    monkeypatch.setattr(twcal, "CACHE", tmp_path / "tw_holidays.json")
    (tmp_path / "tw_holidays.json").write_text(json.dumps({"years": {"2026": ["2026-10-09", "2026-10-26"]}}), "utf-8")
    assert twcal.is_trading_day("2026-10-14") and twcal.is_trading_day("2026-10-27")
    assert not twcal.is_trading_day("2026-10-26") and not twcal.is_trading_day("2026-10-09")
    assert not twcal.is_trading_day("2026-10-17")   # 週六


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


def test_btsim_exit_matches_positions(tmp_path, monkeypatch):
    """回測（tools/btsim trades）跟實際持倉追蹤（positions.update）用同一套出場：量縮到爆量日一半以下、收漲停那天不算，
    出場訊號隔天開盤賣。同一份資料兩邊出場日、出場價、毛報酬要一樣，不然回測驗的不是網站在跑的規則。"""
    from tools import btsim
    rows = []
    for d, a, b in [("2026-09-30", (95, 3000, 95), (45.5, 2000, 45)),
                    ("2026-10-01", (100, 10000, 96), (50.0, 8000, 46)),
                    ("2026-10-02", (102, 6000, 101), (55.0, 3000, 51)),
                    ("2026-10-05", (105, 4000, 104), (54.0, 3000, 55)),
                    ("2026-10-06", (99, 5000, 103), (53.0, 3000, 53.5)),
                    ("2026-10-07", (98, 5000, 99), (52.0, 3000, 53))]:
        for code, (c, v, o) in (("1111", a), ("2222", b)):
            rows.append({"date": d, "code": code, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": v, "adjusted": 0})
    f = tmp_path / "bt.csv"
    pd.DataFrame(rows).to_csv(f, index=False)
    monkeypatch.setattr(btsim, "drop_partial_tail", lambda df: (df, []))
    P = btsim.load(str(f))
    T = btsim.trades(P, pd.DataFrame({"date": ["2026-10-01", "2026-10-01"], "code": ["1111", "2222"]}), slip=False).set_index("code")
    assert T.exit_date.to_dict() == {"1111": "2026-10-06", "2222": "2026-10-06"}
    assert T.exit_px.to_dict() == {"1111": 103, "2222": 53.5}
    assert round(T.gross["1111"], 6) == 3.0 and round(T.gross["2222"], 6) == 7.0    # 跟 test_positions_exit_rules 的 est_return_pct 一樣


def test_btsim_signals_vs_surge_frame(tmp_path, monkeypatch):
    """回測進場 btsim.signals ＝ 網站 K 線買點 tech.surge_frame 再加三道門檻（≥500 張、漲幅 ≤10.5%、股價 ≥10）。
    三檔都過 surge_frame，只有三道門檻也過的那檔進回測。"""
    from screener import tech
    from screener.rules import Panel
    from tools import btsim
    days = [str(dt.date(2026, 1, 1) + dt.timedelta(days=i)) for i in range(66)]
    last = {"1111": (101, 104, 4_000_000), "2222": (101, 104, 400_000), "3333": (101, 111, 4_000_000)}   # (開, 收, 股數)
    base = {"1111": 1_000_000, "2222": 100_000, "3333": 1_000_000}
    rows = []
    for code, (o, c, v) in last.items():
        rows += [{"date": d, "code": code, "open": 100, "high": 101, "low": 99, "close": 100, "volume": base[code], "adjusted": 0} for d in days[:-1]]
        rows.append({"date": days[-1], "code": code, "open": o, "high": c, "low": o, "close": c, "volume": v, "adjusted": 0})
    df = pd.DataFrame(rows)
    assert tech.surge_frame(Panel(df)).loc[days[-1]].all()
    f = tmp_path / "bt.csv"
    df.to_csv(f, index=False)
    monkeypatch.setattr(btsim, "drop_partial_tail", lambda d: (d, []))
    sig = btsim.signals(btsim.load(str(f)))
    assert list(zip(sig.date, sig.code)) == [(days[-1], "1111")]       # 2222 量 400 張 < 500；3333 漲 11% > 10.5%


def test_margin_parse(monkeypatch):
    """融資融券解析（證交所 MI_MARGN 固定欄位位置、櫃買 margin/balance 欄名開頭）：餘額、增減、券餘額、使用率；
    證交所欄位位置對不上就整張不收（寧可沒有也不要抓錯欄，體檢會發現缺）。"""
    from screener import chips
    monkeypatch.setattr(chips.time, "sleep", lambda s: None)
    tw_f = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "限額",
            "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "限額", "資券互抵", "註記"]
    tw_row = ["2330", "台積電", "3,000", "1,000", "0", "10,000", "12,000", "100,000", "0", "0", "0", "400", "500", "100,000", "0", ""]
    tp_f = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
            "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]
    tp_row = ["6488", "環球晶", "1,000", "300", "100", "0", "1,200", "0", "3.50", "34,000", "40", "10", "0", "0", "50", "0", "0.1", "34,000", "0", ""]
    replies = {"MI_MARGN": {"stat": "OK", "tables": [{"fields": tw_f, "data": [tw_row, ["0050", "元大台灣50"] + tw_row[2:]]}]},
               "balance": {"tables": [{"fields": tp_f, "data": [tp_row]}]}}
    monkeypatch.setattr(chips, "_json", lambda s, url, params=None: replies[url.split("/")[-1]])
    m = chips.margin(None, dt.date(2026, 10, 13)).set_index("code")
    assert list(m.index) == ["2330", "6488"]                                          # ETF 不收
    assert m.loc["2330"].to_dict() == {"margin_bal": 12000, "margin_chg": 2000, "short_bal": 500, "util": 12.0}
    assert m.loc["6488"].to_dict() == {"margin_bal": 1200, "margin_chg": 200, "short_bal": 50, "util": 3.5}
    replies["MI_MARGN"]["tables"][0]["fields"] = tw_f[:5] + ["今日餘額", "前日餘額"] + tw_f[7:]   # 欄位換位置
    assert list(chips.margin(None, dt.date(2026, 10, 13)).code) == ["6488"]


def test_today_sys_stats(tmp_path, monkeypatch):
    """首頁系統訊號合計：持有中按最新收盤估（今天才出的算 0）、已出場用實際、每筆扣 0.38%；
    同期 0050 含息＝訊號日 → 出場訊號日（持有中到最新）；照收盤鎖不鎖漲停拆兩組，主數字看沒鎖的。"""
    from screener import today
    monkeypatch.setattr(today, "EX", tmp_path)
    pd.DataFrame({"date": ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"], "code": "0050",
                  "tr": [100.0, 101.0, 102.0, 104.0]}).to_csv(tmp_path / "bench.csv", index=False)
    pos = pd.DataFrame([
        {"signal_date": "2026-10-01", "status": "closed", "est_return_pct": 5.38, "exit_signal_date": "2026-10-05", "close_lu": 0},
        {"signal_date": "2026-10-02", "status": "open", "est_return_pct": -1.62, "exit_signal_date": None, "close_lu": 1},
        {"signal_date": "2026-10-06", "status": "open", "est_return_pct": None, "exit_signal_date": None, "close_lu": 0}])
    st = today._sys_stats(pos)
    assert st["n"] == 3 and st["n_open"] == 2 and st["n_lu"] == 1 and st["n_nolu"] == 2
    assert st["avg"] == round((5.0 - 2.0 - 0.38) / 3, 2)                  # 今天才出的那筆浮動 0、扣 0.38
    assert st["bench"] == round((2.0 + (104 / 101 - 1) * 100 + 0.0) / 3, 2)
    assert st["lu_avg"] == -2.0 and st["nolu_avg"] == round((5.0 - 0.38) / 2, 2) and st["since"] == "2026-10-01"
    assert st["win"] is None                                              # 不滿 30 筆不算勝率


def test_lottery_placebo_liquidity_match():
    """研究判準用的配對安慰劑：同進出場日、只從同一個流動性五分位抽；報酬減同期全市場等權再扣成本。
    做法：流動性最高那 1/5 的股票都漲 10%、其他都 0；交易是最高組的 → 安慰劑每次都抽到 +10%。"""
    import numpy as np

    from screener import lottery
    dates = [str(dt.date(2026, 1, 1) + dt.timedelta(days=i)) for i in range(70)]
    codes = [f"{1000 + i}" for i in range(50)]
    close = pd.DataFrame(100.0, index=dates, columns=codes)
    vol = pd.DataFrame({c: [1000.0 * (i + 1)] * len(dates) for i, c in enumerate(codes)}, index=dates)   # 1049 成交值最大
    top = codes[-10:]                                                                                 # 最高 1/5
    close.loc[dates[-1], top] = 110.0
    q = lottery.liquidity_quintile(close, vol)
    assert set(q.loc[dates[-2]][q.loc[dates[-2]] == 5].index) == set(top)
    trades = pd.DataFrame({"entry": [dates[-2]] * 3, "exit": [dates[-1]] * 3, "code": [top[0], top[3], codes[0]]})
    pl = lottery.placebo(close, trades, n=50, volume=vol, match="liquidity", cost=0.38)
    ew = 10 * 10 / 50                                                                                 # 全市場等權 +2%
    hi, lo = (10 - ew) - 0.38, (0 - ew) - 0.38
    assert abs(pl["median"].iloc[0] - hi) < 1e-9                                                       # 三筆裡兩筆高組 → 中位＝高組
    assert set(np.round(pl["median"], 9)) == {round(hi, 9)}
    pl2 = lottery.placebo(close, trades.iloc[[2]], n=20, volume=vol, match="liquidity", cost=0.38)
    assert set(np.round(pl2["median"], 9)) == {round(lo, 9)}                                          # 低流動性那筆只抽到 0%


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
    import shutil
    import subprocess

    import yaml
    w = yaml.safe_load(open(".github/workflows/daily.yml", encoding="utf-8"))["jobs"]
    tz = dt.timezone(dt.timedelta(hours=8))
    clock = tmp_path / "clock"
    b = tmp_path / "bin"
    b.mkdir()
    # 假時鐘（整數秒）：date 用系統的 GNU date 印假時間（TZ 照 workflow 給的）、sleep 只把時鐘往前撥；用 bash 不開 Python，快
    (b / "date").write_text(f'#!/usr/bin/env bash\nexec {shutil.which("date")} -d "@$(cat "$CLOCK")" "${{@: -1}}"\n')
    (b / "sleep").write_text('#!/usr/bin/env bash\nt=$(cat "$CLOCK"); echo $(( t + ${1%.*} )) > "$CLOCK"\n')
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
        clock.write_text(str(int(dt.datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp())))
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
    assert df.attrs["mis"] == ["TPEX"]
    # daily.yml oddtpex 等官方時設 ODD_NO_MIS：官方 0 列就只回上市，不打 MIS
    monkeypatch.setenv("ODD_NO_MIS", "1")
    df = enrich.oddlot(None, dt.date(2026, 10, 13))
    assert list(df.market) == ["TWSE"] and df.attrs["mis"] == []


def test_oddlot_mis_replaced_by_official(monkeypatch, tmp_path):
    """先用 MIS 補的上櫃那天要登記，傍晚／隔天補抓再抓官方的換掉（官方多了 ETF）；換成官方的就不再重抓。"""
    import json

    from screener import enrich, fetch

    monkeypatch.setattr(enrich, "ODD_HIST", tmp_path / "oddlot_hist.csv.gz")
    monkeypatch.setattr(enrich, "ODD_MIS", tmp_path / "oddlot_mis.json")
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch, "load_history", lambda: pd.DataFrame({"date": ["2026-10-12", "2026-10-13"]}))
    f = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "成交價", "最後揭示買價", "最後揭示賣價"]
    twse = {"fields": f, "data": [["2330", "台積電", "92,010", "1", "230,025,000", "2,500.00", "2,500.00", "2,505.00"]]}
    tpex = {"fields": f, "data": [["6488", "環球晶", "9,544", "1", "9,734,880", "1,020.00", "1,015.00", "1,020.00"],
                                  ["00679B", "元大美債20年", "5,000", "1", "135,000", "27.00", "26.99", "27.00"]]}
    official_tpex, calls = [False], []

    def fake_json(s, url, params):
        calls.append(url)
        if "twse" in url:
            return {"tables": [twse]}
        return {"tables": [tpex if official_tpex[0] else {"fields": [], "data": []}]}

    monkeypatch.setattr(enrich, "_json", fake_json)
    monkeypatch.setattr(enrich, "_tables", lambda j: j.get("tables") or [])
    monkeypatch.setattr(enrich, "_oddlot_mis", lambda d, m: [{"code": "6488", "market": m, "price": 1020.0, "shares": 9544.0,
                                                              "amount": 9734880, "bid": 1015.0, "ask": 1020.0}] if m == "TPEX" else [])
    # 10/12 官方兩邊都有，10/13 上櫃官方還沒有 → MIS 補、登記
    official_tpex[0] = True
    enrich.update_oddlot_history(enrich.oddlot(None, dt.date(2026, 10, 12)), dt.date(2026, 10, 12))
    official_tpex[0] = False
    enrich.backfill_oddlot(None, dt.date(2026, 10, 13), 5)
    assert json.loads((tmp_path / "oddlot_mis.json").read_text()) == {"2026-10-13": ["TPEX"]}
    h = pd.read_csv(tmp_path / "oddlot_hist.csv.gz", dtype={"code": str})
    assert sorted(h[h.date == "2026-10-13"].code) == ["2330", "6488"]

    # 傍晚：官方還沒有 → 照樣 MIS、登記留著；10/12 已經是官方的，不重抓
    calls.clear()
    enrich.backfill_oddlot(None, dt.date(2026, 10, 13), 5)
    assert json.loads((tmp_path / "oddlot_mis.json").read_text()) == {"2026-10-13": ["TPEX"]}
    assert len(calls) == 3   # 只抓 10/13：上市 1 次、上櫃 0 列重抓 2 次

    # 隔天：官方公布了 → 換成官方的（多了 ETF），登記拿掉，之後不再重抓
    official_tpex[0] = True
    enrich.backfill_oddlot(None, dt.date(2026, 10, 13), 5)
    h = pd.read_csv(tmp_path / "oddlot_hist.csv.gz", dtype={"code": str})
    assert sorted(h[h.date == "2026-10-13"].code) == ["00679B", "2330", "6488"]
    assert json.loads((tmp_path / "oddlot_mis.json").read_text()) == {}
    calls.clear()
    enrich.backfill_oddlot(None, dt.date(2026, 10, 13), 5)
    assert calls == []


def test_live_scan_hits(monkeypatch):
    """盤中訊號判斷（live.scan）：爆量突破、一字鎖漲停放行、量不夠／沒過前高／漲太多不算、這輪沒價沿用上一輪、同族群家數。"""
    from screener import live

    monkeypatch.setattr(live, "_LAST_PX", {"5555": 53.0})
    monkeypatch.setattr(live, "_TPL_REF", None)
    now = dt.datetime(2026, 10, 14, 13, 12, tzinfo=live.TZ)
    base = {"yclose": 50.0, "price": 53.0, "open": 50.5, "high": 53.5, "low": 50.2, "vol_lots": 3000.0}
    rows = {"1111": {},                                                                    # 爆量突破 +6%
            "2222": {"price": 55.0, "open": 55.0, "high": 55.0, "low": 55.0, "vol_lots": 400.0},   # 一字鎖：現價＝開盤也要算
            "3333": {"vol_lots": 200.0},                                                   # 量 < 300 張
            "4444": {},                                                                    # 沒過 60 日高
            "5555": {"price": float("nan")},                                               # 這輪沒成交價 → 沿用 53
            "6666": {"price": 55.6, "high": 55.6}}                                         # +11.2% 超過 10.5
    q = pd.DataFrame([{"code": c, "name": c} | base | r for c, r in rows.items()])
    ref = pd.DataFrame({"avg5": [500_000.0, 100_000.0, 500_000.0, 500_000.0, 500_000.0, 500_000.0],
                        "hi": [52.0, 54.0, 52.0, 60.0, 52.0, 52.0]}, index=list(rows))
    stocks = pd.DataFrame({"code": list(rows), "industry": ["半導體", "光電", "半導體", "半導體", "半導體", "光電"],
                           "market": ["TWSE"] * 6})
    out = live.scan(q, ref, stocks, {}, now, {"4444"}).set_index("code")
    assert out.hit.to_dict() == {"1111": True, "2222": True, "3333": False, "4444": False, "5555": True, "6666": False}
    assert out.locked["2222"] and not out.locked["1111"]
    assert out.price["5555"] == 53.0 and out.watch["4444"] and not out.watch["1111"]
    assert out.peers["1111"] == 2   # 同產業另外兩檔（4444、5555）也是預估爆量＋漲 3% 以上；3333 量不夠不算


def test_workflows_gh_token():
    """用到 gh（或體檢會呼叫 gh）的步驟要有 GH_TOKEN，會派工的 workflow 要有 actions: write。
    少了的話 gh 直接失敗，那幾步又多半 continue-on-error，傍晚補抓／早上補跑就默默不叫。"""
    import glob
    import re

    import yaml
    uses = re.compile(r"(^|[\s;&|(`$])gh\s+(api|run|workflow|issue|pr|release)\b|screener\.health")
    dispatch = re.compile(r"gh\s+workflow\s+run|screener\.health")
    seen = 0
    for f in sorted(glob.glob(".github/workflows/*.yml")):
        d = yaml.safe_load(open(f, encoding="utf-8"))
        for jn, j in d["jobs"].items():
            perms = j.get("permissions") or d.get("permissions") or {}
            for st in j.get("steps") or []:
                run = st.get("run") or ""
                if not uses.search(run):
                    continue
                seen += 1
                env = {**(d.get("env") or {}), **(j.get("env") or {}), **(st.get("env") or {})}
                assert "GH_TOKEN" in env, (f, jn, st.get("name"))
                if dispatch.search(run):
                    assert isinstance(perms, dict) and perms.get("actions") == "write", (f, jn, st.get("name"))
    assert seen >= 8


def test_config_matches_backtest():
    """13:12 正式訊號（config.yaml intraday）跟 5 年回測（tools/btsim signals／trades 預設值）要同一套門檻，
    不然虛擬帳戶照的訊號跟回測驗過的不是同一個東西。要改門檻就兩邊一起改、重跑回測。"""
    import inspect

    import yaml

    from tools import btsim
    ic = yaml.safe_load(open("config.yaml", encoding="utf-8"))["intraday"]
    sg = {k: v.default for k, v in inspect.signature(btsim.signals).parameters.items() if v.default is not inspect._empty}
    tr = {k: v.default for k, v in inspect.signature(btsim.trades).parameters.items() if v.default is not inspect._empty}
    assert ic["volume_multiple"] == sg["vol_mult"]
    assert ic["min_change_pct"] == sg["chg_min"] and ic["max_change_pct"] == sg["chg_max"]
    assert ic["new_high_days"] == sg["high_days"]
    assert ic["min_volume_lots"] == sg["min_lots"] and ic["min_price"] == sg["min_price"]
    assert ic["exit_shrink_ratio"] == tr["shrink"] and ic["max_hold_days"] == tr["max_hold"]


def test_daystatus_fmt_runs():
    """tools/daystatus：失敗的標 ❌ 附連結、跑到一半標 ⏳、事件翻成中文。"""
    from tools.daystatus import fmt_runs
    rows = [{"time": "08:40", "name": "盤中連續監控", "event": "workflow_dispatch", "status": "completed", "conclusion": "success", "url": "u1"},
            {"time": "15:20", "name": "台股每日篩選", "event": "schedule", "status": "completed", "conclusion": "failure", "url": "u2"},
            {"time": "18:05", "name": "傍晚補抓消息面", "event": "workflow_dispatch", "status": "in_progress", "conclusion": None, "url": "u3"}]
    out = fmt_runs(rows)
    assert out[0] == "  ✅ 08:40 盤中連續監控（派工叫的）"
    assert out[1].startswith("  ❌ 15:20 台股每日篩選（GitHub 排程）") and out[1].endswith("u2")
    assert out[2].startswith("  ⏳ 18:05") and "u3" not in out[2]
    assert fmt_runs([]) == ["  （沒有）"]


def test_corpact_adjust(tmp_path, monkeypatch):
    """減資／變更面額接價（盤中、每日篩選都靠它）：官方事件往回 ÷factor、量 ×factor；事件當天股價沒跳的不調（假事件）；
    停牌後恢復買賣大跳、又沒有公告的用推估。"""
    from screener import corpact
    days = [f"2026-09-{d:02d}" for d in range(1, 13)]
    rows = []
    for code in ("1111", "2222", "3333"):
        for i, d in enumerate(days):
            if code == "3333" and d in ("2026-09-09", "2026-09-10"):
                continue                                           # 停牌兩天
            px = 100.0
            if d == "2026-09-11":
                px = {"1111": 202.0, "2222": 101.0, "3333": 50.0}[code]
            if d == "2026-09-12":
                px = {"1111": 204.0, "2222": 102.0, "3333": 51.0}[code]
            rows.append({"date": d, "code": code, "open": px, "high": px, "low": px, "close": px, "volume": 1000.0})
    hist = pd.DataFrame(rows)
    cf = tmp_path / "corp_actions.csv"
    pd.DataFrame([{"date": "2026-09-11", "code": "1111", "factor": 0.5, "src": "TWTAUU"},
                  {"date": "2026-09-11", "code": "2222", "factor": 0.7, "src": "TWTAUU"}]).to_csv(cf, index=False)
    monkeypatch.setattr(corpact, "CORP_F", cf)
    monkeypatch.setattr(corpact, "EXDIV", tmp_path / "none.csv")
    ev = corpact.events(hist).set_index("code")
    assert set(ev.index) == {"1111", "3333"}                      # 2222 的假事件被合理性檢查擋掉
    assert ev.factor["1111"] == 0.5 and ev.src["3333"] == "推估" and abs(ev.factor["3333"] - 2.0) < 1e-9
    h = corpact.adjust(hist).set_index(["code", "date"])
    assert h.close["1111", "2026-09-10"] == 200 and h.volume["1111", "2026-09-10"] == 500 and h.close["1111", "2026-09-11"] == 202
    assert h.close["2222", "2026-09-10"] == 100
    assert h.close["3333", "2026-09-08"] == 50 and h.volume["3333", "2026-09-08"] == 2000 and h.close["3333", "2026-09-11"] == 50


def test_surge_frame_definition():
    """「爆量突破 60 日新高」的定義（網站 K 線買點、lottery 地基、回測共用）：量 ≥ 前 5 日均量 3 倍、漲 ≥3%、紅K、收 > 前 60 日最高。
    每檔只差一個條件，只有符合全部的那檔當天算。"""
    from screener import tech
    from screener.rules import Panel
    days = [str(dt.date(2026, 1, 1) + dt.timedelta(days=i)) for i in range(66)]
    last = {"OK": (101, 104, 4000), "VOL": (101, 104, 2900), "CHG": (101, 102, 4000),   # (開, 收, 量)；前一天收 100
            "BLK": (105, 104, 4000), "LOW": (101, 104, 4000)}
    rows = []
    for code, (o, c, v) in last.items():
        for i, d in enumerate(days[:-1]):
            hi = 106 if code == "LOW" and i == 30 else 101           # LOW：60 天內有一天最高 106，收 104 沒過
            rows.append({"date": d, "code": code, "open": 100, "high": hi, "low": 99, "close": 100, "volume": 1000})
        rows.append({"date": days[-1], "code": code, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": v})
    s = tech.surge_frame(Panel(pd.DataFrame(rows)))
    assert s.loc[days[-1]].to_dict() == {"BLK": False, "CHG": False, "LOW": False, "OK": True, "VOL": False}
    assert not s.loc[days[:-1]].any().any()


def test_ewindex_daily_returns_and_regime(tmp_path, monkeypatch):
    """等權指數（盤勢主判、回測對照組）：ETF 不算、除權息日還原、|日報酬|>11% 丟掉、停牌跨缺口；
    線上線下用前一日（不偷看），資料洞那天（檔數少 >2%）不翻。"""
    from screener import ewindex
    monkeypatch.setattr(ewindex, "EX", tmp_path)          # 0050 含息檔不存在 → tr_0050 空的，不影響等權
    d = ["2026-01-05", "2026-01-06", "2026-01-07"]
    rows = [("1111", [100, 102, 102]), ("2222", [50, 50, 47.5]),   # 2222 1/7 除息 2.5 元：還原後 0%
            ("3333", [10, 25, 25]),                              # 減資大跳 +150%：丟掉
            ("4444", [20, None, 22]),                            # 1/6 停牌：1/7 從 1/5 算 +10%
            ("0050", [100, 200, 200]), ("12345", [1, 9, 9])]     # ETF、5 碼不算
    df = pd.DataFrame([{"date": x, "code": c, "close": v} for c, vs in rows for x, v in zip(d, vs) if v is not None])
    ex = pd.DataFrame([{"date": "2026-01-07", "code": "2222", "prev_close": 50, "ref_price": 47.5}])
    r = ewindex.daily_returns(df, ex).set_index("date")
    assert r.n_stocks["2026-01-06"] == 2 and abs(r.ew_ret["2026-01-06"] - 1.0) < 1e-9    # 1111 +2%、2222 0%（3333 丟）
    assert r.n_stocks["2026-01-07"] == 4 and abs(r.ew_ret["2026-01-07"] - 2.5) < 1e-9     # 0%、0%（還原）、0%、+10%（跨缺口）

    days = [str(dt.date(2025, 1, 1) + dt.timedelta(days=i)) for i in range(204)]
    rets = pd.DataFrame({"date": days, "ew_ret": [0.1] * 201 + [-20.0, -1.0, -1.0], "n_stocks": [1000] * 201 + [900, 1000, 1000]})
    f = ewindex.finish(rets).set_index("date")
    assert f.close_above[days[200]] == 1 and f.above[days[201]] == 1
    assert f.gap[days[201]] == 1 and f.close_above[days[201]] == 0
    assert f.above[days[202]] == 1      # 前一天是資料洞，不翻，沿用更早的「線上」
    assert f.above[days[203]] == 0      # 洞之後正常的一天收在線下 → 隔天才翻


def test_intraday_official_signal(tmp_path, monkeypatch):
    """13:12 正式訊號（虛擬帳戶照這個下單）：預估全日量 3 倍、漲 3～10.5%、紅K、過 60 日高才進 positions.csv；
    同一天第二次叫不重複記。"""
    import shutil
    import types

    from screener import corpact, enrich, fetch, intraday, positions

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 10, 14, 13, 12, tzinfo=intraday.TZ)

    monkeypatch.setattr(intraday, "dt", types.SimpleNamespace(datetime=FakeDT, date=dt.date, timedelta=dt.timedelta))
    shutil.copy("config.yaml", tmp_path / "config.yaml")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(fetch, "ROOT", tmp_path)
    monkeypatch.setattr(positions, "FILE", tmp_path / "data" / "positions.csv")
    codes = ["1111", "2222", "3333", "4444"]
    monkeypatch.setattr(fetch, "load_stock_list", lambda m: pd.DataFrame(
        {"code": codes, "name": ["甲", "乙", "丙", "丁"], "market": "TWSE", "industry": "電子"}))
    days = [str(dt.date(2026, 7, 1) + dt.timedelta(days=i)) for i in range(70)]
    monkeypatch.setattr(fetch, "load_history", lambda: pd.DataFrame(
        [{"date": d, "code": c, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000} for d in days for c in codes]))
    monkeypatch.setattr(corpact, "adjust", lambda h, upto=None: h)
    monkeypatch.setattr(enrich, "load", lambda: pd.DataFrame())
    q = pd.DataFrame([  # 5 日均量 1000 張；13:12 約成交全天 9 成，3000 張 → 預估 3.3 倍
        {"code": "1111", "price": 104, "open": 101, "vol_lots": 3000},   # 符合
        {"code": "2222", "price": 104, "open": 101, "vol_lots": 2000},   # 量不夠
        {"code": "3333", "price": 104, "open": 105, "vol_lots": 3000},   # 黑 K
        {"code": "4444", "price": 100.5, "open": 100, "vol_lots": 3000}])  # 沒過前高、漲不到 3%
    q = q.assign(name="x", high=q.price, low=q.open, yclose=100.0, time="13:12:00", date="20261014",
                 bid1=q.price, ask1=None, bid1_lots=10, ask1_lots=None)
    monkeypatch.setattr(intraday, "QUOTES", (q, "20261014"))
    assert intraday.main(["--no-notify"]) == 0
    p = pd.read_csv(tmp_path / "data" / "positions.csv", dtype={"code": str})
    assert list(p.code) == ["1111"] and p.signal_date[0] == "2026-10-14" and p.signal_time[0] == "13:12"
    assert p.alert_price[0] == 104 and p.surge_volume[0] == 3_000_000 and p.status[0] == "open"
    assert json.loads((tmp_path / "data" / "intraday_state.json").read_text())["last_run"] == "2026-10-14"
    assert intraday.main(["--no-notify"]) == 0      # 同一天再叫：已經掃描過，略過
    assert len(pd.read_csv(tmp_path / "data" / "positions.csv")) == 1


def test_revdrift_pick6():
    """revdrift 抽 6 檔（虛擬帳戶 revdrift 桶照這個買）：股價 >2,000 不抽；零股量不夠（前 5 日均量×20% 買不到 2,000 元）不抽；
    同一批次日一定抽出同一組（Cowork 自己算要對得上）。"""
    from screener import revdrift
    b = [f"{1000 + i}" for i in range(10)]
    px = pd.Series({c: 50.0 for c in b} | {"1000": 2500.0})                 # 1000 太貴
    days = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]
    odd = pd.DataFrame(1000, index=days, columns=b)                          # 均量 1000 股 ×20%＝200 股 ≥ 40 股
    odd["1001"] = 100                                                        # 20 股 < 40 股（2,000 元 ÷ 50）
    p1, n1 = revdrift._pick6(b, px, odd, days, "2026-10-13")
    p2, _ = revdrift._pick6(list(reversed(b)), px, odd, days, "2026-10-13")
    assert n1 == 8 and len(p1) == 6 and not {"1000", "1001"} & set(p1)
    assert p1 == p2                                                          # 跟傳進來的順序無關
    p3, n3 = revdrift._pick6(b, px, None, days, "2026-10-13")                 # 沒零股資料：只看股價
    assert n3 is None and "1000" not in p3 and len(p3) == 6
    assert revdrift._pick6(b, px, odd, days[1:], "2026-10-13")[1] is None    # 不滿 5 天零股資料：不用零股門檻


def test_notify_falls_back_to_issue(monkeypatch):
    """沒設 Gmail（現在就是這樣）→ 開 GitHub Issue 通知 Dennis；body 太長截到 65,000 字（GitHub 上限 65,536）。
    有設 Gmail → 寄信、不開 Issue。"""
    import requests

    from screener import notify
    calls = []

    class R:
        status_code = 201

        def json(self):
            return {"html_url": "https://github.com/o/r/issues/1"}

    monkeypatch.setattr(requests, "post", lambda url, headers=None, data=None, timeout=None: calls.append((url, headers, json.loads(data))) or R())
    for k in ("SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    notify.send("⚠️ 台股系統體檢", "x" * 70000)
    url, headers, data = calls[0]
    assert url == "https://api.github.com/repos/o/r/issues" and headers["Authorization"] == "Bearer t"
    assert data["title"] == "⚠️ 台股系統體檢" and len(data["body"]) == 65000

    sent = []
    monkeypatch.setenv("SMTP_USER", "a@b")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setattr(notify, "send_email", lambda subject, body: sent.append(subject))
    notify.send("盤中預警", "| a |\n|---|\n| 1 |")
    assert sent == ["盤中預警"] and len(calls) == 1


def test_fetch_official_day_parse(monkeypatch):
    """每日收盤行情（證交所 MI_INDEX、櫃買 dailyQuotes）：找對表格、數字去千分位、沒成交的 -- 變空、休市回 None、
    證交所舊版 fieldsN／dataN 也吃。格式一變每日流程就整個停，所以用官方回應的樣子固定住。"""
    from screener import fetch
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    tw_f = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價",
            "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]
    tw_rows = [["2330", "台積電", "31,234,567", "45,678", "80,000,000,000", "2,560.00", "2,590.00", "2,555.00", "2,585.00",
                "<p style= color:red>+</p>", "25.00", "2,585.00", "120", "2,590.00", "88", "28.5"],
               ["9999", "停牌股", "0", "0", "0", "--", "--", "--", "--", "", "0.00", "--", "0", "--", "0", "0.00"]]
    tp_f = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數"]
    tp_rows = [["6488", "環球晶", "1,020.00", "+15.00", "1,005.00", "1,025.00", "1,000.00", "1,013.2", "1,234,567", "1", "1"]]
    replies = {}

    class S:
        def get(self, url, params=None, headers=None, timeout=None):
            return type("R", (), {"status_code": 200, "json": lambda self, j=replies[url.split("/")[-1]]: j})()

    d = dt.date(2026, 10, 13)
    replies["MI_INDEX"] = {"stat": "OK", "tables": [{"title": "大盤統計", "fields": ["指數", "收盤指數"], "data": [["加權", "1"]]},
                                                    {"title": "每日收盤行情", "fields": tw_f, "data": tw_rows}]}
    df = fetch.fetch_twse_day(S(), d).set_index("code")
    assert df.close["2330"] == 2585 and df.volume["2330"] == 31234567 and df.open["2330"] == 2560 and df.name["2330"] == "台積電"
    assert pd.isna(df.close["9999"]) and df.date.iloc[0] == "2026-10-13"
    replies["MI_INDEX"] = {"stat": "OK", "fields9": tw_f, "data9": tw_rows[:1]}            # 舊版格式
    assert fetch.fetch_twse_day(S(), d).close.iloc[0] == 2585
    replies["MI_INDEX"] = {"stat": "很抱歉，沒有符合條件的資料!"}                             # 休市
    assert fetch.fetch_twse_day(S(), d) is None
    replies["dailyQuotes"] = {"tables": [{"fields": tp_f, "data": tp_rows}]}
    t = fetch.fetch_tpex_day(S(), d).iloc[0]
    assert (t.code, t.close, t.open, t.high, t.low, t.volume) == ("6488", 1020, 1005, 1025, 1000, 1234567)


def test_institutional_parse(monkeypatch):
    """三大法人（證交所 T86、櫃買 dailyTrade）：外資取「不含外資自營商」那欄、自營商取合計不取自行／避險、股數換張；
    櫃買新版欄名沒寫法人別，照固定順序取。抓錯欄網站的外資連買、法人籌碼頁整個錯。"""
    from screener import enrich
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    tw_f = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
            "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數",
            "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
            "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]
    tw_row = ["2330", "台積電", "9,000,000", "4,000,000", "5,000,000", "0", "0", "777,000", "300,000", "100,000", "200,000",
              "-50,000", "0", "0", "-20,000", "0", "0", "-30,000", "5,150,000"]
    tp_f = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數"]
    tp_row = ["6488", "環球晶"] + [str(i * 1000) for i in range(1, 23)]                  # 第 i 欄（2 起算）＝(i-1) 張
    replies = {"T86": {"stat": "OK", "fields": tw_f, "data": [tw_row]}, "dailyTrade": {"tables": [{"fields": tp_f, "data": [tp_row]}]}}

    class R:
        def __init__(self, j):
            self.j = j

        def json(self):
            return self.j

    monkeypatch.setattr(enrich, "_get", lambda s, url, params=None: R(replies[url.split("/")[-1]]))
    df = enrich.institutional(None, dt.date(2026, 10, 13)).set_index("code")
    assert df.loc["2330"].to_dict() == {"foreign": 5000, "trust": 200, "dealer": -50, "inst_total": 5150}
    assert df.loc["6488"].to_dict() == {"foreign": 3, "trust": 12, "dealer": 21, "inst_total": 22}


def test_daytrade_and_pe_parse(monkeypatch):
    """當沖（證交所 TWTB4U、櫃買 intraday/stat）、本益比（BWIBBU_d、peQryDate）解析：欄名有空白也找得到、
    當沖股數換張、ETF／權證代號不收；本益比 -- 變空、殖利率淨值比對欄。"""
    from screener import chips, enrich
    monkeypatch.setattr(chips.time, "sleep", lambda s: None)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    replies = {
        "TWTB4U": {"tables": [{"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數",
                                          "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"],
                               "data": [["2330", "台積電", "", "5,432,000", "1", "1"], ["0050", "元大台灣50", "", "9,000", "1", "1"]]}]},
        "stat": {"tables": [{"fields": ["代號", "名稱", "當日沖銷交易 成交股數", "買進金額", "賣出金額"],
                             "data": [["6488", "環球晶", "123,000", "1", "1"], ["70001", "權證", "5,000", "1", "1"]]}]},
        "BWIBBU_d": {"tables": [{"fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
                                 "data": [["2330", "台積電", "2585", "0.70", "114", "28.50", "8.10", "115/2"],
                                          ["2409", "友達", "15", "0.00", "114", "--", "0.80", "115/2"]]}]},
        "peQryDate": {"tables": [{"fields": ["股票代號", "名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比", "財報年/季"],
                                  "data": [["6488", "環球晶", "35.20", "8", "114", "0.80", "3.30", "115/2"]]}]},
    }
    fake = lambda s, url, params=None: replies[url.split("/")[-1]]
    monkeypatch.setattr(chips, "_json", fake)
    monkeypatch.setattr(enrich, "_json", fake)
    d = chips.daytrade(None, dt.date(2026, 10, 13)).set_index("code").dt_vol.to_dict()
    assert d == {"2330": 5432.0, "6488": 123.0}
    p = enrich.pe(None, dt.date(2026, 10, 13)).set_index("code")
    assert p.loc["2330"].to_dict() == {"pe": 28.5, "yield": 0.7, "pb": 8.1}
    assert pd.isna(p.pe["2409"]) and p.pb["2409"] == 0.8
    assert p.loc["6488"].to_dict() == {"pe": 35.2, "yield": 0.8, "pb": 3.3}


def test_mis_price_and_volume_profile():
    """MIS 報價取價順序（盤中每輪都靠它）：最新成交 z → 最近一筆 pz → 買賣中價 → 只有一邊就用那邊；「-」當沒有。
    預估全日量用的成交量曲線：開盤前 0.05、13:12 約 0.91、收盤 1，中間線性內插、超出範圍夾住。"""
    from screener import intraday, live
    assert intraday._price({"z": "105.5", "pz": "105", "b": "105_", "a": "106_"}) == 105.5
    assert intraday._price({"z": "-", "pz": "104.5"}) == 104.5
    assert intraday._price({"z": "-", "b": "100.0_99.5_", "a": "101.0_101.5_"}) == 100.5
    assert intraday._price({"z": "-", "b": "100.0_", "a": "-"}) == 100.0
    assert intraday._price({"z": "-"}) is None
    t = lambda h, m: live.vol_fraction(dt.datetime(2026, 10, 14, h, m))
    assert t(8, 50) == 0.05 and t(13, 30) == 1.0 and t(14, 0) == 1.0
    assert abs(t(13, 12) - (0.87 + 12 / 25 * 0.08)) < 1e-9 and abs(t(9, 15) - 0.17) < 1e-9


def test_live_holdings_view(monkeypatch):
    """盤中頁「持股」：預估全日量 < 爆量日一半 → 可能量縮出場；在漲停價上 → 漲停續抱（量縮不算，跟收盤規則一致）；
    跌超過 8% 標警示；剩幾天到 20 天上限；今天才進的、已出場的不列。"""
    from screener import live, positions
    pos = pd.DataFrame([
        {"code": "A", "name": "甲", "signal_date": "2026-10-08", "status": "open", "entry_price": 100.0, "alert_price": 99.0, "surge_volume": 3e6, "days_held": 3},
        {"code": "B", "name": "乙", "signal_date": "2026-10-08", "status": "open", "entry_price": 100.0, "alert_price": 99.0, "surge_volume": 3e6, "days_held": 3},
        {"code": "C", "name": "丙", "signal_date": "2026-09-15", "status": "open", "entry_price": None, "alert_price": 100.0, "surge_volume": 3e6, "days_held": 19},
        {"code": "D", "name": "丁", "signal_date": "2026-10-14", "status": "open", "entry_price": None, "alert_price": 50.0, "surge_volume": 3e6, "days_held": None},
        {"code": "E", "name": "戊", "signal_date": "2026-10-01", "status": "closed", "entry_price": 100.0, "alert_price": 99.0, "surge_volume": 3e6, "days_held": 5}])
    monkeypatch.setattr(positions, "load", lambda: pos)
    q = pd.DataFrame({"code": list("ABCDE"), "price": [95.0, 110.0, 90.0, 52.0, 120.0], "vol_lots": [1000, 1000, 2000, 5000, 5000],
                      "yclose": [96.0, 100.0, 91.0, 50.0, 110.0], "chg": [-1.0, 10.0, -1.1, 4.0, 9.1]})
    out = live.holdings_view(q, dt.datetime(2026, 10, 14, 13, 12), shrink=0.5, drop_pct=8)
    by = {r["code"]: r for r in out}
    assert set(by) == {"A", "B", "C"} and [r["code"] for r in out] == ["C", "A", "B"]   # 照報酬由低到高
    assert by["A"]["status"] == "可能量縮出場" and by["A"]["ret"] == -5 and not by["A"]["drop_alert"]
    assert by["B"]["status"] == "漲停續抱（量縮不算）"
    assert by["C"]["status"] == "量能維持" and by["C"]["drop_alert"] and by["C"]["days_left"] == 0 and by["C"]["entry"] == 100
    assert by["A"]["days_left"] == 16 and by["A"]["shrink_lots"] == 1500


def test_today_ledger_mix():
    """首頁 🧾 虛擬帳戶組成：照 role 分桶（0050 一律算底倉），題材、0050 底倉固定列（沒部位也列 0），其他桶有部位才列，加現金 %。"""
    from screener import today
    lg = {"equity": 100000, "cash": 10000, "positions": [
        {"code": "2330", "role": "core", "mv": 20000}, {"code": "2317", "role": "core", "mv": 10000},
        {"code": "0050", "role": None, "mv": 50000}, {"code": "3711", "role": "revdrift", "mv": 10000}]}
    m = today.ledger_mix(lg)
    assert m["parts"] == [{"name": "題材", "n": 2, "pct": 30.0}, {"name": "revdrift", "n": 1, "pct": 10.0},
                          {"name": "0050 底倉", "n": 1, "pct": 50.0}]
    assert m["cash_pct"] == 10.0
    m = today.ledger_mix({"equity": 100000, "cash": 100000, "positions": [{"code": "6488", "role": "satellite", "mv": 1}]})
    assert [x["name"] for x in m["parts"]] == ["題材", "衛星", "0050 底倉"] and m["parts"][0]["n"] == 0
    assert today.ledger_mix({"equity": 100000, "positions": []}) is None


def test_probe_split_arg():
    """probe.yml：『網址|關鍵字』，但 MIS 多檔的 ex_ch=a|b 是網址的一部分（10/14 踩過，查錯成單檔）。"""
    from tools.probe import split_arg
    assert split_arg("https://x/y?a=1|成交價") == ("https://x/y?a=1", "成交價")
    assert split_arg("https://x/y") == ("https://x/y", "")
    mis = "https://mis.twse.com.tw/stock/api/getOddInfo.jsp?ex_ch=tse_2330.tw|otc_6488.tw&json=1"
    assert split_arg(mis) == (mis, "")
    assert split_arg(mis + "|台積電") == (mis, "台積電")
    assert split_arg("https://m/api?ex_ch=tse_2330.tw|tse_2317.tw") == ("https://m/api?ex_ch=tse_2330.tw|tse_2317.tw", "")


def test_not_json_reason_maintenance(monkeypatch):
    """證交所半夜維護回「網站維護中」HTML：log 寫維護中，不要誤寫成被封鎖（10/14 04:00 實測）。"""
    import pytest

    from screener import fetch

    page = "<!DOCTYPE html><html><head><title>網站維護中 - 臺灣證券交易所</title></head><body>系統維護中</body></html>"

    class R:
        status_code, text = 200, page

        def json(self):
            raise ValueError("not json")

    class S:
        def get(self, *a, **k):
            return R()

    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    with pytest.raises(fetch.SourceUnavailable, match="網站維護中"):
        fetch._get_json(S(), "https://www.twse.com.tw/x", {})
    assert "封鎖" in fetch.not_json_reason("<html><body>Forbidden</body></html>")
