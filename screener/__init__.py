# tw-stock-screener
"""共用設定：寫 .gz 的 CSV 時（1）gzip 檔頭時間固定成 0（2）不壓縮（壓縮等級 0）。

（1）gzip 檔頭會記「寫檔當下的時間」，資料一模一樣也會寫出不同的檔，git 就多存一整份。
10/14 量過：repo 515 MB 裡約 426 MB 是這種白存的（history.csv.gz 44 版裡 33 版只差檔頭、margin／當沖各 24 版）。

（2）壓縮過的檔案每天多一天資料，整個檔的位元組全變，git 只能再存一整份（history 一次 7 MB）。
10/6～10/13 每天會變的 10 個 .gz 真正有變的 85 版共 130 MB，一年約 7～11 GB（GitHub 建議 1 GB 以下）。
壓縮等級 0 的 gzip 裡面就是原文，git 能只存「多了哪幾行」：同樣 85 版只要 2.9 MB（實測）。
檔名、讀法完全不變（pandas、gzip 照樣讀），只是工作目錄裡的檔案變大（history 7 → 22 MB）。
寫完超過 45 MB 的（inst_5y 這種 5 年檔，GitHub 單檔 50 MB 會警告、100 MB 拒收）改回一般壓縮重寫；
呼叫端自己指定 compresslevel 的照它的。

所有程式都會 import screener，在這裡統一處理。
"""
import os as _os

from pandas.core.generic import NDFrame as _NDFrame

_to_csv = _NDFrame.to_csv   # DataFrame、Series 共用這一個；包在這裡兩種都有效
BIG = 45_000_000


def _to_csv_stable(self, path_or_buf=None, *args, **kw):
    comp = kw.get("compression", "infer")
    gz = (comp == "gzip" or (isinstance(comp, dict) and comp.get("method") == "gzip")
          or (comp == "infer" and isinstance(path_or_buf, (str, bytes)) and str(path_or_buf).endswith(".gz"))
          or (comp == "infer" and hasattr(path_or_buf, "suffix") and str(path_or_buf).endswith(".gz")))
    if not gz:
        return _to_csv(self, path_or_buf, *args, **kw)
    c = {**(comp if isinstance(comp, dict) else {}), "method": "gzip"}
    c.setdefault("mtime", 0)
    own = "compresslevel" in c
    c.setdefault("compresslevel", 0)
    kw["compression"] = c
    out = _to_csv(self, path_or_buf, *args, **kw)
    if (not own and isinstance(path_or_buf, (str, _os.PathLike)) and kw.get("mode", "w") == "w"
            and _os.path.getsize(path_or_buf) > BIG):
        kw["compression"] = {**c, "compresslevel": 6}
        out = _to_csv(self, path_or_buf, *args, **kw)
    return out


_NDFrame.to_csv = _to_csv_stable
