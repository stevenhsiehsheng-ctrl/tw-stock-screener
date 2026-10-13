"""強勢續攻（聯一光型）：已經很強的股票整理後帶量再創新高。site/momo.html。

Dennis 10/12 問「怎麼買到像聯一光那樣的股票」。聯一光（3441）7 月底前 60 天已漲近一倍，
整理一個月後 8/20、8/21 漲停帶量突破前高，之後 100 → 250。規則照那天的樣子寫（只用當天以前的資料）：

- 主規則「強勢續攻」：前 120 日已漲 ≥50%、120 日前高是 15 天以前的（中間有整理）、
  今天漲 ≥7% 收過前高、量 ≥ 前 20 日均量 2 倍、前 20 日均成交值 ≥3,000 萬
- 對照「低檔爆量突破」：今天漲停（≥9.5% 且收最高）、量 ≥3 倍、收過 120 日高、
  起漲前 60 日漲跌在 −30%～+20%（低檔整理）、成交值同上

5 年統計由 tools/momo5y.py 用 backtest-data 算好存 data/extras/momo_5y.json（隔天開盤買、扣 0.585%、抱 60 天）。
這是事後照聯一光訂的規則，統計會偏樂觀；中位數是負的，只是翻倍機率比隨便挑高，像彩券。
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "history.csv.gz"
EX = ROOT / "data" / "extras"
STATS = EX / "momo_5y.json"
log = logging.getLogger("momo")

RULES = {"cont": "強勢續攻", "base": "低檔爆量突破"}


def panels(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """日 K（date, code, open, high, close, volume）→ 各欄寬表，只留 4 碼普通股。"""
    df = df[df.code.astype(str).str.fullmatch(r"[1-9]\d{3}")]
    def p(c):
        return df.pivot(index="date", columns="code", values=c).sort_index()
    return {"open": p("open"), "high": p("high"), "close": p("close"), "volume": p("volume").fillna(0)}


def events(P: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """回傳 {規則: 布林寬表}，True＝那天收盤時符合（只用當天以前的資料）。"""
    c, h, v = P["close"], P["high"], P["volume"]
    chg = c / c.shift(1) - 1
    vx = v / v.shift(1).rolling(20, min_periods=15).mean()
    hi120 = c.shift(1).rolling(120, min_periods=100).max()
    turn = (c * v).shift(1).rolling(20, min_periods=15).mean() >= 3e7
    # 120 日前高是幾天前：從後往前找最大值的位置
    hi_age = c.shift(1).rolling(120, min_periods=100).apply(lambda a: len(a) - 1 - int(np.nanargmax(a)), raw=True)
    mom = c.shift(1) / c.shift(121) - 1
    base = c.shift(3) / c.shift(63) - 1
    lu = (chg >= 0.095) & ((h - c).abs() <= 1e-6 * c)
    return {
        "cont": (chg >= 0.07) & (c >= hi120) & (hi_age >= 15) & (mom >= 0.5) & (vx >= 2) & turn,
        "base": lu & (vx >= 3) & (c >= hi120) & (base >= -0.30) & (base <= 0.20) & turn,
    }


def recent(days: int = 10) -> pd.DataFrame:
    """最近 days 個交易日的訊號，每檔每規則留最近一次，附今天的量、本益比、當沖率、注意／處置。"""
    hist = pd.read_csv(HIST, dtype={"code": str})
    P = panels(hist)
    ev = events(P)
    c, v = P["close"], P["volume"]
    last = c.index[-1]
    rows = []
    for rule, e in ev.items():
        win = e.iloc[-days:]
        for code in win.columns[win.any()]:
            d = win.index[win[code].to_numpy()][-1]
            rows.append({"rule": rule, "date": d, "code": code, "sig_close": float(c.at[d, code]),
                         "close": float(c.at[last, code]),
                         "chg": float(c[code].iat[-1] / c[code].iat[-2] - 1) * 100,
                         "lots": float(v[code].iat[-1]) / 1000,
                         "vx": float(v[code].iat[-1] / v[code].iloc[-21:-1].mean())})
    df = pd.DataFrame(rows, columns=["rule", "date", "code", "sig_close", "close", "chg", "lots", "vx"])
    if df.empty:
        return df
    df["since"] = (df.close / df.sig_close - 1) * 100
    sl = pd.read_csv(ROOT / "data" / "stock_list.csv", dtype={"code": str}).set_index("code")
    df["name"] = df.code.map(sl["name"])
    df["industry"] = df.code.map(sl["industry"])
    try:
        df["pe"] = df.code.map(pd.read_csv(EX / "pe.csv", dtype={"code": str}).set_index("code")["pe"])
    except Exception:  # noqa: BLE001
        df["pe"] = np.nan
    try:
        dt = pd.read_csv(EX / "daytrade_hist.csv.gz", dtype={"code": str})
        d5 = sorted(dt.date.unique())[-5:]
        df["dt5"] = df.code.map(dt[dt.date.isin(d5)].groupby("code").dt_ratio.mean())
    except Exception:  # noqa: BLE001
        df["dt5"] = np.nan
    try:
        df["rev_yoy"] = df.code.map(pd.read_csv(EX / "revenue.csv", dtype={"code": str}).set_index("code")["rev_yoy"])
    except Exception:  # noqa: BLE001
        df["rev_yoy"] = np.nan
    try:
        w = pd.read_csv(EX / "warnings.csv", dtype={"code": str})
        df["flag"] = df.code.map(w.groupby("code").flag.agg(lambda s: "處置" if "處置" in set(s) else "注意"))
    except Exception:  # noqa: BLE001
        df["flag"] = np.nan
    df["today"] = last
    return df.sort_values(["rule", "date", "vx"], ascending=[True, False, False], ignore_index=True)


def _num(v, fmt="{:.0f}", none="—"):
    return none if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)


def _stats_html(s: dict) -> str:
    if not s:
        return "<p class='meta'>5 年統計還沒算（tools/momo5y.py）。</p>"
    out = []
    for rule, name in RULES.items():
        r = s.get(rule)
        if not r:
            continue
        yrs = "".join(f"<tr><td>{y}</td><td>{d['n']}</td><td>{d['dbl']:.0f}%</td><td>{d['mean']:+.1f}%</td><td>{d['med']:+.1f}%</td></tr>"
                      for y, d in sorted(r["by_year"].items()))
        out.append(
            f"<h3>{name}：過去 5 年 {r['n']:,} 次（{r['start']}～{r['end']}）</h3>"
            f"<div class='tbl'><table><tr><th></th><th>符合條件</th><th>隨便挑一檔</th></tr>"
            f"<tr><td>60 天內曾翻倍</td><td><b>{r['dbl']:.1f}%</b></td><td>{s['random']['dbl']:.1f}%</td></tr>"
            f"<tr><td>60 天內曾漲 50%</td><td>{r['half']:.0f}%</td><td>—</td></tr>"
            f"<tr><td>抱 60 天中位數（扣費稅）</td><td><b>{r['med']:+.1f}%</b></td><td>{s['random']['med']:+.1f}%</td></tr>"
            f"<tr><td>抱 60 天賺錢比例</td><td>{r['win']:.0f}%</td><td>—</td></tr>"
            f"<tr><td>抱 60 天平均</td><td>{r['mean']:+.1f}%</td><td>{s['random']['mean']:+.1f}%</td></tr>"
            f"<tr><td>減同期全市場等權（平均／中位）</td><td>{r['ex_mean']:+.1f}%／{r['ex_med']:+.1f}%</td><td>—</td></tr>"
            f"</table></div><details><summary>每年分開看</summary><div class='tbl'><table>"
            f"<tr><th>年</th><th>次數</th><th>翻倍</th><th>平均</th><th>中位</th></tr>{yrs}</table></div></details>"
            f"{_exits_html(r)}")
    return "".join(out)


def _exits_html(r: dict) -> str:
    """出場方式比較（tools/momo5y.py EXITS，先定好 6 種再跑，全部列出）。"""
    ex = r.get("exits")
    if not ex:
        return ""
    rows = "".join(f"<tr><td>{x['name']}</td><td>{x['mean']:+.1f}%</td><td>{x['med']:+.1f}%</td><td>{x['win']:.0f}%</td>"
                   f"<td>{x['big']:.0f}%</td><td>{x['worst']:+.1f}%</td><td>{x['days']:.0f}</td><td>{x['years_neg']}/{x['years']}</td></tr>"
                   for x in ex.values())
    return ("<details><summary>什麼時候賣？6 種出場方式比較</summary><div class='tbl'><table>"
            "<tr><th>出場</th><th>平均</th><th>中位</th><th>賺錢</th><th>賺 ≥50%</th><th>最差 1 成</th><th>抱幾天</th><th>平均虧的年</th></tr>"
            f"{rows}</table></div><p class='meta'>停損（跌 10% 賣、從高點回落 15% 賣）能把最慘的 1 成從 −25～−30% 壓到 −15% 左右，"
            "但平均報酬會掉一半以上、賺錢的比例也變低——這類股票賺的是少數幾檔大漲，提早賣就吃不到。"
            "想控制風險，比較有效的是每檔放小錢、分散多檔，而不是設很緊的停損。"
            "觸發看收盤、隔天開盤賣；6 種是先定好再跑的，全部列出，沒有挑最好看的。</p></details>")


def write(site_dir: Path) -> bool:
    df = recent()
    stats = json.loads(STATS.read_text("utf-8")) if STATS.exists() else {}
    def link(c):
        return f"<a href='stock.html?code={html.escape(c)}'>{html.escape(c)}</a>"
    secs = []
    for rule, name in RULES.items():
        d = df[df.rule == rule] if len(df) else df
        if d.empty:
            secs.append(f"<h2>{name}</h2><p class='meta'>最近 10 個交易日沒有符合的。</p>")
            continue
        trs = "".join(
            f"<tr><td>{r.date[5:]}</td><td>{link(r.code)} {html.escape(str(r.name))}<div class='meta'>{html.escape(str(r.industry))}</div></td>"
            f"<td class='n'>{r.sig_close:g} → {r.close:g}<div class='meta'>{r.since:+.1f}%</div></td>"
            f"<td class='n'>{r.chg:+.1f}%<div class='meta'>{r.lots:,.0f} 張・{'<b>' if r.vx >= 2 else ''}量 {r.vx:.1f} 倍{'</b>' if r.vx >= 2 else ''}</div></td>"
            f"<td class='n'>{_num(r.pe)}</td><td class='n'>{_num(r.dt5, '{:.0f}%')}</td><td class='n'>{_num(r.rev_yoy, '{:+.0f}%')}</td>"
            f"<td>{'' if pd.isna(r.flag) else html.escape(str(r.flag))}</td></tr>"
            for r in d.itertuples())
        secs.append(
            f"<h2>{name}</h2><div class='tbl'><table><tr><th>訊號日</th><th>股票</th><th>訊號日 → {df.today.iat[0][5:]} 收盤</th>"
            f"<th>今天</th><th>本益比</th><th>當沖率<div class='meta'>5 日</div></th><th>營收年增</th><th>狀態</th></tr>"
            f"{trs}</table></div>")
    body = (
        "<h1>🚀 強勢續攻（聯一光型）</h1>"
        "<p>聯一光（3441）7 月底前 60 天已經漲了快一倍，整理一個月後 8/20、8/21 漲停帶量突破前高，之後從 100 漲到 250。"
        "這頁每天列出最近 10 個交易日長得像它突破那天的股票，旁邊是同樣條件過去 5 年的結果。</p>"
        "<p class='lead'><b>先看統計再看名單</b>：符合條件的股票 60 天內翻倍的機會大約是隨便挑的 3 倍，但<b>中位數是賠的、一半以上抱 60 天會虧</b>；"
        "平均會是正的，是靠少數幾檔大漲撐起來，跟彩券一樣。真要玩：小錢、分散押很多檔、先想好什麼時候賣。"
        "規則是看了聯一光之後才訂的，歷史數字會偏樂觀。這不是買賣建議。</p>"
        f"{''.join(secs)}"
        "<p class='meta'>主規則：前 120 日已漲 ≥50%、120 日前高是 15 個交易日以前的、今天漲 ≥7% 收過前高、量 ≥ 前 20 日均量 2 倍、前 20 日均成交值 ≥3,000 萬。"
        "對照規則：今天漲停（≥9.5% 且收最高）、量 ≥3 倍、收過 120 日高、起漲前 60 日漲跌 −30%～+20%。只算 4 碼普通股。"
        "量比＝今天量 ÷ 前 20 日均量；本益比、當沖率、營收年增是最新抓到的那天（傍晚補抓後更新）。處置股不能當沖、每 5 或 20 分鐘才撮合一次。"
        "「訊號後」已經漲很多的，現在買跟訊號日買是兩回事，下面的統計不適用。</p>"
        "<h2>過去 5 年同樣條件的結果</h2>"
        f"{_stats_html(stats)}"
        "<p class='meta'>回測：5 年 Yahoo 日 K（含下市股），同一檔 60 天內只算第一次；隔天開盤買（漲停當天通常排不到）、"
        "第 60 個交易日收盤算報酬、扣來回費稅 0.585%；「曾翻倍」看 60 天內最高收盤。隨便挑一檔＝流動性相同門檻下隨機抽股票和日子。</p>")
    from .weekly import _page
    (site_dir / "momo.html").write_text(_page("強勢續攻（聯一光型）", body, "<!--SITENAV:momo-->"), "utf-8")
    return True
