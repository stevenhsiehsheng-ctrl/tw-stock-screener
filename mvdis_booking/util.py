"""紀錄檔與 state.json（記住已經預約成功，避免重複報名）。"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from .config import PKG_DIR, TW

RUNS_DIR = PKG_DIR / "runs"
STATE_FILE = PKG_DIR / "state.json"


def now_tw() -> dt.datetime:
    return dt.datetime.now(TW)


class Log:
    def __init__(self, path: Path | None = None, secrets: list[str] | None = None, verbose: bool = False) -> None:
        self.path = path
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 4]
        self.verbose = verbose
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def _mask(self, s: str) -> str:
        for sec in self.secrets:
            s = s.replace(sec, sec[:3] + "***" if len(sec) > 6 else "***")
        return s

    def _write(self, level: str, msg: str) -> None:
        line = f"{now_tw():%m-%d %H:%M:%S} {msg}"
        line = self._mask(line)
        if level != "debug" or self.verbose:
            try:
                print(line, flush=True)
            except UnicodeEncodeError:
                print(line.encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"[{level}] {line}\n")

    def info(self, msg: str) -> None:
        self._write("info", msg)

    def debug(self, msg: str) -> None:
        self._write("debug", msg)


def load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def new_run_dir(kind: str) -> Path:
    d = RUNS_DIR / f"{now_tw():%Y%m%d-%H%M%S}-{kind}"
    d.mkdir(parents=True, exist_ok=True)
    return d
