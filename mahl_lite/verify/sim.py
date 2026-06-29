"""Thin wrappers around the Icarus Verilog toolchain.

A *compile/run failure* is a normal result, returned as ``(False, output)`` — it is
the design's problem, and the debug loop handles it. A *missing toolchain* is an
environment problem and raises :class:`~mahl_lite.errors.SimError`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Sequence, Tuple

from ..errors import SimError

DEFAULT_FLAGS = ["-g2012"]


def run_iverilog(
    file_paths: Sequence[Path],
    out_name: str,
    flags: Sequence[str] = DEFAULT_FLAGS,
) -> Tuple[bool, str]:
    """Compile ``file_paths`` to ``out_name``. Returns (ok, combined stderr+stdout)."""
    cmd: List[str] = ["iverilog", *flags, "-o", out_name, *[str(p) for p in file_paths]]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise SimError("iverilog not found on PATH. Please install Icarus Verilog.") from e
    output = res.stderr + (("\n" + res.stdout) if res.stdout else "")
    return res.returncode == 0, output


def run_vvp(exe_name: str) -> Tuple[bool, str]:
    """Run a compiled simulation. Returns (ok, combined stdout+stderr)."""
    try:
        res = subprocess.run(["vvp", exe_name], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise SimError("vvp (Icarus runtime) not found on PATH. Please install Icarus Verilog.") from e
    output = (res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")
    return res.returncode == 0, output
