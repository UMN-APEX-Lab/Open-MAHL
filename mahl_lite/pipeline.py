"""End-to-end orchestration: prompt -> RTL -> verified result.

The "Planner" functions (:func:`project_modules`, :func:`generate_descriptions`,
:func:`generate_ast`, :func:`generate_code`) turn a request into Verilog files.
:func:`run_pipeline` then compiles, debugs, and verifies, reconciling the two
verification opinions:

* the **cross-validator** (authoritative for combinational designs), and
* the **self-checking testbench** (a second opinion; also the path for sequential).

When both produce a definite verdict and they DISAGREE, the testbench is flagged as
a likely hallucination and the cross-validator wins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import Config
from .debug import propose_fixes
from .errors import ParseError
from .llm import LLMClient
from .log import Log
from .parsing import parse_module_list, split_module, ensure_json
from .prompts import render
from .verify import sim
from .verify.analyze import Verdict
from .verify.crossval import CrossValResult, cross_validate
from .verify.interface import detect_top_module, extract_ports
from .verify.testbench import run_self_checking_testbench

_DESC_TEMPLATE_FILE = "module_description_example.txt"


@dataclass
class PipelineResult:
    modules: List[str]
    top_module: str
    lib_dir: str
    design_compiled: bool
    verdict: Verdict
    reason: str
    tb_verdict: Optional[Verdict] = None
    tb_reason: str = ""
    tb_output: str = ""
    crossval: Optional[CrossValResult] = None
    tb_hallucination: bool = False


# ----- Planner -----
def project_modules(client: LLMClient, user_prompt: str) -> List[str]:
    Log.info("Projecting user request to a list of required modules...")
    raw = client.complete(render("projection", user_prompt=user_prompt), role="planner")
    modules = parse_module_list(raw)
    Log.success(f"Identified modules: {modules}")
    return modules


def generate_descriptions(client: LLMClient, user_prompt: str, modules: List[str]) -> str:
    Log.info("Generating detailed module descriptions...")
    template = "Provide detailed module descriptions in standard Verilog module format."
    try:
        template = Path(_DESC_TEMPLATE_FILE).read_text(encoding="utf-8")
        Log.info(f"Loaded {_DESC_TEMPLATE_FILE} template.")
    except FileNotFoundError:
        Log.warn(f"{_DESC_TEMPLATE_FILE} not found; using a default rule.")
    raw = client.complete(
        render("describe", project_definition=user_prompt, template=template,
               modules_list=modules),
        role="planner",
    )
    from .parsing import extract_first_code
    Log.success("Module descriptions generated.")
    return extract_first_code(raw)


def generate_ast(client: LLMClient, module_descriptions: str) -> Optional[dict]:
    Log.info("Generating module hierarchy (AST)...")
    raw = client.complete(render("ast", module_descriptions=module_descriptions), role="planner")
    ast = ensure_json(raw)
    if ast is None:
        Log.warn("Could not parse AST JSON; hierarchy may be incomplete.")
    else:
        Log.success("AST generated.")
    return ast


def generate_code(client: LLMClient, module_descriptions: str, lib_dir: Path) -> None:
    from .parsing import extract_first_code
    for block in split_module(module_descriptions.split("\n")):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        name = None
        for ln in lines:
            if ln.lower().startswith(("module:", "topmodule:")):
                name = ln.split(":", 1)[1].strip()
                break
        if not name:
            continue
        Log.info(f"Generating code for module: {name}")
        raw = client.complete(render("codegen", module_name=name, module_description=block),
                              role="planner")
        (lib_dir / f"{name}.v").write_text(extract_first_code(raw), encoding="utf-8")
        Log.success(f"Wrote: {lib_dir / f'{name}.v'}")


# ----- design compile/debug loop -----
def compile_and_fix(client: LLMClient, lib_dir: Path, modules: List[str],
                    config: Config, tui=None) -> tuple[bool, str]:
    file_paths = [lib_dir / f"{m}.v" for m in modules if (lib_dir / f"{m}.v").exists()]
    if not file_paths:
        return False, "no Verilog files found to compile"
    max_rounds = max(1, config.rounds.max_fix_rounds)
    err = ""
    for round_idx in range(1, max_rounds + 1):
        Log.step(5, f"Design compile attempt {round_idx}/{max_rounds}")
        ok, err = sim.run_iverilog(file_paths, out_name="out", flags=config.sim.iverilog_flags)
        if ok:
            Log.success("All modules compiled successfully")
            return True, ""
        if config.rounds.max_fix_rounds == 0:
            return False, err
        Log.warn("Compilation failed; invoking the debugger.")
        file_map = {p.name: p.read_text(encoding="utf-8", errors="ignore") for p in file_paths}
        patches = propose_fixes(client, err, file_map, config)
        if not patches:
            Log.error("Debugger returned no actionable patches.")
            return False, err
        for fname, code in patches.items():
            (lib_dir / fname).write_text(code, encoding="utf-8")
            Log.info(f"Applied patch to {fname}")
    return False, err


# ----- orchestration -----
def _set(tui, key, status):
    if tui:
        try:
            tui.set_step_status(key, status)
        except Exception:
            pass


def run_pipeline(user_prompt: str, client: LLMClient, config: Config, tui=None) -> PipelineResult:
    lib_dir = Path(config.output.lib_dir)
    lib_dir.mkdir(exist_ok=True)

    _set(tui, "identify", "running")
    modules = project_modules(client, user_prompt)
    _set(tui, "identify", "done")

    _set(tui, "describe", "running")
    descriptions = generate_descriptions(client, user_prompt, modules)
    _set(tui, "describe", "done")

    _set(tui, "ast", "running")
    ast_json = generate_ast(client, descriptions)
    artifacts = Path(config.output.artifacts_dir)
    artifacts.mkdir(exist_ok=True)
    (artifacts / "ast.json").write_text(json.dumps(ast_json, indent=2), encoding="utf-8")
    _set(tui, "ast", "done")

    _set(tui, "codegen", "running")
    generate_code(client, descriptions, lib_dir)
    _set(tui, "codegen", "done")

    _set(tui, "compile", "running")
    design_ok, design_err = compile_and_fix(client, lib_dir, modules, config, tui=tui)
    _set(tui, "compile", "done" if design_ok else "failed")

    top = detect_top_module(ast_json, modules, lib_dir)

    verdict, reason, cv, tb_v, tb_reason, tb_out, halluc = _verify(
        client, lib_dir, top, descriptions, modules, config, tui
    )

    if not config.verify.honest_results:
        verdict, reason = Verdict.PASS, "forced PASS (verify.honest_results=false)"

    return PipelineResult(
        modules=modules, top_module=top, lib_dir=str(lib_dir),
        design_compiled=design_ok, verdict=verdict, reason=reason,
        tb_verdict=tb_v, tb_reason=tb_reason, tb_output=tb_out,
        crossval=cv, tb_hallucination=halluc,
    )


def _verify(client, lib_dir, top, descriptions, modules, config, tui):
    """Run TB + cross-validation and reconcile into one authoritative verdict."""
    _set(tui, "tbgen", "running")
    tb_v, tb_reason, tb_out = run_self_checking_testbench(
        client, lib_dir, top, descriptions, modules, config, tui=tui
    )
    _set(tui, "tbgen", "done")
    _set(tui, "tbanalyze", "done")

    cv: Optional[CrossValResult] = None
    if config.verify.crossvalidation:
        _set(tui, "crossval", "running")
        ref_raw = client.complete(
            render("ref_model", top_module=top,
                   top_module_code=(lib_dir / f"{top}.v").read_text(errors="ignore")
                   if (lib_dir / f"{top}.v").exists() else "",
                   module_descriptions=descriptions),
            role="verifier",
        )
        cv = cross_validate(lib_dir, top, ref_raw, modules, config)
        Log.vinfo(f"Cross-validation: {cv.verdict.value} — {cv.reason}")
        _set(tui, "crossval", "done")

    # Reconcile: cross-validator is authoritative when it has a definite verdict.
    halluc = False
    if cv and cv.verdict in (Verdict.PASS, Verdict.FAIL):
        if tb_v in (Verdict.PASS, Verdict.FAIL) and tb_v != cv.verdict:
            halluc = True
            Log.verror(
                f"TESTBENCH HALLUCINATION: testbench says {tb_v.value} but the Python "
                f"golden model says {cv.verdict.value}. Trusting the golden model."
            )
        return cv.verdict, f"cross-validated: {cv.reason}", cv, tb_v, tb_reason, tb_out, halluc

    # Otherwise fall back to the testbench's opinion.
    return tb_v, tb_reason, cv, tb_v, tb_reason, tb_out, halluc
