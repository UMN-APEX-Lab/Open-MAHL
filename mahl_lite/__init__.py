"""MAHL-Lite — a lightweight, open-source distillation of the MAHL RTL pipeline.

MAHL-Lite turns a natural-language hardware request into synthesizable Verilog and
verifies it, using a small team of LLM "agents":

    Planner   describe -> AST -> per-module code generation
    Reasoner  diagnoses compile/sim failures and writes a fix plan ("thinking")
    Coder     turns the fix plan into patched files ("trying it out")
    Verifier  generates testbenches and cross-validates against a Python golden model

It is a deliberately slimmed-down take on MAHL (arXiv:2508.14053): it keeps the
hierarchical description, hierarchical code generation, flow-based validation and
adaptive debugging, and leaves out MAHL's retrieval-augmented generation,
multi-granularity design-space exploration and PPA optimization.

Entry point:  python -m mahl_lite "Build a 4:1 mux from two 2:1 muxes"
"""

__version__ = "0.1.0"

from .errors import (
    MahlError,
    LLMError,
    ParseError,
    SimError,
    VerificationError,
    ConfigError,
)

__all__ = [
    "__version__",
    "MahlError",
    "LLMError",
    "ParseError",
    "SimError",
    "VerificationError",
    "ConfigError",
]
