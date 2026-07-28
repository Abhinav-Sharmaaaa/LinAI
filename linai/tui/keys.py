"""Raw key reading — termios + tty, no curses dependency."""

from __future__ import annotations

import select
import sys
import termios
import tty


class NoTerminalError(SystemExit):
    pass


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

    # Ctrl combinations
    ctrl_map = {
        "\x01": "ctrl_a", "\x02": "ctrl_b", "\x03": "ctrl_c", "\x04": "ctrl_d",
        "\x05": "ctrl_e", "\x06": "ctrl_f", "\x08": "ctrl_h", "\x09": "tab",
        "\x0a": "enter", "\x0b": "ctrl_k", "\x0c": "ctrl_l", "\x0d": "enter",
        "\x0e": "ctrl_n", "\x0f": "ctrl_o", "\x10": "ctrl_p", "\x11": "ctrl_q",
        "\x12": "ctrl_r", "\x13": "ctrl_s", "\x14": "ctrl_t", "\x15": "ctrl_u",
        "\x16": "ctrl_v", "\x17": "ctrl_w", "\x18": "ctrl_x", "\x19": "ctrl_y",
        "\x1a": "ctrl_z", "\x1b": "escape",
        "\x7f": "backspace",
    }

    if ch in ctrl_map:
        return ctrl_map[ch]

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
