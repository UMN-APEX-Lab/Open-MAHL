"""Adaptive debugging: the Reasoner -> Coder split.

When a compile or simulation step fails we don't ask one prompt to both diagnose
*and* rewrite. Instead:

    Reasoner  reads the error + all files and writes a plain-language fix plan
              (this is the "thinking" surfaced in the UI) — no code.
    Coder     reads that plan and the files and returns the corrected files.

This mirrors MAHL's adaptive debugging and tends to produce better fixes than a
single pass. Set ``debug.two_phase: false`` in config to fall back to one call.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .config import Config
from .llm import LLMClient
from .log import Log
from .parsing import ensure_json, extract_code_blocks
from .prompts import render


def propose_fixes(
    client: LLMClient,
    error_text: str,
    file_code_map: Dict[str, str],
    config: Config,
    focus_file: Optional[str] = None,
) -> Dict[str, str]:
    """Return ``{filename: corrected_code}`` for a failing compile/sim.

    Empty dict means the model produced nothing usable (the caller decides what to
    do — usually stop the loop).
    """
    file_list = list(file_code_map.keys())
    files_context = _files_context(file_code_map)
    focus = _focus_instruction(focus_file, file_list)
    hints = _hints(error_text)

    if config.debug.two_phase:
        plan = _reason(client, error_text, files_context, focus, hints)
        raw = client.complete(
            render("debug_code", fix_plan=plan, files_context=files_context,
                   focus_instruction=focus, file_list=", ".join(file_list)),
            role="coder",
        )
    else:
        raw = client.complete(
            render("debug_single", error_text=error_text, hints=hints,
                   files_context=files_context, focus_instruction=focus,
                   file_list=", ".join(file_list)),
            role="coder", agent_name="Debugging Agent",
        )
    return _parse_patches(raw, file_list, error_text)


def _reason(client: LLMClient, error_text: str, files_context: str,
            focus: str, hints: str) -> str:
    plan = client.complete(
        render("debug_reason", error_text=error_text, hints=hints,
               files_context=files_context, focus_instruction=focus),
        role="reasoner",
    )
    for line in plan.strip().splitlines():
        if line.strip():
            Log.think(line.strip())
    return plan


# ----- helpers -----
def _files_context(file_code_map: Dict[str, str]) -> str:
    out = "\n=== ALL FILE CONTENTS ===\n"
    for filename, code in file_code_map.items():
        out += f"\n--- FILE: {filename} ---\n```verilog\n{code}\n```\n"
    return out


def _focus_instruction(focus_file: Optional[str], file_list: List[str]) -> str:
    if focus_file and focus_file in file_list:
        return (f"\nPRIMARY TARGET: fix {focus_file} first — it is the main failing "
                f"component. The other files are context.")
    return ""


def _hints(error_text: str) -> str:
    et = error_text.lower()
    lines = []
    if "syntax error" in et:
        lines.append("- SYNTAX ERROR: check missing semicolons, mismatched parens, typos")
    if "undeclared" in et or "not declared" in et:
        lines.append("- UNDECLARED IDENTIFIER: check declarations and instantiations")
    if "port" in et:
        lines.append("- PORT MISMATCH: verify port names/counts/connections vs definitions")
    if "width" in et or "size" in et:
        lines.append("- WIDTH/SIZE: check signal widths and array dimensions")
    if not lines:
        return ""
    return "=== LIKELY ISSUES ===\n" + "\n".join(lines)


def _parse_patches(raw: str, file_list: List[str], error_text: str) -> Dict[str, str]:
    data = ensure_json(raw)
    if isinstance(data, dict) and isinstance(data.get("patches"), list):
        out: Dict[str, str] = {}
        for p in data["patches"]:
            f, c = p.get("file"), p.get("code")
            if isinstance(f, str) and isinstance(c, str):
                out[f] = c
        if out:
            return out

    # Fallback: a single fenced code block -> guess the target file from the error.
    Log.warn("Debugger did not return valid patch JSON; trying code-block fallback.")
    blocks = extract_code_blocks(raw)
    if blocks:
        import re
        m = re.search(r"(?P<file>\w+\.v)", error_text)
        target = m.group("file") if m else (file_list[0] if file_list else "unknown.v")
        return {target: blocks[0][1]}
    return {}
