"""台股每日篩選器 主程式。

用法：
  python -m screener.main                 # 抓到今天（台北時間）並篩選
  python -m screener.main --date 2026-09-25
  python -m screener.main --no-email      # 不寄信
  python -m screener.main --force         # 就算今天已處理過也重跑
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from . import corpact, enrich, fetch, groups, notify, positions, report, rules, sentiment, stats, tech

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
RESULT_DIR = ROOT / "data" / "results"
STATE_FILE = ROOT / "data" / "state.json"
EXTRAS_DIR = ROOT / "data" / "extras"

log = logging.getLogger("screener")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="資料日期 YYYY-MM-DD（預設今天，台北時間）")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="只用現有歷史資料，不抓新資料")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = yaml.safe_load(Path(a.config).read_text("utf-8"))
    st = cfg.get("settings", {})
    markets = st.get("markets", ["TWSE", "TPEX"])
    keep = int(st.get("history_days", 150))

    target = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ZoneInfo("Asia/Taipei")).date()

    # 1. 更新歷史資料
    if a.no_fetch:
        hist = fetch.load_history()
    else:
        hist = fetch.update_history(target, markets, keep, st.get("source", "auto"))
    if hist.empty:
        log.error("沒有任何歷史資料")
        return 1
    hist = hist[hist.date <= target.isoformat()]
    # 減資、變更面額、大比例配股：之前的價格接起來再算（history.csv.gz 本身維持原始價）
    if not a.no_fetch:
        try:
            corpact.update(dt.date.fromisoformat(hist.date.max()))
        except Exception as e:  # noqa: BLE001
            log.warning("減資／變更面額資料更新失敗：%s", e)
    hist = corpact.adjust(hist)
    data_date = hist.date.max()

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if data_date != target.isoformat():
        log.info("%s 沒有新資料（休市或尚未公布），最新資料日為 %s", target, data_date)
        if not a.force:
            return 0
    if state.get("last_done") == data_date and not a.force:
        log.info("%s 已經處理過，略過（用 --force 可重跑）", data_date)
        return 0

    ndays = hist.date.nunique()
    if ndays < 61:
        log.warning("目前只有 %d 個交易日的資料，季線等長天期指標要累積到 61 天才會準確", ndays)

    # 2. 篩選
    exclude = set(map(str, st.get("exclude_codes") or []))
    hist = hist[~hist.code.isin(exclude)]
    panel = rules.Panel(hist)
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]
    hits = rules.run_strategies(panel, strategies, cfg.get("base_filter", []))
    strat_info = [
        {
            "name": s["name"],
            "desc": "、".join(rules.describe(c) for c in s.get("conditions", [])),
            "codes": hits[s["name"]],
        }
        for s in strategies
    ]
    for s in strat_info:
        log.info("【%s】%d 檔：%s", s["name"], len(s["codes"]), " ".join(s["codes"][:30]))

    # 3. 報表
    all_codes = sorted({c for v in hits.values() for c in v})
    rcfg = cfg.get("report", {})
    spark_days = int(rcfg.get("spark_days", 60))
    met = rules.metrics(panel, all_codes, spark_days)
    groups_data, breadth_rows = [], []
    rs_all = tpl_all = pd.Series(dtype=float)
    kdays = int(rcfg.get("kline_days", 120))
    shrink = float(cfg.get("intraday", {}).get("exit_shrink_ratio", 0.5))
    try:
        rs_all = groups.rs_rating(panel)
        met["rs"] = rs_all.reindex(met.index)
        groups_data = groups.industry_strength(panel, fetch.load_stock_list(markets))
        SITE_DIR.mkdir(parents=True, exist_ok=True)
        groups.write_ohlc(SITE_DIR / "ohlc.json", panel, all_codes, kdays, tech.signals(panel, all_codes, kdays, shrink))
        log.info("族群強弱 %d 個產業；RS 與 K 線資料完成", len(groups_data))
    except Exception as e:  # noqa: BLE001
        log.warning("族群／RS／K 線計算失敗：%s", e)
    # 趨勢樣板、離 52 週高點、大盤寬度（盤中監控也會讀 data/extras/tech.csv、groups.json、breadth.csv）
    try:
        tpl_all = tech.trend_template(panel)
        hi52 = tech.high52_dist(panel)
        met["tpl"] = tpl_all.reindex(met.index)
        met["hi52_dist"] = hi52.reindex(met.index).round(1)
        today = panel.traded.iloc[-1]
        EXTRAS_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"rs": rs_all, "tpl": tpl_all, "hi52_dist": hi52.round(1)}).reindex(today[today].index).rename_axis("code").to_csv(
            EXTRAS_DIR / "tech.csv")
        (EXTRAS_DIR / "groups.json").write_text(json.dumps({"date": data_date, "groups": groups_data}, ensure_ascii=False), "utf-8")
        bdf = tech.save_breadth(EXTRAS_DIR / "breadth.csv", panel)
        breadth_rows = bdf.tail(120)[["date", "above_ma20", "mkt20"]].values.tolist()
        ok = tpl_all.dropna()
        log.info("趨勢樣板：%d / %d 檔符合（%d 檔資料不足 220 天）；寬度 %.1f%%、近 20 日 %+.2f%%",
                 int(ok.astype(bool).sum()), len(ok), int(today.sum() - len(ok.reindex(today[today].index).dropna())),
                 bdf.above_ma20.iloc[-1], bdf.mkt20.iloc[-1])
    except Exception as e:  # noqa: BLE001
        log.warning("趨勢樣板／寬度計算失敗：%s", e)
    stocks = fetch.load_stock_list(markets)

    # 3a. 市場情緒
    senti = {}
    try:
        senti = sentiment.from_history(hist)
        log.info("市場情緒：%s（站上月線 %.0f%%）", senti["level"], senti["above_ma20"])
    except Exception as e:  # noqa: BLE001
        log.warning("情緒計算失敗：%s", e)

    # 3b. 消息面／籌碼面（本益比、法人、月營收、注意處置、重大訊息、新聞）
    xcfg = cfg.get("extras", {})
    ann_map, news_map = {}, {}
    if xcfg.get("enabled", True) and not a.no_fetch:
        try:
            enrich.refresh(dt.date.fromisoformat(data_date))
        except Exception as e:  # noqa: BLE001
            log.warning("消息面資料失敗：%s", e)
        sess = enrich._session()
        try:
            ann = enrich.announcements(sess)
            for code, g in ann.groupby("code"):
                ann_map[code] = list(dict.fromkeys(g.title))[:3]
            log.info("重大訊息 %d 則", len(ann))
        except Exception as e:  # noqa: BLE001
            log.warning("重大訊息失敗：%s", e)
        pri = [c for name in xcfg.get("news_strategies", []) for c in hits.get(name, [])]
        pri = list(dict.fromkeys(pri))[: int(xcfg.get("news_limit", 40))]
        names = stocks.set_index("code").name
        for code in pri:
            try:
                items = enrich.news(sess, code, str(names.get(code, "")))
                if items:
                    news_map[code] = items
            except Exception as e:  # noqa: BLE001
                log.warning("新聞 %s 失敗：%s", code, e)
        log.info("新聞：%d / %d 檔有近期新聞", len(news_map), len(pri))
    extras = enrich.load()
    large, astats = [], {}
    try:
        large = stats.largecap(panel, stocks, extras, rs_all, tpl_all)
        astats = stats.save_alert_stats(panel)
    except Exception as e:  # noqa: BLE001
        log.warning("大型股觀察表／預警統計失敗：%s", e)

    rows = report.build_rows(stocks, met, hits, extras, ann_map, news_map)
    title = rcfg.get("title", "台股每日篩選")
    scanned = int(panel.traded.iloc[-1].sum())
    page = dict(title=title, date=data_date, scanned=scanned, rows=rows, strat_info=strat_info, spark_days=spark_days,
                senti=senti, groups=groups_data, breadth=breadth_rows, large=large, astats=astats)
    report.save_inputs(ROOT / "data" / "report_data.json", **page)
    report.write_site(SITE_DIR, data_date, lambda link: report.render_html(archive_link=link, **page))

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"date": data_date, "strategy": s["name"], "code": c} for s in strat_info for c in s["codes"]],
        columns=["date", "strategy", "code"],
    ).to_csv(RESULT_DIR / f"{data_date}.csv", index=False)

    # 4. 盤中提醒過的股票：檢查量縮出場
    ic = cfg.get("intraday", {})
    exits, holding = positions.update(
        hist, data_date, ic.get("exit_shrink_ratio", 0.5), int(ic.get("max_hold_days", 20)),
        ic.get("stop_loss_pct"))
    try:
        positions.fill_warn(sorted(hist.date.unique()))
    except Exception as e:  # noqa: BLE001
        log.warning("持有訊號補注意處置欄失敗：%s", e)
    pos_md = positions.build_md(exits, holding)
    if senti:
        pos_md = sentiment.md_line(senti) + "\n" + pos_md

    # 5. 通知
    ecfg = cfg.get("email", {})
    total = len(all_codes)
    if ecfg.get("enabled", True) and not a.no_email and (total or exits or ecfg.get("send_when_empty")):
        report_url = os.environ.get("REPORT_URL", "")
        if not report_url and os.environ.get("GITHUB_REPOSITORY"):
            owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
            report_url = f"https://{owner.lower()}.github.io/{repo}/{data_date}.html"
        by_code = {r["code"]: r for r in rows}
        counts = "、".join(f"{s['name']} {len(s['codes'])}" for s in strat_info)
        subject = f"【{title}】{data_date}｜" + (f"出場提醒 {len(exits)}、" if exits else "") + counts
        method = ecfg.get("method", "auto")
        try:
            if method == "gmail" or (method == "auto" and notify.smtp_configured()):
                body = notify.build_email_html(title, data_date, report_url, strat_info, by_code)
                if pos_md:
                    import markdown

                    body = markdown.markdown(pos_md, extensions=["tables"]) + body
                page = (SITE_DIR / f"{data_date}.html").read_bytes()
                notify.send_email(subject, body, (f"report-{data_date}.html", page))
            else:
                notify.create_issue(subject, pos_md + notify.build_issue_md(title, data_date, report_url, strat_info, by_code))
        except Exception as e:  # 通知失敗不影響報表
            log.error("通知失敗：%s", e)

    state["last_done"] = data_date
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    log.info("完成：%s，共 %d 檔被標記", data_date, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
