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

The default `futuristic` theme features:
- **Cycling Border Colors**: Magenta → Cyan → Blue → Bright Cyan (changes every 2 frames)
- **Active Agent Glow**: Pulsing between Cyan → Magenta → Yellow (3-frame cycle)
- **Memory Progress Bar**: Animates with cycling colors when >30% full
- **Double-Line Borders**: Enhanced visual separation with `box.DOUBLE`
- **Running Light Effect**: Like Gemini, borders cycle through neon colors continuously
- **Real-time Animation**: 10 refreshes per second for smooth visual effects
