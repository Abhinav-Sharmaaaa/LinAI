# linai — Cross-Platform AI Terminal Assistant

A lightweight, cross-platform AI assistant for your terminal with TUI, workflows, web search, and file operations. Works on Linux, macOS, and Windows.

![Version](https://img.shields.io/badge/version-0.2.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## Features

- **TUI Mode** — Interactive terminal UI with markdown rendering, syntax highlighting, scrollback, and animations
- **One-Shot Mode** — Pipe questions or run from scripts: `echo "question" | linai`
- **Workflow System** — Create and execute multi-step plans (saved as JSON)
- **Web Search** — Built-in DuckDuckGo search (no API key needed), with per-result title/URL/snippet parsing and a lite-endpoint fallback
- **File Operations** — Read, write, edit, and search files anywhere on disk — any absolute path on any drive (`C:\`, `D:\`, `/mnt/...`), not just your home directory
- **Shell Commands** — Run commands safely with sandboxing
- **Cross-Platform** — Works on Linux, macOS, and Windows
- **Dual API Support** — OpenRouter **and** NVIDIA NIM API, switchable live from the web UI (no restart needed)

## Installation

```bash
# From PyPI (when published)
pip install linai

# From source
git clone https://github.com/Abhinav-Sharmaaaa/LinAI.git
cd LinAI
pip install -e .
```

## Quick Start

### OpenRouter (Default)

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY="sk-or-v1-..."

# Run TUI (interactive) — launches automatically when stdin is a real terminal and no args are given
linai

# Force TUI explicitly (e.g. from a wrapper script)
linai --tui

# One-shot mode
linai "what is the latest Python version?"

# Pipe input
echo "explain asyncio" | linai

# System status
linai --status

# Help
linai --help

# Web UI for configuration and logs
linai-web
```

### NVIDIA NIM API

```bash
# Set NVIDIA API key (get from https://build.nvidia.com)
export NVIDIA_API_KEY="nvapi-..."
export LINAI_API_PROVIDER="nvidia_nim"

# Optional: custom NIM endpoint (default: https://integrate.api.nvidia.com/v1)
export NVIDIA_NIM_API_URL="https://your-nim-endpoint/v1"

# Optional: model override
export LINAI_NVIDIA_NIM_MODEL="nvidia/nemotron-3-ultra"

linai
```

## Web UI

```bash
# Start web UI for configuration and logs
linai-web

# Custom port
linai-web 8080
```

The web UI runs at `http://127.0.0.1:8765` and provides:
- **Configuration Panel** — Set API keys, choose provider (OpenRouter/NVIDIA NIM) via a toggle, select models. Switching providers here takes effect immediately — no restart needed.
- **Logs Viewer** — Real-time log streaming with filtering
- **Workflow Manager** — Create, list, execute workflows visually
- **System Dashboard** — Disk, memory, CPU, uptime
- **API Connection Tester** — Tests against whatever's currently in the form (not just the last-saved config)
- **Model Browser** — Browse available models with pricing

## Usage

### TUI Hotkeys

> **Note:** only `linai` (bare, in a real terminal) or `linai --tui` actually launch the TUI. A `linai tui` subcommand isn't currently implemented in `cli.py` despite being mentioned in `--help`'s usage text — that's a known doc/behavior mismatch, not something you're doing wrong.

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+C` | Cancel streaming |
| `Ctrl+D` / `Esc` | Quit |
| `Ctrl+L` | Clear screen |
| `Ctrl+M` | Model picker (sorted by price) |
| `Ctrl+H` | Help overlay |
| `Ctrl+Y` | Copy output to clipboard |
| `↑/↓` | Input history |
| `PgUp/PgDn` | Scroll output |

### TUI Commands

Type these at the prompt:

| Command | Alias | Action |
|---------|-------|--------|
| `/model` | `/m` | Model picker |
| `/status` | `/s` | System snapshot |
| `/context` | `/c` | Token budget |
| `/clear` | | Reset conversation |
| `/help` | | Help screen |
| `/workflows` | `/w` | List saved workflows |

### Workflow Tools (available to the model)

The AI can create and execute multi-step workflows:

```json
// create_workflow
{
  "name": "Python Version Check",
  "description": "Find latest Python version and save summary",
  "steps": [
    {"name": "search", "tool": "web_search", "args": {"query": "latest Python version 2024"}, "description": "Search for latest version"},
    {"name": "write", "tool": "write_file", "args": {"path": "python_version.md", "content": "# Python Version\n\n...", "diff": "Created summary"}, "description": "Write markdown file"}
  ]
}
```

### One-Shot Examples

```bash
# Quick questions
linai "how to create a virtualenv in Python?"
linai "what's new in Python 3.13?"

# File operations
linai "read my ~/.bashrc and suggest improvements"
linai "create a Python script that fetches Bitcoin price"
linai "search for pokemon files in D drive"

# System tasks
linai "check disk space and clean caches"
linai "run: git status && git diff --stat"

# Web search
linai "latest Rust release notes"
linai "compare React vs Vue 2024"
```

## Configuration

Config file: `~/.config/linai/config.json`

```json
{
  "provider": "openrouter",
  "openrouter_key": "sk-or-v1-...",
  "nvidia_key": "",
  "nvidia_url": "https://integrate.api.nvidia.com/v1",
  "model": "google/gemma-4-26b-a4b-it:free",
  "nvidia_model": "nvidia/nemotron-3-ultra",
  "temperature": 0.2,
  "max_tokens": 1000
}
```

`provider` is the active toggle (`openrouter` or `nvidia_nim` — `nvidia` is also accepted as an alias). Whichever provider is active determines which key/model field is actually used (`openrouter_key`/`model` vs `nvidia_key`/`nvidia_model`). `config.json` is read fresh on every request and takes priority over environment variables — editing it (directly or via the web UI) takes effect immediately, no restart required.

### Environment Variables

Environment variables are used as a **fallback** for any field left empty in `config.json` (e.g. if you've never opened the web UI, or deliberately keep secrets out of the config file).

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (used if `openrouter_key` is empty in config.json) |
| `NVIDIA_API_KEY` / `NVIDIA_NIM_API_KEY` | NVIDIA NIM API key (used if `nvidia_key` is empty in config.json) |
| `LINAI_API_PROVIDER` | `openrouter` (default) or `nvidia_nim` — only used if `provider` is unset in config.json |
| `LINAI_MODEL` | Default model override |
| `LINAI_OPENROUTER_MODEL` | OpenRouter default model |
| `LINAI_NVIDIA_NIM_MODEL` | NVIDIA NIM default model |
| `NVIDIA_NIM_API_URL` / `NVIDIA_API_URL` | Custom NIM endpoint |
| `NO_COLOR` | Disable colors |

### Switching Providers

The easiest way is the web UI toggle (`linai-web`) — it writes straight to `config.json` and applies on your very next message.

You can also switch via environment variables, but note these are only used as a fallback for whatever `config.json` doesn't already specify:

```bash
# Use OpenRouter (default)
export LINAI_API_PROVIDER="openrouter"
export OPENROUTER_API_KEY="sk-or-v1-..."

# Use NVIDIA NIM
export LINAI_API_PROVIDER="nvidia_nim"
export NVIDIA_API_KEY="nvapi-..."
```

## Architecture

```
linai/
├── agent.py      # API calls (streaming + non-streaming, dual provider)
├── cli.py        # CLI entry point, one-shot + TUI routing
├── config.py     # Config, constants, API key management
├── tools.py      # Tool definitions + implementations
├── utils.py      # Colors, wrapping, system status, model cache
└── tui/
    ├── app.py    # TUI rendering, input, markdown rendering
    └── keys.py   # Raw key input handling
```

## Free Models (OpenRouter)

| Model | Context | Best For |
|-------|---------|----------|
| `meta-llama/llama-3.1-8b-instruct:free` | 128k | General chat, coding |
| `google/gemma-2-9b-it:free` | 8k | Fast responses |
| `microsoft/phi-3-mini-128k-instruct:free` | 128k | Long context |
| `nousresearch/hermes-3-llama-3.1-8b:free` | 128k | Reasoning |

Set default: `export LINAI_MODEL="meta-llama/llama-3.1-8b-instruct:free"`

## NVIDIA NIM Models

Popular NIM models (require NVIDIA API key):

- `nvidia/nemotron-3-ultra` — Best reasoning
- `nvidia/nemotron-3-super` — Strong performance
- `nvidia/nemotron-3-nano` — Fast, efficient
- `meta/llama-3.1-8b-instruct` — Meta's model on NIM
- `google/gemma-2-9b` — Google's model on NIM

See [NVIDIA NGC Catalog](https://build.nvidia.com) for full list.

## Windows Support

- Uses `Path.home()` for cross-platform paths
- File tools accept any absolute path, including bare drive letters (`D:`, `D:\`) which are normalized to that drive's root
- Windows-specific system status (disk, memory, uptime via WMI/PowerShell)
- Windows cache locations in `clean_cache`
- Color support via ANSI (Windows 10+)
- TUI works in Windows Terminal, ConEmu, etc.

## License

MIT — see [LICENSE](LICENSE)

## Contributing

1. Fork the repo
2. Create a feature branch
3. Make changes with tests
4. Submit PR

---

**linai** — Your terminal AI companion. 🤖