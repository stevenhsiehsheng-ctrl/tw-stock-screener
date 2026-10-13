"""測資料來源用：印出多個網址的回應開頭（或關鍵字附近）。只讀，不存檔。

python tools/probe.py "URL1|關鍵字" "URL2" ...

MIS 多檔（ex_ch=tse_2330.tw|tse_2317.tw&json=1）裡的 | 是網址的一部分：| 後面那段有 = 或 &、或是 tse_／otc_ 開頭，就當網址、不當關鍵字。
"""
import sys

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept-Language": "zh-TW,zh;q=0.9"}


def split_arg(arg: str) -> tuple[str, str]:
    """『網址|關鍵字』→ (網址, 關鍵字)；最後一個 | 後面像網址參數（有 = 或 &）就整串都是網址。"""
    url, sep, key = arg.rpartition("|")
    if not sep or "=" in key or "&" in key or key.startswith(("tse_", "otc_")):
        return arg, ""
    return url, key


def main(args: list[str]) -> None:
    w = int(__import__("os").environ.get("PROBE_WIDTH", "2500"))
    for arg in args:
        url, key = split_arg(arg)
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
            t = r.text
            i = t.find(key) if key else 0
            print(f"===== {url}\nHTTP {r.status_code} 長度 {len(t)} 型別 {r.headers.get('content-type')} 「{key}」在 {i}")
            print(t[max(i - 300, 0): max(i, 0) + w])
        except Exception as e:  # noqa: BLE001
            print(f"===== {url}\n失敗 {e!r}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
