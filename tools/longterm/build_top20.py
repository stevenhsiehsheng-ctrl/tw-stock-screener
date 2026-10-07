"""把最終 20 檔（代號＋論點／風險／破壞條件）跟量化排名、外部觀點清單合起來，寫 data/longterm/top20.json。

用法：python tools/longterm/build_top20.py <picks.json> <asof YYYY-MM-DD> <screen csv> <views csv> [版本號]
picks.json：[{"code": "2330", "group": "AI 連動", "thesis": "...", "risk": "...", "break_rule": "..."}, ...]
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
METHOD = [
    "① 量化篩選：上市櫃普通股、市值 ≥300 億、近 60 日平均成交值 ≥1 億，共 294 檔。分數＝下列指標排名百分位加權："
    "4 年營收年化成長（近 12 個月合計比 4 年前）25%、近 36 個月營收年增為正的比例 15%、營收韌性（12 個月營收從高點最大跌幅，越小越好）15%、"
    "ROE（股價淨值比÷本益比推算）20%、PEG（本益比÷營收成長）10%、5 年股價最大回檔 10%、殖利率 5%。權重是判斷，不是用歷史資料調出來的。",
    "第一版曾放『近一年營收成長』，結果記憶體、券商排到最前面——那是景氣循環高點的暴增，本益比也因此看起來很低，長期投資最怕這種，所以拿掉、改用營收韌性。",
    "② 外部觀點：整理外資、投信、券商 2026 年公開的看好名單（來源與日期列在最下面），算每檔被幾份專業清單點名。",
    "③ Cowork 逐檔審查：長線論點、主要風險、可觀察的破壞條件，並控制產業集中（同一條供應鏈不要全押）。",
    "金融股的『月營收』跟一般公司不能比，量化分數只當參考；世芯-KY 月營收檔沒有資料，量化排名失真。",
]

CRITERION = ("頁面一律叫『觀察名單』。只看『對每檔自己 10 檔同類』這條（同類＝起點前 250 日日報酬相關最高、名單外、共同交易日 ≥200，起點凍結）。"
             "2027-10-07 收盤結算：從同一池子、用同一套同類規則隨機抽 1 萬組 20 檔，"
             "(a) 各檔 log 超額＝ln(1+個股報酬)−ln(1+自己 10 檔同類報酬中位數)，截在 ±ln2（翻倍／腰斬）後的平均 > 隨機組 95 分位，且 (b) 贏自己同類中位數的檔數 > 隨機組 95 分位 → 有選股能力的證據；"
             "兩個都低於 50 分位 → 沒贏亂抽；其他 → 繼續觀察。不准寫『超額 +X% 所以有能力』，檢定力低就老實低，不延長也不放寬。"
             "隨機組用不配對版；另印起點（10/7 凍結）市值三分位×電子／非電子配對版當參考，不進判準。下市／停牌以最後價轉現金。被換掉的繼續當幽靈追蹤 12 個月。"
             "（Cowork 1258／1355／1555／1655、分身 1415／1515／1615，10/8 有第一天成績前寫死）")


def main(picks_f, asof, screen_f, views_f, version="1"):
    picks = json.loads(Path(picks_f).read_text("utf-8"))
    sc = pd.read_csv(screen_f, dtype={"code": str}).set_index("code")
    names = pd.read_csv(ROOT / "data/stock_list.csv", dtype=str).set_index("code")
    v = pd.read_csv(views_f)
    pro = v[v.kind.isin(["外資券商", "外資共識"])]
    out = []
    for p in picks:
        c = p["code"]
        lists = [r.list_id for r in pro.itertuples() if c in r.codes.split()]
        m = sc.loc[c] if c in sc.index else None
        out.append({**p, "name": p.get("name") or names.name.get(c, c), "industry": names.industry.get(c, ""),
                    "ext_lists": lists, "qrank": int(m["rank"]) if m is not None else None,
                    "metrics": {k: (None if m is None or pd.isna(m[k]) else float(m[k]))
                                for k in ("rev_g4", "rev_cons36", "rev_ttm_dd", "roe", "pe", "yield", "px_cagr5", "px_mdd5")}})
    top = {"asof": asof, "version": int(version), "made_by": "Claude Code 量化＋外部觀點、Cowork 質化審查",
           "views": Path(views_f).name, "screen": Path(screen_f).name, "method": METHOD, "picks": out,
           "criterion": CRITERION, "peers": f"peers_{asof}.json"}
    dst = ROOT / "data/longterm/top20.json"
    dst.write_text(json.dumps(top, ensure_ascii=False, indent=1), "utf-8")
    print(f"寫入 {dst}：{len(out)} 檔，起點 {asof}")


if __name__ == "__main__":
    main(*sys.argv[1:])
