"""系統自我體檢：檢查「上一個交易日」的資料有沒有齊，缺了就自己派工補跑，補不好才寄信。

目的：平常不用 Claude 每天盯。Dennis 收到信再叫 Claude Code 來修。

- 早上（盤中監控 08:40 啟動時，--slot am）：檢查＋自動補跑（美股隔夜、傍晚補抓、每日資料）。
  只有「補跑救不了的」（工作流程失敗）才寄信；補跑的結果留給下午複查。
- 下午（盤中監控 13:30 收工時，--slot pm）：只檢查不補跑，還有缺就寄信。
- 結果寫 data/health/latest.json；同一天同一班已經做過就跳過（GitHub 備援班會排隊在後面重跑）。

交易日：證交所 FMTQIK（每日成交量值）列出的日子；抓不到就退回用 history 的最後一天。

--latest：讀 origin/main 最新那版的資料檔（先 git fetch）。盤中監控整天用 08:40 的 checkout，
不加的話 13:30 複查看到的是早上的舊檔，早上補跑進來的美股、風險燈會被當成還沒到（10/13 誤報）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import logging
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "health" / "latest.json"
HIST = DATA / "health" / "history.jsonl"   # 每次體檢一行，看哪一項常缺、repo 大小趨勢（留最近 120 次）
TPE = ZoneInfo("Asia/Taipei")
log = logging.getLogger(__name__)

# 紐約證交所休市日（費半沒資料的日子；跟 macro.US_HOLIDAYS 不同，那份是債市）
NYSE_HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
                 "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
                 "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                 "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
WATCH = {"台股每日篩選", "盤中連續監控", "美股隔夜", "傍晚補抓消息面", "更新網頁版面", "股東紀念品"}   # 各 .yml 的 name:（研究用回補、程式檢查不看）
REF: str | None = None   # 有設（例 "origin/main"）就讀 git 上那一版，見 --latest


def _src(path: Path) -> io.BytesIO:
    """資料檔內容：REF 有設就讀 git 上那一版，讀不到（檔案還沒進 main 等）退回工作目錄。"""
    if REF:
        try:
            r = subprocess.run(["git", "show", f"{REF}:{path.relative_to(ROOT).as_posix()}"], cwd=ROOT,
                               capture_output=True, timeout=120, check=True)
            return io.BytesIO(r.stdout)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 讀不到 %s，改讀工作目錄：%s", REF, path.name, e)
    return io.BytesIO(path.read_bytes())


def twse_days(today: dt.date) -> list[str] | None:
    """證交所這個月＋上個月的交易日（YYYY-MM-DD）；抓不到回 None。"""
    import requests

    out = []
    first = today.replace(day=1)
    for m in [(first - dt.timedelta(days=1)).replace(day=1), first]:
        try:
            r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                             params={"date": m.strftime("%Y%m%d"), "response": "json"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            for row in r.json().get("data") or []:
                y, mo, d = str(row[0]).strip().split("/")
                out.append(f"{int(y) + 1911:04d}-{int(mo):02d}-{int(d):02d}")
        except Exception as e:  # noqa: BLE001
            log.warning("證交所交易日抓不到（%s）：%s", m, e)
            return None
    return sorted(set(out))


def last_us_day(today: dt.date) -> str:
    """台北 today 早上應該已經有的最後一個美股交易日（today 前一天往回找平日、扣休市）。"""
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in NYSE_HOLIDAYS:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def _max_date(path: Path, col="date", where=None) -> str:
    try:
        df = pd.read_csv(_src(path), usecols=[col] + ([where[0]] if where else []), dtype=str,
                         compression="gzip" if path.suffix == ".gz" else None)
        if where:
            df = df[df[where[0]] == where[1]]
        return str(df[col].max()) if len(df) else ""
    except Exception:  # noqa: BLE001
        return ""


def _thin(path: Path, D: str) -> str:
    """D 那天筆數不到前幾天（中位數）八成＝只抓到一半（例如證交所還沒公布、只有櫃買），回傳說明；正常回傳空字串。"""
    try:
        n = pd.read_csv(_src(path), usecols=["date"], dtype=str, compression="gzip").date.value_counts().sort_index()
        prev = n[n.index < D].tail(5)
        if D in n.index and len(prev) and n[D] < 0.8 * prev.median():
            return f"{D}（只有 {n[D]} 筆，平常約 {int(prev.median())}）"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _json(path: Path, key: str) -> str:
    try:
        return str(json.loads(_src(path).read().decode("utf-8")).get(key) or "")
    except Exception:  # noqa: BLE001
        return ""


def _ledger_last() -> str:
    """data/ledger/ 最新一份虛擬帳戶帳本的日期（Claude Code 16:25 存檔）。"""
    try:
        if REF:
            r = subprocess.run(["git", "ls-tree", "--name-only", f"{REF}:data/ledger"], cwd=ROOT,
                               capture_output=True, text=True, timeout=60, check=True)
            names = r.stdout.split()
        else:
            names = [f.name for f in (DATA / "ledger").glob("*.json")]
        return max((n[:10] for n in names if len(n) == 15 and n.endswith(".json")), default="")
    except Exception:  # noqa: BLE001
        return ""


def checks(D: str, us_day: str) -> list[dict]:
    """每一項：name、ok、有什麼、要什麼、補跑用哪個工作流程（fix）。"""
    ex = DATA / "extras"
    items = [
        ("每日資料（收盤價）", _max_date(DATA / "history.csv.gz"), D, "daily"),
        ("等權指數", _max_date(ex / "ew_index.csv"), D, "daily"),
        ("13:12 鎖漲停家數", _max_date(DATA / "lock_count.csv"), D, None),
        ("三大法人", _max_date(ex / "inst_hist.csv.gz"), D, "extras"),
        ("盤後零股（上市）", _max_date(ex / "oddlot_hist.csv.gz", where=("market", "TWSE")), D, "extras"),
        ("盤後零股（上櫃）", _max_date(ex / "oddlot_hist.csv.gz", where=("market", "TPEX")), D, "extras"),
        ("融資融券", _max_date(ex / "margin_hist.csv.gz"), D, "extras"),
        ("當沖", _max_date(ex / "daytrade_hist.csv.gz"), D, "extras"),
        ("美股隔夜（費半）", _json(DATA / "us" / "meta.json", "sox_date"), us_day, "us"),
        ("風險燈（大環境）", _json(DATA / "macro" / "regime.json", "date"), D, "us"),
        # Cowork 15:47 結算 → Claude Code 16:25 存檔；沒有就是那兩個排程有一個沒跑成，要人看
        ("虛擬帳戶帳本", _ledger_last(), D, None),
    ]
    out = [{"name": n, "have": h or "—", "want": w, "ok": bool(h) and h >= w, "fix": f} for n, h, w, f in items]
    # 日期到了但只抓到一半（證交所、櫃買有一邊還沒公布）：日期看起來對，筆數少一大截
    for i in out:
        f = {"三大法人": ex / "inst_hist.csv.gz", "融資融券": ex / "margin_hist.csv.gz", "當沖": ex / "daytrade_hist.csv.gz"}.get(i["name"])
        if f and i["ok"] and (t := _thin(f, D)):
            i.update(ok=False, have=t)
    return out


def live_today(today: str) -> dict | None:
    """收工後體檢多看一項『今天』的：盤中監控有沒有一路掃到下午。10/22 MIS 整天連不上，前一個交易日的資料都齊，
    體檢照樣說沒事，13:12 正式訊號整天沒做也沒人知道。讀工作目錄的 live_state.json（收工後體檢跟盤中監控同一台機器、
    推上 main 之前跑，不能讀 origin/main）。只在 Actions 裡、今天是交易日才看；其他情況回 None。"""
    from . import twcal

    if not os.environ.get("GITHUB_EVENT_NAME") or not twcal.is_trading_day(today):
        return None
    try:
        st = json.loads((DATA / "live_state.json").read_text("utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    have = f"{st.get('date')} {st.get('last_scan') or '沒掃到'}" if st.get("date") else "—"
    ok = st.get("date") == today and (st.get("last_scan") or "") >= "13:00"
    return {"name": "盤中監控（今天）", "have": have, "want": f"{today} 13:00 後", "ok": ok, "fix": None}


def failed_runs(hours=20) -> list[dict]:
    """最近 hours 小時內失敗的工作流程（GitHub 本來也會寄信給觸發的人，這裡集中列一次）。"""
    try:
        r = subprocess.run(["gh", "run", "list", "-L", "60", "--json",
                            "workflowName,conclusion,createdAt,url"],
                           capture_output=True, text=True, timeout=60, check=True)
        runs = json.loads(r.stdout)
    except Exception as e:  # noqa: BLE001
        log.warning("讀不到工作流程紀錄：%s", e)
        return []
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out = []
    for x in runs:
        t = pd.Timestamp(x["createdAt"]).to_pydatetime()
        if x.get("workflowName") in WATCH and x.get("conclusion") == "failure" and t >= since:
            out.append({"name": x.get("workflowName"), "at": t.astimezone(TPE).strftime("%m-%d %H:%M"),
                        "url": x.get("url")})
    return out


FIX_NAME = {"daily": "每日資料", "extras": "傍晚補抓", "us": "美股隔夜"}


def repo_mb() -> float | None:
    """GitHub 統計的 repo 大小（MB，GitHub 會延遲幾小時更新）。只記錄看趨勢：10/14 02:17 是 520.8 MB，
    #279／#284 之後應該每週只長幾 MB；突然一天長幾十 MB 就是又有檔案整份重存。不在 Actions 裡回 None。"""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    try:
        r = subprocess.run(["gh", "api", f"repos/{repo}", "--jq", ".size"], capture_output=True, text=True, timeout=60, check=True)
        return round(int(r.stdout.strip()) / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


def _dispatched(wf: str, day: str, lo: dt.time, hi: dt.time) -> bool | None:
    """台北 day 的 lo～hi 之間有沒有 workflow_dispatch 叫的 wf（cron-job.org 叫的都是 workflow_dispatch）；讀不到執行紀錄回 None。"""
    try:
        r = subprocess.run(["gh", "run", "list", "--workflow", wf, "-L", "60", "--json", "event,createdAt"],
                           capture_output=True, text=True, timeout=60, check=True)
        runs = json.loads(r.stdout)
    except Exception as e:  # noqa: BLE001
        log.warning("讀不到 %s 的執行紀錄：%s", wf, e)
        return None
    return any(x.get("event") == "workflow_dispatch"
               and (t := pd.Timestamp(x["createdAt"]).tz_convert(TPE)).date().isoformat() == day and lo <= t.time() <= hi
               for x in runs)


def cron_check(D: str, now: dt.datetime) -> list[str] | None:
    """cron-job.org 有沒有準時叫（08:40 盤中監控、15:20 每日資料）。它用 Dennis 的 GitHub token，token 過期就默默不叫了，
    只剩常晚好幾小時的 GitHub 排程（盤中監控 14～17 點才跑、每日資料拖到半夜，Cowork 15:47 結算就沒資料）。只在 Actions 裡看：
    ① 今天 08:30～09:10 有沒有 workflow_dispatch 的盤中監控
    ② 上一個交易日 15:10～15:45 有沒有 workflow_dispatch 的每日資料
    早上體檢跟著 08:40 那班跑；08:40 沒叫到時早上體檢根本不會跑（GitHub 備援班都過 11 點），
    所以收工後體檢在今天沒有早上體檢時也要看（10/14 發現原本只在早上看，token 真的過期反而永遠不會寄信）。
    回傳沒叫到的說明（空的＝都有叫）；不在 Actions 裡回傳 None（不判斷）。"""
    ev = os.environ.get("GITHUB_EVENT_NAME")
    if not ev:
        return None
    out = []
    if _dispatched("live.yml", now.date().isoformat(), dt.time(8, 30), dt.time(9, 10)) is False:
        out.append("盤中監控今天 08:40 沒叫到" + (f"（由 GitHub 備援排程 {now:%H:%M} 啟動）" if ev == "schedule" else ""))
    if _dispatched("daily.yml", D, dt.time(15, 10), dt.time(15, 45)) is False:
        out.append(f"每日資料 {D[5:]} 15:20 那班沒叫到")
    return out


def _busy(wf: str) -> bool:
    """這個工作流程現在有排隊或在跑的（例如 GitHub 排程剛好也在跑美股隔夜）就不再派一次。"""
    try:
        r = subprocess.run(["gh", "run", "list", "--workflow", wf, "-L", "5", "--json", "status"],
                           capture_output=True, text=True, timeout=60, check=True)
        return any(x.get("status") in ("queued", "in_progress", "waiting", "requested", "pending")
                   for x in json.loads(r.stdout))
    except Exception:  # noqa: BLE001
        return False


def dispatch(fix: str, D: str) -> bool:
    cmd = {"daily": ["daily.yml", "-f", f"date={D}", "-f", "test=false"],
           "extras": ["extras_evening.yml", "-f", f"date={D}", "-f", "oddlot_days=5"],
           "us": ["us.yml"]}[fix]
    if _busy(cmd[0]):
        log.info("%s 已經在排隊／跑了，不重派", cmd[0])
        return True
    try:
        subprocess.run(["gh", "workflow", "run", cmd[0], "--ref", "main", *cmd[1:]],
                       capture_output=True, text=True, timeout=60, check=True)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("派工 %s 失敗：%s", cmd[0], getattr(e, "stderr", e))
        return False


WF = {"daily": "daily.yml", "extras": "extras_evening.yml", "us": "us.yml"}


def wait_dispatched(until: str = "08:55", poll: int = 20, grace: int = 90, now=None, sleep=None) -> list[str]:
    """早上補跑的工作流程等它跑完再開始盤中監控：live.py 只在啟動時讀一次資料（昨收、法人、當沖），
    傍晚補抓整晚沒跑的日子（10/13 18:05、21:47 兩班都沒跑）不等的話，盤中頁整天用前天的法人、不掛當沖警示。
    最多等到台北 until；剛派出去的可能還沒出現在列表，前 grace 秒沒看到也繼續等。回傳等不到的。"""
    import time
    now = now or (lambda: dt.datetime.now(TPE))
    sleep = sleep or time.sleep
    t0 = now()
    res = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    if res.get("date") != t0.date().isoformat():
        return []
    left = {WF[f] for f in res.get("dispatched") or [] if f in WF}
    hh, mm = map(int, until.split(":"))
    end = t0.replace(hour=hh, minute=mm, second=0, microsecond=0)
    seen: set[str] = set()
    while left and now() < end:
        for w in sorted(left):
            if _busy(w):
                seen.add(w)
            elif w in seen or (now() - t0).total_seconds() >= grace:
                left.discard(w)
                log.info("%s 跑完了", w)
        if left:
            sleep(poll)
    if left:
        log.info("等到 %s 還沒跑完：%s，先用現有資料開始", until, "、".join(sorted(left)))
    return sorted(left)


def run(slot: str, heal: bool, notify: bool, now: dt.datetime | None = None, latest: bool = False) -> dict:
    global REF
    now = now or dt.datetime.now(TPE)
    today = now.date()
    prev = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    if prev.get("date") == today.isoformat() and slot in (prev.get("slots") or []):
        log.info("今天 %s 班已經體檢過，跳過", slot)
        return prev
    if latest:
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, capture_output=True, timeout=120, check=True)
            REF = "origin/main"
        except Exception as e:  # noqa: BLE001
            log.warning("git fetch 失敗，讀工作目錄的檔：%s", e)
    days = twse_days(today)
    hist = _max_date(DATA / "history.csv.gz")
    D = max([d for d in days if d < today.isoformat()], default=hist) if days else hist
    us_day = last_us_day(today)
    items = checks(D, us_day)
    if slot == "pm" and (lt := live_today(today.isoformat())):
        items.append(lt)
    bad = [i for i in items if not i["ok"]]
    fails = failed_runs(20 if slot == "am" else 5)   # 下午只看早上體檢之後的，早上報過的不重報
    sent = [f for f in sorted({i["fix"] for i in bad if i["fix"]}) if dispatch(f, D)] if heal else []
    done = (prev.get("slots") or []) if prev.get("date") == today.isoformat() else []
    res = {"date": today.isoformat(), "slots": sorted({*done, slot}),
           "checked_at": now.strftime("%Y-%m-%dT%H:%M:%S%z"), "slot": slot, "trading_day": D, "source": REF or "checkout",
           "calendar": "twse" if days else "history", "us_day": us_day, "items": items,
           "bad": [i["name"] for i in bad], "failed_runs": fails, "dispatched": sent,
           "dispatched_am": (prev.get("dispatched") or []) if done == ["am"] else [],
           "cron": cron_check(D, now) if slot == "am" or "am" not in done else prev.get("cron"),   # 早上沒體檢（08:40 沒叫到）下午自己看
           "repo_mb": repo_mb() if slot == "am" else prev.get("repo_mb") if done else None}
    # 早上補跑救得到的先不吵；救不到的（沒有補跑方法、派工失敗、工作流程失敗）才寄。下午還缺就寄。
    # 同一個交易日寄過的缺項不再寄：10/22 的 13:12 鎖漲停家數補不回來，10/23～10/27 每班體檢（連颱風假、補假）都寄一封
    told = set(prev.get("told") or []) if prev.get("trading_day") == D else set()
    unfixed = [i for i in bad if i["fix"] not in sent and i["name"] not in told]
    # cron-job.org：偶爾漏一次不吵，連兩天（今天第一次體檢＋上一次體檢）都沒叫到才寄（多半是 token 過期）
    cron_bad = bool(res["cron"]) and prev.get("date") != res["date"] and bool(prev.get("cron"))
    mail = notify and bool(unfixed or fails or cron_bad)
    res["told"] = sorted(told | ({i["name"] for i in unfixed} if mail else set()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", "utf-8")
    try:
        line = {k: res[k] for k in ("checked_at", "slot", "trading_day", "bad", "dispatched", "cron", "repo_mb")}
        line["failed_runs"] = [f["name"] for f in fails]
        old = HIST.read_text("utf-8").splitlines() if HIST.exists() else []
        HIST.write_text("\n".join((old + [json.dumps(line, ensure_ascii=False)])[-120:]) + "\n", "utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("體檢紀錄寫不進 history.jsonl：%s", e)
    log.info("體檢 %s：交易日 %s，缺 %s，失敗 %d 個，補跑 %s", slot, D, res["bad"] or "無", len(fails), sent or "無")

    if mail:
        from . import notify as nt

        lines = [f"## 系統體檢（{'早上' if slot == 'am' else '收盤後'}）：上一個交易日 {D}", ""]
        if unfixed:
            lines += ["**資料沒到：**", ""] + [f"- {i['name']}：最新 {i['have']}，應該要有 {i['want']}" for i in unfixed] + [""]
        if fails:
            lines += ["**失敗的工作流程：**", ""] + [f"- [{f['name']}]({f['url']})（{f['at']}）" for f in fails] + [""]
        if sent:
            lines += [f"已自動補跑：{'、'.join(FIX_NAME[f] for f in sent)}（結果收盤後再查一次）。", ""]
        if res["dispatched_am"]:
            lines += [f"早上已自動補跑過：{'、'.join(FIX_NAME[f] for f in res['dispatched_am'])}，還是沒補到。", ""]
        if cron_bad:
            lines += ["**cron-job.org 連兩天沒準時叫：**", ""] + [f"- {x}" for x in res["cron"]] + [
                f"- 上一次：{'；'.join(prev['cron'])}", "",
                "多半是 cron-job.org 用的 GitHub token 過期了。到 cron-job.org 看那兩個工作的執行紀錄，"
                "出現 401／403 就到 GitHub 重新產生 token（權限要有 Actions: write）貼回去。", ""]
        lines += ["自動補跑救不回來的才會寄這封信。Claude Code 每個交易日 16:25 會看這份體檢（data/health/latest.json）自己處理，"
                  "你不用做什麼；急的話直接在 Claude Code 對話叫它。"]
        nt.send(f"⚠️ 台股系統體檢 {D}：{len(unfixed) + len(fails) + int(cron_bad)} 項要看", "\n".join(lines))
    return res


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="系統自我體檢")
    ap.add_argument("--slot", choices=["am", "pm"])
    ap.add_argument("--heal", action="store_true", help="缺資料就派工補跑")
    ap.add_argument("--notify", action="store_true", help="有救不回來的就寄信")
    ap.add_argument("--latest", action="store_true", help="讀 origin/main 最新的資料檔（先 git fetch）")
    ap.add_argument("--wait-until", metavar="HH:MM", help="不體檢，等今天早上補跑的工作流程跑完（最晚到台北 HH:MM）")
    a = ap.parse_args()
    if a.wait_until:
        wait_dispatched(a.wait_until)
    elif a.slot:
        run(a.slot, a.heal, a.notify, latest=a.latest)
    else:
        ap.error("要給 --slot 或 --wait-until")


if __name__ == "__main__":
    main()
