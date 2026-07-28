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


def _get_nvidia_nim_key() -> str | None:
    """Get NVIDIA NIM API key from environment."""
    return os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")


def _get_nvidia_nim_base_url() -> str:
    """Get NVIDIA NIM API base URL from environment or use default."""
    url = os.environ.get("NVIDIA_NIM_API_URL") or os.environ.get("NVIDIA_API_URL")
    if url:
        return url.rstrip("/")
    # Default to NVIDIA's hosted NIM API
    return "https://integrate.api.nvidia.com/v1"


# API Provider selection
API_PROVIDER = os.environ.get("LINAI_API_PROVIDER", "openrouter")  # "openrouter" or "nvidia_nim"

# OpenRouter
OPENROUTER_KEY: str | None = _get_openrouter_key()
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# NVIDIA NIM
NVIDIA_NIM_KEY: str | None = _get_nvidia_nim_key()
NVIDIA_NIM_BASE_URL: str = _get_nvidia_nim_base_url()
NVIDIA_NIM_API_URL = f"{NVIDIA_NIM_BASE_URL}/chat/completions"
NVIDIA_NIM_MODELS_URL = f"{NVIDIA_NIM_BASE_URL}/models"

# Active API (determined by provider)
if API_PROVIDER == "nvidia_nim":
    API_URL = NVIDIA_NIM_API_URL
    API_MODELS_URL = NVIDIA_NIM_MODELS_URL
    API_KEY = NVIDIA_NIM_KEY
else:
    API_URL = OPENROUTER_API_URL
    API_MODELS_URL = OPENROUTER_MODELS_URL
    API_KEY = OPENROUTER_KEY

# Config paths
HOME = Path.home()
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

# Default models per provider
DEFAULT_OPENROUTER_MODEL = os.environ.get("LINAI_OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free")
DEFAULT_NVIDIA_NIM_MODEL = os.environ.get("LINAI_NVIDIA_NIM_MODEL", "nvidia/nemotron-3-ultra")

if API_PROVIDER == "nvidia_nim":
    DEFAULT_MODEL = DEFAULT_NVIDIA_NIM_MODEL
else:
    DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL

DEFAULT_CONFIG: dict[str, Any] = {
    "model": os.environ.get("LINAI_MODEL", DEFAULT_MODEL),
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