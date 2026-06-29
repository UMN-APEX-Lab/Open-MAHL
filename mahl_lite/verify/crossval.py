"""Cross-validate a DUT against an independent Python golden model.

The point: the pass/fail decision must NOT come from LLM-written Verilog (which can
hallucinate that 1+1==3 passes). So we:

    1. extract the DUT's real ports (name/direction/width),
    2. ask the LLM for a *Python* reference model of the spec,
    3. generate shared test vectors (directed edge cases + seeded random),
    4. run the vectors through the Python model      -> golden outputs,
    5. drive the DUT with a TEMPLATED (non-LLM) harness that only prints raw
       outputs (it makes no pass/fail judgement),
    6. compare in deterministic Python.

The only LLM artifact in the loop is the Python ``ref()``, and it is itself
cross-checked against the simulated hardware — if both are wrong they will disagree
with each other and we report INCONCLUSIVE rather than a false PASS.

Scope: v1 handles **combinational** modules (outputs depend only on current inputs).
Sequential/clocked designs are detected and routed back to the self-checking
testbench path. Cycle-accurate cross-validation is future work (see REFACTOR_PLAN).

NOTE: this executes LLM-generated Python in a subprocess with a timeout. It is
isolated and time-bounded but not a security sandbox — review models or disable
``verify.crossvalidation`` if you do not trust the model output.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Config
from ..log import Log
from ..parsing import extract_code_blocks
from . import interface as I
from . import sim
from .analyze import Verdict

_CLOCKISH = ("clk", "clock", "rst", "reset", "rstn", "resetn", "en", "enable")
_REF_TIMEOUT_S = 20


@dataclass
class Mismatch:
    vector: Dict[str, int]
    output: str
    expected: int
    got: int


@dataclass
class CrossValResult:
    verdict: Verdict
    reason: str
    num_vectors: int = 0
    mismatches: List[Mismatch] = field(default_factory=list)


# ----- public entry -----
def cross_validate(
    lib_dir: Path,
    top_module: str,
    ref_model_src: str,
    design_modules: List[str],
    config: Config,
) -> CrossValResult:
    ports = I.extract_ports(lib_dir, top_module)
    if not ports:
        return CrossValResult(Verdict.INCONCLUSIVE, f"could not read ports of {top_module}")

    inputs = {n: p for n, p in ports.items() if p.direction == "input"}
    outputs = {n: p for n, p in ports.items() if p.direction == "output"}
    if not inputs or not outputs:
        return CrossValResult(Verdict.INCONCLUSIVE, "module has no usable input/output ports")

    if _looks_sequential(ports):
        return CrossValResult(
            Verdict.INCONCLUSIVE,
            "sequential/clocked design — cross-validation is combinational-only in v1",
        )
    unknown = [n for n, p in ports.items() if not p.width_known]
    if unknown:
        return CrossValResult(
            Verdict.INCONCLUSIVE, f"parameterized/unknown widths: {unknown}"
        )

    ref_code = _extract_python(ref_model_src)
    if not ref_code:
        return CrossValResult(Verdict.INCONCLUSIVE, "no Python reference model produced")

    vectors = gen_vectors(inputs, config.verify.num_random_vectors, config.verify.seed)
    output_order = list(outputs.keys())

    try:
        golden = run_ref_model(ref_code, vectors, output_order)
    except Exception as e:
        return CrossValResult(Verdict.INCONCLUSIVE, f"reference model failed to run: {e}")

    design_files = [lib_dir / f"{m}.v" for m in design_modules if (lib_dir / f"{m}.v").exists()]
    dut = _simulate_dut(lib_dir, top_module, ports, vectors, output_order, design_files, config)
    if dut is None:
        return CrossValResult(Verdict.INCONCLUSIVE, "DUT harness failed to compile/run")

    masks = {n: (1 << outputs[n].width) - 1 for n in output_order}
    mismatches: List[Mismatch] = []
    for vec, exp_row, got_row in zip(vectors, golden, dut):
        for name in output_order:
            exp = exp_row[name] & masks[name]
            got = got_row[name] & masks[name]
            if exp != got:
                mismatches.append(Mismatch(vec, name, exp, got))

    if mismatches:
        return CrossValResult(Verdict.FAIL, _fail_reason(mismatches), len(vectors), mismatches)
    return CrossValResult(Verdict.PASS, "DUT matches Python golden model on all vectors",
                          len(vectors))


# ----- vectors -----
def gen_vectors(inputs: Dict[str, I.Port], n_random: int, seed: int) -> List[Dict[str, int]]:
    names = list(inputs.keys())
    maxv = {n: (1 << inputs[n].width) - 1 for n in names}
    directed: List[Dict[str, int]] = [
        {n: 0 for n in names},                 # all zero
        {n: maxv[n] for n in names},           # all max
        {n: 1 for n in names},                 # all one
        {n: maxv[n] // 2 for n in names},      # mid
    ]
    rng = random.Random(seed)
    rand = [{n: rng.randint(0, maxv[n]) for n in names} for _ in range(max(0, n_random))]
    # de-dup while preserving order
    seen, out = set(), []
    for v in directed + rand:
        key = tuple(v[n] for n in names)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


# ----- python golden model (subprocess + timeout) -----
def run_ref_model(ref_code: str, vectors: List[Dict[str, int]],
                  output_order: List[str]) -> List[Dict[str, int]]:
    driver = (
        ref_code
        + "\n\nif __name__ == '__main__':\n"
          "    import json, sys\n"
          "    data = json.load(open(sys.argv[1]))\n"
          "    rows = []\n"
          "    for inp in data['vectors']:\n"
          "        out = ref(dict(inp))\n"
          "        rows.append({k: int(out[k]) for k in data['outputs']})\n"
          "    json.dump(rows, open(sys.argv[2], 'w'))\n"
    )
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        (dpath / "ref.py").write_text(driver, encoding="utf-8")
        (dpath / "in.json").write_text(
            json.dumps({"vectors": vectors, "outputs": output_order}), encoding="utf-8"
        )
        res = subprocess.run(
            [sys.executable, str(dpath / "ref.py"), str(dpath / "in.json"), str(dpath / "out.json")],
            capture_output=True, text=True, timeout=_REF_TIMEOUT_S,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip()[:400] or "non-zero exit")
        return json.loads((dpath / "out.json").read_text(encoding="utf-8"))


# ----- DUT simulation via a templated harness -----
def _simulate_dut(lib_dir: Path, top_module: str, ports: Dict[str, I.Port],
                  vectors: List[Dict[str, int]], output_order: List[str],
                  design_files: List[Path], config: Config) -> Optional[List[Dict[str, int]]]:
    harness = build_harness(top_module, ports, vectors, output_order)
    with tempfile.TemporaryDirectory() as d:
        hpath = Path(d) / f"cv_{top_module}.v"
        hpath.write_text(harness, encoding="utf-8")
        exe = str(Path(d) / "cv_out")
        ok, out = sim.run_iverilog(design_files + [hpath], out_name=exe, flags=config.sim.iverilog_flags)
        if not ok:
            Log.vwarn(f"cross-val harness failed to compile:\n{out[:300]}")
            return None
        ok, out = sim.run_vvp(exe)
        if not ok:
            Log.vwarn(f"cross-val harness failed to run:\n{out[:300]}")
            return None
    return _parse_dut_output(out, output_order)


def build_harness(top_module: str, ports: Dict[str, I.Port],
                  vectors: List[Dict[str, int]], output_order: List[str]) -> str:
    inputs = {n: p for n, p in ports.items() if p.direction == "input"}
    outputs = {n: p for n, p in ports.items() if p.direction == "output"}

    def decl(p: I.Port) -> str:
        return f"[{p.width - 1}:0] " if p.width and p.width > 1 else ""

    lines = ["`timescale 1ns/1ps", f"module cv_{top_module};"]
    for n, p in inputs.items():
        lines.append(f"  reg  {decl(p)}{n};")
    for n, p in outputs.items():
        lines.append(f"  wire {decl(p)}{n};")

    conns = ", ".join(f".{n}({n})" for n in ports)
    lines.append(f"  {top_module} dut ({conns});")

    fmt = " ".join("%0d" for _ in output_order)
    args = ", ".join(output_order)
    lines.append("  initial begin")
    for vec in vectors:
        for n, p in inputs.items():
            lines.append(f"    {n} = {p.width}'d{vec[n]};")
        lines.append("    #1;")
        lines.append(f'    $display("CV {fmt}", {args});')
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _parse_dut_output(out: str, output_order: List[str]) -> List[Dict[str, int]]:
    rows: List[Dict[str, int]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("CV "):
            continue
        toks = line[3:].split()
        if len(toks) != len(output_order):
            continue
        try:
            rows.append({name: int(tok) for name, tok in zip(output_order, toks)})
        except ValueError:
            continue
    return rows


# ----- misc -----
def _looks_sequential(ports: Dict[str, I.Port]) -> bool:
    return any(n.lower() in _CLOCKISH for n in ports)


def _extract_python(text: str) -> str:
    for lang, code in extract_code_blocks(text):
        if lang.lower() in ("python", "py", ""):
            if "def ref" in code:
                return code
    return text if "def ref" in text else ""


def _fail_reason(mismatches: List[Mismatch], show: int = 3) -> str:
    head = mismatches[:show]
    parts = [
        f"{m.output}: expected {m.expected}, got {m.got} for {m.vector}" for m in head
    ]
    more = f" (+{len(mismatches) - show} more)" if len(mismatches) > show else ""
    return f"{len(mismatches)} mismatch(es) vs golden model; " + "; ".join(parts) + more
