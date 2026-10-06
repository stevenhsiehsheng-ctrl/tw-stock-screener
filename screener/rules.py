"""篩選條件引擎。

所有條件都在「寬表」（列 = 日期、欄 = 股票代號）上向量化計算，
回傳同形狀的布林表；最後取最新一天判斷是否符合。

每個條件都可以加兩個通用參數：
  days_ago: N   用 N 天前的狀態判斷（例如 days_ago: 1 = 前一天）
  within:   N   最近 N 天內「任一天」成立即可（例如 3 天內曾漲停）
  not:   true   反向（不符合才算）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class Panel:
    """把長表轉成各欄位的寬表，並快取常用指標。"""

    def __init__(self, hist: pd.DataFrame):
        hist = hist.dropna(subset=["close"])
        self.dates = sorted(hist.date.unique())
        piv = lambda c: hist.pivot(index="date", columns="code", values=c).sort_index()
        self.close = piv("close")
        self.open = piv("open").reindex_like(self.close)
        self.high = piv("high").reindex_like(self.close)
        self.low = piv("low").reindex_like(self.close)
        self.volume = piv("volume").reindex_like(self.close).fillna(0)
        # 沒成交的日子：收盤延用前一日（避免均線斷掉），但成交量為 0
        self.traded = self.close.notna()
        self.close = self.close.ffill()
        self._cache: dict = {}

    def ma(self, n: int) -> pd.DataFrame:
        k = ("ma", n)
        if k not in self._cache:
            self._cache[k] = self.close.rolling(n, min_periods=n).mean()
        return self._cache[k]

    def vol_avg(self, n: int) -> pd.DataFrame:
        """前 n 日平均量（不含當日）。"""
        k = ("va", n)
        if k not in self._cache:
            self._cache[k] = self.volume.shift(1).rolling(n, min_periods=n).mean()
        return self._cache[k]

    @property
    def prev_close(self) -> pd.DataFrame:
        return self.close.shift(1)

    @property
    def change_pct(self) -> pd.DataFrame:
        return (self.close / self.prev_close - 1) * 100

    def limit_price(self, up: bool = True) -> pd.DataFrame:
        return limit_price(self.prev_close, up)


def limit_price(prev: pd.DataFrame, up: bool = True) -> pd.DataFrame:
    """漲停（跌停）價：前收 ×1.1（×0.9）照升降單位取到合法檔位。"""
    raw = prev * (1.1 if up else 0.9)
    tick = pd.DataFrame(
        np.select(
            [raw < 10, raw < 50, raw < 100, raw < 500, raw < 1000],
            [0.01, 0.05, 0.1, 0.5, 1.0],
            default=5.0,
        ),
        index=raw.index,
        columns=raw.columns,
    )
    f = np.floor if up else np.ceil
    return f(raw / tick + (1e-6 if up else -1e-6)) * tick


def limit_hits(close: pd.DataFrame, traded: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """收在漲停／跌停價（官方檔位口徑；前一天也要有成交，漲跌幅 >10.5% 的是除權息或減資，不算）。"""
    prev = close.shift(1)
    chg = (close / prev - 1).abs()
    ok = traded & traded.shift(1, fill_value=False) & (chg <= 0.105)
    return (ok & (close >= limit_price(prev, True) - 1e-6), ok & (close <= limit_price(prev, False) + 1e-6))


def _ratio(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a / b.where(b > 0)


# ------------------------------------------------------------------ 條件定義
# 每個函式：(panel, 參數 dict) -> 布林寬表
def c_volume_vs_prev(p: Panel, a):
    """當日成交量 ≥ 前一日的 min 倍。"""
    return _ratio(p.volume, p.volume.shift(1)) >= a.get("min", 2)


def c_volume_vs_avg(p: Panel, a):
    """當日成交量 ≥ 前 days 日均量的 min 倍。"""
    return _ratio(p.volume, p.vol_avg(a.get("days", 5))) >= a.get("min", 2)


def c_volume(p: Panel, a):
    """成交量（張）介於 min~max。1 張 = 1000 股。"""
    lots = p.volume / 1000
    return (lots >= a.get("min", 0)) & (lots <= a.get("max", np.inf))


def c_price(p: Panel, a):
    return (p.close >= a.get("min", 0)) & (p.close <= a.get("max", np.inf))


def c_change_pct(p: Panel, a):
    """當日漲跌幅（%）介於 min~max。"""
    c = p.change_pct
    return (c >= a.get("min", -100)) & (c <= a.get("max", 100))


def _normal_day(p: Panel):
    """今天和前一天都有成交，且漲跌幅在 ±10.5% 內（排除停牌後恢復交易、減資等參考價重設）。"""
    return p.traded & p.traded.shift(1, fill_value=False) & (p.change_pct.abs() <= 10.5)


def c_limit_up(p: Panel, a):
    return _normal_day(p) & (p.close >= p.limit_price(True) - 1e-6)


def c_limit_down(p: Panel, a):
    return _normal_day(p) & (p.close <= p.limit_price(False) + 1e-6)


def c_above_ma(p: Panel, a):
    return p.close > p.ma(a.get("period", 60))


def c_below_ma(p: Panel, a):
    return p.close < p.ma(a.get("period", 60))


def c_cross_above_ma(p: Panel, a):
    """今天收盤站上均線，且前一天收盤還在均線下（真正的「突破」）。"""
    ma = p.ma(a.get("period", 60))
    return (p.close > ma) & (p.close.shift(1) <= ma.shift(1))


def c_cross_below_ma(p: Panel, a):
    ma = p.ma(a.get("period", 60))
    return (p.close < ma) & (p.close.shift(1) >= ma.shift(1))


def c_ma_rising(p: Panel, a):
    """均線上揚：今天的 MA 比 days 天前高。"""
    ma = p.ma(a.get("period", 60))
    return ma > ma.shift(a.get("days", 1))


def c_ma_bullish(p: Panel, a):
    """均線多頭排列：periods 由短到長，短均 > 長均。"""
    ps = a.get("periods", [5, 20, 60])
    out = pd.DataFrame(True, index=p.close.index, columns=p.close.columns)
    for s, l in zip(ps, ps[1:]):
        out &= p.ma(s) > p.ma(l)
    return out


def c_ma_bias(p: Panel, a):
    """乖離率（收盤相對均線，%）介於 min~max。"""
    b = (p.close / p.ma(a.get("period", 20)) - 1) * 100
    return (b >= a.get("min", -100)) & (b <= a.get("max", 100))


def c_new_high(p: Panel, a):
    """收盤創 days 日新高（突破前 days 日最高價）。"""
    n = a.get("days", 20)
    return p.close > p.high.shift(1).rolling(n, min_periods=n).max()


def c_new_low(p: Panel, a):
    n = a.get("days", 20)
    return p.close < p.low.shift(1).rolling(n, min_periods=n).min()


def c_up_days(p: Panel, a):
    """連續上漲 days 天（收盤一天比一天高）。"""
    up = (p.close > p.prev_close).astype(int)
    n = a.get("days", 3)
    return up.rolling(n, min_periods=n).sum() >= n


def c_red_candle(p: Panel, a):
    """紅K：收盤 > 開盤，實體至少 min_body%（相對前收）。"""
    body = (p.close - p.open) / p.prev_close * 100
    return body >= a.get("min_body", 0.0001)


def c_near_high(p: Panel, a):
    """收盤距離前 days 日最高價在 pct% 以內（尚未突破也算）。"""
    n = a.get("days", 60)
    hi = p.high.rolling(n, min_periods=n).max()
    return p.close >= hi * (1 - a.get("pct", 5) / 100)


def c_range_pct(p: Panel, a):
    """近 days 日振幅（最高-最低）/收盤，介於 min~max %。"""
    n = a.get("days", 20)
    r = (p.high.rolling(n, min_periods=n).max() - p.low.rolling(n, min_periods=n).min()) / p.close * 100
    return (r >= a.get("min", 0)) & (r <= a.get("max", 1000))


def c_upper_shadow(p: Panel, a):
    """上影線長度（最高價 − 實體上緣）÷ 前收 ≥ min %：盤中衝高後被賣回來（高檔換手／賣壓）。"""
    s = (p.high - np.maximum(p.open, p.close)) / p.prev_close * 100
    return _normal_day(p) & (s >= a.get("min", 4))


def c_intraday_new_high(p: Panel, a):
    """盤中最高價創 days 日新高（收盤不一定守住）。"""
    n = a.get("days", 60)
    return p.high > p.high.shift(1).rolling(n, min_periods=n).max()


CONDITIONS = {
    "volume_vs_prev": (c_volume_vs_prev, "量比前日≥{min}倍"),
    "volume_vs_avg": (c_volume_vs_avg, "量比{days}日均量≥{min}倍"),
    "volume": (c_volume, "成交量{rng}張"),
    "price": (c_price, "股價{rng}元"),
    "change_pct": (c_change_pct, "漲跌幅{rng}%"),
    "limit_up": (c_limit_up, "漲停"),
    "limit_down": (c_limit_down, "跌停"),
    "above_ma": (c_above_ma, "站上MA{period}"),
    "below_ma": (c_below_ma, "跌破MA{period}"),
    "cross_above_ma": (c_cross_above_ma, "突破MA{period}"),
    "cross_below_ma": (c_cross_below_ma, "跌破MA{period}（當日）"),
    "ma_rising": (c_ma_rising, "MA{period}上揚"),
    "ma_bullish": (c_ma_bullish, "均線多頭排列{periods}"),
    "ma_bias": (c_ma_bias, "MA{period}乖離{rng}%"),
    "new_high": (c_new_high, "創{days}日新高"),
    "new_low": (c_new_low, "創{days}日新低"),
    "up_days": (c_up_days, "連漲{days}天"),
    "red_candle": (c_red_candle, "紅K"),
    "near_high": (c_near_high, "距{days}日高點{pct}%內"),
    "range_pct": (c_range_pct, "{days}日振幅{rng}%"),
    "upper_shadow": (c_upper_shadow, "上影線≥{min}%"),
    "intraday_new_high": (c_intraday_new_high, "盤中創{days}日新高"),
}


def describe(cond: dict) -> str:
    fn, tpl = CONDITIONS[cond["type"]]
    args = {"min": "", "max": "", "days": "", "period": "", "periods": "", "pct": ""}
    args.update(cond)
    lo, hi = cond.get("min"), cond.get("max")
    args["rng"] = (
        f"{lo}~{hi}" if lo is not None and hi is not None
        else f"≥{lo}" if lo is not None else f"≤{hi}" if hi is not None else ""
    )
    text = tpl.format(**args)
    if cond.get("days_ago"):
        text = f"{cond['days_ago']}天前{text}"
    if cond.get("within"):
        text = f"近{cond['within']}天內曾{text}"
    if cond.get("not"):
        text = f"非（{text}）"
    return text


def evaluate(p: Panel, cond: dict) -> pd.Series:
    """回傳最新一天每檔股票是否符合此條件。"""
    t = cond.get("type")
    if t not in CONDITIONS:
        raise ValueError(f"不認得的條件類型：{t}（可用：{', '.join(CONDITIONS)}）")
    m = CONDITIONS[t][0](p, cond).fillna(False).astype(bool)
    if cond.get("days_ago"):
        m = m.shift(int(cond["days_ago"]), fill_value=False)
    if cond.get("within"):
        m = m.astype(int).rolling(int(cond["within"]), min_periods=1).max().astype(bool)
    res = m.iloc[-1]
    return ~res if cond.get("not") else res


def run_strategies(p: Panel, strategies: list[dict], base_filter: list[dict]) -> dict[str, list[str]]:
    """回傳 {策略名稱: [符合的股票代號]}。"""
    base = pd.Series(True, index=p.close.columns)
    base &= p.traded.iloc[-1]  # 當天有成交
    for c in base_filter:
        base &= evaluate(p, c)
    out = {}
    for s in strategies:
        if not s.get("enabled", True):
            continue
        m = base.copy()
        for c in s.get("conditions", []):
            m &= evaluate(p, c)
        out[s["name"]] = sorted(m[m].index)
    return out


def metrics(p: Panel, codes: list[str], spark_days: int = 60) -> pd.DataFrame:
    """報表要顯示的數值。"""
    last = lambda df: df.iloc[-1].reindex(codes)
    d = pd.DataFrame(index=codes)
    d["close"] = last(p.close)
    d["change_pct"] = last(p.change_pct)
    d["volume_lots"] = last(p.volume) / 1000
    d["vol_x_prev"] = last(_ratio(p.volume, p.volume.shift(1)))
    d["vol_x_avg5"] = last(_ratio(p.volume, p.vol_avg(5)))
    d["ma20"] = last(p.ma(20))
    d["ma60"] = last(p.ma(60))
    d["bias60"] = (d.close / d.ma60 - 1) * 100
    tail = p.close.iloc[-spark_days:]
    ma_tail = p.ma(60).iloc[-spark_days:]
    d["spark"] = [tail[c].round(2).tolist() for c in codes]
    d["spark_ma"] = [ma_tail[c].round(2).tolist() for c in codes]
    return d
