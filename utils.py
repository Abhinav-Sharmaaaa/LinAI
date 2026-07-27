"""Shared utilities: colors, text wrapping, disk status, model cache."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from linai.config import (
    API_MODELS_URL,
    CONFIG_DIR,
    HOME,
    MAX_TOOL_OUTPUT,
    MODEL_CACHE,
    MODEL_CACHE_TTL,
    OPENROUTER_KEY,
)

# ── Color support ────────────────────────────────────────────────────────

_NO_COLOR = os.environ.get("NO_COLOR", "")

# Pre-compiled ANSI strip regex
_RE_ANSI = re.compile(r"\033\[[0-9;]*[a-zA-Z]")

# Color map — only populated when colors are enabled
_C: dict[str, str] = {}


def init_colors(force_no_color: bool = False) -> dict[str, str]:
    """Return color escape map, or empty strings if NO_COLOR is set."""
    global _C
    if force_no_color or _NO_COLOR:
        _C = {k: "" for k in [
            "reset", "bold", "dim", "red", "green", "yellow",
            "blue", "magenta", "cyan", "white", "bg_dim",
        ]}
        return _C
    _C = {
        "reset":   "\033[0m",
        "bold":    "\033[1m",
        "dim":     "\033[2m",
        "italic":  "\033[3m",
        "red":     "\033[31m",
        "green":   "\033[32m",
        "yellow":  "\033[33m",
        "blue":    "\033[34m",
        "magenta": "\033[35m",
        "cyan":    "\033[36m",
        "white":   "\033[37m",
        "bg_dim":  "\033[48;5;236m",
    }
    return _C


def C() -> dict[str, str]:
    """Get the color map (lazy init)."""
    if not _C:
        init_colors()
    return _C


def strip_ansi(s: str) -> str:
    return _RE_ANSI.sub("", s)


def vislen(s: str) -> int:
    return len(strip_ansi(s))


def cap(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…(truncated, {len(text)} chars)"
    return text


# ── ANSI-aware word wrap ─────────────────────────────────────────────────

def wrap_ansi(text: str, width: int) -> list[str]:
    """Wrap text preserving ANSI escape sequences.

    Tracks ANSI state across line breaks so color codes carry forward.
    O(n) single pass through the text.
    """
    if width < 1:
        width = 1

    lines: list[str] = []
    cur_line = ""
    cur_vis = 0
    ansi_buf = ""       # accumulates ANSI escape characters
    in_ansi = False
    carry_ansi = ""     # ANSI codes to prepend to next line

    # Split into words for word-wrap, but preserve newlines
    for paragraph in text.split("\n"):
        if cur_line:
            lines.append(cur_line)
            cur_line = carry_ansi
            cur_vis = 0

        words = paragraph.split(" ")
        for wi, word in enumerate(words):
            # Process the word character by character to handle ANSI
            word_vis = 0
            word_ansi_prefix = ""
            i = 0
            while i < len(word):
                ch = word[i]
                if ch == "\033":
                    # Start of ANSI sequence
                    j = i + 1
                    if j < len(word) and word[j] == "[":
                        j += 1
                        while j < len(word) and word[j] in "0123456789;":
                            j += 1
                        if j < len(word) and word[j] in "a-zA-Z":
                            seq = word[i:j + 1]
                            word_ansi_prefix += seq
                            i = j + 1
                            continue
                word_vis += 1
                i += 1

            # Space before word (if not first word)
            space_vis = 1 if (cur_vis > 0 and wi > 0) else 0
            needed = word_vis + space_vis

            if cur_vis + needed > width and cur_vis > 0:
                # Wrap
                lines.append(cur_line)
                cur_line = carry_ansi + word_ansi_prefix + word
                cur_vis = word_vis
            else:
                if wi > 0 and cur_vis > 0:
                    cur_line += " "
                    cur_vis += 1
                cur_line += word_ansi_prefix + word
                cur_vis += word_vis

            # Extract trailing ANSI codes for carry
            trailing_ansi = ""
            for m in _RE_ANSI.finditer(word):
                if m.end() == len(word):
                    trailing_ansi += m.group()
            if trailing_ansi:
                carry_ansi = trailing_ansi

    if cur_line:
        lines.append(cur_line)

    return lines or [""]


# ── Model cache ───────────────────────────────────────────────────────────

def fetch_models() -> list[dict]:
    """Fetch and cache model list from OpenRouter. Returns trimmed model dicts."""
    try:
        req = urllib.request.Request(
            API_MODELS_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        models = data.get("data", [])
        # Trim to only the fields we need
        trimmed = []
        for m in models:
            trimmed.append({
                "id": m.get("id", ""),
                "pricing": {"prompt": m.get("pricing", {}).get("prompt", "1")},
                "context_length": m.get("context_length", 0),
                "architecture": {"modality": m.get("architecture", {}).get("modality", "")},
            })
        cache = {"fetched_at": time.time(), "data": trimmed}
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(MODEL_CACHE, "w") as f:
            json.dump(cache, f)
        return trimmed
    except Exception:
        pass

    # Fallback to cache
    if MODEL_CACHE.exists():
        try:
            with open(MODEL_CACHE) as f:
                cached = json.load(f)
            # Check TTL
            fetched_at = cached.get("fetched_at", 0)
            if time.time() - fetched_at < MODEL_CACHE_TTL:
                return cached.get("data", [])
            # Stale but usable
            return cached.get("data", [])
        except Exception:
            pass
    return []


def format_price(prompt_str: str) -> str:
    try:
        p = float(prompt_str)
        if p <= 0:
            return "FREE"
        return f"${p * 1_000_000:.1f}/1M tok"
    except (ValueError, TypeError):
        return "?"


# ── System status ─────────────────────────────────────────────────────────

def get_system_status() -> str:
    """Return a one-line system status string."""
    parts: list[str] = []

    # Disk
    try:
        if os.name == "nt":
            # Windows: use wmic
            r = subprocess.run(
                ["wmic", "logicaldisk", "get", "size,freespace,caption"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.strip().splitlines():
                if line.strip() and not line.startswith("Caption"):
                    parts_ = line.split()
                    if len(parts_) >= 3:
                        free = int(parts_[0]) // (1024**3)
                        total = int(parts_[1]) // (1024**3)
                        pct = int((1 - free/total) * 100) if total > 0 else 0
                        parts.append(f"disk: {free}G/{total}G ({pct}%)")
                        break
        else:
            r = subprocess.run(
                ["df", "/", "--output=used,size,pcent"],
                capture_output=True, text=True, timeout=3,
            )
            lines = r.stdout.strip().splitlines()
            if len(lines) >= 2:
                cols = lines[1].split()
                if len(cols) >= 3:
                    parts.append(f"disk: {cols[0]}/{cols[1]} ({cols[2]})")
    except Exception:
        pass

    # Load (Linux/macOS only)
    if os.name != "nt":
        try:
            with open("/proc/loadavg") as f:
                load = f.read().split()[0]
            parts.append(f"load: {load}")
        except Exception:
            pass

    # Memory
    try:
        if os.name == "nt":
            # Windows: use wmic
            r = subprocess.run(
                ["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/value"],
                capture_output=True, text=True, timeout=5,
            )
            total = free = 0
            for line in r.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "TotalVisibleMemorySize":
                        total = int(v.strip()) // 1024
                    elif k.strip() == "FreePhysicalMemory":
                        free = int(v.strip()) // 1024
            if total > 0:
                used = total - free
                parts.append(f"mem: {used}M/{total}M")
        else:
            r = subprocess.run(
                ["free", "-h"], capture_output=True, text=True, timeout=3,
            )
            for line in r.stdout.splitlines():
                if line.startswith("Mem:"):
                    cols = line.split()
                    if len(cols) >= 3:
                        parts.append(f"mem: {cols[1]}/{cols[2]}")
                    break
    except Exception:
        pass

    # Uptime
    try:
        if os.name == "nt":
            # Windows uptime
            r = subprocess.run(
                ["wmic", "os", "get", "LastBootUpTime", "/value"],
                capture_output=True, text=True, timeout=5,
            )
            import datetime
            for line in r.stdout.strip().splitlines():
                if "=" in line:
                    val = line.split("=", 1)[1].strip()
                    if val:
                        # WMI format: 20240115123456.000000+000
                        boot = datetime.datetime.strptime(val[:14], "%Y%m%d%H%M%S")
                        up = datetime.datetime.now() - boot
                        days = up.days
                        hours = up.seconds // 3600
                        mins = (up.seconds % 3600) // 60
                        if days:
                            parts.append(f"up: {days}d {hours}h {mins}m")
                        else:
                            parts.append(f"up: {hours}h {mins}m")
                        break
        else:
            r = subprocess.run(
                ["uptime", "-p"], capture_output=True, text=True, timeout=3,
            )
            parts.append(f"up: {r.stdout.strip().removeprefix('up ')}")
    except Exception:
        pass

    # Hostname
    try:
        parts.append(f"host: {os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'unknown')}")
    except Exception:
        pass

    return "  ".join(parts) if parts else "(status unavailable)"


def get_disk_free_report() -> str:
    """Probe common reclaimable areas. Returns a ranked table."""
    if os.name == "nt":
        # Windows cache locations
        probes: list[tuple[str, list[str]]] = [
            ("TEMP",           ["powershell", "-c", "Get-ChildItem $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Sum Length"]),
            ("Recycle Bin",    ["powershell", "-c", "Get-ChildItem 'shell:RecycleBinFolder' -Recurse -ErrorAction SilentlyContinue | Measure-Object -Sum Length"]),
            ("npm cache",      ["powershell", "-c", "Get-ChildItem '~\\.npm\\_cacache' -Recurse -ErrorAction SilentlyContinue | Measure-Object -Sum Length"]),
            ("pip cache",      ["powershell", "-c", "Get-ChildItem '~\\AppData\\Local\\pip\\Cache' -Recurse -ErrorAction SilentlyContinue | Measure-Object -Sum Length"]),
            ("Chocolatey",     ["powershell", "-c", "Get-ChildItem 'C:\\ProgramData\\chocolatey\\lib' -Recurse -ErrorAction SilentlyContinue | Measure-Object -Sum Length"]),
            ("Docker",         ["docker", "system", "df"]),
        ]
    else:
        probes = [
            ("~/.cache/",              ["du", "-sh", str(HOME / ".cache")]),
            ("journal",                ["journalctl", "--disk-usage"]),
            ("/var/log/",              ["du", "-sh", "/var/log"]),
            ("/var/tmp/",              ["du", "-sh", "/var/tmp"]),
            ("/tmp/",                  ["du", "-sh", "/tmp"]),
            ("trash",                  ["du", "-sh", str(HOME / ".local/share/Trash/files")]),
            ("npm cache",              ["du", "-sh", str(HOME / ".npm/_cacache")]),
            ("pacman cache",           ["du", "-sh", "/var/cache/pacman/pkg"]),
            ("yay cache",              ["du", "-sh", str(HOME / ".cache/yay")]),
            ("flatpak unused",         ["flatpak", "uninstall", "--unused", "-y", "--dry-run"]),
            ("docker",                 ["docker", "system", "df"]),
        ]

    rows: list[tuple[str, str]] = []
    for label, cmd in probes:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                cwd=str(HOME),
            )
            val = (r.stdout + r.stderr).strip().splitlines()
            val = val[0] if val else "-"
        except Exception:
            val = "-"
        if val and val != "-":
            rows.append((label, val))

    if not rows:
        return "(no reclaimable areas found)"

    w = max(len(a) for a, _ in rows)
    return "\n".join(f"  {a:<{w}}  {b}" for a, b in rows)