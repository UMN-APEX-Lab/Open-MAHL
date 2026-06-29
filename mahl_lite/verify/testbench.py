"""LLM self-checking testbench: generate, compile-and-fix, run, improve.

This is the verification path for designs the cross-validator can't yet handle
(sequential/clocked), and a second opinion for the ones it can. Unlike the cross
validator, the pass/fail decision here comes from the LLM-written testbench, so its
verdict is treated as a *second opinion* and audited against the cross-validator
when both are available (see pipeline).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from ..config import Config
from ..debug import propose_fixes
from ..llm import LLMClient
from ..log import Log
from ..parsing import extract_first_code, ensure_json
from ..prompts import render
from . import sim
from .analyze import Verdict, analyze_testbench_output


def generate_testbench(client: LLMClient, top_module: str, module_descriptions: str,
                       lib_dir: Path) -> str:
    top_path = lib_dir / f"{top_module}.v"
    top_code = top_path.read_text(encoding="utf-8", errors="ignore") if top_path.exists() else ""
    raw = client.complete(
        render("testbench", top_module=top_module, top_module_code=top_code,
               module_descriptions=module_descriptions),
        role="verifier",
    )
    code = extract_first_code(raw)
    if "TEST PASSED" not in code and "TEST FAILED" not in code:
        Log.vwarn("Generated testbench has no PASS/FAIL marker; it may be INCONCLUSIVE.")
    return code


def compile_and_fix_testbench(client: LLMClient, tb_path: Path, design_files: List[Path],
                              config: Config, max_rounds: int, tui=None) -> Tuple[bool, str]:
    if not design_files:
        return False, "no design files found for testbench compile"
    err = ""
    for round_idx in range(1, max_rounds + 1):
        Log.step(6, f"Testbench compile attempt {round_idx}/{max_rounds}")
        ok, err = sim.run_iverilog(design_files + [tb_path], out_name="out_tb",
                                   flags=config.sim.iverilog_flags)
        if ok:
            Log.vsuccess("Testbench compiled successfully")
            return True, ""
        Log.vwarn("Testbench compile failed; requesting a patch to the TB.")
        file_map = {tb_path.name: tb_path.read_text(encoding="utf-8", errors="ignore")}
        for df in design_files:
            file_map[df.name] = df.read_text(encoding="utf-8", errors="ignore")
        patches = propose_fixes(client, err, file_map, config, focus_file=tb_path.name)
        new_tb = patches.get(tb_path.name)
        if not new_tb:
            Log.verror("No actionable TB patch returned.")
            return False, err
        tb_path.write_text(new_tb, encoding="utf-8")
    return False, err


def improve_testbench_from_output(client: LLMClient, tb_path: Path, tb_output: str,
                                  module_descriptions: str, design_files: List[Path],
                                  config: Config) -> bool:
    """Ask the verifier to revise ONLY the testbench (e.g. add a missing marker)."""
    tb_code = tb_path.read_text(encoding="utf-8", errors="ignore")
    context = "\n=== DESIGN UNDER TEST ===\n"
    for df in design_files:
        if df.exists():
            context += f"\n--- {df.name} ---\n```verilog\n{df.read_text(errors='ignore')}\n```\n"
    raw = client.complete(
        render("testbench_improve", tb_name=tb_path.name, tb_code=tb_code,
               design_context=context, module_descriptions=module_descriptions,
               tb_output=tb_output),
        role="verifier",
    )
    data = ensure_json(raw)
    if isinstance(data, dict):
        for p in data.get("patches", []):
            if p.get("file") == tb_path.name and isinstance(p.get("code"), str):
                tb_path.write_text(p["code"], encoding="utf-8")
                Log.vinfo("Applied TB improvement patch.")
                return True
    return False


def run_self_checking_testbench(client: LLMClient, lib_dir: Path, top_module: str,
                                module_descriptions: str, design_modules: List[str],
                                config: Config, tui=None) -> Tuple[Verdict, str, str]:
    """Generate + iterate the self-checking TB. Returns (verdict, reason, output)."""
    design_files = [lib_dir / f"{m}.v" for m in design_modules if (lib_dir / f"{m}.v").exists()]
    tb_code = generate_testbench(client, top_module, module_descriptions, lib_dir)
    tb_path = lib_dir / f"tb_{top_module}.v"
    tb_path.write_text(tb_code, encoding="utf-8")
    Log.vsuccess(f"Wrote testbench: {tb_path}")

    max_rounds = max(1, config.rounds.max_tb_rounds)
    last_output = ""
    for round_idx in range(1, max_rounds + 1):
        ok, err = compile_and_fix_testbench(client, tb_path, design_files, config,
                                            max_rounds=min(5, max_rounds), tui=tui)
        if not ok:
            return Verdict.INCONCLUSIVE, f"testbench did not compile: {err[:200]}", err

        ok, out = sim.run_vvp("out_tb")
        last_output = out
        if not ok:
            return Verdict.INCONCLUSIVE, "testbench failed to run", out

        verdict, reason = analyze_testbench_output(out)
        if verdict is Verdict.PASS:
            Log.vsuccess("Self-checking testbench PASSED")
            return verdict, reason, out
        if verdict is Verdict.FAIL:
            Log.vwarn(f"Testbench reports FAIL: {reason}")
            return verdict, reason, out

        # INCONCLUSIVE (no marker): try to improve the TB, then retry
        if round_idx < max_rounds and config.rounds.max_tb_rounds > 0:
            Log.vinfo("No marker; asking verifier to improve the testbench...")
            if not improve_testbench_from_output(client, tb_path, out, module_descriptions,
                                                 design_files, config):
                break
    return Verdict.INCONCLUSIVE, "no conclusive testbench result", last_output
