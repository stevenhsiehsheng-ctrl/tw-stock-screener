"""今天（或指定日）系統跑得怎樣：一個指令看完工作流程、體檢、各資料到哪一天。只讀，不改任何東西。

python -m tools.daystatus [YYYY-MM-DD]

- 資料一律讀 origin/main（先 git fetch），看的是已經推上去的，不是本機改到一半的。
- 工作流程用 GitHub 公開 API（不用 token），列台北那天建立的每一班（程式檢查、測資料來源不列），失敗的標 ❌。
  「派工叫的」＝workflow_dispatch：cron-job.org（08:40 盤中監控、15:20 每日資料）、早上體檢補跑、別的流程叫網站重畫、手動。
16:25 排程、接手的人先跑這個，再決定要不要深入看 log。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
TPE = dt.timezone(dt.timedelta(hours=8))
SKIP = {"程式檢查", "測資料來源"}


def _git(*args: str) -> bytes | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return None


def _read(path: str) -> bytes | None:
    """origin/main 上的檔案內容；沒有就讀工作目錄。"""
    b = _git("show", f"origin/main:{path}")
    if b is None and (ROOT / path).exists():
        b = (ROOT / path).read_bytes()
    return b


def _max_date(path: str, where: tuple[str, str] | None = None) -> str:
    b = _read(path)
    if b is None:
        return "（沒有檔）"
    try:
        df = pd.read_csv(io.BytesIO(b), usecols=["date"] + ([where[0]] if where else []), dtype=str,
                         compression="gzip" if path.endswith(".gz") else None)
        if where:
            df = df[df[where[0]] == where[1]]
        return df.date.max() if len(df) else "（空）"
    except Exception as e:  # noqa: BLE001
        return f"（讀不到：{e}）"


def runs(day: str, repo: str, pages: int = 5) -> list[dict]:
    """台北 day 建立的工作流程（GitHub 用 UTC：前一天 16:00Z～當天 15:59Z）。"""
    d = dt.date.fromisoformat(day)
    rng = f"{d - dt.timedelta(days=1)}T16:00:00Z..{d}T15:59:59Z"
    out = []
    for page in range(1, pages + 1):
        r = requests.get(f"https://api.github.com/repos/{repo}/actions/runs",
                         params={"per_page": 100, "page": page, "created": rng}, timeout=30)
        got = r.json().get("workflow_runs", [])
        for x in got:
            if x["name"] in SKIP:
                continue
            t = dt.datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).astimezone(TPE)
            out.append({"time": t.strftime("%H:%M"), "name": x["name"], "event": x["event"],
                        "status": x["status"], "conclusion": x.get("conclusion"), "url": x["html_url"]})
        if len(got) < 100:
            break
    return sorted(out, key=lambda x: x["time"])


def fmt_runs(rs: list[dict]) -> list[str]:
    if not rs:
        return ["  （沒有）"]
    lines = []
    for x in rs:
        mark = {"success": "✅", "failure": "❌", "cancelled": "⏹", "skipped": "·"}.get(x["conclusion"] or "", "⏳")
        how = {"workflow_dispatch": "派工叫的", "schedule": "GitHub 排程", "push": "推送觸發"}.get(x["event"], x["event"])
        lines.append(f"  {mark} {x['time']} {x['name']}（{how}）" + (f" {x['url']}" if x["conclusion"] == "failure" else ""))
    return lines


def main(argv: list[str]) -> int:
    day = argv[0] if argv else dt.datetime.now(TPE).date().isoformat()
    _git("fetch", "-q", "origin", "main")
    url = (_git("remote", "get-url", "origin") or b"").decode().strip()
    repo = url.split("github.com/")[-1].removesuffix(".git") if "github.com/" in url else "stevenhsiehsheng-ctrl/tw-stock-screener"
    sys.path.insert(0, str(ROOT))
    from screener import twcal
    print(f"== {day}（{'交易日' if twcal.is_trading_day(day) else '休市'}）")

    print("\n== 工作流程（台北這天建立的）")
    try:
        print("\n".join(fmt_runs(runs(day, repo))))
    except Exception as e:  # noqa: BLE001
        print(f"  讀不到：{e}")

    print("\n== 體檢 data/health/latest.json")
    h = json.loads(_read("data/health/latest.json") or b"{}")
    print(f"  {h.get('date')} {h.get('slots')}（{h.get('checked_at', '')[11:16]}）交易日 {h.get('trading_day')}")
    print(f"  缺：{h.get('bad') or '無'}｜失敗：{[f.get('name') for f in h.get('failed_runs') or []] or '無'}"
          f"｜補跑：{h.get('dispatched') or h.get('dispatched_am') or '無'}｜cron：{h.get('cron') or '正常'}"
          f"｜repo {h.get('repo_mb') or '—'} MB")

    print("\n== 資料到哪一天")
    state = json.loads(_read("data/state.json") or b"{}")
    mis = json.loads(_read("data/extras/oddlot_mis.json") or b"{}")
    ledgers = sorted(p.split("/")[-1][:10] for p in (_git("ls-tree", "--name-only", "origin/main", "data/ledger/") or b"").decode().split())
    live = json.loads(_read("data/live_state.json") or b"{}")
    off = live.get("official") or {}
    pos = _read("data/positions.csv")
    n_sig = int((pd.read_csv(io.BytesIO(pos), usecols=["signal_date"]).signal_date == day).sum()) if pos else 0
    rows = [("盤中監控（live_state）", f"{live.get('date')} 最後一輪 {live.get('last_scan')}，今日預警 {len(live.get('alerts') or {})} 檔"
             if live.get("date") == day else f"沒有今天的（最後 {live.get('date')}）"),
            ("13:12 正式訊號", f"{off.get('count', '—')} 檔（{off.get('time', '—')}），positions 記了 {n_sig} 筆" if live.get("date") == day else "—"),
            ("每日資料 state.last_done", state.get("last_done", "—")),
            ("收盤價 history", _max_date("data/history.csv.gz")),
            ("三大法人", _max_date("data/extras/inst_hist.csv.gz")),
            ("融資融券", _max_date("data/extras/margin_hist.csv.gz")),
            ("當沖", _max_date("data/extras/daytrade_hist.csv.gz")),
            ("盤後零股 上市", _max_date("data/extras/oddlot_hist.csv.gz", ("market", "TWSE"))),
            ("盤後零股 上櫃", _max_date("data/extras/oddlot_hist.csv.gz", ("market", "TPEX"))
             + ("（MIS 補的，等官方）" if "TPEX" in mis.get(day, []) else "")),
            ("虛擬帳戶帳本", ledgers[-1] if ledgers else "—"),
            ("13:12 盤中零股快照", "有" if _read(f"data/book/odd_{day}.csv") is not None else "沒有"),
            ("休市表 tw_holidays.json", "有" if _read("data/extras/tw_holidays.json") is not None else "沒有（用推定休市日）")]
    for k, v in rows:
        print(f"  {k}：{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
