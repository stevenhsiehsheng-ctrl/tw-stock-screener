# tw-stock-screener
"""共用設定：寫 .gz 的 CSV 時 gzip 檔頭的時間固定成 0。

gzip 檔頭會記「寫檔當下的時間」，資料一模一樣也會寫出不同的檔，git 就多存一整份。
10/14 量過：repo 515 MB 裡約 426 MB 是這種白存的（history.csv.gz 44 版裡 33 版只差檔頭、margin／當沖各 24 版）。
所有程式都會 import screener，在這裡統一處理，內容沒變就是同一個檔、git 不會多存。只影響檔頭，讀檔完全一樣。
"""
from pandas.core.generic import NDFrame as _NDFrame

_to_csv = _NDFrame.to_csv   # DataFrame、Series 共用這一個；包在這裡兩種都有效


def _to_csv_stable(self, path_or_buf=None, *args, **kw):
    comp = kw.get("compression", "infer")
    gz = (comp == "gzip" or (isinstance(comp, dict) and comp.get("method") == "gzip")
          or (comp == "infer" and isinstance(path_or_buf, (str, bytes)) and str(path_or_buf).endswith(".gz"))
          or (comp == "infer" and hasattr(path_or_buf, "suffix") and str(path_or_buf).endswith(".gz")))
    if gz and not (isinstance(comp, dict) and "mtime" in comp):
        kw["compression"] = {**(comp if isinstance(comp, dict) else {}), "method": "gzip", "mtime": 0}
    return _to_csv(self, path_or_buf, *args, **kw)


_NDFrame.to_csv = _to_csv_stable
