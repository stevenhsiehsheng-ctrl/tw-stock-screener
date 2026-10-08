"""全市場等權指數：盤勢主判（Cowork 1858-cw-talk-ewindex、1956-cw-ewgap-c 定案；15:47 和巡查直接讀，不准自己再算）。

data/extras/ew_index.csv 欄位：
- date
- ew_ret：當天等權日報酬（%）。4 碼股（00 開頭的 ETF 不算）、含下市股（下市後自然沒有）；
  報酬「跨缺口」從前一個有效收盤算（最多往回 5 個交易日，ret_mode=cross_gap）；|日報酬|>11% 的丟掉（減資、變更面額、新上市）
- ew_close：ew_ret 連乘，第一天＝100
- ma200：ew_close 的 200 日均（滿 200 天才有）
- close_above：當天收盤 > 當天 ma200（收盤後就知道，給「下一個交易日」用）
- above：前一日的 close_above（不偷看，給回測「當天進場」用）；前一日 gap=1 就沿用再往前的判定
- n_stocks：當天有算到報酬的檔數
- gap：n_stocks 比前 5 日中位少 >2% → 1（資料洞那天不准翻線上線下）
- tr_0050、ma200_0050、above_0050：0050 含息，above_0050 一樣用前一日
- ret_mode：cross_gap

歷史段來自 backtest-data 的 5 年回測檔（Yahoo 還原價；下市股官方原始價用除權息參考價還原）；
每天由 main.py 用 history.csv.gz（官方原始價）× exdiv.csv 的除權息因子補最近幾天，舊的不動。
重建：python -m screener.ewindex --rebuild backtest.csv.gz
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "data" / "extras"
OUT = EX / "ew_index.csv"
STATE = EX / "ew_state.json"   # 最新一天的盤勢（首頁、盤中頁、大環境頁讀）
CAP = 11.0        # |日報酬| 超過這個 % 丟掉
GAP_LIMIT = 5     # 跨缺口最多往回幾個交易日
REDO_DAYS = 10    # 每天重算最近幾個交易日（history 修補缺漏後會變）

log = logging.getLogger("ewindex")


def _universe(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df.code.astype(str).str.fullmatch(r"\d{4}") & ~df.code.astype(str).str.startswith("00")]
    return df.dropna(subset=["close"])


def _factor_pivot(ex: pd.DataFrame, dates, codes) -> pd.DataFrame:
    """除權息因子（prev_close／ref_price）寬表：當天除權息的格子 >1，其他 1。"""
    f = pd.DataFrame(1.0, index=pd.Index(dates, name="date"), columns=codes)
    if ex is None or ex.empty:
        return f
    ex = ex[ex.code.isin(codes) & ex.date.isin(set(dates))].copy()
    ex["f"] = pd.to_numeric(ex.prev_close, errors="coerce") / pd.to_numeric(ex.ref_price, errors="coerce")
    ex = ex[(ex.f > 0.5) & (ex.f < 3)].groupby(["date", "code"]).f.prod()
    for (d, c), v in ex.items():
        f.at[d, c] = v
    return f


def daily_returns(df: pd.DataFrame, ex: pd.DataFrame | None = None, raw_mask=None) -> pd.DataFrame:
    """長表（date, code, close[, adjusted]）→ 每天的等權報酬與檔數。
    ex：除權息表（date, code, prev_close, ref_price）；只套在原始價那些格子（raw_mask，預設全部）。"""
    df = _universe(df)
    close = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    if ex is not None and len(ex):
        f = _factor_pivot(ex, close.index, close.columns)
        if raw_mask is not None and "adjusted" in df:
            raw = set(df.loc[pd.to_numeric(df.adjusted, errors="coerce").fillna(1).eq(0), "code"])
            f.loc[:, [c for c in f.columns if c not in raw]] = 1.0   # Yahoo 還原過的不要再還原一次
        # 往前還原：除權息日之前的價格 ÷ 之後所有因子連乘（同 Yahoo 的做法）
        back = f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
        close = close / back
    prev = (close.ffill(limit=GAP_LIMIT) if GAP_LIMIT else close).shift(1)
    r = (close / prev - 1) * 100
    r = r.where(r.abs() <= CAP)
    out = pd.DataFrame({"ew_ret": r.mean(axis=1), "n_stocks": r.notna().sum(axis=1)})
    out = out[out.n_stocks > 0]
    out.index.name = "date"
    return out.reset_index()


def _bench_tr() -> pd.Series:
    """0050 含息指數：bench_long（Yahoo 還原，2020-10 起）接 bench.csv 的 tr（官方＋除息），以 bench.csv 的刻度為準。"""
    parts = []
    bl = EX / "bench_long.csv"
    if bl.exists():
        x = pd.read_csv(bl, dtype={"code": str})
        x = x[x.code == "0050"].set_index("date").close_adj.astype(float).sort_index()
        parts.append(x.pct_change())
    bf = EX / "bench.csv"
    if bf.exists():
        y = pd.read_csv(bf, dtype={"code": str})
        y = y[y.code.isin(["0050", "50"]) & y.tr.notna()].set_index("date").tr.astype(float).sort_index()
        r2 = y.pct_change().dropna()
        parts.append(r2)
    if not parts:
        return pd.Series(dtype=float)
    r = pd.concat(parts).groupby(level=0).last().sort_index()   # 重疊的日子用 bench.csv
    lvl = (1 + r.fillna(0)).cumprod()
    if bf.exists() and len(y):
        lvl = lvl * (y.iloc[-1] / lvl.loc[y.index[-1]]) if y.index[-1] in lvl.index else lvl
    return lvl


def finish(rets: pd.DataFrame) -> pd.DataFrame:
    """date, ew_ret, n_stocks → 完整欄位。"""
    d = rets.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    d["ew_ret"] = d.ew_ret.astype(float).fillna(0.0).round(5)   # 先四捨五入再連乘，每天重算才不會漂
    d.loc[0, "ew_ret"] = 0.0
    d["ew_close"] = 100 * (1 + d.ew_ret / 100).cumprod()
    d["ma200"] = d.ew_close.rolling(200, min_periods=200).mean()
    med = d.n_stocks.rolling(5, min_periods=1).median().shift(1)
    d["gap"] = ((d.n_stocks < med * 0.98) & med.notna()).astype(int)
    ca = (d.ew_close > d.ma200).where(d.ma200.notna())
    d["close_above"] = ca
    d["above"] = ca.where(d.gap == 0).ffill().shift(1)    # 前一日；gap 日不翻，沿用更早的判定
    tr = _bench_tr()
    d["tr_0050"] = d.date.map(tr)
    m = tr.rolling(200, min_periods=200).mean()
    d["ma200_0050"] = d.date.map(m)
    a50 = (tr > m).where(m.notna()).shift(1)
    d["above_0050"] = d.date.map(a50)
    d["ret_mode"] = "cross_gap"
    for c in ("close_above", "above", "above_0050"):
        d[c] = d[c].map({True: 1, False: 0, 1.0: 1, 0.0: 0}).astype("Int64")
    for c in ("ew_close", "ma200", "tr_0050", "ma200_0050"):
        d[c] = d[c].astype(float).round(4)
    return d[["date", "ew_ret", "ew_close", "ma200", "close_above", "above", "n_stocks", "gap",
              "tr_0050", "ma200_0050", "above_0050", "ret_mode"]]


def _exdiv(long: bool = False) -> pd.DataFrame:
    fs = [EX / "exdiv.csv"] + ([EX / "exdiv_5y.csv.gz"] if long else [])
    xs = [pd.read_csv(f, dtype={"code": str}) for f in fs if f.exists()]
    if not xs:
        return pd.DataFrame(columns=["date", "code", "prev_close", "ref_price"])
    x = pd.concat(xs, ignore_index=True)[["date", "code", "prev_close", "ref_price"]]
    return x.drop_duplicates(["date", "code"], keep="first")


def rebuild(bt_path: str, hist: pd.DataFrame | None = None) -> pd.DataFrame:
    """回測檔（歷史段）＋ history（最近一年，官方原始價×除權息）接起來重算整檔。"""
    bt = pd.read_csv(bt_path, dtype={"code": str})
    a = daily_returns(bt, _exdiv(long=True), raw_mask=True)
    if hist is None:
        from . import fetch
        hist = fetch.load_history()
    b = daily_returns(hist, _exdiv(long=True))
    if len(b) > GAP_LIMIT + 1:
        # 接縫放在回測檔最後 20 天：history 只留現在還在的股票（一年前下市的不在），回測檔最後幾天 Yahoo 常缺一批
        cut = max(sorted(b.date)[GAP_LIMIT + 1], sorted(a.date)[-20] if len(a) >= 20 else "")
        ov = a.merge(b, on="date", suffixes=("_bt", "_off"))
        ov = ov[ov.date >= cut]
        if len(ov):
            log.info("重疊 %d 天：等權日報酬相關 %.3f、平均差 %.3f%%", len(ov),
                     ov.ew_ret_bt.corr(ov.ew_ret_off), (ov.ew_ret_bt - ov.ew_ret_off).mean())
        a = pd.concat([a[a.date < cut], b[b.date >= cut]], ignore_index=True)
    return finish(a)


def state(d: pd.DataFrame) -> dict | None:
    """最新盤勢：收盤 vs 200 日線（給下一個交易日用）。最後一天 gap=1 就用最後一個非 gap 日判定。"""
    x = d[d.ma200.notna()].reset_index(drop=True)
    if x.empty:
        return None
    ok = x[x.gap == 0]
    j = ok.iloc[-1] if len(ok) else x.iloc[-1]
    ca = ok.close_above.astype(int).tolist()
    k = 0
    for c in reversed(ca):
        if c != ca[-1]:
            break
        k += 1
    last = x.iloc[-1]
    st = {
        "date": last.date, "judged_on": j.date, "ew_close": round(float(last.ew_close), 2), "ma200": round(float(last.ma200), 2),
        "dev": round(float(j.ew_close / j.ma200 - 1) * 100, 2), "above": bool(j.close_above == 1),
        "streak": k, "since": ok.date.iloc[-k] if k else j.date, "gap": int(last.gap), "n_stocks": int(last.n_stocks),
        "note": "" if j.close_above == 1 else "盤勢線下：線下沒有可靠的超額（月t 為負）、樣本不足（Cowork 1957 觀察標記、0056 改字，部位不自動砍）",
    }
    if pd.notna(last.tr_0050) and pd.notna(last.ma200_0050):
        st["dev_0050"] = round(float(last.tr_0050 / last.ma200_0050 - 1) * 100, 2)
        st["above_0050"] = bool(last.tr_0050 > last.ma200_0050)
    return st


def save(d: pd.DataFrame) -> None:
    d.to_csv(OUT, index=False)
    st = state(d)
    if st:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")


def section_html() -> str:
    """大環境頁的「盤勢：全市場等權指數」區塊（log 座標折線：等權、200 日線、0050 含息；線下期間塗色）。"""
    if not OUT.exists():
        return ""
    d = pd.read_csv(OUT)
    st = state(d)
    if not st or len(d) < 30:
        return ""
    W, H, L, R, T, B = 800, 260, 44, 8, 10, 22
    t50 = d.tr_0050 / d.tr_0050.dropna().iloc[0] * 100 if d.tr_0050.notna().any() else d.tr_0050
    ys = np.log(pd.concat([d.ew_close, d.ma200, t50]).dropna())
    lo, hi = float(ys.min()), float(ys.max())
    n = len(d)
    X = lambda i: L + (W - L - R) * i / (n - 1)
    Y = lambda v: T + (H - T - B) * (1 - (np.log(v) - lo) / ((hi - lo) or 1))

    def line(v, cls):
        pts = " ".join(f"{X(i):.1f},{Y(x):.1f}" for i, x in enumerate(v) if x == x and x > 0)
        return f"<polyline class='{cls}' points='{pts}' fill='none' vector-effect='non-scaling-stroke'/>"

    shade, i = [], 0
    ca = d.close_above.tolist()
    while i < n:
        if ca[i] == 0:
            j = i
            while j + 1 < n and ca[j + 1] == 0:
                j += 1
            shade.append(f"<rect x='{X(i):.1f}' y='{T}' width='{max(X(j) - X(i), 1.5):.1f}' height='{H - T - B}' class='dn'/>")
            i = j + 1
        else:
            i += 1
    ticks = []
    for v in (60, 80, 100, 150, 200, 300, 400):
        if lo <= np.log(v) <= hi:
            ticks.append(f"<line x1='{L}' x2='{W - R}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' class='gr'/><text x='{L - 4}' y='{Y(v) + 4:.1f}' text-anchor='end'>{v}</text>")
    yrs = []
    for k, dt_ in enumerate(d.date):
        if k and dt_[:4] != d.date.iloc[k - 1][:4]:
            yrs.append(f"<text x='{X(k):.1f}' y='{H - 6}' text-anchor='middle'>{dt_[:4]}</text>")
    svg = (f"<svg viewBox='0 0 {W} {H}' class='ewc' role='img' aria-label='全市場等權指數與 200 日線'>"
           + "".join(shade) + "".join(ticks) + "".join(yrs)
           + line(t50, "t5") + line(d.ma200, "ma") + line(d.ew_close, "ew") + "</svg>")
    below = d[d.close_above.notna()]
    nb = int((below.close_above == 0).sum())
    head = (f"🟢 <b>線上</b>：等權指數離 200 日線 <b>{st['dev']:+.1f}%</b>（{st['since']} 起連 {st['streak']} 天）" if st["above"] else
            f"🌧 <b>線下</b>：等權指數離 200 日線 <b>{st['dev']:+.1f}%</b>（{st['since']} 起 {st['streak']} 天）。{st['note']}")
    return ("<style>.ewc{width:100%;height:auto;display:block;margin:6px 0}.ewc text{font-size:11px;fill:var(--muted)}"
            ".ewc .gr{stroke:var(--line);stroke-width:.6}.ewc .dn{fill:rgba(220,60,60,.13)}"
            ".ewc .ew{stroke:var(--link);stroke-width:2}.ewc .ma{stroke:#e0a020;stroke-width:1.4;stroke-dasharray:4 3}"
            ".ewc .t5{stroke:var(--muted);stroke-width:1.2;opacity:.7}.lg span{margin-right:12px;white-space:nowrap}</style>"
            f"<h2 id='ew'>盤勢：全市場等權指數</h2><div class='light'>{head}"
            f"<div class='meta'>{st['date']} 收盤・{st['n_stocks']:,} 檔"
            + (f"・0050 含息離 200 日線 {st['dev_0050']:+.1f}%" if "dev_0050" in st else "")
            + (f"・⚠️ 今天成分數少 2% 以上（資料洞），盤勢沿用 {st['judged_on']} 的判定" if st["gap"] else "") + "</div></div>"
            + svg
            + "<div class='meta lg'><span style='color:var(--link)'>━ 等權指數</span><span style='color:#e0a020'>┅ 200 日線</span>"
              "<span>━ 0050 含息（同起點）</span><span style='background:rgba(220,60,60,.13)'>　</span> 收在線下的日子（縱軸對數）</div>"
            + f"<p class='meta'>我們買的大多是中小型股，0050 有一半以上是台積電，拿 0050 判盤勢等於問台積電心情，所以盤勢主判改用全市場等權："
              f"上市櫃 4 碼股每天漲跌幅的平均（含之後下市的股票、單日 ±11% 以上的丟掉、停牌的從上一個收盤接著算）連乘。"
              f"收盤在 200 日線下，隔天起就算「線下」；成分數突然少 2% 以上的資料洞那天不翻判定。"
              f"{below.date.iloc[0]} 起 {len(below):,} 個交易日裡有 {nb} 天在線下（{nb / len(below) * 100:.0f}%）。"
              f"每天收盤後更新：<code>data/extras/ew_index.csv</code>。</p>")


def update(hist: pd.DataFrame) -> pd.DataFrame | None:
    """每天：用 history 重算最近 REDO_DAYS 個交易日，接在既有檔後面。檔案不存在就不做（要先 rebuild）。"""
    if not OUT.exists():
        log.warning("ew_index.csv 不存在，跳過（先跑 python -m screener.ewindex --rebuild）")
        return None
    old = pd.read_csv(OUT)
    days = sorted(hist.date.unique())
    if len(days) < GAP_LIMIT + 2:
        return None
    sub = hist[hist.date >= days[max(0, len(days) - REDO_DAYS - GAP_LIMIT - 1)]]
    new = daily_returns(sub, _exdiv())
    new = new[new.date.isin(days[-REDO_DAYS:])]
    keep = old[~old.date.isin(set(new.date))][["date", "ew_ret", "n_stocks"]]
    d = finish(pd.concat([keep, new], ignore_index=True))
    save(d)
    last = d.iloc[-1]
    log.info("等權指數 %s：%.2f、200 日線 %s、收盤%s線上", last.date, last.ew_close,
             f"{last.ma200:.2f}" if pd.notna(last.ma200) else "—",
             "在" if last.close_above == 1 else "不在")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", metavar="BACKTEST_CSV", help="用回測檔＋history 重算整檔")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if a.rebuild:
        d = rebuild(a.rebuild)
        save(d)
        log.info("寫出 %s：%d 天（%s～%s）", OUT, len(d), d.date.min(), d.date.max())


if __name__ == "__main__":
    main()
