"""盤中連續監控：09:00~13:25 每幾分鐘掃描全市場一次。

1. 早期預警：股票一出現「預估爆量＋上漲＋突破 60 日新高」就記下來（網頁即時顯示，Email 每 30 分鐘彙整一次）
2. 持股即時：盤中提醒過、還沒出場的股票，顯示即時報酬與「今天會不會量縮出場」的預估
3. 13:12 正式進場提醒：呼叫 intraday.py（與回測相同條件），同一天只做一次
結果寫成 live.json 推到儲存庫的 live 分支，網頁 live.html 每分鐘去抓。

用法：python -m screener.live [--test]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from . import corpact, enrich, fetch, global_mkt, intraday, notify, positions, rules, sentiment, tech
from .qday import quarter_info

log = logging.getLogger("live")
TZ = ZoneInfo("Asia/Taipei")
ROOT = fetch.ROOT
STATE_F = ROOT / "data" / "live_state.json"
OUT_F = ROOT / "data" / "live.json"
HEAT_DIR = ROOT / "data" / "group_heat"
THEMES_F = ROOT / "themes.yaml"  # 細分題材（被動元件、CCL…）：{題材名: [代號, ...]}，沒有這個檔就只看產業
PUB_DIR = ROOT / ".live_pub"

# 台股一天成交量的累積比例（09:00 起算的分鐘數 → 已完成全天量的比例，含開盤集合競價，約略值）
PROFILE = [(0, .05), (15, .17), (30, .26), (60, .39), (120, .57), (180, .72), (240, .87), (265, .95), (270, 1.0)]


def vol_fraction(now: dt.datetime) -> float:
    m = (now.hour - 9) * 60 + now.minute + now.second / 60
    xs, ys = zip(*PROFILE)
    return float(np.interp(max(0, min(270, m)), xs, ys))


def _hm(s: str, day: dt.datetime) -> dt.datetime:
    h, m = map(int, s.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def _num(v, nd=2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _clean(o):
    """轉成可寫 JSON 的型別（NaN → null）。"""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return _num(o, 4)
    return o if o is None or isinstance(o, str) else str(o)


def build_ref(hist: pd.DataFrame, nh: int) -> pd.DataFrame:
    h = hist.sort_values("date")
    g = h.groupby("code")
    return pd.DataFrame({
        "avg5": g.volume.apply(lambda s: s.iloc[-5:].mean() if len(s) >= 5 else np.nan),
        "hi": g.high.apply(lambda s: s.iloc[-nh:].max() if len(s) >= nh else np.nan),
    })


def watchlist() -> set[str]:
    """最近一天收盤報表中的「準備突破觀察」名單。"""
    d = ROOT / "data" / "results"
    files = sorted(d.glob("*.csv")) if d.exists() else []
    if not files:
        return set()
    r = pd.read_csv(files[-1], dtype=str)
    return set(r[r.strategy == "準備突破觀察"].code)


def watch_date() -> str | None:
    """觀察名單是哪一天收盤報表的。"""
    d = ROOT / "data" / "results"
    files = sorted(d.glob("*.csv")) if d.exists() else []
    return files[-1].stem if files else None


# ------------------------------------------------------------------ 發佈
def publish(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT_F.parent.mkdir(parents=True, exist_ok=True)
    OUT_F.write_text(text, "utf-8")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    run = lambda *a: subprocess.run(["git", *a], cwd=PUB_DIR, check=True, capture_output=True, text=True)
    try:
        first = not (PUB_DIR / ".git").exists()
        if first:
            PUB_DIR.mkdir(exist_ok=True)
            run("init", "-q", "-b", "live")
            run("config", "user.name", "github-actions[bot]")
            run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
            run("remote", "add", "origin", f"https://x-access-token:{token}@github.com/{repo}.git")
        (PUB_DIR / "live.json").write_text(text, "utf-8")
        run("add", "live.json")
        run("commit", "-q", "-m", f"盤中 {payload.get('updated', '')}", *([] if first else ["--amend"]))
        run("push", "-q", "-f", "origin", "HEAD:live")
    except subprocess.CalledProcessError as e:
        log.warning("推送 live 分支失敗：%s", (e.stderr or "")[-300:])


def restore_state(today: str) -> dict:
    """同一天重新啟動時，接續之前的預警紀錄（避免重複通知）。"""
    blank = {"date": today, "alerts": {}, "hold_alerted": [], "last_notify": 0, "pending": [], "official": None}
    cands = []
    if STATE_F.exists():
        cands.append(STATE_F.read_text("utf-8"))
    if os.environ.get("GITHUB_TOKEN"):
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "live"], cwd=ROOT, check=True, capture_output=True, timeout=60)
            cands.append(subprocess.run(["git", "show", "FETCH_HEAD:live.json"], cwd=ROOT, check=True,
                                        capture_output=True, text=True).stdout)
        except Exception:  # noqa: BLE001
            pass
    for c in cands:
        try:
            s = json.loads(c)
            s = s.get("state", s)
            if s.get("date") == today:
                log.info("接續今天的監控紀錄：已預警 %d 檔", len(s.get("alerts", {})))
                return {**blank, **s}
        except Exception:  # noqa: BLE001
            continue
    return blank


# ------------------------------------------------------------------ 單次掃描
_LAST_PX: dict[str, float] = {}
_EXT: dict[str, dict] = {}  # 本益比、月營收年增、外資買賣超、注意/處置（前一天的資料）
_TECH: dict[str, dict] = {}  # RS（前一天收盤）
_GRANK: dict[str, int] = {}  # 產業 → 族群強弱排名（前一天收盤）
_TPL_REF: pd.DataFrame | None = None  # 趨勢樣板：昨天為止的均線合計、52 週高低點


def load_daily_refs(hist: pd.DataFrame) -> pd.DataFrame | None:
    """讀收盤後產生的 RS、族群排名、寬度歷史，並算好盤中趨勢樣板要用的基準。回傳寬度歷史。"""
    global _TPL_REF
    ex = ROOT / "data" / "extras"
    try:
        t = pd.read_csv(ex / "tech.csv", dtype={"code": str}).set_index("code")
        _TECH.update(t[["rs"]].to_dict("index"))
    except Exception as e:  # noqa: BLE001
        log.warning("RS 資料載入失敗：%s", e)
    try:
        g = json.loads((ex / "groups.json").read_text("utf-8"))
        _GRANK.update({x["ind"]: x["rank"] for x in g.get("groups", [])})
    except Exception as e:  # noqa: BLE001
        log.warning("族群排名載入失敗：%s", e)
    try:
        _TPL_REF = tech.tpl_ref(rules.Panel(hist))
    except Exception as e:  # noqa: BLE001
        log.warning("趨勢樣板基準計算失敗：%s", e)
    try:
        return pd.read_csv(ex / "breadth.csv")
    except Exception as e:  # noqa: BLE001
        log.warning("寬度歷史載入失敗：%s", e)
        return None


def scan(q: pd.DataFrame, ref: pd.DataFrame, stocks: pd.DataFrame, lc: dict, now: dt.datetime,
         watch: set[str]) -> pd.DataFrame:
    # 這一輪沒抓到成交價的股票，沿用上一輪的價格（避免顯示「—」或被誤判為已不符合）
    q = q.drop_duplicates("code").copy()
    miss = q.price.isna()
    if miss.any():
        q.loc[miss, "price"] = q.loc[miss, "code"].map(_LAST_PX)
    _LAST_PX.update({c: float(p) for c, p in zip(q.code, q.price) if pd.notna(p)})
    q = q.merge(ref, left_on="code", right_index=True, how="left").merge(
        stocks[["code", "industry", "market"]].drop_duplicates("code"), on="code", how="left")
    q["industry"] = q.industry.fillna("")
    q["chg"] = (q.price / q.yclose - 1) * 100
    q["vol_x"] = q.vol_lots * 1000 / q.avg5.where(q.avg5 > 0)
    q["proj_x"] = q.vol_x / vol_fraction(now)
    q["watch"] = q.code.isin(watch)
    # 漲停鎖住：漲幅接近 10% 且現價就是今日最高（一字鎖時開盤＝現價，紅K 條件會失敗，要另外放行）
    q["locked"] = ((q.chg >= 9.4) & (q.price >= q.high.fillna(q.price))).fillna(False)
    cond = (
        (q.proj_x >= lc.get("volume_multiple", 3))
        & (q.vol_x >= lc.get("min_actual_multiple", 1))
        & (q.vol_lots >= lc.get("min_volume_lots", 300))
        & (q.chg >= lc.get("min_change_pct", 3)) & (q.chg <= lc.get("max_change_pct", 10.5))
        & ((q.price > q.open) | q.locked)
        & (q.price > q.hi)
        & (q.price >= lc.get("min_price", 10))
    )
    q["hit"] = cond.fillna(False)
    # 族群熱度：同產業中其他「預估爆量＋漲 3% 以上」的家數（回測：3~5 家時突破表現最好，6 家以上偏過熱）
    su = ((q.proj_x >= lc.get("volume_multiple", 3)) & (q.chg >= 3) & (q.vol_lots >= 300)).fillna(False).astype(int)
    ind = q.industry.where(q.industry != "", "_")
    grp = su.groupby(ind).transform("sum")
    q["peers"] = (grp - su).where(q.industry != "", 0)
    q["surge"] = su.astype(bool)
    surging = q[su.astype(bool) & (q.industry != "")].groupby("industry").code.apply(list).to_dict()
    q["peers_list"] = [[c for c in surging.get(i, []) if c != code] for code, i in zip(q.code, q.industry)]
    q["tpl"] = None
    if _TPL_REF is not None:
        try:
            qi = q.set_index("code")
            q["tpl"] = tech.tpl_live(qi.price, qi.high, qi["low"] if "low" in qi else qi.price, _TPL_REF).reindex(q.code).values
        except Exception as e:  # noqa: BLE001
            log.warning("盤中趨勢樣板失敗：%s", e)
    return q


def load_themes() -> dict[str, list[str]]:
    if not THEMES_F.exists():
        return {}
    try:
        t = yaml.safe_load(THEMES_F.read_text("utf-8")) or {}
        return {str(k): [str(c) for c in v] for k, v in t.items() if isinstance(v, list) and v}
    except Exception as e:  # noqa: BLE001
        log.warning("題材清單讀取失敗：%s", e)
        return {}


def group_heat(q: pd.DataFrame, st: dict, hm: str, themes: dict[str, list[str]],
               main_n: int = 5, main_by: str = "10:00", main_pct: float = 5) -> list[dict]:
    """每個產業／題材現在有幾檔在發動：
    surge = 預估爆量＋漲 3% 以上（和「族群同步」同定義）、limit = 漲 9.4% 以上、up7 = 漲 7% 以上、
    active = surge 或 limit（漲停鎖住常常沒量，只看爆量會漏掉最強的那幾檔）。
    first5 = 今天第一次達到 active ≥ main_n 家、而且 ≥ 成員數 main_pct% 的時間（記在 state，重啟也接得上）；
    main_by 之前達到就標「主線」。比例門檻是因為產業分類很粗（電子零組件 200 多檔），只看家數大產業隨便都有 5 家。"""
    groups = {("產業", k): list(v) for k, v in q[q.industry != ""].groupby("industry").code}
    groups.update({("題材", k): v for k, v in themes.items()})
    qi = q.set_index("code")
    first = st.setdefault("group_first", {})
    out = []
    for (kind, name), codes in groups.items():
        g = qi.loc[qi.index.intersection(codes)]
        g = g[g.price.notna() & g.chg.notna()]
        if g.empty:
            continue
        key = f"{kind}:{name}"
        act = g.surge | (g.chg >= 9.4)
        na, ns, nl, n7 = int(act.sum()), int(g.surge.sum()), int((g.chg >= 9.4).sum()), int((g.chg >= 7).sum())
        if na >= main_n and na >= len(g) * main_pct / 100 and key not in first:
            first[key] = hm
        if na < 2 and key not in first:
            continue
        hot = g[act | (g.chg >= 7)].sort_values("chg", ascending=False)
        out.append({"kind": kind, "name": name, "n": len(g), "active": na, "surge": ns, "limit": nl, "up7": n7,
                    "avg_chg": _num(g.chg.mean()), "first5": first.get(key), "main": bool(first.get(key, "99") < main_by),
                    "members": [[c, r["name"], _num(r["chg"]), bool(r["locked"])] for c, r in hot.head(15).iterrows()]})
    out.sort(key=lambda d: (d["main"], d["active"], d["limit"]), reverse=True)
    return out


def save_vol_curve(today: str, hm: str, q: pd.DataFrame, codes: set[str]) -> None:
    """記錄預警、候選、持股每次掃描的累計成交量（data/vol_curve/日期.csv），之後拿收盤全日量校正 PROFILE。"""
    x = q[q.code.isin(codes) & q.vol_lots.notna()][["code", "vol_lots", "price"]]
    if x.empty:
        return
    d = ROOT / "data" / "vol_curve"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{today}.csv"
    x.assign(time=hm)[["time", "code", "vol_lots", "price"]].to_csv(f, mode="a", header=not f.exists(), index=False)


def save_heat_log(today: str, heat: list[dict]) -> None:
    """族群熱度存成 data/group_heat/YYYY-MM-DD.csv（每輪覆蓋成最新），之後統計「10 點前就發動的族群」後面幾天的表現。"""
    if not heat:
        return
    df = pd.DataFrame([{**{k: h[k] for k in ("kind", "name", "n", "active", "surge", "limit", "up7", "avg_chg", "first5", "main")},
                        "members": " ".join(m[0] for m in h["members"])} for h in heat])
    df.insert(0, "date", today)
    HEAT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(HEAT_DIR / f"{today}.csv", index=False)


def holdings_view(q: pd.DataFrame, now: dt.datetime, shrink: float, drop_pct: float, max_hold: int = 20) -> list[dict]:
    pos = positions.load()
    if pos.empty:
        return []
    today = now.date().isoformat()
    pos = pos[(pos.status == "open") & (pos.signal_date != today)]
    qi = q.set_index("code")
    frac = vol_fraction(now)
    out = []
    for r in pos.itertuples():
        if r.code not in qi.index:
            continue
        x = qi.loc[r.code]
        entry = r.entry_price if pd.notna(r.entry_price) else r.alert_price
        price = x.price
        ret = (price / float(entry) - 1) * 100 if price and entry and pd.notna(entry) else None
        proj_ratio = (x.vol_lots * 1000 / frac / float(r.surge_volume)
                      if pd.notna(r.surge_volume) and float(r.surge_volume) > 0 else None)
        status = "—"
        if proj_ratio is not None:
            status = "可能量縮出場" if proj_ratio < shrink else "量能維持"
            # 收盤收在漲停價的那天量縮不算（positions.update 同一條規則），盤中在漲停價上就先標續抱
            if proj_ratio < shrink and price and positions._limit_up(float(price), x.yclose):
                status = "漲停續抱（量縮不算）"
        surge_lots = float(r.surge_volume) / 1000 if pd.notna(r.surge_volume) else None
        held = int(r.days_held) + 1 if pd.notna(r.days_held) else None   # days_held 是到昨天收盤，今天再算一天
        out.append({"code": r.code, "name": r.name, "signal_date": r.signal_date, "entry": _num(entry),
                    "price": _num(price), "ret": _num(ret), "chg": _num(x.chg),
                    "vol_lots": _num(x.vol_lots, 0), "proj_ratio": _num(proj_ratio),
                    "proj_lots": _num(x.vol_lots / frac, 0) if frac else None,
                    "shrink_lots": _num(surge_lots * shrink, 0) if surge_lots else None,
                    "days_left": max(max_hold - held, 0) if held is not None else None,
                    "status": status, "drop_alert": ret is not None and ret <= -drop_pct})
    return sorted(out, key=lambda d: d["ret"] if d["ret"] is not None else 0)


def lock_minutes(st: dict) -> dict[str, int]:
    """最後一次掃描時還鎖在漲停的股票，連續鎖了幾分鐘（掃描間隔 3 分鐘，誤差 ±3 分）。"""
    last = st.get("last_scan")
    if not last:
        return {}
    m = lambda t: int(t[:2]) * 60 + int(t[3:5])  # noqa: E731
    return {c: m(last) - m(t) for c, t in (st.get("lock_since") or {}).items()}


def save_book(today: str, when: str, q: pd.DataFrame, codes: set[str]) -> None:
    """委買／委賣第一檔快照（data/book/日期.csv）：13:12 拍正式訊號＋鎖漲停的預警股，收盤後再拍一次同一批。"""
    x = q[q.code.isin(codes)].reindex(columns=["code", "price", "bid1", "bid1_lots", "ask1_lots", "vol_lots"])
    if x.empty:
        return
    d = ROOT / "data" / "book"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{today}.csv"
    x.insert(0, "when", when)
    x.to_csv(f, mode="a", header=not f.exists(), index=False)


def close_book(today: str, stocks: pd.DataFrame, at: dt.datetime) -> None:
    """收盤後抓今天正式訊號的委買／委賣第一檔與全日量：鎖漲停的話委買第一檔就是收盤時沒買到、還在排隊的張數。"""
    codes = set(positions.load().query("signal_date == @today").code)
    bf = ROOT / "data" / "book" / f"{today}.csv"
    if bf.exists():  # 13:12 有拍快照的（含沒進正式提醒、但盤中鎖漲停的預警股）
        codes |= set(pd.read_csv(bf, dtype={"code": str}).code)
    if not codes:
        return
    wait = (at - dt.datetime.now(TZ)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    q, _ = intraday.fetch_quotes(stocks[stocks.code.isin(codes)])
    book = {r.code: {"bid1_lots": r.bid1_lots, "ask1_lots": r.ask1_lots, "vol_lots": r.vol_lots} for r in q.itertuples()}
    positions.set_close_book(today, book)
    save_book(today, "close", q, codes)
    log.info("收盤委買／委賣：%s", "、".join(f"{c} 買{b['bid1_lots']}／賣{b['ask1_lots']}／量{b['vol_lots']}" for c, b in book.items()))


def official_result(today: str) -> dict | None:
    sf = ROOT / "data" / "intraday_state.json"
    if not sf.exists():
        return None
    s = json.loads(sf.read_text())
    if s.get("last_run") != today:
        return None
    pos = positions.load()
    pos = pos[pos.signal_date == today]
    return {"time": s.get("time"), "count": s.get("hits", len(pos)),
            "stocks": [{"code": r.code, "name": r.name, "price": _num(r.alert_price),
                        "rev_yoy": _num((_EXT.get(r.code) or {}).get("rev_yoy"), 0)} for r in pos.itertuples()]}


def stock_row(r, first: dict | None = None) -> dict:
    r = r._asdict() if hasattr(r, "_asdict") else r.to_dict()  # Series 的 .name 是索引，不能用屬性取
    d = {"code": r["code"], "name": r["name"], "industry": r["industry"], "market": r["market"],
         "price": _num(r["price"]), "chg": _num(r["chg"]), "vol_lots": _num(r["vol_lots"], 0),
         "vol_x": _num(r["vol_x"], 1), "proj_x": _num(r["proj_x"], 1), "watch": bool(r["watch"]), "hit": bool(r["hit"]),
         "locked": bool(r.get("locked")), "noprice": r["price"] is None or pd.isna(r["price"]),
         "peers": int(r.get("peers") or 0), "peers_list": list(r.get("peers_list") or []),
         "tpl": None if r.get("tpl") is None or pd.isna(r.get("tpl")) else bool(r.get("tpl")),
         "rs": _num((_TECH.get(r["code"]) or {}).get("rs"), 0), "group_rank": _GRANK.get(r["industry"])}
    x = _EXT.get(r["code"])
    if x:
        d.update(pe=_num(x.get("pe"), 1), rev_yoy=_num(x.get("rev_yoy"), 0), foreign=_num(x.get("foreign"), 0),
                 flag=x.get("flag") if isinstance(x.get("flag"), str) else None,
                 fstreak=_num(x.get("foreign_streak"), 0), tstreak=_num(x.get("trust_streak"), 0),
                 inst5=_num(x.get("inst_pct_5d"), 1), dt=_num(x.get("dt_ratio"), 0),
                 turnover=_num(r["vol_lots"] * 1000 / x["shares"] * 100, 2)
                 if x.get("shares") and pd.notna(x.get("shares")) and pd.notna(r["vol_lots"]) else None)
    # 處置股採分盤集中撮合（約每 5~20 分鐘才成交一次），盤中常沒有即時成交價
    d["split"] = d.get("flag") == "處置"
    if first:
        d.update(first_time=first["time"], first_price=_num(first["price"]),
                 since=_num((r["price"] / first["price"] - 1) * 100) if r["price"] and first["price"] else None)
    return d


def save_alert_log(today: str, alerts: list[dict], official: dict | None) -> None:
    """把今天的早期預警存成 data/alerts/YYYY-MM-DD.csv，之後可以統計「早上預警到底準不準」。"""
    if not alerts:
        return
    off = {s["code"] for s in (official or {}).get("stocks", [])}
    cols = ["code", "name", "industry", "market", "first_time", "first_price", "price", "chg",
            "vol_lots", "vol_x", "proj_x", "watch", "peers", "hit", "tpl", "rs", "group_rank",
            "locked", "lock_since", "rev_yoy"]
    df = pd.DataFrame(alerts).reindex(columns=cols).rename(
        columns={"price": "last_price", "chg": "last_chg", "hit": "still_hit"})
    df.insert(0, "date", today)
    df["official"] = df.code.isin(off)
    d = ROOT / "data" / "alerts"
    d.mkdir(parents=True, exist_ok=True)
    df.sort_values("first_time").to_csv(d / f"{today}.csv", index=False)


# ------------------------------------------------------------------ 通知
def alert_md(rows: list[dict], holds: list[dict], senti: dict | None, page: str) -> str:
    lines = []
    if senti:
        lines += [sentiment.md_line(senti, intraday=True), ""]
    if holds:
        lines += ["## 🔻 持股跌幅警示", "", "| 股票 | 進場價 | 現價 | 報酬 |", "|---|--:|--:|--:|"]
        lines += [f"| {h['code']} {h['name']} | {h['entry']} | {h['price']} | {h['ret']:+.2f}% |" for h in holds]
        lines += ["", "<sub>這只是提醒。回測顯示固定停損會讓報酬變差，出場以「量縮到爆量日一半」為準；若有重大利空，AI 收盤複盤會另外分析。</sub>", ""]
    if rows:
        lines += ["## ⚡ 盤中早期預警（尚未收盤確認）", "",
                  "| 預警時間 | 股票 | 產業 | 現價 | 漲幅 | 目前量(張) | 預估全日量比 | 備註 |",
                  "|---|---|---|--:|--:|--:|--:|---|"]
        for r in rows:
            mk = "TW" if r.get("market") == "TWSE" else "TWO"
            note = "、".join((["⭐昨日觀察名單"] if r.get("watch") else []) +
                             ([f"族群同步 {r['peers']} 家"] if r.get("peers") else []))
            lines.append(f"| {r['first_time']} | [{r['code']} {r['name']}](https://tw.stock.yahoo.com/quote/{r['code']}.{mk}) | "
                         f"{r.get('industry') or ''} | {r['price']} | {r['chg']:+.2f}% | {r['vol_lots']:,.0f} | "
                         f"{r['proj_x']}× | {note} |")
        lines += ["", "<sub>早期預警 = 盤中「預估」全日量達 5 日均量 3 倍、漲 3% 以上、突破 60 日新高。"
                  "回測的進場點是收盤前，太早進場常遇到衝高回落；13:12 會再發正式進場提醒。</sub>"]
    if page:
        lines += ["", f"👉 [盤中即時網頁]({page})"]
    lines.append("\n<sub>僅供參考，不構成投資建議。</sub>")
    return "\n".join(lines)


# ------------------------------------------------------------------ 主程式
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="測試：只掃一次、不等開盤、不通知")
    ap.add_argument("--once", action="store_true", help="只掃一次")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    lc, ic = cfg.get("live", {}), cfg.get("intraday", {})
    if not lc.get("enabled", True) and not a.test:
        log.info("盤中連續監控已關閉")
        return 0

    now = dt.datetime.now(TZ)
    today = now.date().isoformat()
    if now.weekday() >= 5 and not a.test:
        log.info("週末不監控")
        return 0
    end = _hm(lc.get("end", "13:25"), now)
    if now >= end and not a.test:
        log.info("已超過 %s，今天的盤中監控結束", lc.get("end", "13:25"))
        return 0
    open_t = _hm("09:01", now)
    if now < open_t and not a.test:
        log.info("等待開盤（%.0f 秒）", (open_t - now).total_seconds())
        time.sleep((open_t - now).total_seconds())

    markets = cfg.get("settings", {}).get("markets", ["TWSE", "TPEX"])
    stocks = fetch.load_stock_list(markets)
    hist = fetch.load_history()
    hist = corpact.adjust(hist[hist.date < today], upto=today)  # 減資／變更面額：舊價接起來，今天恢復買賣的也算
    ref = build_ref(hist, int(lc.get("new_high_days", 60)))
    breadth_hist = load_daily_refs(hist)
    bh = breadth_hist[breadth_hist.date < today].tail(120)[["date", "above_ma20", "mkt20"]].values.tolist() if breadth_hist is not None else []
    qinfo = quarter_info(today, hist.date.unique())
    try:
        ex = enrich.load()
        cols = [c for c in ["pe", "rev_yoy", "foreign", "flag", "foreign_streak", "trust_streak", "inst_pct_5d", "dt_ratio",
                            "shares"]
                if c in ex.columns]
        _EXT.update(ex[cols].to_dict("index"))
        log.info("載入本益比／營收／法人資料 %d 檔", len(_EXT))
    except Exception as e:  # noqa: BLE001
        log.warning("消息面資料載入失敗：%s", e)
    watch = watchlist()
    log.info("觀察名單 %d 檔", len(watch))
    themes = load_themes()
    if themes:
        log.info("題材清單 %d 個", len(themes))
    glob = {}
    try:
        tsmc = hist[hist.code == "2330"].sort_values("date").close
        glob = global_mkt.snapshot(float(tsmc.iloc[-1]) if len(tsmc) else None)
    except Exception as e:  # noqa: BLE001
        log.warning("美股隔夜資料失敗：%s", e)
    st = restore_state(today)
    interval = float(lc.get("interval_min", 3)) * 60
    alert_start = _hm(lc.get("alert_start", "09:30"), now)
    official_t = _hm(lc.get("official_time", "13:12"), now)
    shrink = float(ic.get("exit_shrink_ratio", 0.5))
    drop_pct = float(lc.get("hold_drop_alert_pct", 7))
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    page = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/live.html" if "/" in repo else ""
    scans = 0

    while True:
        t0 = time.time()
        now = dt.datetime.now(TZ)
        q, day = intraday.fetch_quotes(stocks)
        if q.empty:
            log.warning("這次抓不到報價")
        elif day and day != today.replace("-", "") and not a.test:
            log.info("今天（%s）沒有交易（MIS 日期 %s），可能休市", today, day)
            return 0
        else:
            scans += 1
            q = scan(q, ref, stocks, lc, now, watch)
            hm = now.strftime("%H:%M")
            if now >= alert_start or a.test:
                for r in q[q.hit].itertuples():
                    if r.code not in st["alerts"]:
                        st["alerts"][r.code] = {"time": hm, "price": float(r.price)}
                        st["pending"].append(r.code)
            # 連續鎖漲停從幾點開始（收盤前一直鎖住的，收盤價大概買不到）
            ls = st.setdefault("lock_since", {})
            lk = set(q.code[q.locked])
            for c in list(ls):
                if c not in lk:
                    del ls[c]
            for c in lk:
                ls.setdefault(c, hm)
            st["last_scan"] = hm
            qi = q.set_index("code", drop=False)
            alerts = [stock_row(qi.loc[c], f) | {"lock_since": ls.get(c)} for c, f in st["alerts"].items() if c in qi.index]
            alerts.sort(key=lambda d: d["first_time"], reverse=True)
            cands = [stock_row(r) for r in q[q.hit].sort_values("proj_x", ascending=False).head(60).itertuples()]
            # 昨天收盤報表的「準備突破觀察」整份名單，盤中即時價量（不管有沒有觸發）
            wl = [stock_row(qi.loc[c]) for c in watch if c in qi.index]
            wl.sort(key=lambda d: d["chg"] if d.get("chg") is not None else -99, reverse=True)
            holds = holdings_view(q, now, shrink, drop_pct, int(ic.get("max_hold_days", 20)))
            try:
                heat = group_heat(q, st, hm, themes, int(lc.get("main_group_n", 5)), lc.get("main_group_by", "10:00"),
                                  float(lc.get("main_group_pct", 5)))
            except Exception as e:  # noqa: BLE001
                log.warning("族群熱度失敗：%s", e)
                heat = []

            # 13:12 正式進場提醒（與回測相同條件）
            if now >= official_t and not st.get("official") and not a.test:
                try:
                    intraday.QUOTES = (q.reindex(columns=["code", "name", "price", "open", "high", "low", "yclose", "vol_lots", "time", "date", "bid1", "ask1", "bid1_lots", "ask1_lots"]).copy(), day)
                    intraday.main([])
                except Exception as e:  # noqa: BLE001
                    log.error("正式提醒失敗：%s", e)
                finally:
                    intraday.QUOTES = None
                st["official"] = official_result(today) or {"time": hm, "count": 0, "stocks": []}
                try:
                    save_book(today, "1312", q, {s["code"] for s in st["official"].get("stocks", [])}
                              | {c for c in st["alerts"] if c in set(q.code[q.locked])})
                except Exception as e:  # noqa: BLE001
                    log.warning("委買委賣快照失敗：%s", e)

            try:
                senti = sentiment.from_quotes(q, hist)
                senti["mkt20"] = tech.live_mkt20(breadth_hist, q, today)
            except Exception as e:  # noqa: BLE001
                log.warning("情緒失敗：%s", e)
                senti = None

            # 通知：持股大跌立即；早期預警每 notify_interval_min 分鐘彙整一次
            new_drop = [h for h in holds if h["drop_alert"] and h["code"] not in st["hold_alerted"]]
            due = time.time() - st["last_notify"] >= float(lc.get("notify_interval_min", 30)) * 60
            pend = [d for d in alerts if d["code"] in st["pending"]]
            if lc.get("notify", True) and not a.test and (new_drop or (pend and due)):
                title = f"【盤中預警】{today} {hm}｜" + "、".join(
                    ([f"持股警示 {len(new_drop)}"] if new_drop else []) + ([f"早期預警 {len(pend)}"] if pend else []))
                try:
                    notify.send(title, alert_md(sorted(pend, key=lambda d: d["first_time"]), new_drop, senti, page))
                    st["last_notify"] = time.time()
                    st["pending"] = []
                    st["hold_alerted"] += [h["code"] for h in new_drop]
                except Exception as e:  # noqa: BLE001
                    log.error("通知失敗：%s", e)

            payload = {
                "updated": now.strftime("%Y-%m-%d %H:%M:%S"), "date": today, "scans": scans,
                "interval_min": interval / 60, "end": lc.get("end", "13:25"),
                "vol_fraction": round(vol_fraction(now), 3), "alert_start": lc.get("alert_start", "09:30"),
                "market": {**(senti or {}), "breadth": bh + ([[today, senti.get("above_ma20"), senti.get("mkt20")]] if senti else [])},
                "alerts": alerts, "candidates": cands, "watchlist": wl, "watch_date": watch_date(),
                "holdings": holds, "official": st.get("official"),
                "quotes": len(q), "state": st, "global": glob, "qinfo": qinfo, "group_heat": heat[:20],
                "breadth": bh + ([[today, senti.get("above_ma20"), senti.get("mkt20")]] if senti else []),
            }
            publish(_clean(payload))
            save_alert_log(today, alerts, st.get("official"))
            save_heat_log(today, heat)
            save_vol_curve(today, hm, q, {*st["alerts"], *(c["code"] for c in cands), *(h["code"] for h in holds)})
            STATE_F.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
            log.info("%s 掃描 %d 檔｜符合 %d｜今日預警 %d｜持股 %d（%.0f 秒）",
                     hm, len(q), len(cands), len(alerts), len(holds), time.time() - t0)

        if a.test or a.once:
            return 0
        nxt = t0 + interval
        if not st.get("official") and t0 < official_t.timestamp() < nxt:
            nxt = official_t.timestamp()  # 準時在 13:12 做正式提醒
        if dt.datetime.fromtimestamp(nxt, TZ) >= end:
            break
        time.sleep(max(5, nxt - time.time()))

    # 收盤前最後一次：確保正式提醒有做
    if not st.get("official"):
        try:
            intraday.main([])
        except Exception as e:  # noqa: BLE001
            log.error("正式提醒失敗：%s", e)
    if not a.test:
        try:
            positions.set_lock_minutes(today, lock_minutes(st))
        except Exception as e:  # noqa: BLE001
            log.warning("鎖漲停分鐘數寫入失敗：%s", e)
        try:
            close_book(today, stocks, _hm(lc.get("close_snapshot", "13:31"), dt.datetime.now(TZ)))
        except Exception as e:  # noqa: BLE001
            log.warning("收盤委買委賣抓取失敗：%s", e)
    log.info("盤中監控結束，共掃描 %d 次", scans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
