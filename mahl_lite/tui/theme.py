"""Color presets for the TUI.

``futuristic`` is the default Gemini-inspired theme (cycling neon borders, rainbow
character waves). The others are simple single-accent palettes. Animation helpers
that depend on these palettes live on the TUI class in :mod:`mahl_lite.tui.app`.
"""

STYLE_PRESETS = {
    "genmini": {"primary": "bright_blue", "accent": "cyan", "border": "blue"},
    "claude": {"primary": "magenta", "accent": "bright_magenta", "border": "magenta"},
    "openai": {"primary": "cyan", "accent": "bright_cyan", "border": "cyan"},
    "futuristic": {
        "primary": "bright_magenta",
        "accent": "bright_cyan",
        "border": "blue",
        "glow1": "magenta",
        "glow2": "cyan",
        "glow3": "blue",
        "neon": "bright_yellow",
    },
}

STYLE_NAMES = list(STYLE_PRESETS.keys())
