# linai

> Lightweight cross-platform AI assistant with terminal TUI, workflows, and web search

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey.svg)](https://github.com/Abhinav-Sharmaaaa/LinAI)

## Features

- **Terminal TUI** — Interactive chat with syntax highlighting, markdown rendering, and animations
- **Cross-platform** — Works on Linux and Windows
- **Agent tools** — File ops, shell commands, web search, system info
- **Workflows** — Create and execute multi-step automation plans
- **OpenRouter integration** — Access 100+ models (free and paid)
- **Zero config** — Works out of the box with free models

## Installation

```bash
# From source (recommended)
git clone https://github.com/Abhinav-Sharmaaaa/LinAI.git
cd LinAI
pip install -e .

# Or install in development mode
pip install -e .[dev]
```

## Quick Start

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY="sk-or-v1-..."

# Interactive TUI (default)
linai

# One-shot queries
linai "what is the latest Python version?"
linai "create a workflow to backup my dotfiles"

# System status
linai --status

# Help
linai --help
```

## TUI Hotkeys

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+D` / `Esc` | Quit |
| `Ctrl+L` | Clear screen |
| `Ctrl+M` | Model picker (sorted by price) |
| `Ctrl+H` | Help |
| `Ctrl+Y` | Copy output to clipboard |
| `↑/↓` | History |
| `PgUp/PgDn` | Scroll output |
| `Ctrl+C` | Cancel streaming |

## Built-in Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `/model` | `/m` | Pick model |
| `/status` | `/s` | System snapshot |
| `/context` | `/c` | Token budget |
| `/workflows` | `/w` | List workflows |
| `/clear` | — | Reset conversation |
| `/help` | `/h` | Show help |

## Workflows

Create multi-step automation plans:

```bash
# In TUI or one-shot
linai "create a workflow that searches for Python 3.13 features and writes a summary to python313.md"
```

Or use the workflow tools directly:
- `create_workflow(name, description, steps[])` — Define a workflow
- `execute_workflow(workflow_id)` — Run a saved workflow
- `list_workflows()` — Show all saved workflows

Workflows persist in `~/.config/linai/workflows.json`

## Tools Available to the Model

| Tool | Description |
|------|-------------|
| `read_file` | Read file in home directory |
| `write_file` | Create/overwrite file |
| `edit_file` | Exact-string replace |
| `run_cmd` | Shell command (≤30s) |
| `list_dir` | List directory |
| `grep_file` | Regex search in file |
| `search_dir` | Find files by name/content |
| `read_system_file` | Read `/etc`, `/var`, etc. |
| `free_up_space` | Analyze disk usage |
| `clean_cache` | Clean cache dirs |
| `web_search` | DuckDuckGo search |
| `create_workflow` | Define multi-step plan |
| `execute_workflow` | Run workflow |
| `list_workflows` | Show saved workflows |

## Configuration

Config file: `~/.config/linai/config.json`

```json
{
  "model": "meta-llama/llama-3.1-8b-instruct:free",
  "temperature": 0.2,
  "max_tokens": 1000
}
```

Environment variables:
- `OPENROUTER_API_KEY` — Your OpenRouter API key (required)
- `LINAI_MODEL` — Default model override
- `NO_COLOR` — Disable colors

## Free Models

Works with these free OpenRouter models by default:
- `meta-llama/llama-3.1-8b-instruct:free` (default)
- `google/gemma-2-9b-it:free`
- `microsoft/phi-3-mini-128k-instruct:free`
- And more — use `/model` to browse

## Project Structure

```
linai/
├── cli.py          # CLI entry point, config, one-shot mode
├── agent.py        # OpenRouter API streaming, system prompt
├── tools.py        # Tool implementations (13 tools)
├── config.py       # Constants, paths, API endpoints
├── utils.py        # Colors, wrapping, system status, model cache
├── tui/
│   ├── app.py      # TUI rendering, input, markdown, animations
│   └── keys.py     # Cross-platform key handling
├── __init__.py
├── __main__.py
├── pyproject.toml
└── README.md
```

## Development

```bash
# Run tests
pytest

# Type check
mypy linai

# Run from source
python -m linai
```

## Windows Notes

- Uses `wmic`/`powershell` for system status
- Path handling uses `pathlib` (cross-platform)
- Shell commands run via `cmd.exe /c` on Windows
- Protected paths include `AppData`, `Windows`, `Program Files`

## License

MIT — see [LICENSE](LICENSE) for details.

## Links

- [OpenRouter](https://openrouter.ai) — API provider
- [GitHub](https://github.com/Abhinav-Sharmaaaa/LinAI) — Source code