"""追蹤盤中提醒過的股票，收盤後檢查「量縮」出場條件。

檔案：data/positions.csv（每一列是一筆盤中提醒的訊號）
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "positions.csv"
COLS = ["code", "name", "industry", "signal_date", "signal_time", "alert_price", "entry_price",
        "surge_volume", "status", "exit_signal_date", "exit_reason", "exit_close", "days_held", "est_return_pct",
        "exit_open", "close_lu", "lu_lock_min", "rev_yoy",
        # 排隊買漲停買不買得到：正式提醒時、收盤後的委買／委賣第一檔張數與累計成交張數
        "bid1_lots_1312", "ask1_lots_1312", "vol_lots_1312", "bid1_lots_close", "ask1_lots_close", "vol_lots_close",
        # 訊號日開盤前就知道的注意／處置狀態（前一交易日以前的公告），與前 7 天被注意幾天
        "warn", "warn_n5"]


def load() -> pd.DataFrame:
    text = ["code", "name", "industry", "signal_date", "signal_time", "status", "exit_signal_date", "exit_reason", "warn"]
    if FILE.exists():
        df = pd.read_csv(FILE, dtype={c: str for c in text})
    else:
        df = pd.DataFrame(columns=COLS)
    df = df.reindex(columns=list(dict.fromkeys([*COLS, *df.columns])))  # 舊檔沒有 exit_open 也能讀
    return df.astype({c: object for c in text})


def save(df: pd.DataFrame) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    df[COLS].to_csv(FILE, index=False)


def add_signals(rows: list[dict]) -> None:
    df = load()
    new = pd.DataFrame(rows)
    new["status"] = "open"
    df = pd.concat([df, new], ignore_index=True)
    df = df.drop_duplicates(["code", "signal_date"], keep="first")
    save(df)


def _limit_up(close: float, prev: float | None) -> bool:
    """收盤是否在漲停價（跟 rules.limit_price 同一套檔位）。"""
    if not prev or prev <= 0:
        return False
    raw = prev * 1.1
    tick = 0.01 if raw < 10 else 0.05 if raw < 50 else 0.1 if raw < 100 else 0.5 if raw < 500 else 1.0 if raw < 1000 else 5.0
    return close >= np.floor(raw / tick + 1e-6) * tick - 1e-6


def set_lock_minutes(day: str, mins: dict[str, int]) -> None:
    """盤中監控結束時寫入：收盤前連續鎖在漲停價幾分鐘（0 = 收盤前沒鎖住）。"""
    df = load()
    m = df.signal_date == day
    if not m.any():
        return
    df.loc[m, "lu_lock_min"] = [mins.get(c, 0) for c in df.loc[m, "code"]]
    save(df)


def set_close_book(day: str, book: dict[str, dict]) -> None:
    """收盤後的委買／委賣第一檔張數與全日成交張數（盤中監控 13:31 抓一次）。"""
    df = load()
    for i in df.index[df.signal_date == day]:
        b = book.get(df.at[i, "code"]) or {}
        for k in ("bid1_lots", "ask1_lots", "vol_lots"):
            df.at[i, f"{k}_close"] = b.get(k)
    save(df)


def update(hist: pd.DataFrame, data_date: str, shrink: float = 0.5, max_hold: int = 20,
           stop_pct: float | None = None) -> tuple[list[dict], list[dict]]:
    """用收盤後的完整資料更新持有中的訊號。回傳 (今天出現出場訊號的, 仍持有的)。"""
    df = load()
    if df.empty:
        return [], []
    dates = sorted(hist.date.unique())
    day = hist[hist.date == data_date].set_index("code")
    prev_date = max((d for d in dates if d < data_date), default=None)
    prev = hist[hist.date == prev_date].set_index("code").close if prev_date else pd.Series(dtype=float)
    # 出場訊號是收盤後才出現、隔天開盤賣：已出場的補上隔天開盤價，報酬改用開盤價算
    opens = hist.set_index(["date", "code"]).open
    for i, r in df[(df.status == "closed") & df.exit_open.isna() & df.exit_signal_date.notna()].iterrows():
        nxt = next((d for d in dates if r.exit_signal_date < d <= data_date), None)
        o = opens.get((nxt, r.code)) if nxt else None
        if o is not None and pd.notna(o) and o > 0:
            entry = float(r.entry_price) if pd.notna(r.entry_price) else float(r.alert_price)
            df.at[i, "exit_open"] = float(o)
            df.at[i, "est_return_pct"] = round((float(o) / entry - 1) * 100, 2)
    exits, holding = [], []
    for i, r in df.iterrows():
        if r.status != "open" or r.code not in day.index:
            if r.status == "open":
                holding.append(r.to_dict())
            continue
        vol, close = float(day.at[r.code, "volume"]), float(day.at[r.code, "close"])
        if r.signal_date == data_date:
            # 爆量當天：用收盤資料確認進場價與爆量日成交量
            df.at[i, "entry_price"] = close
            df.at[i, "surge_volume"] = vol
            df.at[i, "days_held"] = 0
            df.at[i, "close_lu"] = int(_limit_up(close, prev.get(r.code)))  # 收盤鎖漲停的那群才有超額，要追蹤買不買得到
            d = df.loc[i].to_dict()
            d.update(vol_lots=vol / 1000, shrink_lots=shrink * vol / 1000, days_left=max_hold)
            holding.append(d)
            continue
        if r.signal_date not in dates:
            continue
        held = sum(1 for d in dates if r.signal_date < d <= data_date)
        entry = float(r.entry_price) if pd.notna(r.entry_price) else float(r.alert_price)
        surge = float(r.surge_volume) if pd.notna(r.surge_volume) else None
        df.at[i, "days_held"] = held
        df.at[i, "est_return_pct"] = round((close / entry - 1) * 100, 2)
        reason = None
        if stop_pct and close <= entry * (1 - stop_pct / 100):
            reason = f"收盤跌破進場價 {stop_pct:g}%（停損）"
        elif surge and vol < shrink * surge and not _limit_up(close, prev.get(r.code)):  # 漲停那天量縮不算，續抱
            reason = f"量縮至爆量日 {vol / surge:.0%}"
        elif held >= max_hold:
            reason = f"已持有 {held} 天（上限）"
        if reason:
            df.at[i, "status"] = "closed"
            df.at[i, "exit_signal_date"] = data_date
            df.at[i, "exit_reason"] = reason
            df.at[i, "exit_close"] = close
            exits.append(df.loc[i].to_dict())
        else:
            d = df.loc[i].to_dict()
            d["vol_ratio"] = vol / surge if surge else None
            d.update(vol_lots=vol / 1000, shrink_lots=shrink * surge / 1000 if surge else None,
                     days_left=max(max_hold - held, 0), lu_today=_limit_up(close, prev.get(r.code)))
            holding.append(d)
    save(df)
    log.info("出場訊號 %d 檔，持有中 %d 檔", len(exits), len(holding))
    return exits, holding


def build_md(exits: list[dict], holding: list[dict]) -> str:
    if not exits and not holding:
        return ""
    lines = []
    if exits:
        lines += ["## 🔴 出場提醒（明天開盤賣出）", "",
                  "| 股票 | 進場日 | 進場價(收盤) | 今日收盤 | 估計報酬 | 原因 |", "|---|---|--:|--:|--:|---|"]
        for r in exits:
            lines.append(f"| {r['code']} {r['name']} | {r['signal_date']} | {r['entry_price']:.2f} | "
                         f"{r['exit_close']:.2f} | {r['est_return_pct']:+.2f}% | {r['exit_reason']} |")
        lines.append("")
    if holding:
        lines += ["## 🟡 持有中（尚未量縮）", "",
                  "| 股票 | 進場日 | 進場價 | 已持有 | 估計報酬 | 今日量 / 爆量日 | 今日量(張) | 出場門檻(張) | 最多再抱 |",
                  "|---|---|--:|--:|--:|--:|--:|--:|--:|"]
        n = lambda v: f"{v:,.0f}" if v is not None and pd.notna(v) else "—"  # noqa: E731
        for r in holding:
            vr = r.get("vol_ratio")
            ep = r.get("entry_price") if pd.notna(r.get("entry_price")) else r.get("alert_price")
            lu = "（今天收漲停，量縮不算）" if r.get("lu_today") else ""
            lines.append(f"| {r['code']} {r['name']} | {r['signal_date']} | "
                         f"{ep:.2f} | {int(r['days_held']) if pd.notna(r.get('days_held')) else 0} 天 | "
                         f"{(str(r['est_return_pct']) + '%') if pd.notna(r.get('est_return_pct')) else '—'} | "
                         f"{(f'{vr:.0%}') if vr else '—'}{lu} | {n(r.get('vol_lots'))} | {n(r.get('shrink_lots'))} | "
                         f"{(str(int(r['days_left'])) + ' 天') if r.get('days_left') is not None and pd.notna(r.get('days_left')) else '—'} |")
        lines.append("")
    lines.append("<sub>出場規則：收盤成交量低於爆量日的一半（買盤退潮，漲停那天不算）→ 隔天開盤賣出；最多持有 20 天。「出場門檻」＝爆量日成交量的一半，哪天收盤量低於它（而且沒收漲停）就出場。出場後的報酬以隔天開盤價計。清單包含所有盤中提醒過的股票（不代表你實際買了）。</sub>")
    return "\n".join(lines) + "\n\n"


def fill_warn(dates: list[str]) -> int:
    """把還沒填的 warn／warn_n5 補上（收盤後跑；用 enrich 的注意處置歷史，口徑是訊號日前一交易日以前公告的）。"""
    from . import enrich
    if not enrich.WARN_HIST.exists():
        return 0
    h = pd.read_csv(enrich.WARN_HIST, dtype=str)
    df = load()
    todo = df[df.warn_n5.isna() & df.signal_date.notna()].index
    for i in todo:
        day = df.at[i, "signal_date"]
        prev = max((d for d in dates if d < day), default=None)
        if prev is None or h.date.min() > prev:
            continue   # 歷史還沒涵蓋到這天，不亂填
        w, n = enrich.warn_at(df.at[i, "code"], day, h, prev)
        df.at[i, "warn"], df.at[i, "warn_n5"] = w, n
    save(df)
    return len(todo)
