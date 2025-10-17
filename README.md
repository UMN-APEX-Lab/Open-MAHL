# MAHLT - Multi-Agent Hardware Language Toolkit

A futuristic terminal-based RTL (Register Transfer Level) generation and verification tool powered by LLMs, featuring an animated TUI with Gemini-inspired visual effects.

## Features

🎨 **Futuristic UI Theme** (Default)
- Cycling rainbow borders with running light effects
- Pulsing glow animations for active agents
- Neon color palette (Magenta, Cyan, Blue, Yellow)
- Double-line borders for enhanced visual appeal

🤖 **Multi-Agent System**
- **Code Planning Agent**: Module identification, description generation, AST building, code generation
- **Verification Agent**: Testbench generation and validation
- **Debugging Agent**: Comprehensive error analysis and fixing
- **LLM Memory**: Real-time context window tracking with progress bar

⚡ **Smart Pipeline**
- Automated RTL generation from natural language
- Intelligent module dependency resolution
- Iterative compile-fix-verify workflow
- Auto-scrolling live logs (shows last 25 messages)

🔍 **Enhanced Debugging**
- Limited compile attempts to prevent infinite loops
- Comprehensive error categorization
- Full context inclusion for better fixes
- Visual feedback with step-by-step progress

## Usage

```bash
# Use default futuristic theme
python gen_tui1.py "Build a 4:1 mux using two 2:1 muxes"

# Try other themes
python gen_tui1.py --style claude "Create a RISC-V ALU"
python gen_tui1.py --style openai "Design a simple CPU"
python gen_tui1.py --style genmini "Make a memory controller"

# Specify model and rounds
python gen_tui1.py --model openai --max-fix-rounds 10 --max-tb-rounds 10 "Your design"
```

## Available Themes

- `futuristic` (default) - Animated borders with cycling neon colors
- `claude` - Magenta accent with clean design
- `openai` - Cyan accent with modern look
- `genmini` - Blue accent with traditional style

## Requirements

```bash
pip install rich openai python-dotenv
# For local models: pip install ollama
```

## Environment Setup

Create a `.env` file:
```
OPENAI_API_KEY=your_api_key_here
LLM_MODEL=openai  # or llama3.3, gemma3, etc.
```

## What Makes It Futuristic?

The default `futuristic` theme features **Gemini-inspired** flowing rainbow animations:

### Character-by-Character Wave Animation 🌊
- **Rainbow Wave Effect**: Each character cycles through colors independently
- **Flowing Motion**: Colors flow left-to-right creating a wave pattern
- **Color Sequence**: Magenta → Bright Magenta → Cyan → Bright Cyan → Blue → (repeat)
- **Phase Offset**: Each character position has unique phase, creating gradient
- **Smooth Flow**: 10 FPS animation creates continuous wave motion

Applied to:
- `MAHLT` header - constant rainbow wave
- Active agent names - flowing colors when working
- `RUNNING` status text - animated wave
- Current step indicator - shows what's executing
- Memory context label - waves when >30% full

### Visual Design
- **Static Outer Borders**: Clean blue borders on main panels (easy to focus)
- **Animated Inner Text**: Active text flows with rainbow waves
- **Active Agent Borders**: Cycle through Magenta → Cyan → Blue
- **Memory Progress Bar**: Animated when context fills up
- **Heavy Borders for Active**: Bold (box.HEAVY) borders draw attention
- **10 FPS Refresh**: Smooth, fluid animations

### Design Philosophy
- **Outer panels**: Static borders - prevents visual distraction
- **Inner text**: Character-by-character waves - like Gemini
- **Selective animation**: Only active elements animate
- **No double-line borders**: Clean, readable interface

### Example Wave Pattern
```
Frame 0:  M A H L T
          🟣🟪🔵🔷💙

Frame 3:  M A H L T  ← wave flows right
          🟪🔵🔷💙🟣

Frame 6:  M A H L T
          🔵🔷💙🟣🟪
```

Each character continuously cycles through the rainbow palette, creating that signature Gemini flowing effect! ✨
