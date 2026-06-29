"""The Rich-based terminal UI.

A faithful port of the original animated TUI (Gemini-style rainbow waves, cycling
neon borders, live log, agent and step panels, context-memory gauge), adapted to
MAHL-Lite's agent roles and :class:`~mahl_lite.config.Config`. The pipeline drives
it through ``set_step_status`` / ``set_active_agent`` / ``add_memory`` / ``note``;
all of those are no-ops-safe so the pipeline never depends on the UI.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import Config
from ..log import MAX_LOG_LINES
from .theme import STYLE_PRESETS

MAX_DISPLAY_LINES = 25

try:
    from rich.align import Align
    from rich import box
    from rich.console import Group
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except Exception:
    _RICH = False


def rich_available() -> bool:
    return _RICH


class LogFileMonitor:
    """Tails the log file in a background thread and refreshes the UI on change."""

    def __init__(self, log_file_path: str, tui, refresh_callback):
        self.log_file_path = log_file_path
        self.tui = tui
        self.refresh_callback = refresh_callback
        self.last_size = 0
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def _loop(self):
        while self.running:
            try:
                if os.path.exists(self.log_file_path):
                    size = os.path.getsize(self.log_file_path)
                    if size != self.last_size:
                        self._update()
                        self.last_size = size
                time.sleep(0.2)
            except Exception:
                pass

    def _update(self):
        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        styled = []
        for line in (l.strip() for l in lines):
            if not line:
                continue
            if line.startswith("[ERROR]"):
                styled.append((line, "red"))
            elif line.startswith("[WARN]"):
                styled.append((line, "yellow"))
            elif line.startswith("[OK]"):
                styled.append((line, "green"))
            elif line.startswith("[INFO]"):
                styled.append((line, "cyan"))
            elif line.startswith("\U0001f9e0"):
                styled.append((line, "magenta"))
            elif line.startswith("\U0001f539"):
                styled.append((line, "bright_white"))
            else:
                styled.append((line, "white"))
        self.tui.logs = styled[-MAX_LOG_LINES:]
        self.refresh_callback()


class TUI:
    def __init__(self, config: Config):
        self.config = config
        self.style_name = config.ui.style
        self.palette = STYLE_PRESETS.get(self.style_name, STYLE_PRESETS["genmini"])
        self.is_futuristic = self.style_name == "futuristic"
        self.model = config.models.default
        self.lib_dir = Path(config.output.lib_dir)
        self.max_fix_rounds = config.rounds.max_fix_rounds
        self.max_tb_rounds = config.rounds.max_tb_rounds

        self.logs: List[Tuple[str, str]] = []
        self.animation_frame = 0
        self.steps = [
            ("identify", "Identify modules", "pending"),
            ("describe", "Generate descriptions", "pending"),
            ("ast", "Build AST", "pending"),
            ("codegen", "Generate code", "pending"),
            ("compile", "Compile & fix (design)", "pending"),
            ("tbgen", "Generate & run testbench", "pending"),
            ("crossval", "Cross-validate vs golden", "pending"),
            ("tbanalyze", "Analyze results", "pending"),
        ]
        self.agents = ["Planner Agent", "Reasoner Agent", "Coder Agent", "Verifier Agent", "Memory"]
        self.active_agent: Optional[str] = None

        self.memory_used = 0
        self.memory_capacity = config.memory.capacity_chars
        self.summary_lines: List[str] = []
        self.footer_help = "Press Ctrl+C to exit"
        self.live_display = None
        self.file_monitor: Optional[LogFileMonitor] = None

        self.layout = Layout()
        self.layout.split(
            Layout(name="header", size=4),
            Layout(name="body"),
            Layout(name="footer", size=1),
        )
        self.layout["body"].split_row(
            Layout(name="left", ratio=1), Layout(name="right", ratio=2)
        )
        self.layout["left"].split(
            Layout(name="steps_panel", size=11),
            Layout(name="agents_panel", size=12),
            Layout(name="verification_panel"),
        )

    # ----- animation helpers -----
    def get_cycling_color(self):
        if not self.is_futuristic:
            return self.palette["border"]
        colors = [self.palette["glow1"], self.palette["glow2"], self.palette["glow3"], self.palette["accent"]]
        return colors[(self.animation_frame // 2) % len(colors)]

    def get_char_color(self, i):
        if not self.is_futuristic:
            return self.palette["accent"]
        colors = [self.palette["glow1"], self.palette["primary"], self.palette["glow2"],
                  self.palette["accent"], self.palette["glow3"], self.palette["accent"]]
        phase = (self.animation_frame + i) % (len(colors) * 3)
        return colors[(phase // 3) % len(colors)]

    def colorize_text_wave(self, text_str):
        if not self.is_futuristic:
            return Text(text_str, style="")
        result = Text()
        for i, char in enumerate(text_str):
            result.append(char, style=f"bold {self.get_char_color(i)}")
        return result

    # ----- panels -----
    def header_panel(self):
        if self.is_futuristic:
            title = Text(" ")
            title.append_text(self.colorize_text_wave("MAHL-Lite"))
            title.append(" — ", style="dim")
            title.append("Terminal Coder ", style=self.palette["accent"])
        else:
            title = Text(" MAHL-Lite — Terminal Coder ", style=self.palette["primary"])
        subtitle = Text()
        subtitle.append("Model: ", style="dim")
        subtitle.append(self.model, style=self.palette["primary"])
        subtitle.append("   Output: ", style="dim")
        subtitle.append(str(self.lib_dir), style=self.palette["accent"])

        fix = "off" if self.max_fix_rounds == 0 else f"{self.max_fix_rounds} rounds"
        tb = "off" if self.max_tb_rounds == 0 else f"{self.max_tb_rounds} rounds"
        debug_info = Text()
        debug_info.append("Debug: ", style="dim")
        debug_info.append(f"Design={fix}, TB={tb}", style=self.palette.get("glow2", self.palette["accent"]))

        running = next(((k, label) for k, label, s in self.steps if s == "running"), None)
        cur = Text("Current: ", style="dim")
        if running:
            cur.append_text(self.colorize_text_wave(running[1]) if self.is_futuristic
                            else Text(running[1], style=self.palette["accent"]))
        else:
            cur.append("Ready", style=self.palette["accent"])

        block = Align.left(Text.assemble(title, "\n", subtitle, "\n", debug_info, "\n", cur))
        return Panel(block, border_style=self.palette["border"], box=box.ROUNDED)

    def _steps_table(self, keys, title):
        t = Table.grid(padding=(0, 1))
        t.add_column(justify="left", ratio=3)
        t.add_column(justify="right", ratio=1)
        for key, label, status in self.steps:
            if key not in keys:
                continue
            icon = {"pending": "•", "running": "⟲", "done": "✔", "failed": "✖"}.get(status, "•")
            if self.is_futuristic and status == "running":
                status_text = self.colorize_text_wave(status.upper())
            else:
                color = {"pending": "dim", "running": self.palette["accent"], "done": "green", "failed": "red"}[status]
                status_text = Text(status.upper(), style=color)
            t.add_row(f"{icon} {label}", status_text)
        return Panel(t, title=title, border_style=self.palette["border"], box=box.ROUNDED)

    def steps_table(self):
        return self._steps_table({"identify", "describe", "ast", "codegen", "compile"}, "Steps")

    def verification_table(self):
        return self._steps_table({"tbgen", "crossval", "tbanalyze"}, "Verification")

    def agents_panel(self):
        boxes = []
        for agent in self.agents:
            if agent == "Memory":
                boxes.append(self._memory_box())
                continue
            active = agent == self.active_agent
            if active:
                content = Text(f"⚡ {agent}", style=f"bold {self.palette['accent']}")
                boxes.append(Panel(content, border_style=self.palette["accent"], box=box.HEAVY, padding=(0, 1)))
            else:
                boxes.append(Panel(Text(f"  {agent}", style="dim"), border_style="dim", box=box.ROUNDED, padding=(0, 1)))
        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_column()
        for i in range(0, len(boxes), 2):
            grid.add_row(boxes[i], boxes[i + 1] if i + 1 < len(boxes) else "")
        return Panel(grid, title="Active Agents", border_style=self.palette["border"], box=box.ROUNDED)

    def _memory_box(self):
        pct = min(100, int((self.memory_used / max(1, self.memory_capacity)) * 100))
        kb = self.memory_used / 1024
        tokens = self.memory_used // 4
        bar_style = self.get_cycling_color() if (self.is_futuristic and pct > 30) else ("cyan" if pct > 50 else "dim")
        progress = Progress(TextColumn("[progress.description]{task.description}"),
                            BarColumn(bar_width=None, complete_style=bar_style),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"), expand=True)
        progress.add_task("", total=100, completed=pct)
        info = Text(f"🧠 Context: {kb:.1f}KB (~{tokens:,} tokens)", style="cyan" if pct > 50 else "dim")
        border = self.get_cycling_color() if (self.is_futuristic and pct > 30) else ("cyan" if pct > 50 else "dim")
        bx = box.HEAVY if pct > 30 else box.ROUNDED
        return Panel(Group(info, progress), title="LLM Memory", border_style=border, box=bx, padding=(0, 1))

    def log_panel(self):
        verification_active = any(s in ("running", "done", "failed")
                                  for k, _l, s in self.steps if k.startswith("tb") or k == "crossval")
        if not self.logs:
            content = Text("Logs will appear here...", style="dim")
        else:
            source = self.logs
            if verification_active:
                vonly = [ls for ls in source if "[VERIF]" in ls[0]]
                if vonly:
                    source = vonly
            display = source[-MAX_DISPLAY_LINES:]
            elements = []
            if len(source) > MAX_DISPLAY_LINES:
                elements.append(Text(f"⋮ ({len(source) - MAX_DISPLAY_LINES} earlier hidden) ⋮",
                                     style="dim italic", justify="center"))
            txt = Text(overflow="fold", no_wrap=False)
            for line, style in display:
                txt.append(line, style=style)
                txt.append("\n")
            elements.append(txt)
            content = Group(*elements) if len(elements) > 1 else txt
        title = "Verification Log" if verification_active else "Live Log"
        return Panel(Align(content, align="left", vertical="bottom"), title=title,
                     border_style=self.palette["border"], box=box.ROUNDED)

    def summary_panel(self):
        txt = Text()
        for line in self.summary_lines:
            txt.append(line + "\n")
        return Panel(txt, title="Summary", border_style=self.palette["border"], box=box.ROUNDED)

    # ----- render & state -----
    def render(self):
        self.layout["header"].update(self.header_panel())
        self.layout["steps_panel"].update(self.steps_table())
        self.layout["agents_panel"].update(self.agents_panel())
        self.layout["verification_panel"].update(self.verification_table())
        self.layout["right"].update(self.summary_panel() if self.summary_lines else self.log_panel())
        self.layout["footer"].update(Panel(Text(self.footer_help, style="dim"),
                                           border_style=self.palette["border"], box=box.ROUNDED))
        return self.layout

    def set_step_status(self, step_key, status):
        for i, (k, label, _s) in enumerate(self.steps):
            if k == step_key:
                self.steps[i] = (k, label, status)
                break
        self.refresh()

    def set_active_agent(self, agent_name):
        self.active_agent = agent_name
        self.refresh()

    def add_memory(self, text):
        self.memory_used += len(text.encode("utf-8"))
        if self.memory_used > self.memory_capacity:
            self.memory_capacity = int(self.memory_used * 1.2)
        self.refresh()

    def note(self, msg, style="white"):
        self.logs.append((msg, style))
        if len(self.logs) > MAX_LOG_LINES:
            self.logs = self.logs[-MAX_LOG_LINES:]
        self.refresh()

    def refresh(self):
        if not self.live_display:
            return
        self.animation_frame = (self.animation_frame + 1) % 100
        self.live_display.update(self.render())

    def start_file_monitoring(self, log_file_path):
        self.file_monitor = LogFileMonitor(log_file_path, self, self.refresh)
        self.file_monitor.start()

    def stop_file_monitoring(self):
        if self.file_monitor:
            self.file_monitor.stop()
            self.file_monitor = None
