"""Lightweight Linux AI assistant with a terminal TUI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from linai import __version__
from linai.agent import (
    SYSTEM_MSG,
    call_nonstreaming,
    execute_tool,
    looks_uncertain,
    looks_like_recurring_request,
    made_tool_call,
)
from linai.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    HISTORY_FILE,
    MAX_TURNS,
    get_runtime,
)
from linai.config import load_config as _load_config
from linai.config import save_config as _save_config
from linai.utils import cap, init_colors


def load_config() -> dict[str, Any]:
    """Thin wrapper kept for backwards compatibility within this module."""
    return _load_config()


def save_config(cfg: dict[str, Any]) -> None:
    """Thin wrapper kept for backwards compatibility within this module."""
    _save_config(cfg)


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
    forced_search_retry_used = False
    forced_workflow_retry_used = False
    recurring_intent = looks_like_recurring_request(question)
    created_workflow_this_turn = False

    while True:
        try:
            reply_text, tool_calls = call_nonstreaming(
                messages, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception as e:
            print(f"\033[31merror: {e}\033[0m", file=sys.stderr)
            sys.exit(1)

        # Safety net: if the model answered with hedging/denial ("no such
        # thing", "could you clarify", etc.) instead of calling web_search,
        # force exactly one search-required retry rather than accepting an
        # unverified guess. Weaker models sometimes ignore the system
        # prompt's search policy outright.
        if not tool_calls and not forced_search_retry_used and looks_uncertain(reply_text):
            forced_search_retry_used = True
            try:
                reply_text, tool_calls = call_nonstreaming(
                    messages, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                    tool_choice={"type": "function", "function": {"name": "web_search"}},
                )
            except Exception:
                pass  # fall through with the original (uncertain) reply

        if made_tool_call(tool_calls, "create_workflow"):
            created_workflow_this_turn = True

        # Safety net: the user asked for something recurring/automatic, but
        # this turn is about to end (no more tool calls) without ever
        # having called create_workflow — e.g. the model just called
        # list_workflows and stopped, or answered in prose only. Force one
        # more pass requiring create_workflow instead of silently doing
        # nothing useful.
        if (not tool_calls and recurring_intent and not created_workflow_this_turn
                and not forced_workflow_retry_used):
            forced_workflow_retry_used = True
            messages.append({"role": "assistant", "content": reply_text})
            messages.append({
                "role": "user",
                "content": (
                    "Actually create this as a saved workflow now with create_workflow, "
                    "then schedule it with schedule_workflow so it runs automatically as requested."
                ),
            })
            try:
                reply_text, tool_calls = call_nonstreaming(
                    messages, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                    tool_choice={"type": "function", "function": {"name": "create_workflow"}},
                )
            except Exception:
                pass  # fall through with the original reply

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
            if name == "create_workflow":
                created_workflow_this_turn = True

        messages[:] = [messages[0]] + messages[-(MAX_TURNS * 2):]
        _save_history(messages)


def main() -> None:
    args = sys.argv[1:]

    # --run-workflow: deterministic, LLM-free execution of a saved workflow.
    # This is what schedule_workflow's OS-level task/cron entry actually
    # calls — an unattended scheduled run must not depend on the model
    # correctly choosing to call execute_workflow; this runs it directly.
    if "--run-workflow" in args:
        idx = args.index("--run-workflow")
        if idx + 1 >= len(args):
            print("error: --run-workflow requires a workflow id", file=sys.stderr)
            sys.exit(1)
        from linai.tools import tool_execute_workflow
        workflow_id = args[idx + 1]
        result = tool_execute_workflow(workflow_id)
        print(result)
        return

    # Handle --model override early
    model = None
    if "--model" in args:
        idx = args.index("--model")
        if idx + 1 < len(args):
            model = args[idx + 1]
            cfg = load_config()
            key = "nvidia_model" if cfg.get("provider") in ("nvidia", "nvidia_nim") else "model"
            cfg[key] = model
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
        one_shot(question, model=model or get_runtime(cfg)["model"], temperature=temperature, max_tokens=max_tokens)
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
  linai --run-workflow <id>  run a saved workflow directly, no LLM call
                             (this is what a scheduled task/cron entry calls)
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
  schedule_workflow - register a workflow to run recurringly via Windows
                      Task Scheduler / cron (the only real scheduling
                      linai has — no built-in daemon)
  unschedule_workflow      - remove a workflow's recurring schedule
  list_scheduled_workflows - show workflows with an active schedule

Env:
  OPENROUTER_API_KEY   required
  LINAI_MODEL          default model id
  NO_COLOR             disable colors

Docs: https://github.com/linai
"""


if __name__ == "__main__":
    main()