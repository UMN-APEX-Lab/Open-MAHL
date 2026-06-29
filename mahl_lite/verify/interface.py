"""Extract module interfaces (ports + widths) and infer the top module.

Two jobs:

* :func:`extract_ports` — the precise port list (name, direction, bit width) a
  cross-validation harness needs to drive a DUT. Regex-based by default; if
  ``pyverilog`` is installed it is used to *cross-check* the port set and a
  mismatch is logged (pyverilog is a parser, not a simulator, so it only validates
  here — it never runs anything).
* :func:`detect_top_module` — pick the design's root from the AST, the declared
  module list, or, failing those, the instantiation graph parsed from sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..log import Log
from ..parsing import first_key

# module name #(...) ( ports ) ; body endmodule
MODULE_DECL_RE = re.compile(
    r"(?ms)^\s*module\s+([a-zA-Z_]\w*)\s*(?:#\s*\(.*?\)\s*)?\(\s*(.*?)\s*\)\s*;(?P<body>.*?)(?:endmodule\b)"
)
PORT_LINE_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:reg|wire|logic)?\s*(\[[^\]]+\])?\s*([\w$, \t]+)\s*;"
)
_RANGE_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")

VERILOG_PRIMITIVES = {
    "and", "nand", "nor", "or", "xor", "xnor", "buf", "not",
    "bufif0", "bufif1", "notif0", "notif1", "nmos", "pmos", "cmos",
    "rnmos", "rpmos", "rcmos", "tran", "rtran", "tranif0", "tranif1",
    "rtranif0", "rtranif1", "pulldown", "pullup", "tri", "tri0", "tri1",
    "triand", "trior", "wand", "wor", "supply0", "supply1",
}


@dataclass
class Port:
    name: str
    direction: str           # "input" | "output" | "inout"
    width: Optional[int]     # bit width if known from a constant range, else None
    msb: Optional[int] = None
    lsb: Optional[int] = None

    @property
    def width_known(self) -> bool:
        return self.width is not None


def _is_dut_name(name: str) -> bool:
    ln = name.lower()
    return name not in VERILOG_PRIMITIVES and not (ln.startswith("tb_") or ln.endswith("_tb"))


_DIR_SPLIT = re.compile(r"\b(input|output|inout)\b")
_NAME_RE = re.compile(r"[a-zA-Z_]\w*")
_TYPE_KW_RE = re.compile(r"\b(reg|wire|logic|signed|var)\b")


def _width_from_decl(decl_text: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(width, msb, lsb) for one declaration chunk.

    No ``[..]`` -> scalar (width 1). Constant ``[m:l]`` -> that width. A range that
    isn't a pair of integer literals (e.g. ``[WIDTH-1:0]``) -> unknown (None).
    """
    if "[" not in decl_text:
        return 1, 0, 0
    m = _RANGE_RE.search(decl_text)
    if not m:
        return None, None, None
    msb, lsb = int(m.group(1)), int(m.group(2))
    return abs(msb - lsb) + 1, msb, lsb


def _names_in(decl_text: str) -> List[str]:
    t = _RANGE_RE.sub(" ", decl_text)        # numeric ranges
    t = re.sub(r"\[[^\]]*\]", " ", t)        # any remaining (parameterized) ranges
    t = _TYPE_KW_RE.sub(" ", t)              # reg/wire/logic/signed/var
    return _NAME_RE.findall(t)


def _ports_from_header(header: str) -> Dict[str, Port]:
    """Parse ANSI-style ports from a module header (commas/newlines, no `;`)."""
    ports: Dict[str, Port] = {}
    tokens = _DIR_SPLIT.split(header)        # [pre, dir, decl, dir, decl, ...]
    i = 1
    while i + 1 < len(tokens) + 1 and i < len(tokens):
        direction = tokens[i]
        decl = tokens[i + 1] if i + 1 < len(tokens) else ""
        width, msb, lsb = _width_from_decl(decl)
        for nm in _names_in(decl):
            ports[nm] = Port(nm, direction, width, msb, lsb)
        i += 2
    return ports


def _ports_from_body(body: str) -> Dict[str, Port]:
    """Parse non-ANSI `;`-terminated port declarations from a module body."""
    ports: Dict[str, Port] = {}
    for pl in PORT_LINE_RE.finditer(body):
        direction, range_str, names = pl.groups()
        width, msb, lsb = _width_from_decl(range_str or "")
        for nm in (n.strip() for n in names.split(",")):
            if nm:
                ports[nm] = Port(nm, direction, width, msb, lsb)
    return ports


def _ports_from_match(header: str, body: str) -> Dict[str, Port]:
    ports = _ports_from_body(body)           # non-ANSI declarations (if any)
    ports.update(_ports_from_header(header))  # ANSI header wins on overlap
    return ports


def extract_ports_from_text(text: str, module_name: str) -> Dict[str, Port]:
    for m in MODULE_DECL_RE.finditer(text):
        if m.group(1) == module_name:
            return _ports_from_match(m.group(2), m.group("body"))
    return {}


def extract_ports(lib_dir: Path, module_name: str) -> Dict[str, Port]:
    path = lib_dir / f"{module_name}.v"
    if not path.exists():
        return {}
    return extract_ports_from_text(path.read_text(encoding="utf-8", errors="ignore"), module_name)


# ----- hierarchy / top-module detection -----
def parse_modules_from_dir(lib_dir: Path) -> Dict[str, dict]:
    mods: Dict[str, dict] = {}
    for p in lib_dir.glob("*.v"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in MODULE_DECL_RE.finditer(txt):
            name, header, body = m.group(1), m.group(2), m.group("body")
            mods[name] = {
                "ports": _ports_from_match(header, body),
                "body": body,
                "instantiates": set(),
            }
    names = [n for n in mods if _is_dut_name(n)]
    if names:
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
    allm = {n for n in mods if _is_dut_name(n)}
    if not allm:
        return None
    inst = {c for info in mods.values() for c in info["instantiates"] if _is_dut_name(c)}
    roots = list(allm - inst) or list(allm)
    for m in modules_list:           # prefer an LLM-declared root if it is present
        if m in roots:
            return m
    roots.sort(key=lambda n: (len(mods[n]["instantiates"]), len(mods[n]["ports"])), reverse=True)
    return roots[0]


def detect_top_module(ast_json: Optional[dict], modules_list: List[str], lib_dir: Path) -> str:
    if isinstance(ast_json, dict):
        root = first_key(ast_json)
        if root and _is_dut_name(root):
            Log.vinfo(f"Top module from AST: {root}")
            return root
    for module in modules_list:
        if module and _is_dut_name(module):
            Log.vinfo(f"Top module from modules list: {module}")
            return module
    try:
        top = _infer_top_from_mods(parse_modules_from_dir(lib_dir), modules_list)
        if top:
            Log.vinfo(f"Top module parsed from sources: {top}")
            return top
    except Exception as e:
        Log.vwarn(f"Top parser failed; using fallback ({e})")
    Log.vwarn("Could not determine top module, using 'top' as fallback")
    return "top"


# ----- optional pyverilog validation -----
def pyverilog_available() -> bool:
    try:
        import pyverilog.vparser.parser  # noqa: F401
        return True
    except Exception:
        return False


def pyverilog_port_names(text: str, module_name: str) -> Optional[set]:
    """Authoritative port-name set via pyverilog, or ``None`` if it is unavailable.

    pyverilog is a parser (not a simulator); we use it only to double-check the
    regex result. Its noisy LALR/stderr chatter is suppressed.
    """
    try:
        import os
        import tempfile
        from contextlib import redirect_stderr
        from pyverilog.vparser.parser import parse as pv_parse
    except Exception:
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
            f.write(text)
            tmp = f.name
        with open(os.devnull, "w") as devnull, redirect_stderr(devnull):
            ast, _ = pv_parse([tmp])
        return set(_iter_pyverilog_ports(ast, module_name))
    except Exception as e:
        Log.vwarn(f"pyverilog parse skipped for {module_name}: {e}")
        return None
    finally:
        if tmp:
            try:
                import os
                os.remove(tmp)
            except OSError:
                pass


def validate_ports(text: str, module_name: str, ports: Dict[str, Port]) -> None:
    """Warn (do not fail) if pyverilog's port set disagrees with the regex one."""
    pv = pyverilog_port_names(text, module_name)
    if pv and pv != set(ports):
        Log.vwarn(
            f"port mismatch for {module_name}: only-regex={set(ports) - pv}, "
            f"only-pyverilog={pv - set(ports)}"
        )


def _iter_pyverilog_ports(node, module_name: str):
    from pyverilog.vparser.ast import ModuleDef, Ioport, Decl, Input, Output, Inout
    for child in getattr(node, "children", lambda: [])():
        if isinstance(child, ModuleDef) and child.name == module_name:
            for item in child.children():
                for sub in getattr(item, "children", lambda: [])():
                    if isinstance(sub, Ioport) and sub.first is not None:
                        yield sub.first.name
                    elif isinstance(sub, (Input, Output, Inout)):
                        yield sub.name
        else:
            yield from _iter_pyverilog_ports(child, module_name)
