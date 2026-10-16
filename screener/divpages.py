"""配息資料給外部用：每檔 ETF 一頁的配息紀錄（給 Google 搜尋帶人進來）、試算表用的公開 CSV、sitemap。

產生：
- site/div/index.html：全部有配息的 ETF 清單
- site/div/<代號>.html（兩年內配息至少 MIN_PAYOUTS 次的 ETF）：「0056 除息日、配息紀錄」——下次除息、近 12 月配息與殖利率、過去兩年每次配多少
- site/data/dividends.csv：每次除息一列（id, ex_date, code, name, cash_div, status, ym），已除＋已公告的預告
- site/data/div_summary.csv：每檔一列（id＝#代號、現價、近 12 月配息、殖利率、下次除息日與金額），付費試算表用 IMPORTDATA 讀
- site/calc/*.html：三個試算頁（screener/calcpages.py）
- site/sitemap.xml：給 Google Search Console 提交

資料：data/extras/exdiv.csv（已除，約兩年）、exdiv_upcoming.csv（預告）、etf.csv.gz（ETF 名稱、收盤）、
history.csv.gz（個股收盤）、data/stock_list.csv（個股名稱）。只整理公開資料，不寫任何買賣建議。
商品連結在 config.yaml 的 product.url（空的就不放購買區塊）；網址在 product.site_url。
"""
from __future__ import annotations

import datetime as dt
import html
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EX = DATA / "extras"
log = logging.getLogger(__name__)

DISCLAIMER = "本站僅整理證交所、櫃買中心公開資訊，不構成任何投資建議，資料可能有誤或延遲，以官方公告為準，投資盈虧請自行負責。"
MIN_PAYOUTS = 4   # 兩年內至少配過幾次才產生單檔頁
SITE_URL = "https://stevenhsiehsheng-ctrl.github.io/tw-stock-screener/"


def _cfg() -> dict:
    try:
        c = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8")) or {}
    except Exception:
        return {}
    return c.get("product") or {}


def _r(v, nd=3):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def load(today: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (events, summary)。events：每次除息一列；summary：每檔一列。"""
    today = today or dt.date.today().isoformat()
    done = pd.read_csv(EX / "exdiv.csv", dtype={"code": str}, usecols=["date", "code", "cash_div"])
    done = done[done.cash_div.fillna(0) > 0].assign(status="已除息")
    up = pd.read_csv(EX / "exdiv_upcoming.csv", dtype={"code": str}, usecols=["date", "code", "kind", "cash_div"])
    up = up[up.kind.astype(str).str.contains("息")]
    up = up.assign(status=np.where(up.cash_div.fillna(0) > 0, "已公告", "待公告"),
                   cash_div=up.cash_div.where(up.cash_div.fillna(0) > 0))[["date", "code", "cash_div", "status"]]
    # 預告表在除息當天還會出現；已除的表有同一筆就用已除的
    ev = pd.concat([done, up[~up.set_index(["date", "code"]).index.isin(done.set_index(["date", "code"]).index)]])
    ev.loc[(ev.status != "已除息") & (ev.date < today), "status"] = "已除息"
    ev = ev.sort_values(["date", "code"]).drop_duplicates(["date", "code"], keep="first")

    e = pd.read_csv(EX / "etf.csv.gz", dtype={"code": str}, usecols=["date", "code", "close", "name"]).sort_values("date")
    names = e.dropna(subset=["name"]).drop_duplicates("code", keep="last").set_index("code").name
    px = e.dropna(subset=["close"]).drop_duplicates("code", keep="last").set_index("code")[["close", "date"]]
    try:
        s = pd.read_csv(DATA / "stock_list.csv", dtype={"code": str}).set_index("code").name
        names = pd.concat([names, s[~s.index.isin(names.index)]])
        h = pd.read_csv(DATA / "history.csv.gz", dtype={"code": str}, usecols=["date", "code", "close"])
        h = h[h.date >= sorted(h.date.unique())[-10]].dropna(subset=["close"]).sort_values("date")
        hp = h.drop_duplicates("code", keep="last").set_index("code")[["close", "date"]]
        px = pd.concat([px, hp[~hp.index.isin(px.index)]])
    except FileNotFoundError:
        pass
    ev["name"] = ev.code.map(names).fillna("")
    ev = ev.rename(columns={"date": "ex_date"})[["ex_date", "code", "name", "cash_div", "status"]].reset_index(drop=True)

    y_ago = (dt.date.fromisoformat(today) - dt.timedelta(days=365)).isoformat()
    rows = []
    for code, g in ev.groupby("code"):
        past = g[g.status == "已除息"]
        ttm = past[past.ex_date > y_ago]
        nxt = g[(g.ex_date >= today) & (g.status != "已除息")].head(1)
        close = px.close.get(code, np.nan)
        cash = ttm.cash_div.sum() if len(ttm) else 0.0
        rows.append({"code": code, "name": names.get(code, ""), "close": _r(close, 2),
                     "price_date": px.date.get(code, ""), "ttm_cash": _r(cash, 4), "ttm_count": len(ttm),
                     "ttm_yield_pct": _r(cash / close * 100, 2) if close and np.isfinite(close) and cash else None,
                     "last_ex_date": past.ex_date.iloc[-1] if len(past) else "",
                     "last_cash": _r(past.cash_div.iloc[-1], 4) if len(past) else None,
                     "next_ex_date": nxt.ex_date.iloc[0] if len(nxt) else "",
                     "next_cash": _r(nxt.cash_div.iloc[0], 4) if len(nxt) else None,
                     "next_status": nxt.status.iloc[0] if len(nxt) else ""})
    return ev, pd.DataFrame(rows)


def is_etf(code: str) -> bool:
    return str(code).startswith("00")


def freq(n: int) -> str:
    return {12: "月配", 4: "季配", 2: "半年配", 1: "年配"}.get(n, f"近 12 月配 {n} 次" if n else "近 12 月沒配息")


CSS = ("<style>body{font:15px/1.7 system-ui,-apple-system,'Noto Sans TC',sans-serif;max-width:860px;margin:0 auto;padding:16px;"
       "background:#f7f7f5;color:#1d1d1b}a{color:#2f5bd3}h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:22px 0 8px}"
       ".kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}"
       ".kpi div{background:rgba(128,128,128,.1);border-radius:10px;padding:10px 12px}.kpi b{display:block;font-size:20px}"
       ".kpi span{font-size:12px;opacity:.7}table{border-collapse:collapse;width:100%;font-size:14px}"
       "th,td{padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.22);text-align:left}td.n{text-align:right;font-variant-numeric:tabular-nums}"
       ".cta{border:1px solid #2f5bd3;border-radius:12px;padding:12px 14px;margin:18px 0;background:rgba(47,91,211,.06)}"
       ".note{font-size:12px;opacity:.7;margin-top:24px}.wrap{overflow-x:auto}"
       "@media(prefers-color-scheme:dark){body{background:#141413;color:#ecebe6}a{color:#7d9cf0}}</style>")


def _cta(cfg: dict, prefix: str) -> str:
    url, title = cfg.get("url"), cfg.get("title") or "存股股息追蹤試算表"
    if not url:
        return ""
    return (f"<div class='cta'><b>📒 {html.escape(title)}</b><br>輸入你持有的股票和張數，自動算出每月領多少股息、"
            f"下次除息日和整體殖利率，資料每天自動更新。<br><a href='{html.escape(url)}' rel='nofollow'>看試算表介紹 →</a></div>")


def _fmt(v, nd=3):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{nd}f}".rstrip("0").rstrip(".")


def etf_page(code: str, s: dict, g: pd.DataFrame, cfg: dict, year: int) -> str:
    name = s["name"] or code
    t = f"{code} {name} 除息日、配息紀錄與殖利率（{year}）"
    nxt = (f"{s['next_ex_date']}（{'每股 ' + _fmt(s['next_cash'], 4) + ' 元' if s['next_cash'] else '金額待公告'}）"
           if s["next_ex_date"] else "尚未公告")
    desc = (f"{code} {name} 下次除息日 {nxt}；近 12 個月配息 {_fmt(s['ttm_cash'], 4)} 元、{freq(s['ttm_count'])}，"
            f"以 {s['price_date'] or '最近'} 收盤估算殖利率 {_fmt(s['ttm_yield_pct'], 2)}%。")
    rows = "".join(f"<tr><td>{r.ex_date}</td><td class='n'>{_fmt(r.cash_div, 4)}</td><td>{r.status}</td></tr>"
                   for r in g.sort_values("ex_date", ascending=False).itertuples())
    return ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(t)}</title><meta name='description' content='{html.escape(desc)}'>"
            f"<link rel='canonical' href='{SITE_URL}div/{code}.html'>{CSS}</head><body><!--SITENAV:exdiv:../-->"
            f"<h1>{html.escape(t)}</h1><p>{html.escape(desc)}</p>"
            "<div class='kpi'>"
            f"<div><span>下次除息日</span><b>{s['next_ex_date'] or '未公告'}</b></div>"
            f"<div><span>下次每股配息</span><b>{_fmt(s['next_cash'], 4) if s['next_cash'] else ('待公告' if s['next_ex_date'] else '—')}</b></div>"
            f"<div><span>近 12 月配息（元）</span><b>{_fmt(s['ttm_cash'], 4)}</b></div>"
            f"<div><span>近 12 月殖利率</span><b>{_fmt(s['ttm_yield_pct'], 2)}%</b></div>"
            f"<div><span>配息頻率</span><b>{freq(s['ttm_count'])}</b></div>"
            f"<div><span>收盤價（{s['price_date'] or '—'}）</span><b>{_fmt(s['close'], 2)}</b></div></div>"
            f"{_cta(cfg, '../')}"
            f"<h2>{code} 歷次配息</h2><div class='wrap'><table><tr><th>除息日</th><th>每股現金股利（元）</th><th>狀態</th></tr>{rows}</table></div>"
            "<p>殖利率＝近 12 個月已除息的現金股利合計 ÷ 最近收盤價，只是用過去配息估算，不代表未來會配一樣多。</p>"
            "<p><a href='index.html'>全部 ETF 配息一覽</a> · <a href='../exdiv.html'>除權息行事曆</a> · <a href='../etf.html'>ETF 專區</a></p>"
            f"<p class='note'>{DISCLAIMER}</p></body></html>")


def index_page(summ: pd.DataFrame, cfg: dict, date: str) -> str:
    summ = summ.sort_values(["next_ex_date", "code"], key=lambda c: c.replace("", "9999") if c.name == "next_ex_date" else c)
    rows = "".join(
        f"<tr><td><a href='{r.code}.html'>{r.code}</a></td><td>{html.escape(str(r.name))}</td><td>{r.next_ex_date or '—'}</td>"
        f"<td class='n'>{_fmt(r.next_cash, 4) if r.next_cash else ('待公告' if r.next_ex_date else '—')}</td>"
        f"<td class='n'>{_fmt(r.ttm_cash, 4)}</td><td class='n'>{_fmt(r.ttm_yield_pct, 2)}</td><td>{freq(r.ttm_count)}</td></tr>"
        for r in summ.itertuples())
    t = f"台股 ETF 除息日與配息一覽（{date[:4]}）"
    return ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{t}</title><meta name='description' content='全部台股 ETF 的下次除息日、每股配息、近 12 月殖利率與配息頻率，每天收盤後更新。'>"
            f"<link rel='canonical' href='{SITE_URL}div/index.html'>{CSS}</head><body><!--SITENAV:exdiv:../-->"
            f"<h1>{t}</h1><p>資料日期 {date}，每天收盤後自動更新。點代號看那檔 ETF 的歷次配息。試算工具：<a href='../calc/tax.html'>股利所得稅</a> · <a href='../calc/nhi.html'>二代健保補充保費</a> · <a href='../calc/goal.html'>存股目標</a></p>{_cta(cfg, '../')}"
            "<div class='wrap'><table><tr><th>代號</th><th>名稱</th><th>下次除息日</th><th>下次配息</th><th>近 12 月配息</th>"
            f"<th>殖利率 %</th><th>頻率</th></tr>{rows}</table></div><p class='note'>{DISCLAIMER}</p></body></html>")


def write(site_dir: Path, today: str | None = None) -> bool:
    today = today or dt.date.today().isoformat()
    cfg = _cfg()
    ev, summ = load(today)
    if summ.empty:
        return False
    (site_dir / "data").mkdir(parents=True, exist_ok=True)
    # id＝「#0056」：Google 試算表 IMPORTDATA 會把 0056 讀成數字 56，查表要用一定是文字的鍵
    ev.insert(0, "id", "#" + ev.code)
    ev["ym"] = "m" + ev.ex_date.str[:4] + ev.ex_date.str[5:7]   # 除息月份，同理用文字（2026-10 會被讀成日期）
    ev.to_csv(site_dir / "data" / "dividends.csv", index=False, float_format="%.4f")
    summ.insert(0, "id", "#" + summ.code)
    summ.to_csv(site_dir / "data" / "div_summary.csv", index=False)
    d = site_dir / "div"
    d.mkdir(exist_ok=True)
    # 只做配息紀錄夠多的 ETF：紀錄太少的頁只是模板換代號，Google 會當成大量低品質頁面
    n_past = ev[ev.status == "已除息"].groupby("code").size()
    etfs = summ[summ.code.map(is_etf) & (summ.code.map(n_past).fillna(0) >= MIN_PAYOUTS)]
    year = int(today[:4])
    for s in etfs.to_dict("records"):
        (d / f"{s['code']}.html").write_text(etf_page(s["code"], s, ev[ev.code == s["code"]], cfg, year), "utf-8")
    (d / "index.html").write_text(index_page(etfs, cfg, today), "utf-8")
    base = cfg.get("site_url") or SITE_URL
    from . import calcpages   # 試算頁一起寫，才能放進同一份 sitemap
    urls = ["", "exdiv.html", "etf.html", "div/index.html"] + calcpages.write(site_dir) + [f"div/{c}.html" for c in etfs.code]
    (site_dir / "sitemap.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
        + "".join(f"<url><loc>{base}{u}</loc><lastmod>{today}</lastmod></url>" for u in urls) + "</urlset>\n", "utf-8")
    log.info("配息頁 %d 檔、dividends.csv %d 筆", len(etfs), len(ev))
    return True
