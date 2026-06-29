# MAHL-Lite — Open-Source Refactor Plan

> A lightweight, single-pipeline distillation of **MAHL** (*Multi-Agent LLM-Guided
> Hierarchical chiplet design with adaptive debugging*, arXiv:2508.14053).
> This plan turns the current 1,929-line `gen_tui1.py` monolith into a readable,
> configurable Python package suitable for open source.

---

## 0. Naming

- Drop "downgrade." Use **MAHL-Lite** (tagline: *"a single-file-friendly,
  open-source distillation of the MAHL RTL pipeline"*).
- This repo keeps MAHL's **hierarchical description + hierarchical codegen +
  flow-based validation + adaptive debugging**. It intentionally omits MAHL's
  RAG, multi-granularity design-space exploration, and PPA optimization. Say this
  plainly in the README so users know what "Lite" means.

---

## 1. Target package layout

```
mahl_lite/
  __init__.py
  __main__.py          # CLI entry (argparse) + interactive prompts
  config.py            # dataclass config + loader (env > config.yaml > defaults)
  llm.py               # LLM client: openai/ollama, per-role models, retries, NO eval()
  prompts.py           # loads templates from prompts/, .format() helpers
  pipeline.py          # describe -> ast -> codegen orchestration (the "Planner")
  debug.py             # NEW: Reasoner -> Coder two-phase adaptive debug
  verify/
    __init__.py
    sim.py             # iverilog/vvp wrappers (run_iverilog, run_vvp)
    interface.py       # port extraction: pyverilog (preferred) + regex fallback
    testbench.py       # LLM self-checking TB generation + compile/fix loop
    crossval.py        # NEW: Python golden model + vector co-sim cross-check
    analyze.py         # honest PASS/FAIL/INCONCLUSIVE verdict logic
  tui/
    __init__.py
    app.py             # TUI class, layout, render loop, file monitor
    theme.py           # STYLE_PRESETS + wave/glow animation helpers
  log.py               # Log class (file + sink + console)
prompts/               # editable prompt templates (the main "user knobs")
  projection.txt
  describe.txt
  ast.txt
  codegen.txt
  debug_reason.txt     # NEW: the "thinker"
  debug_code.txt       # NEW: the "coder"
  testbench.txt
  ref_model.txt        # NEW: Python golden-model generator
config.yaml            # user-tunable knobs (see §2)
config.example.yaml
.env.example           # OPENAI_API_KEY=...
requirements.txt
LICENSE
CITATION.cff           # cite the MAHL paper
README.md              # rewritten
module_description_example.txt   # keep (it's a template already)
examples/              # sample prompts + expected module sets
```

Entry point: `python -m mahl_lite "..."` (hard rename; `gen_tui1.py` is removed —
see §11). The README is updated to the new command.

---

## 2. Config system (`config.py` + `config.yaml`)

Single source of truth, resolved in order: **CLI flag > env var > `config.yaml` >
dataclass default**. Everything currently hardcoded becomes a knob:

```yaml
# config.yaml
models:
  default: openai            # "openai" or an ollama model name
  planner:  null             # null => inherit default
  reasoner: null             # e.g. a stronger model for diagnosis
  coder:    null             # e.g. a cheaper/faster model for rewrites
  verifier: null
sampling:
  openai_model_id: gpt-4o    # was hardcoded
  temperature: 0.6
  top_p: 1.0
  max_tokens: 4096
rounds:
  max_fix_rounds: 10         # design compile/fix
  max_tb_rounds: 10          # testbench iterate
debug:
  two_phase: true            # Reasoner -> Coder (false = old single-call)
verify:
  crossvalidation: true      # NEW: enable Python golden-model cross-check
  num_random_vectors: 64
  seed: 0
  honest_results: true       # NEW: report real PASS/FAIL (no forced PASSED)
sim:
  iverilog_flags: ["-g2012"]
memory:
  capacity_chars: 400000
ui:
  style: futuristic          # futuristic | claude | openai | genmini
output:
  lib_dir: module_lib
  artifacts_dir: artifacts
```

---

## 3. LLM client (`llm.py`) — also fixes security

- One `LLMClient` with `.complete(prompt, role=...)` choosing the per-role model.
- **Remove `eval()`** in module projection (current line 334). Replace with
  `ast.literal_eval` + a JSON fallback. (Arbitrary-code-exec risk in an
  open-source tool that runs LLM output.)
- Add simple retry/backoff and keep the graceful "Error: ..." string contract the
  callers already check, OR move to exceptions consistently (pick one — see §11).
- Keep ollama optional-import behavior.

---

## 4. Adaptive debugging: Reasoner → Coder (`debug.py`)

Replaces the single `propose_fixes_with_llm` call with two phases, used by BOTH
the design-compile loop and the testbench-compile loop:

```
compile/sim error
  └─ Reasoner  (prompts/debug_reason.txt)
        in:  error text + all file contents + (optional) failing vectors
        out: structured diagnosis + ROOT CAUSE + step-by-step fix plan  (NO code)
  └─ Coder     (prompts/debug_code.txt)
        in:  the fix plan + all file contents + filenames
        out: {"patches":[{"file","code"}]}  (full corrected files)
  └─ recompile / re-run
```

- The Reasoner's plan is logged to the TUI ("thinking"), so the user *sees* the
  reasoning before the patch — this is the behavior you described.
- `two_phase: false` falls back to the current one-shot path for speed/cost.
- Keep "full file rewrite" patch contract (simplest, already works). Diffs are a
  later optimization.

---

## 5. Honest verification (`verify/analyze.py`)

Current code returns `True` for pass AND fail, and the pipeline forces
`"PASSED"`. Replace with a real verdict:

```
TEST PASSED   marker present, no FAILED   -> PASS  ✅
TEST FAILED   marker present              -> FAIL  ❌ (carry the reason)
no marker / sim error / X-Z everywhere    -> INCONCLUSIVE ⚠
```

- Pipeline returns the true status; TUI summary shows PASS/FAIL/INCONCLUSIVE.
- Exit codes: 0 pass, 2 fail, 3 inconclusive (good for CI).
- Optional `--lenient` flag preserves the old "never red" demo behavior.

---

## 6. NEW — Cross-validation against a Python golden model (`verify/crossval.py`)

**Goal:** defeat testbench hallucination (the "asserts 1+1==3" problem) by moving
the pass/fail judgment OUT of the LLM-written Verilog and INTO independent,
deterministic Python.

**Flow:**
```
1. interface.py  -> extract DUT ports (name, width, dir) from the .v
                    - preferred: pyverilog parse (robust)
                    - fallback : existing regex parser (no extra dep)
2. ref_model.txt -> LLM generates a PURE PYTHON reference function:
                       def ref(inputs: dict) -> dict   # implements the spec
3. crossval.py   -> generate shared test vectors:
                       directed (edge cases) + N random (seeded)
4. golden        -> run vectors through the Python ref()  => expected outputs
5. dut sim       -> generate a *templated* (non-LLM) Verilog harness that:
                       $readmemh / $fscanf the vectors, drive DUT,
                       $fwrite raw outputs to results.txt   (NO self-judgment)
                    compile+run with iverilog/vvp
6. compare       -> Python diffs DUT results vs golden, element by element
                    => authoritative PASS/FAIL  (LLM cannot fake this)
7. cross-check   -> also run the LLM self-checking TB; if its verdict disagrees
                    with the Python cross-check, flag "TESTBENCH HALLUCINATION"
```

**Two independent sources of truth** (Python golden vs DUT sim) catch DUT bugs;
the third (LLM self-check TB) is *audited* against them to catch hallucinated
testbenches.

**Why the harness is templated, not LLM-generated:** the judging logic must be
trustworthy. The only LLM-authored artifact in the loop is the Python `ref()`,
which is itself cross-checked against the DUT — if `ref()` is wrong too, the
mismatch surfaces as INCONCLUSIVE rather than a false PASS.

**Scope / phasing (important):**
- Phase A: **combinational** top modules (mux, alu, decoder) — vectors are
  pure input→output, easiest and highest value. Ship this first.
- Phase B: **sequential** (clocked) modules — needs cycle-accurate Python model
  + clocked harness, or switch to **cocotb** (Python testbench driving a real
  sim). Bigger lift; gate behind config; fall back to LLM self-check TB meanwhile.
- pyverilog is **optional**: if not installed, use the regex parser and skip the
  strict-width driving (warn). cocotb is **optional** and only for Phase B.

**New deps (all optional, documented):** `pyverilog` (interface), `cocotb`
(Phase B only). Core still runs on just `rich` + `openai`/`ollama` + Icarus.

---

## 7. Agent roster (map to the paper, keep it Lite)

| MAHL-Lite agent      | Does                                            | Paper analog                |
|----------------------|-------------------------------------------------|-----------------------------|
| **Planner**          | projection, descriptions, AST, codegen          | hierarchical description gen |
| **Reasoner**         | error diagnosis + fix plan (the "thinker")      | adaptive debugging          |
| **Coder**            | applies fix plan -> patched files               | adaptive debugging          |
| **Verifier**         | TB gen + Python ref model + cross-validation     | diverse flow-based validation |
| *Memory* (widget)    | context-window tracking display                 | —                           |

Keep the TUI's 3-box look if you like; internally these are distinct roles with
their own prompt files and optional per-role models.

---

## 8. Open-source hygiene

- `.env` is gitignored and was never committed — **key is safe.** Add
  `.env.example`.
- Add `LICENSE` (**PolyForm Noncommercial 1.0.0** — see §11), `requirements.txt`
  (core vs `[verify]` extras), `CITATION.cff` pointing to arXiv:2508.14053.
- Rewrite `README.md`: what it is, how it maps to/diverges from MAHL, install,
  quickstart, config reference, the cross-validation feature, limitations.
- Remove `out`, `out_tb` binaries from the tree (already gitignored; ensure not
  tracked). Add `artifacts/.gitkeep` instead of committing `ast.json`.
- Replace the misleading "always PASSED" docs with honest behavior.

---

## 9. Suggested order of work (incremental, each step runnable)

1. **Scaffold package** + move code verbatim into modules (no behavior change),
   add `gen_tui1.py` shim. Verify it still runs.
2. **Config + prompts extraction** (§2, §3) — pull every hardcoded prompt/knob
   out. Verify.
3. **Security**: kill `eval()`. Verify projection still parses.
4. **Honest pass/fail** (§5). Verify exit codes.
5. **Reasoner→Coder debug** (§4). Verify on a deliberately broken module.
6. **Cross-validation Phase A** (§6, combinational). Verify on mux/alu, including
   a planted hallucinated TB to confirm it's caught.
7. **Docs + hygiene** (§8).
8. (Later) Cross-validation Phase B / cocotb.

---

## 10. Risks / notes

- LLM `ref()` could be wrong → mitigated by treating golden-vs-DUT disagreement as
  INCONCLUSIVE (needs human/another round), not silent PASS.
- pyverilog install friction (PLY/Jinja2 + needs iverilog for preprocessing) →
  optional with regex fallback.
- Sequential cross-val is genuinely harder; don't overpromise in v1.

---

## 11. Decisions (RESOLVED)

1. **Hard rename.** Move fully to `python -m mahl_lite`; drop `gen_tui1.py`
   entirely (no shim) and update the README to the new entry point.
2. **Real exceptions.** Replace all `"Error: ..."` sentinel strings with a small
   exception hierarchy (`mahl_lite/errors.py`: `MahlError` base, plus
   `LLMError`, `ParseError`, `SimError`, `VerificationError`). Callers handle
   exceptions; the CLI/TUI is the top-level catch boundary.
3. **License — PolyForm Noncommercial 1.0.0.** Permits anyone to use, modify, and
   distribute for any *noncommercial* purpose (research, education, personal),
   which matches "develop freely, research purposes only." NOTE: this is
   *source-available*, not OSI "open source" (OSI licenses cannot restrict to
   non-commercial). Alternative seen on research repos: CC BY-NC 4.0 (but CC is
   not intended for software). Going with PolyForm Noncommercial.
4. **Cross-val v1 = combinational-only.** Ship it; mark sequential/clocked
   designs **experimental** and route them through the existing LLM self-checking
   testbench (with honest pass/fail). We DO care about cycle-accurate / complex
   designs — that's Phase B (cocotb or cycle-accurate Python golden model), a
   fast-follow after the first public release. Ship v1 now, iterate in the open.
```
