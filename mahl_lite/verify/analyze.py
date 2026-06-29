"""Turn raw testbench output into an honest verdict.

The previous tool reported PASSED no matter what. We instead trust the marker the
testbench is asked to print, and treat its absence as INCONCLUSIVE rather than a
silent pass. ``FAILED`` wins over ``PASSED`` if both appear (conservative).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Tuple


class Verdict(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"

    @property
    def icon(self) -> str:
        return {"PASS": "✅", "FAIL": "❌", "INCONCLUSIVE": "⚠"}[self.value]


_FAIL_RE = re.compile(r"TEST\s+FAILED\s*:?\s*(.*)", re.IGNORECASE)
_PASS_RE = re.compile(r"TEST\s+PASSED", re.IGNORECASE)


def analyze_testbench_output(output: str) -> Tuple[Verdict, str]:
    """Return (verdict, human-readable reason)."""
    if output is None:
        return Verdict.INCONCLUSIVE, "no output captured"

    fail = _FAIL_RE.search(output)
    if fail:
        reason = fail.group(1).strip() or "testbench reported TEST FAILED"
        return Verdict.FAIL, reason
    if _PASS_RE.search(output):
        return Verdict.PASS, "testbench reported TEST PASSED"
    return Verdict.INCONCLUSIVE, "no 'TEST PASSED' / 'TEST FAILED' marker in output"
