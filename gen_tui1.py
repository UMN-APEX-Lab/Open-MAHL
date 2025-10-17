#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_tui.py — Terminal Coder (gen•mini style)
--------------------------------------------
A single-file terminal app that runs the entire RTL generation & debugging pipeline
with a pretty Rich-based TUI inspired by "Claude Code" / "OpenAI terminal coder" styles.

Features
- Style presets: --style claude | openai | genmini (default: genmini)
- Full-screen-ish Rich layout with header, step tracker, live logs, and a final summary
- Testbench verification pipeline: generate TB → compile & fix TB → run TB → analyze output
- "Verification" section in the GUI: dedicated panel on the left and filtered log on the right
- Graceful fallback to non-TUI if Rich isn't installed or --no-tui is passed
- Minimal dependencies at runtime: rich (optional), termcolor (optional)

Usage
-----
$ python gen_tui.py "Build a 4:1 mux using two 2:1 muxes and a top module"
$ python gen_tui.py --style claude --model openai "Make an ALU with add/sub/and/or"

Env
---
- OPENAI_API_KEY for OpenAI calls
- For Ollama, ensure `ollama` daemon is running and model pulled
"""

import os
import re
import sys
import json
import argparse
import subprocess
import time
import threading
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# --------------------------
# Optional UI libs
# --------------------------
_USE_RICH = True
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.align import Align
    from rich.table import Table
    from rich.live import Live
    from rich import box
    console = Console()  # default console; may be overridden in main()
except Exception:
    console = None
    _USE_RICH = False

try:
    from termcolor import colored
except Exception:
    def colored(s, *_args, **_kwargs):  # type: ignore
        return s

# dotenv optional
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --------------------------
# Pretty Logger + TUI plumbing
# --------------------------
# Configuration constants
MAX_LOG_LINES = 500  # Keep in memory for file logging
MAX_DISPLAY_LINES = 25  # Number of lines to display in TUI (keeps newest messages visible)

class Log:
    """
    Centralized logger that can write both to plain console and a TUI sink (if provided).
    Also writes to a file so the TUI can monitor it in a separate thread.
    """
    sink = None  # callable(str, style), set by TUI
    use_color = True
    log_file = None  # Path to log file for TUI
    _lock = threading.Lock()  # Thread safety

    @staticmethod
    def attach_sink(fn):
        Log.sink = fn

    @staticmethod
    def set_log_file(file_path: str):
        Log.log_file = file_path
        # Clear the log file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("")

    @staticmethod
    def _write(line: str, style: str = "white"):
        with Log._lock:
            # Write to file for TUI monitoring
            if Log.log_file:
                try:
                    with open(Log.log_file, 'a', encoding='utf-8') as f:
                        f.write(f"{line}\n")
                except Exception:
                    pass  # Ignore file write errors

            # to TUI sink first
            if Log.sink is not None:
                Log.sink(line, style)
            # and to plain stdout if no rich or sink disabled
            if not _USE_RICH or console is None or Log.sink is None:
                print(line)

    # General logs
    @staticmethod
    def info(msg: str): Log._write(f"[INFO] {msg}", "cyan")
    @staticmethod
    def warn(msg: str): Log._write(f"[WARN] {msg}", "yellow")
    @staticmethod
    def error(msg: str): Log._write(f"[ERROR] {msg}", "red")
    @staticmethod
    def success(msg: str): Log._write(f"[OK] {msg}", "green")
    @staticmethod
    def step(n: int, msg: str): Log._write(f"🔹 Step {n}: {msg}", "bright_white")

    # Verification-focused logs (keep level first so styling still works; tag with [VERIF])
    @staticmethod
    def vinfo(msg: str): Log._write(f"[INFO][VERIF] {msg}", "cyan")
    @staticmethod
    def vwarn(msg: str): Log._write(f"[WARN][VERIF] {msg}", "yellow")
    @staticmethod
    def verror(msg: str): Log._write(f"[ERROR][VERIF] {msg}", "red")
    @staticmethod
    def vsuccess(msg: str): Log._write(f"[OK][VERIF] {msg}", "green")

# Styles
STYLE_PRESETS = {
    "genmini": {"primary": "bright_blue", "accent": "cyan", "border": "blue"},
    "claude":  {"primary": "magenta",     "accent": "bright_magenta", "border": "magenta"},
    "openai":  {"primary": "cyan",        "accent": "bright_cyan", "border": "cyan"},
    "futuristic": {
        "primary": "bright_magenta",
        "accent": "bright_cyan",
        "border": "blue",
        "glow1": "magenta",
        "glow2": "cyan",
        "glow3": "blue",
        "neon": "bright_yellow"
    }
}

# --------------------------
# LLM plumbing (lazy imports to avoid hard deps)
# --------------------------
def openai_llm_call(prompt: str) -> str:
    try:
        from openai import OpenAI
    except Exception as e:
        Log.error("OpenAI SDK not installed. Try `pip install openai`.")
        raise
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=4096,
        top_p=1,
    )
    return (resp.choices[0].message.content or "").strip()

def local_llm_call(prompt: str, model: str) -> str:
    try:
        import ollama
    except Exception:
        Log.error("Ollama not available. Install `ollama` Python package and run the daemon.")
        raise
    if model.startswith("gemma3"):
        options = {"temperature": 0.6, "top_p": 0.95, "top_k": 64}
    elif model.startswith("llama3.3"):
        options = {"temperature": 0.6}
    elif model.startswith("deepseek-r1"):
        options = {"temperature": 0.6}
    else:
        options = {"temperature": 0.3}
    r = ollama.chat(messages=[{"role": "user", "content": prompt}], model=model, options=options)
    if isinstance(r, dict):
        if "message" in r and isinstance(r["message"], dict):
            return r["message"].get("content", "").strip()
        return (r.get("content", "") or "").strip()
    msg = getattr(r, "message", None)
    return (msg.content if msg else "").strip()

def llm_call(prompt: str, model: str = "openai", agent_name: str = None, tui=None) -> str:
    # Add a small delay to make progress visible in the UI
    time.sleep(0.25)
    # Track active agent
    if tui and agent_name:
        tui.set_active_agent(agent_name)

    # Track LLM conversation history: add prompt to memory
    if tui:
        tui.add_memory(prompt)

    try:
        result = openai_llm_call(prompt) if model == "openai" else local_llm_call(prompt, model)

        # Track LLM conversation history: add response to memory
        if tui:
            tui.add_memory(result)

        # Clear active agent
        if tui and agent_name:
            tui.set_active_agent(None)
        return result
    except Exception as e:
        Log.error(f"LLM call failed: {e}")
        # Clear active agent on error
        if tui and agent_name:
            tui.set_active_agent(None)
        # Return a fallback response instead of crashing
        return f"Error: LLM call failed - {e}"

# --------------------------
# Helpers
# --------------------------
def strip_think_blocks(text: str) -> str:
    if "<think>" in text and "</think>" in text:
        try:
            return text.split("</think>", 1)[1]
        except Exception:
            return text
    return text

def extract_code_blocks(text: str):
    import re
    text = strip_think_blocks(text)
    # Handle both ```lang and ``` patterns, with optional whitespace
    code_fence = re.compile(r"```\s*(\w+)?\s*\n(.*?)```", re.DOTALL)
    blocks = []
    for m in code_fence.finditer(text):
        lang = (m.group(1) or "").strip()
        code = m.group(2).strip()
        if code:  # Only add non-empty blocks
            blocks.append((lang, code))
    return blocks

def extract_first_code(text: str) -> str:
    blocks = extract_code_blocks(text)
    if blocks:
        return blocks[0][1]
    return strip_think_blocks(text).strip()

def ensure_json(s: str) -> Optional[dict]:
    """
    Extract and parse JSON from various formats in LLM responses.
    Handles: raw JSON, JSON in code blocks, JSON with extra text, etc.
    """
    # Try 1: Direct parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # Try 2: Extract from code blocks (look for json language tag)
    for lang, code in extract_code_blocks(s):
        if lang.lower() in ("json", ""):
            try:
                return json.loads(code)
            except Exception:
                pass

    # Try 3: Find JSON object using regex (find first {...} that spans multiple lines)
    import re
    json_pattern = re.compile(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', re.DOTALL)
    for match in json_pattern.finditer(s):
        try:
            return json.loads(match.group(0))
        except Exception:
            continue

    # Try 4: Extract first code block regardless of language
    try:
        first_code = extract_first_code(s)
        if first_code:
            return json.loads(first_code)
    except Exception:
        pass

    return None

def split_module(lines: List[str]) -> List[str]:
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.lower().startswith(("module:", "topmodule:")):
            buf = []
            while i < len(lines) and lines[i].strip() != "":
                buf.append(lines[i])
                i += 1
            out.append("\n".join(buf))
        else:
            i += 1
    return out

def first_key(d: dict) -> Optional[str]:
    if isinstance(d, dict) and d:
        return next(iter(d.keys()))
    return None

# --------------------------
# Core prompts
# --------------------------
def user_prompt_projection(user_prompt: str, model: str, tui=None) -> Optional[List[str]]:
    Log.info("Projecting user request to list of required modules...")
    try:
        prompt = f"""Given the following user request, identify all required modules. When generating complicated module, there should be a top module called ..._core
        User request: {user_prompt}

        Please provide:
        1. List of required modules in verilog level mentioning only module name such as mux4_1 will need mux4_1 and mux2_1. And a example output for this situation would be just: ['mux4_1', 'mux2_1']
        2. General description of how these modules should be connected

        Your response should only contain the list of modules in python list string format! out side I will try to eval() it, so don't put anything else.
        """
        r = llm_call(prompt, model=model, agent_name="Code Planning Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return None
        extracted = extract_first_code(r)
        try:
            lst = eval(extracted, {}, {})
            Log.success(f"Identified modules: {lst}")
            return list(lst)
        except Exception as e:
            Log.warn(f"Could not parse module list from LLM response: {e}")
            Log.warn(f"LLM said:\n{r}")
            return None
    except Exception as e:
        Log.error(f"Module projection failed: {e}")
        return None

def generate_module_description(project_definition: str, modules_list: List[str], model: str, tui=None) -> str:
    Log.info("Generating detailed module descriptions...")
    try:
        template = "Please provide detailed module descriptions following standard Verilog module format."
        try:
            with open("module_description_example.txt", "r", encoding="utf-8") as f:
                template = f.read()
                Log.info("Loaded module_description_example.txt template.")
        except FileNotFoundError:
            Log.warn("module_description_example.txt not found. Using default rules.")

        prompt = f"""Using the following template and rules, generate detailed module descriptions for each module. Think carefully about module connections. Your response should have empty lines between the module descriptions, as module1 then \n then module2.
        the total number of module descriptions should match the [Modules to describe] list. When generating complicated module, there should be a top module called ..._core
        Use markdown syntax to format your response.

        Project definition and requirements considerations:
        {project_definition}

        Template and Rules:
        {template}

        Modules to describe:
        {modules_list}

        Format your response using markdown with ``` for code blocks.
        """
        r = llm_call(prompt, model=model, agent_name="Code Planning Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return "Error: Failed to generate module descriptions"
        if not _USE_RICH:
            print(r)
        extracted = extract_first_code(r)
        Log.success("Module descriptions generated.")
        return extracted
    except Exception as e:
        Log.error(f"Module description generation failed: {e}")
        return f"Error: Module description generation failed - {e}"

def generate_ast(module_descriptions: str, model: str, tui=None) -> Optional[dict]:
    Log.info("Generating AST for module hierarchy...")
    try:
        prompt = f"""Analyze these modules and generate an AST tree in JSON format representing their hierarchy. The result should contain a single root.

Module Descriptions:
{module_descriptions}

Only return the JSON that contains the hierarchy of the modules.
Example (for ['mux4_1','mux2_1']):
{{"mux4_1": {{"mux2_1": {{}}}}}}
"""
        r = llm_call(prompt, model=model, agent_name="Code Planning Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return None
        extracted = extract_first_code(r)
        try:
            ast = json.loads(extracted)
            Log.success("AST generated.")
            return ast
        except Exception as e:
            Log.warn(f"Could not parse AST JSON from LLM response: {e}")
            Log.warn(f"LLM said:\n{r}")
            return None
    except Exception as e:
        Log.error(f"AST generation failed: {e}")
        return None

def generate_module_code(module_name: str, module_description: str, model: str, tui=None) -> str:
    Log.info(f"Generating code for module: {module_name}")
    try:
        prompt = f"""Generate Verilog code for the following module description.
        Use markdown syntax to format your response. You must not generate any definition block of any dependency code for the current module generation task.
        You must not generate any placeholder code.

        Module Name: {module_name}
        Description: {module_description}

        Format your response using markdown with ``` for code blocks.
        """
        r = llm_call(prompt, model=model, agent_name="Code Planning Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return f"// Error: Failed to generate code for {module_name}\n// {r}"
        code = extract_first_code(r)
        Log.success(f"Code generated: {module_name}")
        return code
    except Exception as e:
        Log.error(f"Code generation failed for {module_name}: {e}")
        return f"// Error: Code generation failed for {module_name}\n// {e}"

def propose_fixes_with_llm(error_text: str, file_code_map: Dict[str, str], model: str, focus_file: str = None, tui=None) -> Dict[str, str]:
    Log.info("Requesting LLM patches for compile errors...")
    try:
        file_list = list(file_code_map.keys())

        # ALWAYS include full file contents for comprehensive debugging
        files_context = "\n=== ALL FILE CONTENTS ===\n"
        for filename, code in file_code_map.items():
            files_context += f"\n--- FILE: {filename} ---\n```verilog\n{code}\n```\n"

        # Add focus instruction if specified
        focus_instruction = ""
        if focus_file and focus_file in file_list:
            focus_instruction = f"\n🎯 PRIMARY TARGET: Focus on fixing the {focus_file} file first, as this is the main failing component. The other files provide necessary context."

        # Parse error to identify specific issues
        error_analysis = "\n=== ERROR ANALYSIS ===\n"
        if "syntax error" in error_text.lower():
            error_analysis += "- SYNTAX ERROR detected: Check for missing semicolons, mismatched parentheses, or typos\n"
        if "undeclared" in error_text.lower() or "not declared" in error_text.lower():
            error_analysis += "- UNDECLARED IDENTIFIER: Check variable/module declarations and instantiations\n"
        if "port" in error_text.lower():
            error_analysis += "- PORT MISMATCH: Verify port names, counts, and connections match module definitions\n"
        if "width" in error_text.lower() or "size" in error_text.lower():
            error_analysis += "- WIDTH/SIZE ISSUE: Check signal widths and array dimensions\n"

        prompt = f"""You are an expert Verilog debugger. Analyze these compilation errors and provide comprehensive fixes.

=== COMPILATION ERROR ===
{error_text}

{error_analysis}

{files_context}

{focus_instruction}

=== YOUR TASK ===
1. Carefully analyze the error messages - identify the root cause
2. Review ALL file contents above for context
3. Fix ALL issues - don't leave placeholders or TODOs
4. Ensure proper Verilog syntax (SystemVerilog 2012 compatible)
5. Verify module instantiations match their definitions
6. Check that all signals are properly declared
7. Ensure port connections are correct

FILES TO CHECK: {', '.join(file_list)}

=== RESPONSE FORMAT ===
Return ONLY a JSON object (no commentary outside):
{{
  "patches": [
    {{"file": "<EXACT_FILENAME>", "code": "<COMPLETE_CORRECTED_FILE_CONTENT>"}},
    ...
  ]
}}

CRITICAL:
- Use exact filenames from the list above
- Include the FULL corrected file content (not diffs)
- Fix all errors, not just the first one
- Ensure the code is production-ready (no placeholders)
"""
        r = llm_call(prompt, model=model, agent_name="Debugging Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return {}
        data = ensure_json(r)
        if not data or "patches" not in data or not isinstance(data["patches"], list):
            Log.warn("LLM did not return valid patch JSON; attempting fallback via code-blocks.")
            patches: Dict[str, str] = {}
            blocks = extract_code_blocks(r)
            if blocks:
                target = None
                m = re.search(r"(?P<file>\\w+\\.v)", error_text)
                target = m.group("file") if m else (file_list[0] if file_list else "unknown.v")
                patches[target] = blocks[0][1]
                return patches
            return {}
        out: Dict[str, str] = {}
        for p in data["patches"]:
            f = p.get("file")
            c = p.get("code")
            if isinstance(f, str) and isinstance(c, str):
                out[f] = c
        return out
    except Exception as e:
        Log.error(f"LLM patch proposal failed: {e}")
        return {}

# --------------------------
# Verification / compile loop (design files)
# --------------------------
def run_iverilog(file_paths: List[Path], out_name: str = "out"):
    cmd = ["iverilog", "-g2012", "-o", out_name] + [str(p) for p in file_paths]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return (res.returncode == 0, res.stderr + (("\n" + res.stdout) if res.stdout else ""))
    except FileNotFoundError:
        return (False, "iverilog not found on PATH. Please install Icarus Verilog.")
    except Exception as e:
        return (False, f"Unexpected error invoking iverilog: {e}")

def run_vvp(exe_name: str):
    try:
        res = subprocess.run(["vvp", exe_name], capture_output=True, text=True)
        return (res.returncode == 0, (res.stdout or "") + (("\n" + res.stderr) if res.stderr else ""))
    except FileNotFoundError:
        return (False, "vvp (Icarus runtime) not found on PATH. Please install Icarus Verilog.")
    except Exception as e:
        return (False, f"Unexpected error invoking vvp: {e}")

def compile_and_fix(module_lib_dir: Path, modules_list: List[str], model: str, max_rounds: int = 5, tui=None):
    if tui: 
        tui.set_step_status("compile", "running")
        time.sleep(0.3)
    Log.info("Starting final compilation & debug loop.")
    file_paths = [module_lib_dir / f"{m}.v" for m in modules_list if (module_lib_dir / f"{m}.v").exists()]
    if not file_paths:
        Log.error("No Verilog files found to compile.")
        if tui: tui.set_step_status("compile", "failed")
        return False, "No Verilog files found."

    for round_idx in range(1, max_rounds + 1):
        Log.step(5, f"Final compile attempt {round_idx}/{max_rounds}")
        ok, err = run_iverilog(file_paths, out_name="out")
        if ok:
            Log.success("All modules compiled successfully ✅")
            if tui: 
                tui.set_step_status("compile", "done")
                time.sleep(0.2)
            return True, ""
        Log.warn("Compilation failed; invoking LLM debugger for patches.")
        if tui: tui.note(f"[round {round_idx}] Compile failed; requesting patches...", "yellow")
        file_code_map = {p.name: p.read_text(encoding="utf-8", errors="ignore") for p in file_paths}
        patches = propose_fixes_with_llm(err, file_code_map, model=model, tui=tui)
        if not patches:
            Log.error("LLM returned no actionable patches.")
            if tui: tui.set_step_status("compile", "failed")
            return False, err
        for fname, new_code in patches.items():
            target = module_lib_dir / fname
            if not target.exists():
                Log.warn(f"Patch references missing file {fname}; creating new file.")
            target.write_text(new_code, encoding="utf-8")
            Log.info(f"Applied patch to {fname}")
    Log.error("Reached maximum debug rounds; compilation still failing.")
    if tui: tui.set_step_status("compile", "failed")
    return False, "Max rounds reached."

# --------------------------
# Testbench generation / compile / run / analyze
# --------------------------
# --- Top-module parser (from generated design files) ---
# (Pattern adapted from verifier_fastapi.py)
# Handle optional parameter list in decl: module name #(...) ( ... );
MODULE_DECL_RE = re.compile(
    r"(?ms)^\s*module\s+([a-zA-Z_]\w*)\s*(?:#\s*\(.*?\)\s*)?\(\s*(.*?)\s*\)\s*;(?P<body>.*?)(?:endmodule\b)"
)
PORT_LINE_RE   = re.compile(r"\b(input|output|inout)\b\s*(?:reg|wire|logic)?\s*(\[[^\]]+\])?\s*([\w$, \t]+)\s*;")
VERILOG_PRIMITIVES = {
    "and","nand","nor","or","xor","xnor","buf","not",
    "bufif0","bufif1","notif0","notif1","nmos","pmos","cmos","rnmos","rpmos","rcmos",
    "tran","rtran","tranif0","tranif1","rtranif0","rtranif1",
    "pulldown","pullup","tri","tri0","tri1","triand","trior","wand","wor","supply0","supply1"
}
def _parse_modules_from_dir(lib_dir: Path) -> Dict[str, dict]:
    mods: Dict[str, dict] = {}
    texts: Dict[str, str] = {}
    for p in lib_dir.glob("*.v"):
        try:
            texts[p.name] = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
    # Find module decls + ports
    for txt in texts.values():
        for m in MODULE_DECL_RE.finditer(txt):
            name, header, body = m.group(1), m.group(2), m.group("body")
            ports: Dict[str, dict] = {}
            # ANSI-style (in header) and non-ANSI (in body)
            for pl in PORT_LINE_RE.finditer(header + ";"):
                d, rng, names = pl.groups()
                for nm in [n.strip() for n in names.split(",") if n.strip()]:
                    ports[nm] = {"dir": d, "range": rng}
            for pl in PORT_LINE_RE.finditer(body):
                d, rng, names = pl.groups()
                for nm in [n.strip() for n in names.split(",") if n.strip()]:
                    ports[nm] = {"dir": d, "range": rng}
            mods[name] = {"ports": ports, "body": body, "instantiates": set()}
    # Find instantiations among discovered modules (exclude TBs & primitives)
    names = [n for n in mods.keys()
             if n not in VERILOG_PRIMITIVES and not (n.lower().startswith("tb_") or n.lower().endswith("_tb"))]
    if names:
        # Avoid matching the 'module <name>' lines; allow parameterized instantiation '#(...)'
        inst_re = re.compile(
            r"(?m)^\s*(?!module\b)(?:(?:" + "|".join(re.escape(n) for n in names) + r"))\b\s+[\w$]+\s*(?:#\s*\(.*?\)\s*)?\("
        )
        for mod, info in mods.items():
            for im in inst_re.finditer(info["body"]):
                callee = im.group(0).split()[0]
                if callee != mod:
                    info["instantiates"].add(callee)
    return mods

def _infer_top_from_mods(mods: Dict[str, dict], modules_list: List[str]) -> Optional[str]:
    if not mods:
        return None
    # Exclude TB-like names and primitives from candidacy
    def ok_name(n: str) -> bool:
        ln = n.lower()
        return n not in VERILOG_PRIMITIVES and not (ln.startswith("tb_") or ln.endswith("_tb"))
    allm = {n for n in mods.keys() if ok_name(n)}
    if not allm:
        return None
    inst = {c for info in mods.values() for c in info["instantiates"] if ok_name(c)}
    roots = list(allm - inst) or list(allm)
    # 1) If LLM-proposed order lists a present root, prefer that (first match wins)
    for m in modules_list:
        if m in roots:
            return m
    # 2) Prefer the root with more instantiations, then more ports
    roots.sort(key=lambda n: (len(mods[n]["instantiates"]), len(mods[n]["ports"])), reverse=True)
    return roots[0]

def detect_top_module(ast_json: Optional[dict], modules_list: List[str], lib_dir: Path) -> str:
    """Detect top module from AST and modules_list, with fallback to parsing generated files."""
    # First try: AST root (most reliable for current run)
    if ast_json and isinstance(ast_json, dict):
        ast_root = first_key(ast_json)
        if ast_root and ast_root not in VERILOG_PRIMITIVES and not (ast_root.lower().startswith("tb_") or ast_root.lower().endswith("_tb")):
            Log.vinfo(f"Top module from AST: {ast_root}")
            return ast_root
    
    # Second try: First module in the declared list (from current prompt)
    for module in modules_list:
        if module and module not in VERILOG_PRIMITIVES and not (module.lower().startswith("tb_") or module.lower().endswith("_tb")):
            Log.vinfo(f"Top module from modules list: {module}")
            return module
    
    # Third try: Parse generated files (fallback)
    try:
        mods = _parse_modules_from_dir(lib_dir)
        top = _infer_top_from_mods(mods, modules_list)
        if top:
            Log.vinfo(f"Top module parsed from sources: {top}")
            return top
    except Exception as e:
        Log.vwarn(f"Top parser failed; using fallback ({e})")
    
    # Final fallback
    Log.vwarn("Could not determine top module, using 'top' as fallback")
    return "top"

def generate_testbench_code(top_module: str, module_descriptions: str, model: str, module_lib_dir: Path, tui=None) -> str:
    Log.vinfo(f"Generating self-checking testbench for top '{top_module}'...")
    try:
        # Read the actual top module code for interface information
        top_module_path = module_lib_dir / f"{top_module}.v"
        top_module_code = ""
        if top_module_path.exists():
            top_module_code = top_module_path.read_text(encoding="utf-8", errors="ignore")

        prompt = f"""Write a comprehensive self-checking Verilog testbench for the top module "{top_module}".

CRITICAL REQUIREMENTS:
1. Study the actual module interface carefully from the code below
2. Include `timescale 1ns/1ps` directive
3. Instantiate the DUT with correct port connections
4. Generate appropriate stimulus for ALL input ports
5. If there's a clock signal, generate it properly (always @(#5) clk = ~clk)
6. If there's a reset signal, assert it at the beginning
7. Create comprehensive test cases that verify the design functionality
8. Print EXACTLY one of these markers at the end:
   - "TEST PASSED" if all checks pass
   - "TEST FAILED: <specific reason>" if any check fails
9. Use $display statements to show test progress
10. End simulation with $finish after all tests
11. Compile under Icarus Verilog (iverilog -g2012, vvp)

--- ACTUAL TOP MODULE CODE ---
```verilog
{top_module_code}
```

--- MODULE DESCRIPTIONS (for context) ---
{module_descriptions}

IMPORTANT:
- Match the exact port names and widths from the module definition above
- Do not use placeholder comments - generate complete, working test cases
- Verify expected outputs against actual outputs
- Use behavioral code in the testbench (not structural)

Return ONLY the testbench code in a fenced block.
"""
        r = llm_call(prompt, model=model, agent_name="Verification Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return f"""`timescale 1ns/1ps
// Error: Failed to generate testbench
// {r}
module tb_{top_module};
  // Placeholder testbench due to generation error
  initial begin
    $display("TEST FAILED: testbench generation error");
    $finish;
  end
endmodule
"""
        code = extract_first_code(r)
        if "TEST PASSED" not in code and "TEST FAILED" not in code:
            # Ensure markers exist to simplify analysis
            code = f"""`timescale 1ns/1ps
// Auto-added pass/fail markers wrapper if missing
{code}

initial begin
  // Fallback marker in case TB didn't print anything
  #10 $display("TEST FAILED: no marker emitted");
  $finish;
end
"""
        Log.vsuccess("Testbench generated.")
        return code
    except Exception as e:
        Log.error(f"Testbench generation failed: {e}")
        return f"""`timescale 1ns/1ps
// Error: Testbench generation failed
// {e}
module tb_{top_module};
  // Placeholder testbench due to generation error
  initial begin
    $display("TEST FAILED: testbench generation error");
    $finish;
  end
endmodule
"""

def compile_and_fix_testbench(tb_path: Path, module_lib_dir: Path, modules_list: List[str], model: str, max_rounds: int = 3, tui=None):
    if tui: 
        tui.set_step_status("tbcompile", "running")
        time.sleep(0.15)
    Log.vinfo("Compiling testbench with design files...")
    design_files = [module_lib_dir / f"{m}.v" for m in modules_list if (module_lib_dir / f"{m}.v").exists()]
    if not design_files:
        msg = "Design files for testbench compile not found."
        Log.verror(msg)
        if tui: 
            tui.set_step_status("tbcompile", "failed")
            tui.note(f"[VERIF]Error: {msg}", "red")
        else:
            # Also print to terminal in plain mode
            print(f"Error: {msg}")
        return False, msg

    for round_idx in range(1, max_rounds + 1):
        Log.step(6, f"Testbench compile attempt {round_idx}/{max_rounds}")
        ok, err = run_iverilog(design_files + [tb_path], out_name="out_tb")
        if ok:
            Log.vsuccess("Testbench compiled successfully ✅")
            if tui: 
                tui.set_step_status("tbcompile", "done")
                time.sleep(0.1)
            return True, ""
        Log.vwarn("Testbench compile failed; requesting LLM patch to the TB.")
        if tui: 
            tui.note(f"[VERIF][tb round {round_idx}] tb compile failed; patching TB...", "yellow")
            tui.note("[VERIF]Compilation error:", "red")
            # Show error details in the UI
            for line in err.split('\n')[:10]:  # Show first 10 lines of error
                if line.strip():
                    tui.note(f"[VERIF]  {line}", "red")
            if len(err.split('\n')) > 10:
                tui.note("[VERIF]  ... (error truncated)", "dim")
        else:
            # Also print to terminal in plain mode
            print(f"[tb round {round_idx}] tb compile failed; patching TB...")
            print("Compilation error:")
            for line in err.split('\n')[:10]:  # Show first 10 lines of error
                if line.strip():
                    print(f"  {line}")
            if len(err.split('\n')) > 10:
                print("  ... (error truncated)")
        # Include both testbench and design files for better context
        file_code_map = {tb_path.name: tb_path.read_text(encoding="utf-8", errors="ignore")}
        # Add design files to provide context
        for design_file in design_files:
            file_code_map[design_file.name] = design_file.read_text(encoding="utf-8", errors="ignore")
        patches = propose_fixes_with_llm(err, file_code_map, model=model, focus_file=tb_path.name, tui=tui)
        if not patches:
            Log.verror("LLM returned no actionable TB patches.")
            if tui: 
                tui.set_step_status("tbcompile", "failed")
                tui.note("[VERIF]LLM could not generate patches for the error:", "red")
                for line in err.split('\n')[:5]:  # Show first 5 lines of error
                    if line.strip():
                        tui.note(f"[VERIF]  {line}", "red")
            else:
                # Also print to terminal in plain mode
                print("LLM could not generate patches for the error:")
                for line in err.split('\n')[:5]:  # Show first 5 lines of error
                    if line.strip():
                        print(f"  {line}")
            return False, err
        # Apply only TB patch
        new_code = patches.get(tb_path.name)
        if new_code:
            tb_path.write_text(new_code, encoding="utf-8")
            Log.vinfo(f"Applied TB patch to {tb_path.name}")
            continue
    Log.verror("Reached maximum TB compile debug rounds; still failing.")
    if tui: 
        tui.set_step_status("tbcompile", "failed")
        tui.note("[VERIF]Final compilation error:", "red")
        # Show the last error details
        for line in err.split('\n')[:10]:  # Show first 10 lines of error
            if line.strip():
                tui.note(f"[VERIF]  {line}", "red")
        if len(err.split('\n')) > 10:
            tui.note("[VERIF]  ... (error truncated)", "dim")
    else:
        # Also print to terminal in plain mode
        print("Final compilation error:")
        for line in err.split('\n')[:10]:  # Show first 10 lines of error
            if line.strip():
                print(f"  {line}")
        if len(err.split('\n')) > 10:
            print("  ... (error truncated)")
    return False, "Max TB compile rounds reached."

def analyze_testbench_output(output: str) -> Tuple[bool, str]:
    up = output.upper()
    if "TEST PASSED" in up or "PASSED" in up.lower() or "OK" in up or "SUCCESS" in up:
        return True, "Self-check reported PASS"
    if "TEST FAILED" in up or "FAIL" in up or "ERROR" in up:
        return False, "Self-check reported FAIL/ERROR"
    # No explicit marker found -> treat as failure
    return False, "No PASS marker found in output"

def improve_testbench_from_output(tb_path: Path, tb_output: str, module_descriptions: str, model: str, design_files: List[Path] = None, tui=None) -> bool:
    """
    Ask LLM to revise ONLY the testbench to address failing checks or missing markers.
    Returns True if a patch was applied, else False.
    """
    Log.vinfo("Analyzing TB output and proposing TB-only fixes...")
    try:
        tb_code = tb_path.read_text(encoding="utf-8", errors="ignore")

        # ALWAYS include design files for comprehensive debugging
        design_context = "\n=== DESIGN UNDER TEST ===\n"
        if design_files:
            for design_file in design_files:
                if design_file.exists():
                    design_code = design_file.read_text(encoding="utf-8", errors="ignore")
                    design_context += f"\n--- {design_file.name} ---\n```verilog\n{design_code}\n```\n"

        # Analyze the output for common issues
        output_analysis = "\n=== OUTPUT ANALYSIS ===\n"
        if "TEST FAILED" in tb_output.upper():
            output_analysis += "- Test explicitly reported FAILURE\n"
        if "TEST PASSED" not in tb_output.upper() and "PASSED" not in tb_output:
            output_analysis += "- Missing PASS marker - testbench must print 'TEST PASSED' or 'TEST FAILED'\n"
        if "x" in tb_output.lower() or "z" in tb_output.lower():
            output_analysis += "- Uninitialized signals detected (X/Z values)\n"
        if "$finish" not in tb_output.lower() and "finish" not in tb_output.lower():
            output_analysis += "- Simulation may not have terminated properly\n"

        prompt = f"""You are a verification expert. Fix this failing testbench to make it robust and comprehensive.

=== CURRENT TESTBENCH ({tb_path.name}) ===
```verilog
{tb_code}
```

{design_context}

=== MODULE DESCRIPTIONS ===
{module_descriptions}

=== SIMULATION OUTPUT ===
{tb_output}

{output_analysis}

=== YOUR TASK ===
Fix ONLY the testbench file to address the issues. DO NOT modify the DUT.

Requirements:
1. Ensure proper initialization of all signals
2. Generate correct stimulus for all inputs
3. Add or fix clock generation if needed
4. Add or fix reset logic if needed
5. Verify outputs against expected values
6. Print clear test messages using $display
7. MUST print exactly "TEST PASSED" or "TEST FAILED: <reason>" at the end
8. Call $finish to end simulation
9. Make test cases comprehensive and realistic

CRITICAL:
- Keep DUT instantiation and interface intact
- Fix timing issues (add delays if needed)
- Ensure all test conditions are properly checked
- No placeholder code - make it production-ready

=== RESPONSE FORMAT ===
Return ONLY a JSON object:
{{
  "patches": [
    {{"file": "{tb_path.name}", "code": "<COMPLETE_FIXED_TESTBENCH>"}}
  ]
}}
"""
        r = llm_call(prompt, model=model, agent_name="Verification Agent", tui=tui)
        if r.startswith("Error:"):
            Log.error(f"LLM call failed: {r}")
            return False
        data = ensure_json(r)
        if not data or "patches" not in data:
            Log.vwarn("LLM did not return a JSON patch for TB improvement.")
            return False
        for p in data["patches"]:
            if p.get("file") == tb_path.name and isinstance(p.get("code"), str):
                tb_path.write_text(p["code"], encoding="utf-8")
                Log.vinfo("Applied TB improvement patch.")
                return True
        return False
    except Exception as e:
        Log.error(f"TB improvement failed: {e}")
        return False

def testbench_pipeline(module_lib_dir: Path, modules_list: List[str], top_module: str,
                       module_descriptions: str, model: str, max_tb_rounds: int,
                       tui=None) -> Tuple[bool, str, str]:
    """
    Returns (ok, output_text, reason)
    """
    # Step: generate TB
    if tui:
        tui.set_step_status("tbgen", "running")
        time.sleep(0.1)
    tb_code = generate_testbench_code(top_module, module_descriptions, model=model, module_lib_dir=module_lib_dir, tui=tui)
    tb_name = f"tb_{top_module}.v"
    tb_path = module_lib_dir / tb_name
    tb_path.write_text(tb_code, encoding="utf-8")
    Log.vsuccess(f"Wrote testbench: {tb_path}")
    if tui:
        tui.set_step_status("tbgen", "done")
        time.sleep(0.05)

    # Loop: compile -> run -> analyze -> maybe patch TB -> repeat
    for round_idx in range(1, max_tb_rounds + 1):
        # Compile TB with LIMITED attempts per iteration to avoid nested loop explosion
        # First iteration gets more attempts, subsequent iterations after improvements get fewer
        if round_idx == 1:
            compile_attempts = min(5, max_tb_rounds)  # Initial generation gets up to 5 tries
        else:
            compile_attempts = min(3, max(1, max_tb_rounds // 3))  # After improvements, limit to 3 tries

        okc, errc = compile_and_fix_testbench(tb_path, module_lib_dir, modules_list, model=model, max_rounds=compile_attempts, tui=tui)
        if not okc:
            # If compile fails after limited attempts, exit early
            Log.verror(f"TB compile failed after {compile_attempts} attempts in round {round_idx}/{max_tb_rounds}")
            if round_idx > 1:
                Log.verror("Testbench improvements introduced compile errors - exiting")
            return False, errc, "TB compile failed"

        # Run TB
        if tui:
            tui.set_step_status("tbrun", "running")
            time.sleep(0.05)
        okr, out = run_vvp("out_tb")
        if not okr:
            if tui:
                tui.set_step_status("tbrun", "failed")
            Log.verror("Failed to run testbench.")
            return False, out, "TB run failed"
        Log.vinfo("Testbench executed. Analyzing output...")
        if tui:
            tui.set_step_status("tbrun", "done")
            tui.set_step_status("tbanalyze", "running")

        # Analyze
        pass_ok, reason = analyze_testbench_output(out)
        if pass_ok:
            if tui:
                tui.set_step_status("tbanalyze", "done")
            Log.vsuccess("TESTBENCH PASS ✅ — finishing pipeline.")
            return True, out, "PASS"
        else:
            Log.vwarn(f"Testbench indicates failure or no marker ({reason}).")
            if tui:
                tui.note(f"Test failed: {reason}", "yellow")
                tui.note("Testbench output:", "white")
                # Show testbench output in logs for debugging
                for line in out.split('\n')[:20]:  # Show first 20 lines
                    if line.strip():
                        tui.note(f"  {line}", "dim")
                if len(out.split('\n')) > 20:
                    tui.note("  ... (output truncated)", "dim")
            
            if round_idx >= max_tb_rounds:
                if tui:
                    tui.set_step_status("debug", "failed")
                    tui.set_step_status("tbanalyze", "failed")
                return False, out, reason
            
            # Try to fix design code first, then testbench
            Log.vinfo("Attempting to fix design code based on testbench failure...")
            if tui:
                tui.set_step_status("debug", "running")
                tui.note("🔧 Attempting to fix design code...", "cyan")
            
            # Get all design files and try to fix them
            design_files = [module_lib_dir / f"{m}.v" for m in modules_list if (module_lib_dir / f"{m}.v").exists()]
            if design_files:
                file_code_map = {f.name: f.read_text(encoding="utf-8", errors="ignore") for f in design_files}
                patches = propose_fixes_with_llm(f"Testbench failed with output: {out}", file_code_map, model=model, tui=tui)
                
                if patches:
                    Log.vinfo(f"Applied {len(patches)} patches to design files")
                    if tui:
                        tui.note(f"Applied {len(patches)} patches to design files", "green")
                    for fname, new_code in patches.items():
                        target = module_lib_dir / fname
                        target.write_text(new_code, encoding="utf-8")
                        Log.vinfo(f"Applied patch to {fname}")
                        if tui:
                            tui.note(f"  🔧 Fixed: {fname}", "green")
                else:
                    Log.vwarn("No design patches available, trying testbench fixes...")
                    if tui:
                        tui.note("No design patches available, trying testbench fixes...", "yellow")
            
            # Ask LLM to improve ONLY the testbench based on output
            design_files = [module_lib_dir / f"{m}.v" for m in modules_list if (module_lib_dir / f"{m}.v").exists()]
            changed = improve_testbench_from_output(tb_path, out, module_descriptions, model=model, design_files=design_files, tui=tui)
            if not changed:
                if tui:
                    tui.note("Unable to auto-fix testbench", "red")
                    tui.set_step_status("tbanalyze", "failed")
                return False, out, "Unable to auto-fix TB from output"
            else:
                if tui:
                    tui.note("Applied testbench improvements", "green")
            
            # Next round
            if tui:
                tui.set_step_status("debug", "done")
                tui.set_step_status("tbanalyze", "done")
            Log.vinfo("Re-running TB after applying improvements...")
            if tui:
                tui.note(f"🔄 Retry {round_idx + 1}/{max_tb_rounds}...", "cyan")
    # Should not reach here
    return False, "Unknown TB state", "Exhausted rounds"

# --------------------------
# File Monitor for TUI
# --------------------------
class LogFileMonitor:
    """
    Monitors a log file for changes and triggers TUI updates only when content changes.
    This eliminates flickering and ensures real-time updates.
    """
    def __init__(self, log_file_path: str, tui, refresh_callback):
        self.log_file_path = log_file_path
        self.tui = tui
        self.refresh_callback = refresh_callback
        self.last_size = 0
        self.running = False
        self.thread = None
        
    def start(self):
        """Start monitoring the log file in a separate thread"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        """Stop monitoring the log file"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            
    def _monitor_loop(self):
        """Main monitoring loop - runs in separate thread"""
        while self.running:
            try:
                if os.path.exists(self.log_file_path):
                    current_size = os.path.getsize(self.log_file_path)
                    if current_size != self.last_size:
                        # File has changed, update TUI
                        self._update_tui_from_file()
                        self.last_size = current_size
                time.sleep(0.2)  # Check ~5x/sec to reduce flicker but stay snappy
            except Exception:
                pass  # Ignore errors in monitoring thread
                
    def _update_tui_from_file(self):
        """Read log file and update TUI with new content"""
        try:
            with open(self.log_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Update TUI logs with new content
            new_logs = []
            for line in lines:
                line = line.strip()
                if line:
                    # Determine style based on log level
                    if line.startswith('[INFO]'):
                        new_logs.append((line, "cyan"))
                    elif line.startswith('[WARN]'):
                        new_logs.append((line, "yellow"))
                    elif line.startswith('[ERROR]'):
                        new_logs.append((line, "red"))
                    elif line.startswith('[OK]'):
                        new_logs.append((line, "green"))
                    elif line.startswith('🔹'):
                        new_logs.append((line, "bright_white"))
                    else:
                        new_logs.append((line, "white"))
            
            # Update TUI logs (keep last MAX_LOG_LINES to avoid memory issues)
            self.tui.logs = new_logs[-MAX_LOG_LINES:]
            
            # Trigger refresh
            self.refresh_callback()
            
        except Exception:
            pass  # Ignore file read errors

# --------------------------
# TUI
# --------------------------
class TUI:
    def __init__(self, style_name: str, model: str, lib_dir: Path, max_fix_rounds: int = 5, max_tb_rounds: int = 3):
        # Jest/CI detection to avoid alternate screen & throttle updates
        self._under_jest = bool(
            os.environ.get("JEST_WORKER_ID")
            or os.environ.get("JEST") == "true"
            or os.environ.get("CI") == "true"
        )
        self._last_update = 0.0

        self.style_name = style_name
        self.palette = STYLE_PRESETS.get(style_name, STYLE_PRESETS["genmini"])
        self.model = model
        self.lib_dir = lib_dir
        self.max_fix_rounds = max_fix_rounds
        self.max_tb_rounds = max_tb_rounds
        self.logs: List[Tuple[str, str]] = []  # (text, style)

        # Animation frame counter for cycling effects (futuristic style)
        self.animation_frame = 0
        self.is_futuristic = (style_name == "futuristic")
        # Flat step list kept for compatibility; we'll render as two sections
        self.steps = [
            ("identify", "Identify modules", "pending"),
            ("describe", "Generate descriptions", "pending"),
            ("ast", "Build AST", "pending"),
            ("codegen", "Generate code", "pending"),
            ("compile", "Compile & fix (design)", "pending"),
            # Verification section steps:
            ("tbgen", "Generate testbench", "pending"),
            ("tbcompile", "Compile & fix (TB)", "pending"),
            ("tbrun", "Run testbench", "pending"),
            ("tbanalyze", "Analyze results", "pending"),
            ("debug", "Debug & fix failures", "pending"),
        ]
        # Agent tracking - consolidated into 3 main agents
        self.agents = [
            "Code Planning Agent",
            "Verification Agent",
            "Debugging Agent",
            "Memory"
        ]
        self.active_agent = None  # Currently active agent

        # Memory tracking for LLM conversation history (context window)
        self.memory_used = 0  # Total tokens/characters in LLM conversation history
        # Capacity based on model context window (rough estimate: 4 chars per token)
        # GPT-4o: ~128k tokens = ~512KB, but we'll use conservative estimate
        self.memory_capacity = 400000  # ~100k tokens worth of characters

        self.summary_lines: List[str] = []
        self.footer_help = "Press Ctrl+C to exit"
        self.live_display = None  # Will be set by the Live context
        self.file_monitor = None  # Will be set for file-based monitoring

        # Layout: Header / Body / Footer
        self.layout = Layout()
        self.layout.split(
            Layout(name="header", size=4),
            Layout(name="body"),
            Layout(name="footer", size=1)
        )
        # Body: left / right
        self.layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=2),
        )
        # Left: Steps + Agents + Verification panels (stacked)
        self.layout["left"].split(
            Layout(name="steps_panel", size=12),
            Layout(name="agents_panel", size=12),
            Layout(name="verification_panel"),
        )

    # ----- Animation Helpers -----
    def get_cycling_color(self):
        """Get cycling border color for futuristic animations"""
        if not self.is_futuristic:
            return self.palette["border"]

        # Cycle through neon colors
        colors = [self.palette["glow1"], self.palette["glow2"], self.palette["glow3"], self.palette["accent"]]
        idx = (self.animation_frame // 2) % len(colors)  # Change every 2 frames
        return colors[idx]

    def get_active_glow_color(self):
        """Get pulsing glow color for active elements"""
        if not self.is_futuristic:
            return self.palette["accent"]

        # Pulse between bright cyan and magenta
        colors = [self.palette["accent"], self.palette["primary"], self.palette["neon"]]
        idx = (self.animation_frame // 3) % len(colors)  # Slower pulse
        return colors[idx]

    # ----- Panels -----
    def header_panel(self):
        if self.is_futuristic:
            # Futuristic header with gradient-like effect
            title = Text()
            title.append(" MAHLT ", style=f"bold {self.get_cycling_color()}")
            title.append("— ", style="dim")
            title.append("Terminal Coder ", style=f"bold {self.palette['accent']}")

            subtitle = Text()
            subtitle.append("Model: ", style="dim")
            subtitle.append(self.model, style=self.palette["primary"])
            subtitle.append("   Output: ", style="dim")
            subtitle.append(str(self.lib_dir), style=self.palette["accent"])
        else:
            title = Text(" MAHL — Terminal Coder ", style=self.palette["primary"], justify="left")
            subtitle = Text(f"Model: {self.model}   Output: {self.lib_dir}", style=self.palette["accent"])

        # Debug rounds info
        fix_rounds_text = "no debugging" if self.max_fix_rounds == 0 else f"{self.max_fix_rounds} rounds"
        tb_rounds_text = "no debugging" if self.max_tb_rounds == 0 else f"{self.max_tb_rounds} rounds"

        if self.is_futuristic:
            debug_info = Text()
            debug_info.append("Debug: ", style="dim")
            debug_info.append(f"Design={fix_rounds_text}, TB={tb_rounds_text}", style=self.palette["glow2"])
        else:
            debug_info = Text(f"Debug: Design={fix_rounds_text}, TB={tb_rounds_text}", style=self.palette["accent"])

        # Current step indicator
        running = next(((k, label) for k, label, status in self.steps if status == "running"), None)
        if running:
            if self.is_futuristic:
                current_step_text = Text()
                current_step_text.append("Current: ", style="dim")
                current_step_text.append(running[1], style=self.get_active_glow_color())
            else:
                current_step_text = Text(f"Current: {running[1]}", style=self.palette["accent"])
        else:
            current_step_text = Text("Current: Ready", style=self.palette["accent"])

        block = Align.left(Text.assemble(title, "\n", subtitle, "\n", debug_info, "\n", current_step_text))

        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(block, border_style=border_style, box=border_box)

    def steps_table(self):
        """General build steps (pre-verification)."""
        t = Table.grid(padding=(0,1))
        t.add_column(justify="left", ratio=3)
        t.add_column(justify="right", ratio=1)
        general_keys = {"identify","describe","ast","codegen","compile"}
        for key, label, status in self.steps:
            if key not in general_keys:
                continue
            icon = {"pending":"•","running":"⟲","done":"✔","failed":"✖"}.get(status, "•")

            # Futuristic status styling
            if self.is_futuristic:
                status_colors = {
                    "pending": "dim",
                    "running": self.get_active_glow_color(),
                    "done": self.palette["glow2"],
                    "failed": "red"
                }
                status_style = status_colors[status]
            else:
                status_style = {"pending":"dim","running":self.palette["accent"],"done":"green","failed":"red"}[status]

            t.add_row(f"{icon} {label}", Text(status.upper(), style=status_style))

        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(t, title="Steps", border_style=border_style, box=border_box)

    def verification_table(self):
        """Verification steps (testbench pipeline)."""
        t = Table.grid(padding=(0,1))
        t.add_column(justify="left", ratio=3)
        t.add_column(justify="right", ratio=1)
        for key, label, status in self.steps:
            if not key.startswith("tb") and key != "debug":
                continue
            icon = {"pending":"•","running":"⟲","done":"✔","failed":"✖"}.get(status, "•")

            # Futuristic status styling
            if self.is_futuristic:
                status_colors = {
                    "pending": "dim",
                    "running": self.get_active_glow_color(),
                    "done": self.palette["glow2"],
                    "failed": "red"
                }
                status_style = status_colors[status]
            else:
                status_style = {"pending":"dim","running":self.palette["accent"],"done":"green","failed":"red"}[status]

            t.add_row(f"{icon} {label}", Text(status.upper(), style=status_style))

        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(t, title="Verification", border_style=border_style, box=border_box)

    def agents_panel(self):
        """Agent boxes showing which agent is currently active."""
        from rich.columns import Columns
        from rich.progress import Progress, BarColumn, TextColumn
        from rich.console import Group

        agent_boxes = []
        for agent in self.agents:
            is_active = (agent == self.active_agent)

            # Special handling for Memory agent - show LLM context history progress bar
            if agent == "Memory":
                # Calculate memory percentage (context window usage)
                mem_percent = min(100, int((self.memory_used / self.memory_capacity) * 100))
                mem_kb = self.memory_used / 1024
                capacity_kb = self.memory_capacity / 1024

                # Estimate token count (rough: 4 chars per token)
                tokens_estimate = self.memory_used // 4
                tokens_capacity = self.memory_capacity // 4

                # Create progress bar with cycling colors for futuristic style
                if self.is_futuristic and mem_percent > 30:
                    bar_style = self.get_cycling_color()
                else:
                    bar_style = "cyan" if mem_percent > 50 else "dim"

                progress = Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=None, complete_style=bar_style),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    expand=True
                )
                task_id = progress.add_task("", total=100, completed=mem_percent)

                # Memory info text with futuristic styling
                if self.is_futuristic:
                    mem_info = Text()
                    mem_info.append("🧠 ", style="bright_white")
                    mem_info.append("Context: ", style=self.get_cycling_color())
                    mem_info.append(f"{mem_kb:.1f}KB ", style="bright_white")
                    mem_info.append(f"(~{tokens_estimate:,} tokens)", style="dim bright_cyan")
                else:
                    mem_info = Text(f"🧠 Context: {mem_kb:.1f}KB (~{tokens_estimate:,} tokens)",
                                   style="cyan" if mem_percent > 50 else "dim")

                box_content = Group(mem_info, progress)

                # Futuristic border style
                if self.is_futuristic:
                    border_style = self.get_cycling_color() if mem_percent > 30 else "dim"
                    border_box = box.DOUBLE if mem_percent > 50 else box.ROUNDED
                else:
                    border_style = "cyan" if mem_percent > 50 else "dim"
                    border_box = box.ROUNDED if mem_percent <= 50 else box.HEAVY

                agent_box = Panel(
                    box_content,
                    title="LLM Memory",
                    border_style=border_style,
                    box=border_box,
                    padding=(0, 1)
                )
            else:
                # Regular agent boxes
                if is_active:
                    # Futuristic pulsing glow when active
                    if self.is_futuristic:
                        glow_color = self.get_active_glow_color()
                        box_content = Text()
                        box_content.append("⚡ ", style="bright_yellow")
                        box_content.append(agent, style=f"bold {glow_color}")
                        agent_box = Panel(
                            box_content,
                            border_style=glow_color,
                            box=box.DOUBLE,
                            padding=(0, 1)
                        )
                    else:
                        # Standard highlighted box when active
                        box_content = Text(f"⚡ {agent}", style=f"bold {self.palette['accent']}")
                        agent_box = Panel(
                            box_content,
                            border_style=self.palette["accent"],
                            box=box.HEAVY,
                            padding=(0, 1)
                        )
                else:
                    # Dimmed box when inactive
                    box_content = Text(f"  {agent}", style="dim")
                    agent_box = Panel(
                        box_content,
                        border_style="dim",
                        box=box.ROUNDED,
                        padding=(0, 1)
                    )
            agent_boxes.append(agent_box)

        # Arrange in a grid (2 columns)
        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_column()
        for i in range(0, len(agent_boxes), 2):
            if i + 1 < len(agent_boxes):
                grid.add_row(agent_boxes[i], agent_boxes[i+1])
            else:
                grid.add_row(agent_boxes[i], "")

        # Futuristic border for outer panel
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(grid, title="Active Agents", border_style=border_style, box=border_box)

    def log_panel(self):
        from rich.padding import Padding
        from rich.console import Group

        # Decide whether to show verification-only logs
        verification_active = any(
            status in ("running", "done", "failed")
            for key, _label, status in self.steps if key.startswith("tb")
        )
        # Filter logs if verification section active
        if not self.logs:
            txt = Text("Logs will appear here...", style="dim")
            content = txt
        else:
            # Filter to verification-only if active and any exist
            source = self.logs
            if verification_active:
                verif_only = [ls for ls in source if "[VERIF]" in ls[0]]
                if verif_only:
                    source = verif_only

            # Show only the last MAX_DISPLAY_LINES for better performance and auto-scroll
            display_logs = source[-MAX_DISPLAY_LINES:]

            # Add indicator at the top if messages are hidden
            elements = []
            if len(source) > MAX_DISPLAY_LINES:
                header = Text(f"⋮ ({len(source) - MAX_DISPLAY_LINES} earlier messages hidden) ⋮", style="dim italic", justify="center")
                elements.append(header)

            # Build text with newest at the bottom (most recent lines)
            txt = Text(overflow="fold", no_wrap=False)
            for line, style in display_logs:
                txt.append(line, style=style)
                txt.append("\n")
            elements.append(txt)

            # Group elements and align to bottom
            content = Group(*elements) if len(elements) > 1 else txt

        # Anchor to bottom so newest lines are visible (auto-scroll behavior)
        title = "Verification Log" if verification_active else "Live Log"

        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(
            Align(content, align="left", vertical="bottom"),
            title=title,
            border_style=border_style,
            box=border_box
        )

    def footer_panel(self):
        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        footer_text = Text(self.footer_help, style="dim")
        return Panel(footer_text, border_style=border_style, box=border_box)

    def summary_panel(self):
        if not self.summary_lines: return None
        txt = Text()
        for line in self.summary_lines:
            txt.append(line + "\n")

        # Futuristic border
        border_style = self.get_cycling_color() if self.is_futuristic else self.palette["border"]
        border_box = box.DOUBLE if self.is_futuristic else box.ROUNDED

        return Panel(txt, title="Summary", border_style=border_style, box=border_box)

    # ----- Render & update -----
    def render(self):
        self.layout["header"].update(self.header_panel())
        self.layout["steps_panel"].update(self.steps_table())
        self.layout["agents_panel"].update(self.agents_panel())
        self.layout["verification_panel"].update(self.verification_table())
        # Right: either log or summary if present
        if self.summary_lines:
            self.layout["right"].update(self.summary_panel())
        else:
            self.layout["right"].update(self.log_panel())
        self.layout["footer"].update(self.footer_panel())
        return self.layout

    def set_step_status(self, step_key: str, status: str):
        for i, (k, label, _s) in enumerate(self.steps):
            if k == step_key:
                self.steps[i] = (k, label, status)
                break
        # Immediately refresh so the panels update in-place
        self.refresh()

    def set_active_agent(self, agent_name: Optional[str]):
        """Set the currently active agent (or None to clear)"""
        self.active_agent = agent_name
        self.refresh()

    def add_memory(self, text: str):
        """Add text to LLM conversation history tracking (cumulative context window)"""
        self.memory_used += len(text.encode('utf-8'))
        # Expand capacity if needed (simulates dynamic context window)
        if self.memory_used > self.memory_capacity:
            self.memory_capacity = int(self.memory_used * 1.2)  # Expand capacity by 20%
        self.refresh()

    def note(self, msg: str, style: str = "white"):
        self.logs.append((msg, style))
        # Keep only the last MAX_LOG_LINES to prevent memory issues
        if len(self.logs) > MAX_LOG_LINES:
            self.logs = self.logs[-MAX_LOG_LINES:]
        self.refresh()
    
    def refresh(self):
        """Manually refresh the display and re-render panels (Jest-safe throttle)"""
        if not self.live_display:
            return
        if self._under_jest:
            now = time.time()
            if now - self._last_update < 0.25:
                return
            self._last_update = now

        # Increment animation frame for cycling effects
        self.animation_frame = (self.animation_frame + 1) % 100

        # Rebuild panels and let Live's refresh_per_second handle the draw
        self.live_display.update(self.render())
    
    def start_file_monitoring(self, log_file_path: str):
        """Start file-based monitoring for real-time updates"""
        self.file_monitor = LogFileMonitor(log_file_path, self, self.refresh)
        self.file_monitor.start()
    
    def stop_file_monitoring(self):
        """Stop file-based monitoring"""
        if self.file_monitor:
            self.file_monitor.stop()
            self.file_monitor = None

# --------------------------
# Orchestration
# --------------------------
def run_pipeline(user_prompt: str, model: str, lib: str, max_fix_rounds: int, max_tb_rounds: int, tui: Optional[TUI] = None):
    try:
        # Clean lib directory to avoid stale files from previous runs
        lib_dir = Path(lib)
        if lib_dir.exists():
            Log.info(f"Cleaning lib directory: {lib_dir}")
            for file in lib_dir.glob("*.v"):
                file.unlink()
        lib_dir.mkdir(exist_ok=True)
    
        # Step 1: Identify modules
        if tui:
            tui.set_step_status("identify", "running")
            time.sleep(0.2)
        modules = None
        for _ in range(3):
            modules = user_prompt_projection(user_prompt, model=model, tui=tui)
            if modules: break
        if not modules:
            if tui: tui.set_step_status("identify", "failed")
            return False, {"error":"Failed to identify modules"}
        if tui: 
            tui.set_step_status("identify", "done")
            time.sleep(0.1)

        # Step 2: Descriptions + split
        if tui:
            tui.set_step_status("describe", "running")
            time.sleep(0.05)
        descr = generate_module_description(project_definition=user_prompt, modules_list=modules, model=model, tui=tui)
        lines = descr.splitlines()
        blocks = split_module(lines)
        attempts = 0
        while len(blocks) != len(modules) and attempts < 2:
            Log.warn(f"Module blocks ({len(blocks)}) != list length ({len(modules)}). Regenerating...")
            descr = generate_module_description(project_definition=user_prompt, modules_list=modules, model=model, tui=tui)
            blocks = split_module(descr.splitlines())
            attempts += 1
        if len(blocks) == 0:
            if tui: tui.set_step_status("describe", "failed")
            return False, {"error":"Failed to produce module descriptions"}
        if tui: 
            tui.set_step_status("describe", "done")
            time.sleep(0.05)

        # Step 3: AST
        if tui:
            tui.set_step_status("ast", "running")
            time.sleep(0.05)
        ast = generate_ast(descr, model=model, tui=tui) or {}
        artifacts = Path("artifacts"); artifacts.mkdir(exist_ok=True)
        (artifacts / "ast.json").write_text(json.dumps(ast, indent=2), encoding="utf-8")
        Log.info(f"AST saved to {artifacts / 'ast.json'}")
        if tui: 
            tui.set_step_status("ast", "done")
            time.sleep(0.05)

        # Step 4: Codegen files
        if tui:
            tui.set_step_status("codegen", "running")
            time.sleep(0.05)
        for i, block in enumerate(blocks):
            hdr = block.strip().splitlines()[0]
            name = hdr.split(":", 1)[1].strip() if ":" in hdr else None
            if not name:
                Log.warn(f"Could not parse module name from header: {hdr}")
                continue
            target = lib_dir / f"{name}.v"
            if target.exists():
                Log.info(f"Skipping existing module: {name}")
            else:
                Log.info(f"Generating module {i+1}/{len(blocks)}: {name}")
                code = generate_module_code(name, block, model=model, tui=tui)
                target.write_text(code, encoding="utf-8")
                Log.success(f"Wrote {target}")
                if tui:
                    time.sleep(0.05)
        if tui: 
            tui.set_step_status("codegen", "done")
            time.sleep(0.05)

        # Step 5: Compile & fix (design)
        ok, err = compile_and_fix(lib_dir, modules, model=model, max_rounds=max_fix_rounds, tui=tui)
        if not ok:
            result = {"modules": modules, "lib_dir": str(lib_dir), "ok": False, "error": err}
            return False, result

        # Step 6~9: Testbench pipeline (Verification section)
        top = detect_top_module(ast, modules, lib_dir)
        Log.vinfo(f"Detected top module: {top}")
        ok_tb, tb_output, tb_reason = testbench_pipeline(
            lib_dir, modules, top, descr, model=model, max_tb_rounds=max_tb_rounds, tui=tui
        )

        result = {
            "modules": modules,
            "lib_dir": str(lib_dir),
            "ok": ok_tb,
            "tb_reason": tb_reason,
            "tb_output": tb_output[:4000] if isinstance(tb_output, str) else tb_output
        }
        return ok_tb, result
    except Exception as e:
        Log.error(f"Pipeline failed with unexpected error: {e}")
        if tui:
            # Update all steps to failed status
            for i, (k, label, _s) in enumerate(tui.steps):
                tui.steps[i] = (k, label, "failed")
        return False, {"error": f"Pipeline failed: {e}", "lib_dir": str(lib_dir) if 'lib_dir' in locals() else lib}

def main():
    parser = argparse.ArgumentParser(description="MAHLT — Terminal Coder (TUI)")
    parser.add_argument("user_prompt", nargs="?", help="Describe the design to generate")
    parser.add_argument("--style", choices=list(STYLE_PRESETS.keys()), default="futuristic")
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "openai"),
                        help="openai (uses $OPENAI_API_KEY) or an Ollama model name (e.g., llama3.3)")
    parser.add_argument("--lib", default="module_lib", help="Output directory for .v files")
    parser.add_argument("--max-fix-rounds", type=int, default=None, help="Max rounds for design compile/fix (0 = no debugging)")
    parser.add_argument("--max-tb-rounds", type=int, default=None, help="Max rounds for testbench fix based on output (0 = no debugging)")
    parser.add_argument("--no-tui", action="store_true", help="Disable Rich TUI and use plain logs")
    args = parser.parse_args()

    # Acquire prompt if not provided
    prompt = args.user_prompt
    if not prompt:
        print("Enter your design request (single line). Press Ctrl+C to cancel.")
        try:
            prompt = input("> ").strip()
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(130)
        except EOFError:
            print("\nNo input available. Please provide a prompt as an argument.")
            print("Usage: python gen_tui.py \"your design request\"")
            sys.exit(1)
        if not prompt:
            print("Empty prompt.")
            sys.exit(1)
    
    if not args.model:
        print("Available local models: gpt-oss, llama3.3")
        print("Supported Online Models: openai")
        print("Enter your desired model:")
        args.model = input("> ").strip()
        if not args.model:
            print("Empty model.")
            sys.exit(1)
    else:
        print(f"Using model: {args.model}")

    # Prompt for max_fix_rounds if not provided
    if args.max_fix_rounds is None:
        print("Enter max fix rounds for design compilation (0 = no debugging, default: 10):")
        try:
            fix_rounds_input = input("> ").strip()
            args.max_fix_rounds = int(fix_rounds_input) if fix_rounds_input else 10
        except ValueError:
            print("Invalid input, using default: 10")
            args.max_fix_rounds = 10
    else:
        print(f"Using max fix rounds: {args.max_fix_rounds}")

    # Prompt for max_tb_rounds if not provided
    if args.max_tb_rounds is None:
        print("Enter max testbench rounds (0 = no debugging, default: 10):")
        try:
            tb_rounds_input = input("> ").strip()
            args.max_tb_rounds = int(tb_rounds_input) if tb_rounds_input else 10
        except ValueError:
            print("Invalid input, using default: 10")
            args.max_tb_rounds = 10
    else:
        print(f"Using max testbench rounds: {args.max_tb_rounds}")

    use_tui = _USE_RICH and not args.no_tui
    if not use_tui:
        # Plain run
        Log.info("Launching in plain mode (Rich TUI disabled or unavailable).")
        ok, result = run_pipeline(prompt, args.model, args.lib, args.max_fix_rounds, args.max_tb_rounds, tui=None)
        if ok:
            Log.success(f"Done. Files in: {result['lib_dir']}")
            sys.exit(0)
        else:
            Log.error(f"Failed: {result.get('error') or result.get('tb_reason') or 'unknown error'}")
            # Print TB output if present
            if result.get("tb_output"):
                print("\n--- Testbench Output ---\n")
                print(result["tb_output"])
            sys.exit(2)

    # TUI mode with file-based logging
    try:
        tui = TUI(style_name=args.style, model=args.model, lib_dir=Path(args.lib), 
                  max_fix_rounds=args.max_fix_rounds, max_tb_rounds=args.max_tb_rounds)

        # Set up logging sink to use TUI's note method for rolling
        Log.attach_sink(lambda msg, style: tui.note(msg, style))

        # Set up file-based logging
        log_file_path = "tui_log.txt"
        Log.set_log_file(log_file_path)

        # Jest/CI-safe console: avoid alt-screen & color, reduce flicker
        under_jest = bool(
            os.environ.get("JEST_WORKER_ID")
            or os.environ.get("JEST") == "true"
            or os.environ.get("CI") == "true"
        )
        use_screen = sys.stdout.isatty() and not under_jest
        console_local = Console(
            force_terminal=use_screen,
            no_color=under_jest,
            soft_wrap=True,
        ) if _USE_RICH else None

        try:
            with Live(
                tui.render(),
                console=console_local if console_local else console,
                refresh_per_second=10,
                screen=use_screen,      # no alternate screen under Jest/CI
                transient=False    # keep persistent output when not using alt screen
            ) as live:
                tui.live_display = live  # Store reference for manual refresh
                
                # Start file monitoring for real-time updates
                tui.start_file_monitoring(log_file_path)
                
                # Run pipeline
                ok, result = run_pipeline(prompt, args.model, args.lib, args.max_fix_rounds, args.max_tb_rounds, tui=tui)

                # Stop file monitoring
                tui.stop_file_monitoring()
                
                # Prepare summary
                tui.summary_lines.append(colored("Result", "white"))
                status = "SUCCESS ✅" if ok else "FAILED ❌"
                tui.summary_lines.append(f"Status: {status}")
                tui.summary_lines.append(f"Output dir: {result.get('lib_dir','(n/a)')}")
                if "modules" in result:
                    tui.summary_lines.append(f"Modules: {result['modules']}")
                if "tb_reason" in result:
                    tui.summary_lines.append(f"Testbench: {result['tb_reason']}")
                if result.get("tb_output"):
                    tui.summary_lines.append("TB Output (truncated):")
                    tb_out_preview = (result["tb_output"][:800] + ("..." if len(result["tb_output"]) > 800 else ""))
                    tui.summary_lines.append(tb_out_preview)
                if not ok and "error" in result:
                    tui.summary_lines.append("Error:\n" + (result.get("error", "") or "(no message)"))
                # Redraw summary
                tui.render()

                # Keep UI visible; wait for Enter inside the Live context
                tui.footer_help = "Press Enter to exit"
                tui.render()
                try:
                    sys.stdin.readline()
                except Exception:
                    pass

                # Clean up log file
                try:
                    os.remove(log_file_path)
                except Exception:
                    pass
            # (Already waiting for Enter inside Live; no extra prompt here)
        except Exception as tui_error:
            print(f"TUI display failed: {tui_error}")
            print("Falling back to plain mode...")
            # Fall back to plain mode
            Log.attach_sink(None)  # Clear the sink
            ok, result = run_pipeline(prompt, args.model, args.lib, args.max_tb_rounds, args.max_tb_rounds, tui=None)
            if ok:
                Log.success(f"Done. Files in: {result['lib_dir']}")
                sys.exit(0)
            else:
                Log.error(f"Failed: {result.get('error') or result.get('tb_reason') or 'unknown error'}")
                if result.get("tb_output"):
                    print("\n--- Testbench Output ---\n")
                    print(result["tb_output"])
                sys.exit(2)
    except Exception as e:
        print(f"TUI mode failed: {e}")
        print("Falling back to plain mode...")
        # Fall back to plain mode
        Log.attach_sink(None)  # Clear the sink
        ok, result = run_pipeline(prompt, args.model, args.lib, args.max_tb_rounds, args.max_tb_rounds, tui=None)
        if ok:
            Log.success(f"Done. Files in: {result['lib_dir']}")
            sys.exit(0)
        else:
            Log.error(f"Failed: {result.get('error') or result.get('tb_reason') or 'unknown error'}")
            if result.get("tb_output"):
                print("\n--- Testbench Output ---\n")
                print(result["tb_output"])
            sys.exit(2)

if __name__ == "__main__":
    main()
