"""Configuration for MAHL-Lite.

Everything a user might want to tune lives here. Precedence, lowest to highest:

    dataclass defaults  <  config.yaml  <  environment variables  <  CLI flags

So the defaults below always produce a working setup; ``config.yaml`` lets a user
change things without touching code; env vars and CLI flags win for one-off runs.

Roles
-----
The pipeline talks to the LLM under four *roles* — ``planner``, ``reasoner``,
``coder`` and ``verifier``. Each role can use a different model (e.g. a stronger
model to diagnose bugs, a cheaper one to apply the fix). A role set to ``None``
inherits ``models.default``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, List, Optional

from .errors import ConfigError

ROLES = ("planner", "reasoner", "coder", "verifier")


@dataclass
class ModelsConfig:
    default: str = "openai"          # "openai" or an Ollama model name (e.g. "llama3.3")
    planner: Optional[str] = None
    reasoner: Optional[str] = None
    coder: Optional[str] = None
    verifier: Optional[str] = None

    def for_role(self, role: str) -> str:
        if role not in ROLES:
            raise ConfigError(f"Unknown LLM role {role!r}; expected one of {ROLES}")
        return getattr(self, role) or self.default


@dataclass
class SamplingConfig:
    openai_model_id: str = "gpt-4o"  # OpenAI model used when models.* == "openai"
    temperature: float = 0.6
    top_p: float = 1.0
    max_tokens: int = 4096
    retries: int = 2                 # extra attempts on a failed LLM call


@dataclass
class RoundsConfig:
    max_fix_rounds: int = 10         # design compile/fix attempts (0 disables debugging)
    max_tb_rounds: int = 10          # testbench iterate attempts (0 disables debugging)


@dataclass
class DebugConfig:
    two_phase: bool = True           # True: Reasoner -> Coder; False: single-call patcher


@dataclass
class VerifyConfig:
    crossvalidation: bool = True     # cross-check DUT against a Python golden model
    num_random_vectors: int = 64
    seed: int = 0
    honest_results: bool = True      # report real PASS/FAIL/INCONCLUSIVE (not forced PASS)


@dataclass
class SimConfig:
    iverilog_flags: List[str] = field(default_factory=lambda: ["-g2012"])


@dataclass
class MemoryConfig:
    capacity_chars: int = 400_000    # context-window estimate for the TUI gauge


@dataclass
class UIConfig:
    style: str = "futuristic"        # futuristic | claude | openai | genmini
    no_tui: bool = False


@dataclass
class OutputConfig:
    lib_dir: str = "module_lib"
    artifacts_dir: str = "artifacts"


@dataclass
class Config:
    models: ModelsConfig = field(default_factory=ModelsConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    rounds: RoundsConfig = field(default_factory=RoundsConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    # ----- construction -----
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        """Build a Config from defaults, overlaying config.yaml then env vars."""
        cfg = cls()
        yaml_path = _resolve_config_path(path)
        if yaml_path is not None:
            cfg._merge(_read_yaml(yaml_path))
        cfg._apply_env()
        return cfg

    # ----- internals -----
    def _merge(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise ConfigError("config file must contain a top-level mapping")
        _merge_into(self, data)

    def _apply_env(self) -> None:
        # LLM_MODEL keeps the old behaviour of selecting the default model via env.
        env_model = os.getenv("LLM_MODEL")
        if env_model:
            self.models.default = env_model


def _resolve_config_path(path: Optional[str]) -> Optional[Path]:
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        return p
    default = Path("config.yaml")
    return default if default.exists() else None


def _read_yaml(path: Path) -> dict:
    try:
        import yaml  # optional dependency; only needed if a config.yaml exists
    except ImportError as e:
        raise ConfigError(
            f"{path} exists but PyYAML is not installed (pip install pyyaml)"
        ) from e
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # malformed YAML
        raise ConfigError(f"could not parse {path}: {e}") from e


def _merge_into(node: Any, data: dict) -> None:
    """Recursively overlay a plain dict onto a (possibly nested) dataclass."""
    valid = {f.name for f in fields(node)}
    for key, value in data.items():
        if key not in valid:
            raise ConfigError(f"unknown config key: {key!r}")
        current = getattr(node, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge_into(current, value)
        else:
            setattr(node, key, value)
