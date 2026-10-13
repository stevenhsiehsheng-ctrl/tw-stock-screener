"""虛擬帳戶帳本存檔前檢查（Claude Code 16:25 排程用）：協作板 ledger_daily/日期 → data/ledger/日期.json。

    python -m screener.ledger_save 匯出的.json            # 只檢查
    python -m screener.ledger_save 匯出的.json --write    # 檢查沒有錯誤才存

匯出的檔可以是協作板文件整份（{"id", "data": {...}}）或只有帳本本身。
- 錯誤（不存）：缺必要欄位、日期格式不對或不是交易日、note 沒寫只含虛擬帳戶、出現真實持股相關字眼
- 警告（照存、要回報 Cowork）：持股市值＋現金≠總資產、市值≠股數×收盤、收盤價跟 history／ETF 對不上、各桶現金加總≠現金
格式跟現有檔一樣（key 排序、縮排 1、中文不跳脫），ledger_nav／ledgerpage 讀得到。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "ledger"
NEED = ("date", "equity", "cash", "positions", "trades", "nav", "note")
# 真實持股（Dennis、媽媽）絕對不能進 repo：帳本裡出現這些字就擋下，請 Cowork 拿掉再寫
PRIVATE = ("Dennis", "dennis", "媽媽", "真實持股", "真實帳戶", "持股日報", "encrypted", "密碼", "ciphertext")


def _closes(date: str) -> dict:
    """那天各股收盤：個股看 history，ETF 看 etf.csv。"""
    out = {}
    for f in (DATA / "history.csv.gz", DATA / "extras" / "etf.csv.gz"):
        try:
            df = pd.read_csv(f, usecols=["date", "code", "close"], dtype={"code": str})
            out.update(df[df.date == date].set_index("code").close.to_dict())
        except Exception:  # noqa: BLE001
            pass
    return out


def _days() -> list[str]:
    try:
        return sorted(pd.read_csv(DATA / "history.csv.gz", usecols=["date"]).date.unique())
    except Exception:  # noqa: BLE001
        return []


def check(doc: dict, days: list[str] | None = None, closes: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """回傳 (帳本, 錯誤, 警告)。"""
    led = doc.get("data", doc) if isinstance(doc.get("data"), dict) else doc
    err, warn = [], []
    miss = [k for k in NEED if k not in led]
    if miss:
        err.append(f"缺欄位：{'、'.join(miss)}")
    d = str(led.get("date", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        err.append(f"日期格式不對：{d!r}")
    else:
        days = _days() if days is None else days
        if days and d not in days:
            err.append(f"{d} 不是交易日（history 裡沒有這天；history 最新 {days[-1]}）")
    if "虛擬" not in str(led.get("note", "")):
        err.append("note 沒寫只含虛擬帳戶")
    text = json.dumps(led, ensure_ascii=False)
    hit = [w for w in PRIVATE if w in text]
    if hit:
        err.append(f"出現真實持股相關字眼（{'、'.join(hit)}），真實持股不能進 repo")
    if err:
        return led, err, warn

    pos = led.get("positions") or []
    mv = sum(float(p.get("mv") or 0) for p in pos)
    if abs(mv + float(led["cash"]) - float(led["equity"])) > 1:
        warn.append(f"持股市值 {mv:,.1f}＋現金 {float(led['cash']):,.1f}＝{mv + float(led['cash']):,.1f}，"
                    f"總資產寫 {float(led['equity']):,.1f}（差 {float(led['equity']) - mv - float(led['cash']):+.1f}）")
    closes = _closes(d) if closes is None else closes
    for p in pos:
        sh, c, m = p.get("shares"), p.get("close"), p.get("mv")
        if None not in (sh, c, m) and abs(float(sh) * float(c) - float(m)) > 1:
            warn.append(f"{p.get('code')} 市值 {m} ≠ {sh} 股 × {c}")
        ref = closes.get(str(p.get("code")))
        if c is not None and ref is not None and abs(float(c) - float(ref)) > 0.011:
            warn.append(f"{p.get('code')} 收盤寫 {c}，{d} 收盤是 {ref}")
    cbb = led.get("cash_by_bucket")
    if isinstance(cbb, dict) and abs(sum(float(v or 0) for v in cbb.values()) - float(led["cash"])) > 1:
        warn.append(f"各桶現金加總 {sum(float(v or 0) for v in cbb.values()):,.1f} ≠ 現金 {float(led['cash']):,.1f}")
    late = [t for t in led.get("trades") or [] if str(t.get("date", "")) > d]
    if late:
        warn.append(f"有 {len(late)} 筆交易日期在帳本日期之後")
    return led, err, warn


def save(led: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{led['date']}.json"
    f.write_text(json.dumps(led, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8")
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description="虛擬帳戶帳本存檔前檢查")
    ap.add_argument("file", help="協作板 ledger_daily 匯出的 JSON")
    ap.add_argument("--write", action="store_true", help="沒有錯誤就存到 data/ledger/日期.json")
    a = ap.parse_args()
    led, err, warn = check(json.loads(Path(a.file).read_text("utf-8")))
    for e in err:
        print("❌", e)
    for w in warn:
        print("⚠️", w)
    if err:
        print("有錯誤，沒有存檔")
        return 1
    print(f"✅ {led['date']}：總資產 {float(led['equity']):,.1f}、持股 {len(led.get('positions') or [])} 檔、"
          f"交易 {len(led.get('trades') or [])} 筆、待執行 {len(led.get('pending') or [])} 筆")
    if a.write:
        print("已存：", save(led).relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
