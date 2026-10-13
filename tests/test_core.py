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
def test_live_odd_rows():
    from screener import live
    msg = [{"c": "2330", "t": "13:30:00", "z": "2490.0000", "v": "34420", "a": "2495.0000_2500.0000_2505.0000_2510.0000_2515.0000_",
            "b": "2490.0000_2485.0000_2480.0000_2475.0000_2470.0000_", "f": "268_305_467_634_412_", "g": "764_974_1690_378_457_"},
           {"c": "6789", "t": "13:12:00", "z": "-", "pz": "88.5", "v": "0", "a": "-", "b": "88.5000_", "f": "-", "g": "12000_"}]
    r = live.odd_rows(msg)
    assert r[0]["odd_bid1"] == 2490 and r[0]["odd_bid1_sh"] == 764 and r[0]["odd_bid5_sh"] == 764 + 974 + 1690 + 378 + 457
    assert r[0]["odd_ask5_sh"] == 268 + 305 + 467 + 634 + 412 and r[0]["odd_vol_sh"] == 34420
    assert r[1]["odd_ask1"] is None and r[1]["odd_ask5_sh"] == 0 and r[1]["odd_bid1_sh"] == 12000 and r[1]["odd_price"] == 88.5   # 鎖漲停：沒人賣


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
