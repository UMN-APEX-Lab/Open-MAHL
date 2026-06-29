"""Rich terminal UI (optional). Import lazily so the core runs without Rich."""

from .app import TUI, rich_available

__all__ = ["TUI", "rich_available"]
