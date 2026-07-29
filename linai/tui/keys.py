"""Raw key reading — termios+tty on POSIX, msvcrt on Windows. No curses dependency.

Both platforms expose the same public surface: getch() -> str, and
NoTerminalError, raised when there's no real terminal attached (piped
stdin, redirected input, etc). Callers (tui/app.py) don't need to know
which platform they're on.
"""

from __future__ import annotations

import os
import sys

# Shared key-name vocabulary both backends map onto. app.py compares
# getch()'s return value against these strings, plus single printable
# characters returned as-is.
_CTRL_NAMES = {
    1: "ctrl_a", 2: "ctrl_b", 3: "ctrl_c", 4: "ctrl_d",
    5: "ctrl_e", 6: "ctrl_f", 8: "ctrl_h", 9: "tab",
    10: "enter", 11: "ctrl_k", 12: "ctrl_l", 13: "enter",
    14: "ctrl_n", 15: "ctrl_o", 16: "ctrl_p", 17: "ctrl_q",
    18: "ctrl_r", 19: "ctrl_s", 20: "ctrl_t", 21: "ctrl_u",
    22: "ctrl_v", 23: "ctrl_w", 24: "ctrl_x", 25: "ctrl_y",
    26: "ctrl_z", 27: "escape",
    127: "backspace",
}


class NoTerminalError(SystemExit):
    pass


if os.name == "nt":
    import msvcrt

    # On Windows, the physical Backspace key sends 0x08 via msvcrt — the
    # same byte POSIX terminals use for Ctrl+H. Prioritize Backspace since
    # it's needed for basic text editing; Ctrl+H's "help overlay" binding
    # is unavoidably ambiguous with Backspace on Windows consoles (an
    # OS-level limitation, not something distinguishable here).
    _CTRL_NAMES_WIN = dict(_CTRL_NAMES)
    _CTRL_NAMES_WIN[8] = "backspace"

    # Scan codes following a 0x00 or 0xe0 prefix byte (msvcrt's convention
    # for "extended" keys: arrows, Home/End, PageUp/PageDown, Delete,
    # Insert, function keys). Same information POSIX gets via ESC [ X
    # sequences, just delivered as raw scan codes instead.
    _EXTENDED_MAP = {
        72: "up", 80: "down", 75: "left", 77: "right",
        71: "home", 79: "end",
        73: "pageup", 81: "pagedown",
        83: "delete", 82: "insert",
    }

    def getch() -> str:
        """Read a single key press. Returns a key name string."""
        if not sys.stdin.isatty():
            raise NoTerminalError("linai: no terminal attached (use one-shot mode or a real TTY)")
        try:
            first = msvcrt.getch()
        except OSError:
            raise NoTerminalError("linai: no terminal attached (use one-shot mode or a real TTY)")
        except KeyboardInterrupt:
            # The Windows console intercepts Ctrl+C as SIGINT by default
            # instead of handing msvcrt the raw 0x03 byte. Translate it to
            # the same "ctrl_c" string POSIX returns so app.py's existing
            # cancel-streaming handling works unchanged.
            return "ctrl_c"

        # Extended-key prefix: the real key is in a second byte.
        if first in (b"\x00", b"\xe0"):
            second = msvcrt.getch()
            code = second[0]
            if code in _EXTENDED_MAP:
                return _EXTENDED_MAP[code]
            # Unrecognized extended key (e.g. a function key) — no POSIX
            # equivalent is defined for these either, so just drop it.
            return ""

        code = first[0]
        if code in _CTRL_NAMES_WIN:
            return _CTRL_NAMES_WIN[code]

        try:
            ch = first.decode("utf-8")
        except UnicodeDecodeError:
            return ""

        return ch

else:
    import select
    import termios
    import tty

    def getch() -> str:
        """Read a single key press. Returns a key name string."""
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            raise NoTerminalError("linai: no terminal attached (use one-shot mode or a real TTY)")
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        if ch and ord(ch) in _CTRL_NAMES:
            return _CTRL_NAMES[ord(ch)]

        if ch == "\x1b":
            # Escape sequence
            if select.select([sys.stdin], [], [], 0.05)[0]:
                seq = sys.stdin.read(1)
                if seq == "[":
                    nxt = sys.stdin.read(1)
                    arrow_map = {"A": "up", "B": "down", "C": "right", "D": "left"}
                    if nxt in arrow_map:
                        return arrow_map[nxt]
                    # Home/End/PageUp: sequences like [1~, [4~, [5~, [6~
                    if nxt in "123456":
                        final = sys.stdin.read(1)
                        combined = nxt + final
                        seq_map = {
                            "1~": "home", "4~": "end",
                            "5~": "pageup", "6~": "pagedown",
                            "3~": "delete",
                        }
                        if combined in seq_map:
                            return seq_map[combined]
                        # Might be more chars, consume rest
                        while final and final not in "~":
                            final = sys.stdin.read(1)
                    return "escape"
                elif seq == "O":
                    # SS3 sequences (some terminals)
                    nxt = sys.stdin.read(1)
                    ss3_map = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
                    if nxt in ss3_map:
                        return ss3_map[nxt]
                return "escape"
            return "escape"

        if len(ch) == 1 and ch.isprintable():
            return ch

        # Fallback for any other char
        return ch