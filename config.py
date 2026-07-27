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
    # Try standard OpenRouter env var first
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # Try NVIDIA-specific env vars (for compatibility)
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")
    if key:
        return key
    # Check claudish active key file
    claudish_key_file = HOME / ".claudish_active_key"
    if claudish_key_file.exists():
        try:
            content = claudish_key_file.read_text()
            match = re.search(r'OPENROUTER_API_KEY=(.+)', content)
            if match:
                return match.group(1).strip('"\n')
        except Exception:
            pass
    return None


OPENROUTER_KEY: str | None = _get_openrouter_key()

# OpenRouter API endpoints
API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Config paths
CONFIG_DIR = HOME / ".config" / "linai"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
MODEL_CACHE = CONFIG_DIR / ".models.json"

# Constants
MAX_TURNS = 8
MAX_TOOL_OUTPUT = 3000
MAX_OUTPUT_LINES = 500
MODEL_CACHE_TTL = 86400
STREAM_READ_SIZE = 8192
REDRAW_INTERVAL_MS = 40

# Default model - using a reliable free model from OpenRouter
DEFAULT_CONFIG: dict[str, Any] = {
    "model": os.environ.get("LINAI_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
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

# Windows-specific protected paths
if os.name == "nt":
    WRITE_DENY_PREFIXES.extend([
        str(HOME / "AppData") + os.sep,
        "C:\\Windows\\",
        "C:\\Program Files\\",
        "C:\\Program Files (x86)\\",
    ])