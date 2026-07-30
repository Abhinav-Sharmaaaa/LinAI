"""TUI rendering, input, and main loop."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import threading
from typing import Any, Optional

from linai import __version__
from linai.agent import SYSTEM_MSG, call_streaming, execute_tool, looks_like_recurring_request, made_tool_call
from linai.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    HISTORY_FILE,
    MAX_OUTPUT_LINES,
    MAX_TURNS,
    REDRAW_INTERVAL_MS,
    get_runtime,
    load_config,
    save_config,
)
from linai.tui.keys import getch
from linai.utils import C, cap, fetch_models, format_price, get_system_status, init_colors, strip_ansi, vislen, wrap_ansi

# ── Command handler result ────────────────────────────────────────────────
_HANDLED = True
_NOT_HANDLED = False

# ── Animation frames ──────────────────────────────────────────────────────
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
PROGRESS_FRAMES = ["▱▱▱▱▱", "▰▱▱▱▱", "▰▰▱▱▱", "▰▰▰▱▱", "▰▰▰▰▱", "▰▰▰▰▰"]
PULSE_FRAMES = ["●○○○", "○●○○", "○○●○", "○○○●", "○○●○", "○●○○"]


class TUI:
    def __init__(self, model: str | None = None, temperature: float | None = None,
                 max_tokens: int | None = None, no_color: bool = False):
        self.cols, self.lines = self._term_size()
        self.output_lines: list[str] = []
        self.view_offset: int = 0          # 0 = scrolled to bottom
        self.all_output: list[str] = []    # unbounded for scrollback
        self.history: list[str] = []
        # Input history (user commands) - load from loaded messages
        hist = [m["content"] for m in self._load_history() if m.get("role") == "user"]
        self.history = hist[-50:] if hist else []
        self.hist_idx: int = -1
        self.scrollback: int = 0
        self._model = model or get_runtime()["model"]
        self._temperature = temperature if temperature is not None else DEFAULT_CONFIG["temperature"]
        self._max_tokens = max_tokens if max_tokens is not None else DEFAULT_CONFIG["max_tokens"]
        self.messages: list[dict] = self._load_history()
        self._pending_cancel = False
        self._last_redraw = 0.0
        self._needs_redraw = True
        self._stdout_lock = threading.Lock()
        if no_color:
            init_colors(force_no_color=True)

    # ── history persistence ─────────────────────────────────────────────
    @staticmethod
    def _load_history() -> list[dict]:
        """Load conversation history from disk."""
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE) as f:
                    return [json.loads(line) for line in f if line.strip()]
        except Exception:
            pass
        return [{"role": "system", "content": SYSTEM_MSG}]

    @staticmethod
    def _save_history(messages: list[dict]) -> None:
        """Save conversation history to disk."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_FILE, "w") as f:
                for msg in messages:
                    f.write(json.dumps(msg) + "\n")
        except Exception:
            pass

    # ── terminal size ─────────────────────────────────────────────────
    @staticmethod
    def _term_size() -> tuple[int, int]:
        s = shutil.get_terminal_size()
        return max(s.columns, 40), max(s.lines, 12)

    # ── layout ────────────────────────────────────────────────────────
    @property
    def _out_top(self) -> int:
        return 2   # rows 1=header, 2=sep, 3+=output

    @property
    def _sep_row(self) -> int:
        return 2

    @property
    def _input_row(self) -> int:
        return self.lines

    @property
    def _status_row(self) -> int:
        return self.lines - 1

    @property
    def _out_rows(self) -> int:
        return max(0, self._status_row - self._out_top)

    # ── token estimate ────────────────────────────────────────────────
    def _context_tokens(self) -> int:
        total = sum(len(json.dumps(m)) for m in self.messages)
        return total // 4

    # ── redraw throttling ────────────────────────────────────────────
    def _should_redraw(self) -> bool:
        now = time.monotonic()
        if now - self._last_redraw >= REDRAW_INTERVAL_MS / 1000.0:
            self._last_redraw = now
            return True
        return False

    # ── drawing ───────────────────────────────────────────────────────
    def full_redraw(self, force: bool = False) -> None:
        if not force and not self._should_redraw():
            return
        self.cols, self.lines = self._term_size()
        c = C()

        # Header (row 1)
        sys.stdout.write(f"\033[1;1H")
        sys.stdout.write(self._header_line() + "\033[K")

        # Separator (row 2)
        bar = "─" * self.cols
        sys.stdout.write(f"\033[{self._sep_row};1H{c['dim']}{bar}{c['reset']}\033[K")

        # Output area
        out_rows = self._out_rows
        visible = self.output_lines[-out_rows:] if out_rows else []

        if self.view_offset > 0:
            # Scrolled up
            start = max(0, len(self.output_lines) - out_rows - self.view_offset)
            end = start + out_rows
            visible = self.output_lines[start:end]

        # Pad with empty lines if needed
        while len(visible) < out_rows:
            visible.insert(0, "")

        for i, row in enumerate(visible):
            sys.stdout.write(f"\033[{self._out_top + i};1H")
            plain = strip_ansi(row)
            if len(plain) > self.cols:
                row = plain[:self.cols]
            sys.stdout.write(row + "\033[K")

        # Clear remaining rows
        last_out = self._out_top + len(visible)
        for y in range(last_out, self._status_row):
            sys.stdout.write(f"\033[{y};1H\033[K")

        # Status separator
        sys.stdout.write(f"\033[{self._status_row};1H{c['dim']}{bar}{c['reset']}\033[K")

        # Input line
        sys.stdout.write(f"\033[{self._input_row};1H{c['cyan']}linai> {c['reset']}")

        # Scroll indicator
        if self.view_offset > 0:
            ind = f" ↑{self.view_offset} "
            sys.stdout.write(f"\033[{self._status_row};{self.cols - len(ind)}H{c['dim']}{ind}{c['reset']}")

        sys.stdout.flush()
        self._needs_redraw = False

    def _header_line(self) -> str:
        c = C()
        name = self._model.split("/")[-1]
        tok = self._context_tokens()
        left = f" {c['bold']}{c['cyan']}✦ linai{c['reset']}  {c['dim']}{name}{c['reset']}  {c['dim']}~{tok}tok{c['reset']}"
        hotkeys = f"{c['dim']}[/model /status /context /clear /help]{c['reset']} "
        pad = max(0, self.cols - vislen(left) - vislen(hotkeys))
        return left + " " * pad + hotkeys

    def draw_text(self, text: str) -> None:
        """Draw text with markdown rendering and syntax highlighting."""
        rendered = self._render_markdown(text)
        self.output_lines.extend(rendered)
        self.all_output.extend(rendered)
        self._trim_output()
        if self.view_offset > 0:
            self.view_offset = 0  # auto-scroll to bottom on new content
        self._needs_redraw = True

    def draw_user(self, text: str) -> None:
        c = C()
        self.draw_text(f"{c['cyan']}linai>{c['reset']} {text}")

    def draw_tool_call(self, name: str, raw_args: dict) -> None:
        c = C()
        s = f"[→ {name}({json.dumps(raw_args, separators=(',',':'))[:100]})]"
        self.draw_text(f"{c['yellow']}{s}{c['reset']}")

    def draw_tool_result(self, result: str) -> None:
        c = C()
        for ln in result.split("\n"):
            self.draw_text(f"{c['magenta']}  {ln}{c['reset']}")

    def draw_label(self, text: str) -> None:
        c = C()
        self.draw_text(f"{c['dim']}{text}{c['reset']}")

    def draw_error(self, text: str) -> None:
        c = C()
        self.draw_text(f"{c['red']}{text}{c['reset']}")

    def _trim_output(self) -> None:
        c = C()
        if len(self.output_lines) > MAX_OUTPUT_LINES:
            removed = len(self.output_lines) - MAX_OUTPUT_LINES
            self.output_lines = self.output_lines[-MAX_OUTPUT_LINES:]
            self.output_lines.insert(0, f"{c['dim']}…({removed} lines omitted)…{c['reset']}")

    # ── markdown rendering with syntax highlighting ────────────────────────
    def _render_markdown(self, text: str) -> list[str]:
        """Render markdown text to colored terminal lines with syntax highlighting."""
        c = C()
        lines = []

        in_code_block = False
        code_block_lang = ""
        code_lines = []

        for line in text.split("\n"):
            # Handle code blocks
            if line.startswith("```"):
                if not in_code_block:
                    # Starting code block
                    in_code_block = True
                    code_block_lang = line[3:].strip().lower()
                    code_lines = []
                else:
                    # Ending code block - render it
                    in_code_block = False
                    highlighted = self._highlight_code(code_lines, code_block_lang)
                    lines.extend(highlighted)
                    code_block_lang = ""
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            # Handle headers
            if line.startswith("# "):
                lines.append(f"{c['bold']}{c['cyan']}{line[2:]}{c['reset']}")
            elif line.startswith("## "):
                lines.append(f"{c['bold']}{c['blue']}{line[3:]}{c['reset']}")
            elif line.startswith("### "):
                lines.append(f"{c['bold']}{c['magenta']}{line[4:]}{c['reset']}")
            elif line.startswith("#### "):
                lines.append(f"{c['bold']}{line[5:]}{c['reset']}")

            # Handle horizontal rule
            elif line.strip() in ("---", "***", "___"):
                lines.append(f"{c['dim']}{'─' * self.cols}{c['reset']}")

            # Handle bullet points
            elif line.lstrip().startswith("- ") or line.lstrip().startswith("* "):
                indent = len(line) - len(line.lstrip())
                content = line.lstrip()[2:]
                lines.append(f"{' ' * indent}{c['dim']}•{c['reset']} {self._format_inline(content, c)}")

            # Handle numbered lists
            elif re.match(r"^\s*\d+\.\s", line):
                indent = len(line) - len(line.lstrip())
                match = re.match(r"^\s*(\d+\.)\s(.*)$", line)
                if match:
                    num, content = match.groups()
                    lines.append(f"{' ' * indent}{c['dim']}{num}{c['reset']} {self._format_inline(content, c)}")

            # Handle blockquotes
            elif line.lstrip().startswith("> "):
                content = line.lstrip()[2:]
                lines.append(f"{c['dim']}│{c['reset']} {c['italic'] if 'italic' in c else ''}{self._format_inline(content, c)}{c['reset']}")

            # Handle bold/italic inline in regular text
            else:
                lines.append(self._format_inline(line, c))

        # Handle unclosed code block
        if in_code_block and code_lines:
            highlighted = self._highlight_code(code_lines, code_block_lang)
            lines.extend(highlighted)

        return wrap_ansi("\n".join(lines), self.cols)

    def _highlight_code(self, code_lines: list[str], lang: str) -> list[str]:
        """Basic syntax highlighting for code blocks."""
        c = C()
        highlighted = []

        # Simple keyword highlighting for common languages
        keywords = {
            'python': ['def', 'class', 'import', 'from', 'as', 'if', 'else', 'elif', 'for', 'while', 'try', 'except', 'finally', 'with', 'return', 'yield', 'lambda', 'async', 'await', 'pass', 'break', 'continue', 'raise', 'assert', 'del', 'global', 'nonlocal', 'True', 'False', 'None', 'and', 'or', 'not', 'in', 'is'],
            'javascript': ['function', 'const', 'let', 'var', 'if', 'else', 'for', 'while', 'return', 'class', 'extends', 'import', 'export', 'async', 'await', 'try', 'catch', 'finally', 'true', 'false', 'null', 'undefined'],
            'bash': ['if', 'then', 'else', 'fi', 'for', 'while', 'do', 'done', 'case', 'esac', 'function', 'export', 'alias', 'source', 'cd', 'ls', 'grep', 'find', 'cat', 'echo'],
            'json': ['true', 'false', 'null'],
        }

        lang_keywords = keywords.get(lang, keywords['python'])
        keyword_pattern = re.compile(r'\b(' + '|'.join(re.escape(kw) for kw in lang_keywords) + r')\b')
        string_pattern = re.compile(r'(["\'])(?:\\.|[^\\])*?\1')
        comment_pattern = re.compile(r'(#.*|//.*|/\*.*?\*/)')
        number_pattern = re.compile(r'\b\d+\.?\d*\b')

        for line in code_lines:
            # Apply highlighting
            highlighted_line = line
            # Comments first (to avoid highlighting inside comments)
            highlighted_line = comment_pattern.sub(lambda m: f"{c['dim']}{m.group()}{c['reset']}", highlighted_line)
            # Strings
            highlighted_line = string_pattern.sub(lambda m: f"{c['green']}{m.group()}{c['reset']}", highlighted_line)
            # Numbers
            highlighted_line = number_pattern.sub(lambda m: f"{c['yellow']}{m.group()}{c['reset']}", highlighted_line)
            # Keywords
            highlighted_line = keyword_pattern.sub(lambda m: f"{c['bold']}{c['cyan']}{m.group()}{c['reset']}", highlighted_line)

            highlighted.append(highlighted_line)

        return highlighted

    def _format_inline(self, text: str, c: dict) -> str:
        """Format inline markdown: **bold**, *italic*, `code`, [link](url)."""
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', f"{c['bold']}\\1{c['reset']}", text)
        # Italic
        text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', f"{c['italic'] if 'italic' in c else ''}\\1{c['reset']}", text)
        # Inline code
        text = re.sub(r'`(.+?)`', f"{c['yellow']}\\1{c['reset']}", text)
        # Links
        text = re.sub(r'\[(.+?)\]\((.+?)\)', f"{c['blue']}\\1{c['reset']} {c['dim']}(\\2){c['reset']}", text)
        return text

    # ── animated progress display ──────────────────────────────────────────
    def _spin_while(self, target, *args, message: str = "", **kwargs):
        """Run a blocking call on a background thread while continuously
        animating a spinner in the status corner. Unlike ticking the
        spinner only when a chunk of text happens to arrive, this actually
        animates during the dead time before anything comes back at all —
        the initial network wait, or a slow tool call like web_search —
        which previously showed nothing whatsoever until something arrived.
        Returns target's return value; re-raises any exception it raised.
        """
        c = C()
        result_box: dict = {}
        error_box: dict = {}

        def _runner():
            try:
                result_box["value"] = target(*args, **kwargs)
            except BaseException as e:
                error_box["error"] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        i = 0
        # Fixed spot on the status row (right-aligned), same row the scroll
        # indicator uses. Must be an absolute cursor position — writing
        # relative to "wherever the cursor currently is" only holds still
        # if nothing else ever moves the cursor between ticks, which isn't
        # true here (tool output, redraws, scrolling all do).
        label_width = len(message) + 4
        col = max(1, self.cols - label_width)
        try:
            while t.is_alive():
                with self._stdout_lock:
                    frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                    suffix = f" {c['dim']}{message}{c['reset']}" if message else ""
                    sys.stdout.write(f"\033[s\033[{self._status_row};{col}H{c['dim']}{frame}{c['reset']}{suffix}\033[K\033[u")
                    sys.stdout.flush()
                i += 1
                time.sleep(0.08)
        finally:
            # Always clear the spinner, even if KeyboardInterrupt fired
            # mid-sleep and is about to unwind straight out of this
            # function — otherwise a stray frame is left on screen.
            with self._stdout_lock:
                sys.stdout.write(f"\033[s\033[{self._status_row};{col}H\033[K\033[u")
                sys.stdout.flush()
        t.join()

        if "error" in error_box:
            raise error_box["error"]
        return result_box.get("value")

    # ── input ──────────────────────────────────────────────────────────
    def read_line(self) -> str | None:
        """Read one line. Returns None on quit, '' for no-op."""
        self.full_redraw(force=True)
        buf = ""
        cursor = 0

        while True:
            self._redraw_input(buf, cursor)
            try:
                ch = getch()
            except Exception:
                return None

            if ch == "ctrl_d":
                if buf:
                    # Delete char under cursor
                    buf = buf[:cursor] + buf[cursor + 1:]
                    self._redraw_input(buf, cursor)
                    continue
                return None

            if ch == "escape":
                return None

            if ch == "enter":
                print()
                return buf.strip()

            if ch == "ctrl_l":
                self.output_lines.clear()
                self.all_output.clear()
                self.view_offset = 0
                self.full_redraw(force=True)
                return ""

            if ch == "ctrl_m" or ch == "ctrl_t":
                return self._model_picker()

            if ch == "ctrl_h":
                self._draw_help()
                return ""

            if ch == "ctrl_y":
                self._copy_last_response()
                return ""

            if ch == "backspace":
                if cursor > 0:
                    buf = buf[:cursor - 1] + buf[cursor:]
                    cursor -= 1
                continue

            if ch == "left":
                cursor = max(0, cursor - 1)
                continue

            if ch == "right":
                cursor = min(len(buf), cursor + 1)
                continue

            if ch == "home" or ch == "ctrl_a":
                cursor = 0
                continue

            if ch == "end" or ch == "ctrl_e":
                cursor = len(buf)
                continue

            if ch == "ctrl_u":
                buf = ""
                cursor = 0
                continue

            if ch == "ctrl_k":
                buf = buf[:cursor]
                continue

            if ch == "up":
                if self.history and self.hist_idx < len(self.history) - 1:
                    self.hist_idx += 1
                    buf = self.history[-(self.hist_idx + 1)]
                    cursor = len(buf)
                continue

            if ch == "down":
                if self.hist_idx > 0:
                    self.hist_idx -= 1
                    buf = self.history[-(self.hist_idx + 1)]
                    cursor = len(buf)
                elif self.hist_idx == 0:
                    self.hist_idx = -1
                    buf = ""
                    cursor = 0
                continue

            if ch == "pageup":
                self.view_offset += max(1, self._out_rows - 2)
                self.full_redraw(force=True)
                continue

            if ch == "pagedown":
                self.view_offset = max(0, self.view_offset - max(1, self._out_rows - 2))
                self.full_redraw(force=True)
                continue

            if ch == "tab":
                continue

            if len(ch) == 1 and ch.isprintable():
                buf = buf[:cursor] + ch + buf[cursor:]
                cursor += 1
                continue

    def _redraw_input(self, buf: str, cursor: int) -> None:
        c = C()
        y = self._input_row
        sys.stdout.write(f"\033[{y};1H\033[K{c['cyan']}linai> {c['reset']}{buf}")
        sys.stdout.write(f"\033[{y};{8 + cursor}H")
        sys.stdout.flush()

    # ── model picker ───────────────────────────────────────────────────
    def _model_picker(self) -> str:
        c = C()
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write(f"\033[36m  model picker — loading catalog …\033[0m\n")
        sys.stdout.flush()

        all_models = fetch_models()
        if not all_models:
            provider = get_runtime()["provider"]
            hint = "NVIDIA_API_KEY" if provider == "nvidia_nim" else "OPENROUTER_API_KEY"
            sys.stdout.write(f"\033[33m  (fetch failed for {provider}; check {hint})\033[0m\n")
            try:
                input("\nPress Enter to return...")
            except (EOFError, KeyboardInterrupt):
                pass
            self.full_redraw(force=True)
            return ""

        # Sort: free first, then by prompt price ascending
        def price_key(m):
            try:
                p = float(m.get("pricing", {}).get("prompt", "1"))
                return (0, p) if p <= 0 else (1, p)
            except (ValueError, TypeError):
                return (2, 0)

        all_models.sort(key=price_key)

        # Filter to text-capable models
        candidates = [
            m for m in all_models
            if "text" in json.dumps(m.get("architecture", {})).lower()
            or m.get("id")
        ]
        if not candidates:
            candidates = all_models
        if len(candidates) > 30:
            candidates = candidates[:30]

        cols_w = max(len(m["id"]) for m in candidates) + 2
        sys.stdout.write("\033[2J\033[H")
        for i, m in enumerate(candidates, 1):
            price = format_price(m.get("pricing", {}).get("prompt", "?"))
            marker = " *" if m["id"] == self._model else "  "
            sys.stdout.write(f"\033[36m{i:>2}\033[0m{marker} \033[1m{m['id']:<{cols_w}}\033[0m  {price}\n")
        sys.stdout.write("\n")

        try:
            raw = input("\033[36mpick # (or Enter to keep current) >\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""

        if raw:
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(candidates):
                    new_model = candidates[idx]["id"]
                    if new_model != self._model:
                        self._model = new_model
                        self._save_model(new_model)
                        self.draw_label(f"model → {new_model}")
            except (ValueError, IndexError):
                # Allow typing a model id directly
                hit = next((m for m in candidates if m["id"] == raw), None)
                if hit:
                    self._model = hit["id"]
                    self._save_model(hit["id"])
                    self.draw_label(f"model → {hit['id']}")

        self.full_redraw(force=True)
        return ""

    @staticmethod
    def _save_model(model_id: str) -> None:
        try:
            cfg = load_config()
            key = "nvidia_model" if cfg.get("provider") in ("nvidia", "nvidia_nim") else "model"
            save_config({key: model_id})
        except Exception:
            pass

    # ── copy to clipboard ──────────────────────────────────────────────
    def _copy_last_response(self) -> None:
        c = C()
        # Find the last assistant response in all_output
        if not self.all_output:
            self.draw_label("nothing to copy")
            return

        # Plain text of all output (for clipboard)
        plain = "\n".join(strip_ansi(ln) for ln in self.all_output)
        for cmd in [
            ["xclip", "-selection", "clipboard"],
            ["xclip", "-selection", "primary"],
            ["wl-copy"],
            ["pbcopy"],
        ]:
            try:
                r = subprocess.run(cmd, input=plain, text=True, timeout=3, capture_output=True)
                if r.returncode == 0:
                    self.draw_label("copied to clipboard")
                    return
            except Exception:
                continue
        self.draw_label("clipboard copy failed (install xclip or wl-clipboard)")

    # ── help overlay ───────────────────────────────────────────────────
    def _draw_help(self) -> None:
        c = C()
        lines = [
            "",
            f"  {c['bold']}linai v{__version__} hotkeys{c['reset']}",
            f"  Ctrl+D / Esc   quit",
            f"  Ctrl+L         clear conversation",
            f"  Ctrl+M         model picker (shows price, sorted)",
            f"  Ctrl+Y         copy output to clipboard",
            f"  Ctrl+H         this help",
            f"  Up / Down      input history",
            f"  PageUp / Down  scroll output",
            f"  Ctrl+A / Home  start of line",
            f"  Ctrl+E / End   end of line",
            f"  Ctrl+U         clear line",
            f"  Ctrl+K         kill to end of line",
            f"  Ctrl+C         cancel current request",
            "",
            f"  {c['bold']}commands (type at prompt){c['reset']}",
            f"  /model, /m     pick model",
            f"  /status, /s    system snapshot",
            f"  /context, /c   token-budget snapshot",
            f"  /workflows, /w list saved workflows",
            f"  /clear         reset conversation",
            f"  /help          this screen",
            "",
        ]
        for ln in lines:
            self.draw_text(ln)

    # ── built-in commands ──────────────────────────────────────────────
    def handle_command(self, cmd: str) -> bool:
        c = C()
        cmd = cmd.strip().lower()

        if cmd in ("/model", "/m"):
            return _HANDLED  # handled by Ctrl+M via read_line

        if cmd in ("/status", "/s"):
            status = get_system_status()
            self.draw_text(f"{c['cyan']}{status}{c['reset']}")
            return _HANDLED

        if cmd in ("/context", "/c"):
            tok = self._context_tokens()
            self.draw_text(f"{c['dim']}context: {len(self.messages)} msgs, ~{tok} tok{c['reset']}")
            return _HANDLED

        if cmd == "/clear":
            self.messages = [{"role": "system", "content": SYSTEM_MSG}]
            self._save_history(self.messages)
            self.draw_label("conversation cleared")
            return _HANDLED

        if cmd in ("/help", "/h"):
            self._draw_help()
            return _HANDLED

        if cmd in ("/workflows", "/w"):
            from linai.tools import tool_list_workflows
            result = tool_list_workflows({})
            self.draw_text(f"{c['cyan']}{result}{c['reset']}")
            return _HANDLED

        return _NOT_HANDLED

    # ── agent / model streaming + tool loop ────────────────────────────
    def run_agent(self, user_text: str) -> None:
        c = C()
        self.messages.append({"role": "user", "content": user_text})
        self._save_history(self.messages)  # Save immediately after user message

        recurring_intent = looks_like_recurring_request(user_text)
        forced_workflow_retry_used = False
        created_workflow_this_turn = False

        while True:
            reply_chunks: list[str] = []
            tool_calls: list[dict] = []
            self._pending_cancel = False

            # _quick_draw_chunk renders each raw network chunk independently
            # for a live "typing" effect, but a chunk can split mid-word or
            # mid-markdown-token — rendering it in isolation produces
            # garbled/cut-off lines. Remember where this turn's provisional
            # lines start so we can discard them once we have the complete
            # text and re-render it correctly in one pass.
            stream_line_start = len(self.output_lines)
            stream_all_start = len(self.all_output)

            def on_text(chunk: str) -> None:
                # Ctrl+C interrupts this thread's wait loop, not the
                # in-flight network call on the background thread — if
                # that orphaned call is still streaming when the user has
                # already moved on, don't let it keep mutating output.
                if self._pending_cancel:
                    return
                reply_chunks.append(chunk)
                self._quick_draw_chunk(chunk)

            try:
                # Runs call_streaming on a background thread while this
                # thread continuously animates the spinner — including
                # during the network wait before the first token arrives,
                # which previously showed nothing at all.
                reply_text, tool_calls = self._spin_while(
                    call_streaming,
                    self.messages,
                    model=self._model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    on_text=on_text,
                    message="thinking...",
                )
            except KeyboardInterrupt:
                self._pending_cancel = True
                self.draw_label("(cancelled)")
                self.messages.append({"role": "assistant", "content": "(cancelled by user)"})
                self._save_history(self.messages)
                self.full_redraw(force=True)
                return
            except Exception as e:
                self.draw_error(f"model error: {e}")
                self.messages.append({"role": "assistant", "content": f"(error: {e})"})
                self._save_history(self.messages)
                self.full_redraw(force=True)
                return

            if made_tool_call(tool_calls, "create_workflow"):
                created_workflow_this_turn = True

            if (not tool_calls and recurring_intent and not created_workflow_this_turn
                    and not forced_workflow_retry_used):
                forced_workflow_retry_used = True
                self.messages.append({"role": "assistant", "content": reply_text})
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Actually create this as a saved workflow now with create_workflow, "
                        "then schedule it with schedule_workflow so it runs automatically as requested."
                    ),
                })
                try:
                    reply_text, tool_calls = self._spin_while(
                        call_streaming,
                        self.messages,
                        model=self._model,
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                        on_text=on_text,
                        message="thinking...",
                        tool_choice={"type": "function", "function": {"name": "create_workflow"}},
                    )
                except Exception:
                    pass  # fall through with the original reply
                # Undo the two nudge messages we just appended above — the
                # normal assistant_msg/tool result appends below will add
                # the real turn back in, whether or not the retry helped.
                del self.messages[-2:]

            # Discard the provisional per-chunk lines drawn live during
            # streaming and re-render the complete text in one pass — this
            # is what actually fixes broken word-wrap/markdown, since
            # _render_markdown needs the whole line/block to format it
            # correctly, not an arbitrary network-chunk-sized slice of it.
            del self.output_lines[stream_line_start:]
            del self.all_output[stream_all_start:]
            if reply_text:
                self.draw_text(reply_text)

            # Ensure trailing newline before tools
            if reply_text and not reply_text.endswith("\n"):
                self.draw_text("")

            # Build assistant message
            assistant_msg: dict = {"role": "assistant", "content": reply_text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            if not tool_calls:
                # Save history for normal (non-tool) responses
                self._save_history(self.messages)
                break

            for tc in tool_calls:
                raw_args: dict = {}
                try:
                    raw_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    pass
                self.draw_tool_call(tc["function"]["name"], raw_args)
                try:
                    result = cap(str(self._spin_while(
                        execute_tool, tc, message=f"running {tc['function']['name']}...",
                    )))
                except KeyboardInterrupt:
                    self._pending_cancel = True
                    result = "(cancelled by user)"
                self.draw_tool_result(result)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": result,
                })
                if tc["function"]["name"] == "create_workflow":
                    created_workflow_this_turn = True

            # Trim context window
            self.messages[:] = [self.messages[0]] + self.messages[-(MAX_TURNS * 2):]
            # Save after trimming
            self._save_history(self.messages)

        self.draw_text("")
        self.full_redraw(force=True)

    def _quick_draw_chunk(self, chunk: str) -> None:
        """Fast inline draw for streaming chunks — no full redraw."""
        c = C()
        rendered = self._render_markdown(chunk)
        self.output_lines.extend(rendered)
        self.all_output.extend(rendered)
        if self.view_offset == 0:
            self._needs_redraw = True

    # ── main loop ──────────────────────────────────────────────────────
    def run(self) -> None:
        init_colors()
        sys.stdout.write("\033[2J\033[H")
        self.full_redraw(force=True)

        # Display prior conversation if history was loaded
        if len(self.messages) > 1:
            c = C()
            self.draw_label("(continuing prior conversation)")
            for msg in self.messages[1:]:  # Skip system message
                if msg.get("role") == "user":
                    self.draw_text(f"{c['cyan']}linai> {c['reset']}{msg.get('content', '')}")
                elif msg.get("role") == "assistant":
                    self.draw_text(msg.get("content", ""))

        while True:
            line = self.read_line()
            if line is None:
                break
            if not line:
                continue
            if line.startswith("/") or line in ("help", "status", "model", "context", "clear"):
                if self.handle_command("/" + line.lstrip("/")):
                    self.full_redraw(force=True)
                    continue
            self.history.append(line)
            self.hist_idx = -1
            self.draw_user(line)
            self.run_agent(line)

        # Save history on exit
        self._save_history(self.messages)

        sys.stdout.write("\033[2J\033[H")
        c = C()
        sys.stdout.write(f"{c['dim']}bye{c['reset']}\n")
        sys.stdout.flush()