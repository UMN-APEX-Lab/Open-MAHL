"""Centralized logging.

``Log`` writes each line to three places (any of which may be inactive):

* a **file** (so the TUI can tail it from a background thread without races),
* a **sink** callable installed by the TUI (``Log.attach_sink``), and
* **stdout** — but only when no sink is attached (i.e. plain / ``--no-tui`` mode),
  so the TUI screen is never corrupted by stray prints.

Levels map to colour styles understood by the TUI. ``[VERIF]``-tagged variants
let the TUI filter the verification log into its own panel.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

# Keep this many lines in the on-disk log; the TUI decides how many to *show*.
MAX_LOG_LINES = 500


class Log:
    sink: Optional[Callable[[str, str], None]] = None  # set by the TUI
    log_file: Optional[str] = None
    _lock = threading.Lock()

    # ----- wiring -----
    @staticmethod
    def attach_sink(fn: Optional[Callable[[str, str], None]]) -> None:
        Log.sink = fn

    @staticmethod
    def set_log_file(file_path: str) -> None:
        Log.log_file = file_path
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("")

    # ----- core -----
    @staticmethod
    def _write(line: str, style: str = "white") -> None:
        with Log._lock:
            if Log.log_file:
                try:
                    with open(Log.log_file, "a", encoding="utf-8") as f:
                        f.write(f"{line}\n")
                except OSError:
                    pass  # never let logging crash the pipeline
            if Log.sink is not None:
                Log.sink(line, style)
            else:
                print(line)

    # ----- general levels -----
    @staticmethod
    def info(msg: str) -> None:
        Log._write(f"[INFO] {msg}", "cyan")

    @staticmethod
    def warn(msg: str) -> None:
        Log._write(f"[WARN] {msg}", "yellow")

    @staticmethod
    def error(msg: str) -> None:
        Log._write(f"[ERROR] {msg}", "red")

    @staticmethod
    def success(msg: str) -> None:
        Log._write(f"[OK] {msg}", "green")

    @staticmethod
    def step(n: int, msg: str) -> None:
        Log._write(f"\U0001f539 Step {n}: {msg}", "bright_white")

    @staticmethod
    def think(msg: str) -> None:
        """Reasoner output — the 'thinking' shown before a fix is attempted."""
        Log._write(f"\U0001f9e0 {msg}", "magenta")

    # ----- verification-tagged levels -----
    @staticmethod
    def vinfo(msg: str) -> None:
        Log._write(f"[INFO][VERIF] {msg}", "cyan")

    @staticmethod
    def vwarn(msg: str) -> None:
        Log._write(f"[WARN][VERIF] {msg}", "yellow")

    @staticmethod
    def verror(msg: str) -> None:
        Log._write(f"[ERROR][VERIF] {msg}", "red")

    @staticmethod
    def vsuccess(msg: str) -> None:
        Log._write(f"[OK][VERIF] {msg}", "green")
