# MAHL-Lite

**A lightweight, open-source distillation of the [MAHL](https://arxiv.org/abs/2508.14053) RTL pipeline.**

MAHL-Lite turns a natural-language hardware request into synthesizable Verilog and
*verifies it* — with a small team of LLM "agents" and a futuristic terminal UI. It
is built for reading, hacking, and research: every prompt is an editable file and
every knob lives in one `config.yaml`.

```bash
python -m mahl_lite "Build a 4:1 mux from two 2:1 muxes"
```

---

## What it does

```
        Planner                Reasoner ─► Coder            Verifier
   describe ▸ AST ▸ codegen   diagnose ▸ patch (on error)   testbench + golden model
        │                          │                              │
        ▼                          ▼                              ▼
   module_lib/*.v  ──►  iverilog compile ──►  cross-validated PASS / FAIL / INCONCLUSIVE
```

1. **Planner** — lists the modules, writes hierarchical descriptions, builds an AST,
   and generates one `.v` file per module.
2. **Adaptive debugging (Reasoner → Coder)** — when a compile or simulation step
   fails, a *Reasoner* first diagnoses the root cause and writes a fix plan (you see
   it think 🧠), then a *Coder* applies that plan. This two-step split mirrors MAHL's
   adaptive debugging and beats a single diagnose-and-patch call.
3. **Verifier** — generates a self-checking testbench **and** an independent Python
   golden model, then cross-validates (below).

### Cross-validation: defeating testbench hallucination

An LLM-written testbench can confidently "verify" that 1 + 1 = 3. So MAHL-Lite moves
the verdict **out of the LLM-written Verilog and into deterministic Python**:

1. extract the DUT's real ports (names / widths) — regex, double-checked by
   [pyverilog](https://github.com/PyHDI/Pyverilog) when installed;
2. ask the LLM for a **Python** reference model of the spec;
3. generate shared test vectors (directed edge cases + seeded random);
4. run them through the Python model → **golden** outputs;
5. drive the DUT with a *templated, non-LLM* harness that only prints raw outputs;
6. **compare in Python** — that comparison is the authoritative verdict.

If the self-checking testbench disagrees with the golden model, it is flagged as a
**TESTBENCH HALLUCINATION** and the golden model wins. Results are honest:

| Verdict | Meaning | Exit code |
|---|---|---|
| `PASS ✅` | DUT matches the golden model / testbench | 0 |
| `FAIL ❌` | mismatch found (carries the reason) | 2 |
| `INCONCLUSIVE ⚠` | could not decide (no marker, didn't run, etc.) | 3 |

> **v1 scope:** cross-validation covers **combinational** modules. Sequential /
> clocked designs are detected and routed to the self-checking testbench path
> (still with honest pass/fail). Cycle-accurate cross-validation is on the roadmap.

---

## Relationship to the MAHL paper

MAHL-Lite keeps MAHL's **hierarchical description**, **hierarchical code
generation**, **flow-based validation**, and **adaptive debugging**. It intentionally
**leaves out** MAHL's retrieval-augmented generation, multi-granularity design-space
exploration, and PPA optimization. If you use this in research, please
[cite the paper](#citation).

---

## Install

```bash
pip install -r requirements.txt          # rich, openai, python-dotenv, pyyaml
# optional: pip install ollama pyverilog  # local models / robust port checks
```

You also need **Icarus Verilog** on your PATH:

```bash
sudo apt install iverilog        # Debian/Ubuntu
brew install icarus-verilog      # macOS
```

Create a `.env` (see `.env.example`) for the OpenAI backend:

```
OPENAI_API_KEY=sk-...
```

---

## Usage

```bash
python -m mahl_lite "Create a RISC-V ALU with add/sub/and/or"

python -m mahl_lite --style claude --model openai "Design a 2:1 mux"
python -m mahl_lite --no-tui --max-fix-rounds 5 "..."     # plain logs, CI-friendly
python -m mahl_lite --no-crossval "..."                   # testbench only
python -m mahl_lite --lenient "..."                       # demo mode: always PASS
```

Local models via Ollama: `--model llama3.3` (run the daemon, pull the model first).

---

## Configuration (the point of "Lite")

Two places, no code required:

- **`config.yaml`** — models (and per-role model overrides), sampling, debug rounds,
  the `two_phase` debug toggle, cross-validation settings, UI theme, output dirs.
  Precedence: defaults < `config.yaml` < environment < CLI flags.
- **`prompts/*.txt`** — every prompt the agents use. Edit them freely; placeholders
  look like `{{name}}`.

Per-role models let you, for example, reason about bugs with a strong model and
write the patch with a cheaper one:

```yaml
models:
  default: openai
  reasoner: openai        # strong
  coder: llama3.3         # cheap/fast
```

Themes: `futuristic` (default), `claude`, `openai`, `genmini`.

---

## Project layout

```
mahl_lite/
  __main__.py   config.py  llm.py  prompts.py  parsing.py  errors.py  log.py
  pipeline.py            # Planner + orchestration
  debug.py              # Reasoner -> Coder
  verify/  sim.py  interface.py  testbench.py  crossval.py  analyze.py
  tui/     app.py  theme.py
prompts/        # editable prompt templates
config.yaml     # editable settings
REFACTOR_PLAN.md  # design notes / roadmap
```

---

## Caveats

- Cross-validation runs **LLM-generated Python** in a timed subprocess. It is
  isolated and time-bounded but **not a security sandbox** — review models or set
  `verify.crossvalidation: false` if you don't trust the output.
- Sequential cross-validation is experimental (see scope note above).

---

## Citation

```bibtex
@article{mahl2025,
  title  = {MAHL: Multi-Agent LLM-Guided Hierarchical Chiplet Design with Adaptive Debugging},
  author = {Tang, Jinwei},
  year   = {2025},
  eprint = {2508.14053},
  archivePrefix = {arXiv}
}
```

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — free to use, modify, and
distribute for any **noncommercial** purpose (research, education, personal). This is
source-available, not an OSI "open source" license, because it restricts use to
noncommercial purposes.
