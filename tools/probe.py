"""測資料來源用：印出多個網址的回應開頭（或關鍵字附近）。只讀，不存檔。

python tools/probe.py "URL1|關鍵字" "URL2" ...
"""
import sys

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept-Language": "zh-TW,zh;q=0.9"}
W = int(__import__("os").environ.get("PROBE_WIDTH", "2500"))
for arg in sys.argv[1:]:
    url, _, key = arg.partition("|")
    try:
        r = requests.get(url, headers=H, timeout=30)
        r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
        t = r.text
        i = t.find(key) if key else 0
        print(f"===== {url}\nHTTP {r.status_code} 長度 {len(t)} 型別 {r.headers.get('content-type')} 「{key}」在 {i}")
        print(t[max(i - 300, 0): max(i, 0) + W])
    except Exception as e:  # noqa: BLE001
        print(f"===== {url}\n失敗 {e!r}")
    print()
