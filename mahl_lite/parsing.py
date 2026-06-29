"""Pure helpers for pulling structured data out of free-form LLM responses.

These never call the network. They are deliberately forgiving: models wrap output
in prose, fenced blocks, or stray text, so each helper tries a few strategies
before giving up. The one place we used to call ``eval()`` on model output is now
:func:`parse_module_list`, which uses :func:`ast.literal_eval` instead.
"""

from __future__ import annotations

import ast
import json
import re
from typing import List, Optional, Tuple

from .errors import ParseError

_CODE_FENCE = re.compile(r"```\s*(\w+)?\s*\n(.*?)```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", re.DOTALL)
_LIST_LITERAL = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Drop a leading ``<think>...</think>`` reasoning block (some local models emit it)."""
    if "<think>" in text and "</think>" in text:
        return text.split("</think>", 1)[1]
    return text


def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Return ``[(language, code), ...]`` for every fenced block, in order."""
    text = strip_think_blocks(text)
    blocks: List[Tuple[str, str]] = []
    for m in _CODE_FENCE.finditer(text):
        lang = (m.group(1) or "").strip()
        code = m.group(2).strip()
        if code:
            blocks.append((lang, code))
    return blocks


def extract_first_code(text: str) -> str:
    """First fenced block's body, or the whole (think-stripped) text if none."""
    blocks = extract_code_blocks(text)
    if blocks:
        return blocks[0][1]
    return strip_think_blocks(text).strip()


def ensure_json(s: str) -> Optional[dict]:
    """Best-effort JSON object extraction. Returns ``None`` if nothing parses."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        pass
    for lang, code in extract_code_blocks(s):
        if lang.lower() in ("json", ""):
            try:
                return json.loads(code)
            except json.JSONDecodeError:
                pass
    for match in _JSON_OBJECT.finditer(s):
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(extract_first_code(s))
    except (json.JSONDecodeError, TypeError):
        return None


def parse_module_list(text: str) -> List[str]:
    """Parse a Python-style list of module names, e.g. ``['mux4_1', 'mux2_1']``.

    Replaces the old ``eval()`` call. Raises :class:`ParseError` if no list is found.
    """
    candidate = extract_first_code(text)
    value = None
    try:
        value = ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        data = ensure_json(text)
        if isinstance(data, list):
            value = data
        elif isinstance(data, dict):
            # tolerate {"modules": [...]} shapes
            for v in data.values():
                if isinstance(v, list):
                    value = v
                    break
        if value is None:
            # last resort: find a bracketed list literal anywhere in the text
            for m in _LIST_LITERAL.finditer(candidate + "\n" + text):
                try:
                    parsed = ast.literal_eval(m.group(0))
                except (ValueError, SyntaxError):
                    continue
                if isinstance(parsed, (list, tuple)):
                    value = parsed
                    break
    if not isinstance(value, (list, tuple)):
        raise ParseError(f"could not parse a module list from LLM response:\n{text[:500]}")
    return [str(x).strip() for x in value if str(x).strip()]


def split_module(lines: List[str]) -> List[str]:
    """Split a markdown description into per-module blocks keyed by ``module:``/``topmodule:``."""
    out: List[str] = []
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
