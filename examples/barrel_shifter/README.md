# Example: 8-bit barrel shifter (with a golden oracle)

A worked example showing how to validate MAHL-Lite output against a **trusted,
human-written golden testbench** instead of trusting the LLM's own self-check.

It answers two separate questions:

1. **Is the generated RTL correct?** — run the golden testbench on it.
2. **Was the generated testbench correct?** — run it on the golden (correct) RTL and
   compare its verdict with the golden one.

This catches a failure mode that cross-validation alone can miss: when the generated
RTL *and* the generated testbench share the same wrong interpretation of an ambiguous
spec, they agree with each other (self-check says PASS) but both disagree with the
golden oracle.

## Files

| file | role |
|---|---|
| `prompt.txt` | the **unambiguous** spec (pinned interface + polarity) to feed the tool |
| `golden/barrel_shifter.v` | trusted reference implementation |
| `golden/tb_barrel_shifter_golden.v` | trusted testbench — exhaustive (all 4096 inputs), the oracle |
| `validate.py` | runs the three checks; auto-wraps any port naming; sim runs under a timeout |

## Usage

From the repo root:

```bash
# 1. Generate RTL from the unambiguous spec
python -m mahl_lite --no-tui --lib examples/barrel_shifter/out "$(cat examples/barrel_shifter/prompt.txt)"

# 2. Is the generated RTL correct? (golden testbench is the oracle)
python examples/barrel_shifter/validate.py examples/barrel_shifter/out

# 3. Was the generated testbench correct too? (point at the generated tb_*.v)
python examples/barrel_shifter/validate.py examples/barrel_shifter/out \
    --generated-tb examples/barrel_shifter/out/tb_barrel_shifter.v
```

`validate.py` detects the DUT by its interface (so unused submodules don't confuse
it) and auto-generates a thin wrapper, so it works even if the tool named the top
`barrel_shifter_core` or used different port names.

## Reading the output

```
[1] generated RTL  vs  GOLDEN testbench : PASS | FAIL | INCONCLUSIVE
[2] generated TB   on the generated RTL : what the tool's own testbench concluded
[3] generated TB   on the GOLDEN RTL    : does the generated TB accept a correct design?
```

- `[1] FAIL` → the design is genuinely wrong (per the spec).
- `[3] FAIL` → the **generated testbench is buggy** — it rejects a known-correct design.
- `[2]` disagreeing with `[1]` → the generated testbench gave a false PASS/FAIL on the DUT.

Exit code: `0` PASS, `2` FAIL, `3` INCONCLUSIVE (mirrors the main tool).

## Make your own example

Copy this folder and provide, for your design:

1. `prompt.txt` — pin the **module name, exact port names/widths, and every
   convention** (here: `direction 0 = left, 1 = right`, logical/zero-fill). Ambiguity
   is what lets the RTL and its testbench "agree" on the wrong thing.
2. `golden/<dut>.v` — a known-correct implementation.
3. `golden/tb_<dut>_golden.v` — a trusted testbench that prints exactly
   `TEST PASSED` / `TEST FAILED: ...` and calls `$finish`.
4. adjust the interface shape in `validate.py` (`GOLDEN_IF`) to your ports.
