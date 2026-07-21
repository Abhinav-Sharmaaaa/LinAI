"""Config + constants."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

HOME = Path.home()

def _get_openrouter_key() -> str | None:
    """Get OpenRouter key from environment or claudish active key file."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # Check claudish active key file
    claudish_key_file = HOME / ".claudish_active_key"
    if claudish_key_file.exists():
        try:
            content = claudish_key_file.read_text()
            # Extract export OPENROUTER_API_KEY="..." line
            match = re.search(r'OPENROUTER_API_KEY=(.+)', content)
            if match:
                return match.group(1).strip('"\n')
        except Exception:
            pass
    return None


OPENROUTER_KEY: str | None = _get_openrouter_key()

API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_MODELS_URL = "https://openrouter.ai/api/v1/models"
CONFIG_DIR = HOME / ".config" / "linai"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
MODEL_CACHE = CONFIG_DIR / ".models.json"
MAX_TURNS = 8
MAX_TOOL_OUTPUT = 3000
MAX_OUTPUT_LINES = 500       # bounded output buffer for TUI
MODEL_CACHE_TTL = 86400     # 24h in seconds
STREAM_READ_SIZE = 8192     # bytes per read during streaming
REDRAW_INTERVAL_MS = 40     # minimum ms between redraws

DEFAULT_CONFIG: dict[str, Any] = {
    "model": os.environ.get("LINAI_MODEL", "cohere/north-mini-code:free"),
    "temperature": 0.2,
    "max_tokens": 1000,
}

# Shell commands that are always denied (absolute deny list)
DENY_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"mkfs\.",
    r"dd\s+if=",
    r":\(\)\s*\{.*&\s*\};",   # fork bomb
    r">\s*/dev/sd",
    r"chmod\s+4777",
    r"chmod\s+-R\s+777\s+/",
]

# Paths the agent must never write to
WRITE_DENY_PREFIXES = [
    str(HOME / ".ssh") + "/",
    str(HOME / ".gnupg") + "/",
    str(HOME / ".password-store") + "/",
    "/etc/",
    "/usr/",
    "/boot/",
]
