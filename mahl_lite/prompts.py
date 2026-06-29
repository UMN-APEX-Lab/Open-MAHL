"""Prompt templates.

All prompts live as plain ``.txt`` files under the top-level ``prompts/`` directory
so users can edit them without touching Python. Placeholders use a ``{{name}}``
syntax and are filled by simple text substitution — so the JSON examples and
Verilog ``$display`` calls inside a prompt are left completely untouched (unlike
``str.format``, which would choke on every brace and ``$``).

    render("codegen", module_name="mux2_1", module_description=desc)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .errors import ConfigError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_]\w*)\}\}")


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise ConfigError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render(name: str, **kwargs) -> str:
    """Fill ``{{placeholders}}`` in prompt ``name`` with the given values."""
    text = load_prompt(name)
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", str(value))
    missing = sorted(set(_PLACEHOLDER.findall(text)))
    if missing:
        raise ConfigError(f"prompt {name!r} has unfilled placeholders: {missing}")
    return text
