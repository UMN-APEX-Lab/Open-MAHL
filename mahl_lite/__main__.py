"""Command-line entry point.

    python -m mahl_lite "Build a 4:1 mux from two 2:1 muxes"
    python -m mahl_lite --style claude --model openai "Create a RISC-V ALU"
    python -m mahl_lite --no-tui --max-fix-rounds 5 "..."

Exit codes:  0 = PASS, 1 = error, 2 = FAIL, 3 = INCONCLUSIVE.
"""

from __future__ import annotations

import argparse
import os
import sys

from .config import Config
from .errors import MahlError
from .llm import LLMClient
from .log import Log
from .pipeline import PipelineResult, run_pipeline
from .tui.theme import STYLE_NAMES
from .verify.analyze import Verdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_EXIT = {Verdict.PASS: 0, Verdict.FAIL: 2, Verdict.INCONCLUSIVE: 3}


def build_config(args) -> Config:
    cfg = Config.load(args.config)
    if args.style:
        cfg.ui.style = args.style
    if args.model:
        cfg.models.default = args.model
    if args.lib:
        cfg.output.lib_dir = args.lib
    if args.max_fix_rounds is not None:
        cfg.rounds.max_fix_rounds = args.max_fix_rounds
    if args.max_tb_rounds is not None:
        cfg.rounds.max_tb_rounds = args.max_tb_rounds
    if args.no_tui:
        cfg.ui.no_tui = True
    if args.no_crossval:
        cfg.verify.crossvalidation = False
    if args.lenient:
        cfg.verify.honest_results = False
    return cfg


def summarize(result: PipelineResult) -> list[str]:
    lines = [
        f"Status: {result.verdict.value} {result.verdict.icon}",
        f"Reason: {result.reason}",
        f"Top module: {result.top_module}",
        f"Modules: {result.modules}",
        f"Output dir: {result.lib_dir}",
        f"Design compiled: {'yes' if result.design_compiled else 'no'}",
    ]
    if result.crossval is not None:
        cv = result.crossval
        lines.append(f"Cross-validation: {cv.verdict.value} ({cv.num_vectors} vectors)")
    if result.tb_verdict is not None:
        lines.append(f"Testbench (2nd opinion): {result.tb_verdict.value}")
    if result.tb_hallucination:
        lines.append("⚠ TESTBENCH HALLUCINATION: testbench disagreed with the golden model.")
    return lines


def run_plain(prompt: str, cfg: Config) -> PipelineResult:
    Log.info("Running in plain mode (no TUI).")
    return run_pipeline(prompt, LLMClient(cfg), cfg, tui=None)


def run_with_tui(prompt: str, cfg: Config) -> PipelineResult:
    from rich.console import Console
    from rich.live import Live
    from .tui import TUI

    tui = TUI(cfg)
    Log.attach_sink(lambda msg, style: tui.note(msg, style))
    log_file = "tui_log.txt"
    Log.set_log_file(log_file)
    use_screen = sys.stdout.isatty()
    console = Console(force_terminal=use_screen, soft_wrap=True)

    try:
        with Live(tui.render(), console=console, refresh_per_second=10,
                  screen=use_screen, transient=False) as live:
            tui.live_display = live
            tui.start_file_monitoring(log_file)
            client = LLMClient(cfg, tui=tui)
            result = run_pipeline(prompt, client, cfg, tui=tui)
            tui.stop_file_monitoring()

            tui.summary_lines = ["Result"] + summarize(result)
            tui.render()
            tui.footer_help = "Press Enter to exit"
            tui.render()
            try:
                sys.stdin.readline()
            except Exception:
                pass
    finally:
        Log.attach_sink(None)
        try:
            os.remove(log_file)
        except OSError:
            pass
    return result


def main() -> int:
    p = argparse.ArgumentParser(prog="mahl_lite", description="MAHL-Lite — LLM RTL generation & verification")
    p.add_argument("user_prompt", nargs="?", help="Describe the design to generate")
    p.add_argument("--config", help="Path to a config.yaml (default: ./config.yaml if present)")
    p.add_argument("--style", choices=STYLE_NAMES, help="UI theme")
    p.add_argument("--model", help="openai (uses $OPENAI_API_KEY) or an Ollama model name")
    p.add_argument("--lib", help="Output directory for .v files")
    p.add_argument("--max-fix-rounds", type=int, default=None, help="Design compile/fix rounds (0 = off)")
    p.add_argument("--max-tb-rounds", type=int, default=None, help="Testbench rounds (0 = off)")
    p.add_argument("--no-tui", action="store_true", help="Disable the Rich TUI")
    p.add_argument("--no-crossval", action="store_true", help="Disable Python golden-model cross-validation")
    p.add_argument("--lenient", action="store_true", help="Always report PASS (demo mode)")
    args = p.parse_args()

    cfg = build_config(args)

    prompt = args.user_prompt
    if not prompt:
        try:
            prompt = input("Describe the design to generate:\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 130
    if not prompt:
        print("Empty prompt.")
        return 1

    from .tui import rich_available
    use_tui = rich_available() and not cfg.ui.no_tui

    try:
        result = run_with_tui(prompt, cfg) if use_tui else run_plain(prompt, cfg)
    except MahlError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    if not use_tui:
        print("\n=== Result ===")
        for line in summarize(result):
            print(line)
    return _EXIT.get(result.verdict, 1)


if __name__ == "__main__":
    sys.exit(main())
