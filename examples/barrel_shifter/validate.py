#!/usr/bin/env python3
"""Validate a generated barrel_shifter against the GOLDEN (trusted) testbench.

Two independent oracles let us answer two different questions:

  [1] Is the generated RTL correct?        -> run the GOLDEN testbench on it.
  [2] Was the generated testbench correct?  -> run it on the GOLDEN (correct) RTL,
                                               and compare its verdict on the DUT
                                               with the golden verdict.

The generated design may use any module/port names; we detect its interface and
auto-generate a thin wrapper so the golden artifacts connect regardless. All
simulations run under a wall-clock timeout, so a runaway (never-$finish)
testbench is reported as a hang instead of wedging the run.

Usage:
  python validate.py <candidate_dir> [--generated-tb <tb.v>]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from mahl_lite.verify.interface import parse_modules_from_dir  # noqa: E402

GOLDEN_RTL = HERE / "golden" / "barrel_shifter.v"
GOLDEN_TB = HERE / "golden" / "tb_barrel_shifter_golden.v"
SIM_TIMEOUT_S = 30

# golden interface: role -> (width, is_input)
GOLDEN_IF = {"data_in": (8, True), "shift_amount": (3, True),
             "direction": (1, True), "data_out": (8, False)}


def run_sim(files, workdir: Path):
    """Compile + run; return (verdict, detail). verdict in PASS/FAIL/INCONCLUSIVE."""
    exe = workdir / "sim.out"
    comp = subprocess.run(["iverilog", "-g2012", "-o", str(exe), *map(str, files)],
                          capture_output=True, text=True)
    if comp.returncode != 0:
        return "INCONCLUSIVE", "compile error: " + (comp.stderr or comp.stdout).strip().splitlines()[0]
    try:
        run = subprocess.run(["vvp", str(exe)], capture_output=True, text=True, timeout=SIM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "INCONCLUSIVE", f"hang: testbench did not finish within {SIM_TIMEOUT_S}s"
    out = run.stdout or ""
    if "TEST FAILED" in out:
        line = next((l for l in out.splitlines() if "TEST FAILED" in l), "TEST FAILED")
        return "FAIL", line.strip()
    if "TEST PASSED" in out:
        return "PASS", "TEST PASSED"
    return "INCONCLUSIVE", "no PASS/FAIL marker in output"


def infer_mapping(ports):
    """Map golden role -> candidate port name, by (direction, width). None if ambiguous."""
    mapping = {}
    for role, (width, is_in) in GOLDEN_IF.items():
        cands = [n for n, p in ports.items()
                 if (p.direction == "input") == is_in and p.width == width]
        if len(cands) != 1:
            return None
        mapping[role] = cands[0]
    return mapping


def port_decls(ports):
    out = []
    for n, p in ports.items():
        rng = f"[{p.width - 1}:0] " if p.width and p.width > 1 else ""
        out.append(f"    {p.direction} {rng}{n}")
    return ",\n".join(out)


def alias_as_golden(top, mapping):
    """A module named `barrel_shifter` that forwards to the candidate top."""
    conns = ", ".join(f".{m}({g})" for g, m in mapping.items())
    return ("module barrel_shifter (input [7:0] data_in, input [2:0] shift_amount,\n"
            "    input direction, output [7:0] data_out);\n"
            f"  {top} u ({conns});\nendmodule\n")


def golden_as_candidate(top, ports, mapping):
    """A module named like the candidate top, backed by the golden RTL."""
    conns = ", ".join(f".{g}({m})" for g, m in mapping.items())
    return (f"module {top} (\n{port_decls(ports)}\n);\n"
            f"  barrel_shifter g ({conns});\nendmodule\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate_dir")
    ap.add_argument("--generated-tb", help="generated testbench to sanity-check")
    args = ap.parse_args()

    cand = Path(args.candidate_dir)
    dut_files = [p for p in cand.glob("*.v")
                 if not p.name.startswith("tb_") and not p.name.endswith("_tb.v")]
    if not dut_files:
        print(f"no DUT .v files in {cand}")
        return 1
    # Pick the DUT as the module whose interface matches the barrel-shifter shape
    # (8/3/1-bit inputs + 8-bit output), so dead/unused submodules don't confuse us.
    top, ports, mapping = None, None, None
    for name, info in parse_modules_from_dir(cand).items():
        ln = name.lower()
        if ln.startswith("tb_") or ln.endswith("_tb"):
            continue
        m = infer_mapping(info["ports"])
        if m is not None:
            top, ports, mapping = name, info["ports"], m
            break
    if mapping is None:
        print("INTERFACE NONCONFORMANCE: no module matches the required "
              "8/3/1-bit in + 8-bit out shape; cannot validate.")
        return 1
    print(f"candidate top: {top}  ports: {sorted(ports)}")
    if mapping != {k: k for k in GOLDEN_IF}:
        print(f"port mapping (golden -> candidate): {mapping}")

    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        need_wrap = (top != "barrel_shifter")
        extra = []
        if need_wrap:
            w = work / "wrap_as_golden.v"
            w.write_text(alias_as_golden(top, mapping))
            extra = [w]

        # [1] Is the generated RTL correct?  (golden testbench is the oracle)
        v1, d1 = run_sim([GOLDEN_TB, *dut_files, *extra], work)
        print(f"\n[1] generated RTL  vs  GOLDEN testbench : {v1}  ({d1})")

        if not args.generated_tb:
            print("\n(omit --generated-tb to skip the generated-testbench audit)")
            return 0 if v1 == "PASS" else (2 if v1 == "FAIL" else 3)

        gtb = Path(args.generated_tb)
        # [2] What did the generated testbench conclude about the DUT?
        v2, d2 = run_sim([gtb, *dut_files], work)
        print(f"[2] generated TB   on the generated RTL : {v2}  ({d2})")

        # [3] Does the generated testbench accept a KNOWN-CORRECT design?
        if top == "barrel_shifter":
            golden_backed = [GOLDEN_RTL]
        else:
            gb = work / "golden_backed.v"
            gb.write_text(golden_as_candidate(top, ports, mapping))
            golden_backed = [gb, GOLDEN_RTL]
        v3, d3 = run_sim([gtb, *golden_backed], work)
        print(f"[3] generated TB   on the GOLDEN RTL    : {v3}  ({d3})")

        print("\nConclusion:")
        print(f"  - Generated RTL is {'CORRECT' if v1=='PASS' else 'INCORRECT' if v1=='FAIL' else 'UNVERIFIED'} "
              "(golden oracle).")
        if v3 == "FAIL":
            print("  - Generated testbench is BUGGY: it rejects a known-correct design.")
        elif v3 == "PASS" and v1 in ("PASS", "FAIL") and v2 != v1:
            kind = "false PASS (hallucination)" if v2 == "PASS" else "false FAIL"
            print(f"  - Generated testbench DISAGREES with the golden verdict: {kind}.")
        elif v3 == "PASS" and v2 == v1:
            print("  - Generated testbench agrees with the golden oracle here.")
        else:
            print("  - Generated testbench audit inconclusive.")
    return 0 if v1 == "PASS" else (2 if v1 == "FAIL" else 3)


if __name__ == "__main__":
    sys.exit(main())
