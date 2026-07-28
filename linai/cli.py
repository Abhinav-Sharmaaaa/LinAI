"""Lightweight Linux AI assistant with a terminal TUI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from linai import __version__
from linai.agent import SYSTEM_MSG, call_nonstreaming, execute_tool
from linai.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    HISTORY_FILE,
    MAX_TURNS,
)
from linai.utils import cap, init_colors


def load_config() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            # Merge with defaults for new keys
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except (json.JSONDecodeError, IOError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def print_status() -> None:
    """linai-status: one-line system overview."""
    from linai.utils import get_system_status
    print(get_system_status())


def _save_history(messages: list[dict]) -> None:
    """Save conversation history to disk."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
    except Exception:
        pass


def one_shot(
    question: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> None:
    """Non-TUI path: pipes, scripts, low-token mode."""
    # Load existing history if available, otherwise start fresh
    messages = [{"role": "system", "content": SYSTEM_MSG}]
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                loaded = [json.loads(line) for line in f if line.strip()]
                if loaded and loaded[0].get("role") == "system":
                    messages = loaded
        except Exception:
            pass

    messages.append({"role": "user", "content": question})

    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    sp = 0

    while True:
        try:
            reply_text, tool_calls = call_nonstreaming(
                messages, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception as e:
            print(f"\033[31merror: {e}\033[0m", file=sys.stderr)
            sys.exit(1)

        assistant_msg: dict = {"role": "assistant", "content": reply_text}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            print(reply_text)
            _save_history(messages)
            break

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = execute_tool(tc)
            print(f"\033[33m[→ {name}({json.dumps(args, separators=(',',':'))[:100]})]\033[0m")
            sys.stdout.write(f"\033[35m{cap(result)}\n\033[0m")
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": result,
            })

        messages[:] = [messages[0]] + messages[-(MAX_TURNS * 2):]
        _save_history(messages)


def main() -> None:
    args = sys.argv[1:]

    # Handle --model override early
    model = None
    if "--model" in args:
        idx = args.index("--model")
        if idx + 1 < len(args):
            model = args[idx + 1]
            cfg = load_config()
            cfg["model"] = model
            save_config(cfg)

    # Handle --no-color early (before anything prints)
    no_color = "--no-color" in args
    if no_color:
        args.remove("--no-color")
    if no_color:
        import os
        os.environ["NO_COLOR"] = "1"

    # Initialize colors
    init_colors(force_no_color=no_color)

    # --version
    if "--version" in args:
        print(f"linai {__version__}")
        return

    # --help
    if "--help" in args:
        print(_HELP_TEXT.format(version=__version__))
        return

    # --status
    if "--status" in args:
        print_status()
        return

    # Extract optional temperature / max_tokens overrides
    cfg = load_config()
    temperature = cfg.get("temperature", 0.2)
    max_tokens = cfg.get("max_tokens", 400)

    if "--temperature" in args:
        idx = args.index("--temperature")
        if idx + 1 < len(args):
            try:
                temperature = float(args[idx + 1])
                args.pop(idx + 1)
            except ValueError:
                pass
            args.pop(idx)

    if "--max-tokens" in args:
        idx = args.index("--max-tokens")
        if idx + 1 < len(args):
            try:
                max_tokens = int(args[idx + 1])
                args.pop(idx + 1)
            except ValueError:
                pass
            args.pop(idx)

    # --tui
    if "--tui" in args:
        args.remove("--tui")
        if sys.stdin.isatty():
            from linai.tui.app import TUI
            TUI(model=model, temperature=temperature, max_tokens=max_tokens, no_color=no_color).run()
        else:
            print("error: --tui needs a real terminal", file=sys.stderr)
            sys.exit(1)
        return

    # Route: args or pipe → one-shot; tty → TUI
    if not sys.stdin.isatty() or args:
        question = " ".join(args) if args else sys.stdin.read().strip()
        if not question:
            sys.exit(0)
        one_shot(question, model=model or cfg["model"], temperature=temperature, max_tokens=max_tokens)
        return

    # Default TUI
    if sys.stdin.isatty():
        from linai.tui.app import TUI
        TUI(model=model, temperature=temperature, max_tokens=max_tokens, no_color=no_color).run()
    else:
        print("error: interactive mode needs a terminal.", file=sys.stderr)
        sys.exit(1)


_HELP_TEXT = f"""linai {{version}} — lightweight Linux AI assistant with a terminal TUI

Usage:
  linai                      full TUI (default when stdin is a tty)
  linai "question"           one-shot (pipeable, scriptable)
  linai tui                  full TUI
  linai --status             print system status
  linai --version            print version
  linai --help               show this help
  linai --model <id> "q"     per-invocation model override
  linai --temperature 0.7 "q"  temperature override
  echo "q" | linai            scripted (no TUI)

TUI hotkeys:
  Enter          send
  Ctrl+D / Esc   quit
  Ctrl+L         clear screen
  Ctrl+M         model picker (sorted by price, free first)
  Ctrl+H         help
  Ctrl+Y         copy output to clipboard
  Up / Down      history
  PageUp / Down  scroll output
  Ctrl+C         cancel current request

Built-in commands (type at prompt):
  /model, /m     pick model
  /status, /s    system snapshot
  /context, /c   token-budget snapshot
  /clear         reset conversation
  /help          this screen
  /workflows, /w list saved workflows

Workflow tools (available to the model):
  create_workflow  - define a multi-step plan (name, description, steps[])
  execute_workflow - run a saved workflow by ID
  list_workflows   - show all saved workflows

Env:
  OPENROUTER_API_KEY   required
  LINAI_MODEL          default model id
  NO_COLOR             disable colors

Docs: https://github.com/linai
"""


if __name__ == "__main__":
    main()
