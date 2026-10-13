"""通知：聲音、桌面通知、Email（Gmail）。失敗不會讓程式停下來。"""
from __future__ import annotations

import os
import shutil
import smtplib
import subprocess
import sys
import threading
import time
from email.message import EmailMessage

EMAIL_COOLDOWN_S = 600

_WIN_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:MVDIS_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:MVDIS_BODY)) | Out-Null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


class Notifier:
    def __init__(self, cfg: dict, log) -> None:
        self.cfg = cfg["notify"]
        self.log = log
        self._last_email: dict = {}

    # ------------------------------------------------------------ 對外
    def alert(self, title: str, body: str, *, email: bool = False, key: str | None = None, beeps: int = 3) -> None:
        self.log.info(f"🔔 {title}：{body}")
        if self.cfg.get("sound") and beeps:
            self.sound(beeps)
        if self.cfg.get("desktop"):
            self.desktop(title, body)
        if email:
            self.email(title, body, key=key or title)

    def sound(self, times: int = 3) -> None:
        threading.Thread(target=self._sound, args=(times,), daemon=True).start()

    def desktop(self, title: str, body: str) -> None:
        try:
            self._desktop(title, body[:200])
        except Exception as e:     # 通知失敗不影響主流程
            self.log.debug(f"桌面通知失敗：{e!r}")

    def email(self, subject: str, body: str, key: str | None = None, force: bool = False) -> bool:
        e = self.cfg.get("email") or {}
        if not e.get("enabled"):
            return False
        k = key or subject
        now = time.monotonic()
        if not force and now - self._last_email.get(k, -1e9) < EMAIL_COOLDOWN_S:
            return False
        self._last_email[k] = now
        threading.Thread(target=self._send_email, args=(e, subject, body), daemon=True).start()
        return True

    def send_email_now(self, subject: str, body: str) -> None:
        """測試用：同步寄出，錯誤會丟出來。"""
        e = self.cfg.get("email") or {}
        if not e.get("enabled"):
            raise RuntimeError("config.yaml 的 notify.email.enabled 不是 true")
        self._send_email(e, subject, body, raise_errors=True)

    # ------------------------------------------------------------ 實作
    def _sound(self, times: int) -> None:
        try:
            if sys.platform.startswith("win"):
                import winsound
                for _ in range(times):
                    winsound.Beep(1500, 250)
                    winsound.Beep(1000, 250)
                    time.sleep(0.15)
                return
            if sys.platform == "darwin" and shutil.which("afplay"):
                for _ in range(times):
                    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], timeout=5, check=False)
                return
            player = shutil.which("paplay") or shutil.which("aplay")
            snd = "/usr/share/sounds/freedesktop/stereo/complete.oga"
            if player and os.path.exists(snd) and player.endswith("paplay"):
                for _ in range(times):
                    subprocess.run([player, snd], timeout=5, check=False)
                return
        except Exception:
            pass
        for _ in range(times):
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(0.3)

    def _desktop(self, title: str, body: str) -> None:
        if sys.platform.startswith("win"):
            env = dict(os.environ, MVDIS_TITLE=title, MVDIS_BODY=body)
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _WIN_TOAST],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", "on run argv", "-e",
                              "display notification (item 2 of argv) with title (item 1 of argv)",
                              "-e", "end run", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-u", "critical", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _send_email(self, e: dict, subject: str, body: str, raise_errors: bool = False) -> None:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[考照預約] {subject}"
            msg["From"] = e["smtp_user"]
            msg["To"] = e.get("mail_to") or e["smtp_user"]
            msg.set_content(body)
            with smtplib.SMTP_SSL(e.get("smtp_host") or "smtp.gmail.com", int(e.get("smtp_port") or 465), timeout=30) as s:
                s.login(e["smtp_user"], e["smtp_password"])
                s.send_message(msg)
            self.log.info(f"已寄出 Email：{subject}")
        except Exception as ex:
            self.log.info(f"Email 寄送失敗：{ex!r}")
            if raise_errors:
                raise
