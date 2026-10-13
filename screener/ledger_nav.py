"""虛擬帳戶拆桶績效（Cowork 1358／1455）：每一桶自己的時間加權報酬，跟同期 0050 含息比。

帳戶總累計含 0050 底倉，看不出選股有沒有用；這裡把 data/ledger/*.json（Cowork 15:47 結算、本尊 16:25 存檔）
按桶拆開：題材 core、revdrift、衛星 satellite、突破 breakout、0050 底倉 etf；cash_by_bucket 出現前（10/8 以前）
沒分桶的現金算一個「現金」桶。

- 每桶市值＝該桶持股市值＋該桶現金。每天的損益（執行損益口徑）＝持股市值變化 − 買進花的錢（含手續費）＋賣出拿回的錢（扣費稅），
  照 trades 的 bucket 算；桶跟桶之間撥款（例如賣 0050 撥給 revdrift）是「資金進出」，不算損益。
- 對帳：①各桶資金進出加總要等於 0（＝各桶損益加總等於「持股市值＋現金」的變化，差 <1 元），對不上就是帳本漏記交易或除息沒入帳；
  ②帳本自己的 equity 欄要等於持股市值＋現金。報酬一律用持股市值＋現金算（10/7 那份 equity 欄少了 51.4 元，10/8 又對上）。
- 報酬：份額法（時間加權），撥款當天收盤進出；當天才開張的桶，當天報酬＝執行損益 ÷ 撥進來的錢。
- 0050 含息：帳本的 bench_tr_cum，從該桶開張那天收盤起算。

用法：python -m screener.ledger_nav [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "ledger"
NAMES = {"core": "題材", "revdrift": "revdrift", "satellite": "衛星", "breakout": "突破", "etf": "0050 底倉", "cash": "現金"}
ORDER = ["core", "revdrift", "satellite", "breakout", "etf", "cash"]
TOL = 1.0


def _bucket(role: str | None, code: str | None) -> str:
    return "etf" if role == "etf" or code == "0050" else role or "other"


def _values(lg: dict) -> dict[str, float]:
    v: dict[str, float] = {}
    for x in lg.get("positions") or []:
        b = _bucket(x.get("role"), x.get("code"))
        v[b] = v.get(b, 0.0) + float(x.get("mv") or 0)
    cbb = lg.get("cash_by_bucket")
    for b, c in (cbb.items() if cbb else [("cash", lg.get("cash") or 0)]):
        v[b] = v.get(b, 0.0) + float(c or 0)
    return v


def _holdings(lg: dict) -> dict[tuple[str, str], tuple[float, float]]:
    """(桶, 代號) → (股數, 收盤)"""
    out = {}
    for x in lg.get("positions") or []:
        out[(_bucket(x.get("role"), x.get("code")), str(x.get("code")))] = (float(x.get("shares") or 0), float(x.get("close") or 0))
    return out


def _pnl(prev: dict | None, lg: dict) -> tuple[dict[str, float], list[str]]:
    """當天各桶損益＝持股市值變化 − 淨買進現金；回傳 (損益, 股數對不上的警告)。"""
    h0 = _holdings(prev) if prev else {}
    h1 = _holdings(lg)
    pnl: dict[str, float] = {}
    net_sh: dict[tuple[str, str], float] = {}
    for t in lg.get("trades") or []:
        k = (_bucket(t.get("bucket"), t.get("code")), str(t.get("code")))
        s, p = float(t.get("shares") or 0), float(t.get("price") or 0)
        cost = float(t.get("fee") or 0) + float(t.get("tax") or 0)
        cash = -s * p - cost if t.get("side") == "buy" else s * p - cost
        pnl[k[0]] = pnl.get(k[0], 0.0) + cash
        net_sh[k] = net_sh.get(k, 0.0) + (s if t.get("side") == "buy" else -s)
    warn = []
    for k in set(h0) | set(h1) | set(net_sh):
        s0, c0 = h0.get(k, (0.0, 0.0))
        s1, c1 = h1.get(k, (0.0, 0.0))
        if abs(s0 + net_sh.get(k, 0.0) - s1) > 1e-6:
            warn.append(f"{lg['date']} {k[1]}（{NAMES.get(k[0], k[0])}）股數對不上：昨天 {s0:g} ＋ 今天買賣 {net_sh.get(k, 0):+g} ≠ 今天 {s1:g}")
        pnl[k[0]] = pnl.get(k[0], 0.0) + s1 * c1 - s0 * c0
    return pnl, warn


def compute(ledgers: list[dict] | None = None) -> dict | None:
    if ledgers is None:
        ledgers = [json.loads(f.read_text("utf-8")) for f in sorted(LEDGER.glob("20??-??-??.json"))]
    if not ledgers:
        return None
    first = ledgers[0]
    capital = round(float(first["equity"]) / float(first["nav"])) if first.get("nav") else float(first["equity"])
    prev, v0 = None, {"cash": float(capital)}
    eq0 = float(capital)
    st: dict[str, dict] = {}          # 桶 → {start, idx, b0, days, value}
    days, warns, resid_max = [], [], 0.0
    for lg in ledgers:
        d = lg["date"]
        v1 = _values(lg)
        pnl, w = _pnl(prev, lg)
        warns += w
        eq1 = sum(v1.values())
        if abs(float(lg["equity"]) - eq1) > TOL:
            warns.append(f"{d} 帳本 equity {float(lg['equity']):,.1f} ≠ 持股市值＋現金 {eq1:,.1f}（差 {float(lg['equity']) - eq1:+.1f}）")
        bench = float(lg.get("bench_tr_cum") or 0)
        flows = {}
        for b in set(v0) | set(v1) | set(pnl):
            p = pnl.get(b, 0.0)
            f = v1.get(b, 0.0) - v0.get(b, 0.0) - p
            flows[b] = f
            base = v0.get(b, 0.0)
            den = base if base > TOL else max(f, 0.0)
            r = p / den if den > TOL else 0.0
            s = st.get(b)
            if s is None or (base <= TOL and s["value"] <= TOL and f > TOL):   # 開張（或關掉後又撥錢進來，重新起算）
                if v1.get(b, 0.0) <= TOL and abs(p) <= TOL:
                    continue
                s = st[b] = {"start": d, "idx": 1.0, "b0": bench, "days": 0, "value": 0.0, "pnl": 0.0, "flow": 0.0}
            s["idx"] *= 1 + r
            s["days"] += 1
            s["value"] = v1.get(b, 0.0)
            s["pnl"] += p
            s["flow"] += f
            s["bench"] = bench
            s["end"] = d
        resid = (eq1 - eq0) - sum(pnl.values())
        resid_max = max(resid_max, abs(resid), abs(sum(flows.values())))
        days.append({"date": d, "equity": eq1, "resid": round(resid, 2),
                     "value": {b: round(x, 1) for b, x in v1.items()}, "pnl": {b: round(x, 1) for b, x in pnl.items()},
                     "flow": {b: round(x, 1) for b, x in flows.items() if abs(x) > TOL}})
        if abs(resid) > TOL:
            warns.append(f"{d} 各桶損益加總 {sum(pnl.values()):+.1f} ≠ 持股市值＋現金的變化 {eq1 - eq0:+.1f}（差 {resid:+.1f}，交易沒記齊？）")
        prev, v0, eq0 = lg, v1, eq1
    last = ledgers[-1]["date"]
    out = {}
    for b in sorted(st, key=lambda k: ORDER.index(k) if k in ORDER else 99):
        s = st[b]
        ret = (s["idx"] - 1) * 100
        bch = ((1 + s["bench"] / 100) / (1 + s["b0"] / 100) - 1) * 100
        out[b] = {"name": NAMES.get(b, b), "start": s["start"], "end": s["end"], "open": s["end"] == last and s["value"] > TOL,
                  "days": s["days"], "value": round(s["value"], 1), "pnl": round(s["pnl"], 1),
                  "ret": round(ret, 2), "bench": round(bch, 2), "excess": round(ret - bch, 2)}
    return {"asof": last, "capital": capital, "buckets": out, "resid_max": round(resid_max, 2), "ok": resid_max <= TOL,
            "warnings": warns, "days": days}


def main():
    ap = argparse.ArgumentParser(description="虛擬帳戶拆桶績效")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = compute()
    if r is None:
        print("沒有帳本")
        return
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    print(f"帳本到 {r['asof']}，起始資金 {r['capital']:,}；對帳最大差 {r['resid_max']} 元（{'OK' if r['ok'] else '對不上'}）")
    for b, x in r["buckets"].items():
        if b == "cash":
            continue
        print(f"{x['name']:<8} {x['start']}～{x['end']}{'' if x['open'] else '（已結束）'} {x['days']:>2} 天  市值 {x['value']:>9,.1f}  "
              f"損益 {x['pnl']:>+8.1f}  報酬 {x['ret']:>+6.2f}%  0050 {x['bench']:>+6.2f}%  超額 {x['excess']:>+6.2f}")
    for w in r["warnings"]:
        print("⚠️", w)


if __name__ == "__main__":
    main()
