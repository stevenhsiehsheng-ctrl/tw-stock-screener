"""python -m mvdis_booking [run|recon|check|notify-test]"""
from __future__ import annotations

import argparse
import sys

from .config import DEFAULTS, ConfigError, _merge, load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mvdis_booking", description="監理服務網機車考照預約小幫手")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "recon", "check", "notify-test"],
                    help="run＝開始盯場次（預設）；recon＝偵察網頁結構；check＝檢查設定檔；notify-test＝測試通知")
    ap.add_argument("--config", help="設定檔路徑（預設 mvdis_booking/config.yaml）")
    ap.add_argument("-v", "--verbose", action="store_true", help="顯示更多細節")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 命令列顯示中文
    except Exception:     # 舊版或被導向的輸出：維持原樣
        pass
    _disable_quick_edit()

    if args.command == "recon":
        try:
            # 偵察用不到個人資料：applicant 還沒填（例如身分證字號空白）也照樣用 target 的設定實際查詢
            cfg = load_config(args.config, require_applicant=False)
        except ConfigError as e:
            # 偵察不需要個人資料：設定檔還沒填好也能跑，只是不會實際查詢
            print(f"（設定檔還沒準備好：{e}\n  先只偵察查詢頁。）")
            cfg = _merge(DEFAULTS, {})
            cfg["target"]["office_label"] = ""
        from .recon import recon
        return recon(cfg)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"設定檔有問題：{e}")
        return 2

    if args.command == "check":
        from .runner import describe_target
        print("設定檔沒問題：\n" + describe_target(cfg))
        return 0
    if args.command == "notify-test":
        from .notify import Notifier
        from .util import Log
        n = Notifier(cfg, Log())
        n.alert("測試通知", "看到這個通知、聽到聲音，就代表通知設定成功。")
        if cfg["notify"]["email"].get("enabled"):
            try:
                n.send_email_now("測試通知", "收到這封信，就代表 Email 通知設定成功。")
                print("測試信已寄出，請檢查信箱（也看一下垃圾郵件匣）。")
            except Exception as e:
                print(f"Email 寄送失敗：{e}\n請確認 smtp_user 是完整的 Gmail 地址、smtp_password 是 16 碼「應用程式密碼」。")
                return 1
        import time
        time.sleep(3)   # 讓聲音放完
        return 0
    from .runner import run
    return run(cfg, verbose=args.verbose)


def _disable_quick_edit() -> None:
    """Windows 的黑色視窗預設「快速編輯」：不小心點一下視窗就會讓程式整個卡住。把它關掉。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)                       # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)   # 關 QUICK_EDIT、開 EXTENDED_FLAGS
    except Exception:     # 不是一般命令列視窗：不用處理
        pass


if __name__ == "__main__":
    sys.exit(main())
