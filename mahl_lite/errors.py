"""Exception hierarchy for MAHL-Lite.

Every failure mode raises a subclass of :class:`MahlError`. The CLI / TUI layer
(``mahl_lite.__main__``) is the single top-level boundary that catches these and
turns them into user-facing messages and exit codes. Library code should *raise*,
not return error strings.
"""


class MahlError(Exception):
    """Base class for all MAHL-Lite errors."""


class ConfigError(MahlError):
    """Invalid or unreadable configuration."""


class LLMError(MahlError):
    """An LLM backend call failed or returned something unusable."""


class ParseError(MahlError):
    """Failed to parse structured content (module list, AST, patch JSON, ports)."""


class SimError(MahlError):
    """A toolchain invocation (iverilog / vvp) could not be run."""


class VerificationError(MahlError):
    """Verification could not be carried out (distinct from a design that FAILED)."""
