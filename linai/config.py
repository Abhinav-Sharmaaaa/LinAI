"""Config + constants."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

HOME = Path.home()

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

DEFAULT_OPENROUTER_MODEL = os.environ.get("LINAI_OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free")
DEFAULT_NVIDIA_NIM_MODEL = os.environ.get("LINAI_NVIDIA_NIM_MODEL", "nvidia/nemotron-3-ultra")
DEFAULT_NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# The on-disk schema (as written by webui.py). "provider" is the toggle.
# "nvidia" and "nvidia_nim" are treated as synonyms everywhere.
DEFAULT_CONFIG: dict[str, Any] = {
    "provider": os.environ.get("LINAI_API_PROVIDER", "openrouter"),
    "openrouter_key": "",
    "nvidia_key": "",
    "nvidia_url": DEFAULT_NVIDIA_NIM_BASE_URL,
    "model": os.environ.get("LINAI_MODEL", DEFAULT_OPENROUTER_MODEL),
    "nvidia_model": os.environ.get("LINAI_NVIDIA_NIM_MODEL", DEFAULT_NVIDIA_NIM_MODEL),
    "temperature": 0.2,
    "max_tokens": 1000,
}


def normalize_provider(provider: str | None) -> str:
    """Canonicalize provider strings. 'nvidia' (from webui) == 'nvidia_nim'."""
    if provider in ("nvidia", "nvidia_nim"):
        return "nvidia_nim"
    return "openrouter"


def load_config() -> dict[str, Any]:
    """Read config.json fresh from disk, merged with defaults.

    This is the single source of truth. Every module (agent, cli, webui,
    server) should call this instead of caching values at import time,
    otherwise changes made in the web UI never take effect without a
    full process restart.
    """
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                on_disk = json.load(f)
            cfg.update({k: v for k, v in on_disk.items() if v not in (None, "")})
        except (json.JSONDecodeError, IOError):
            pass
    cfg["provider"] = normalize_provider(cfg.get("provider"))
    return cfg


def get_runtime(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve the active provider/url/key/model, config.json first, env as fallback.

    Call this right before making an API request (agent.py) rather than
    relying on module-level constants computed once at import time.
    """
    cfg = cfg or load_config()
    provider = cfg["provider"]

    if provider == "nvidia_nim":
        api_key = cfg.get("nvidia_key") or _get_nvidia_nim_key_env()
        base_url = (cfg.get("nvidia_url") or DEFAULT_NVIDIA_NIM_BASE_URL).rstrip("/")
        api_url = f"{base_url}/chat/completions"
        models_url = f"{base_url}/models"
        model = cfg.get("nvidia_model") or DEFAULT_NVIDIA_NIM_MODEL
    else:
        api_key = cfg.get("openrouter_key") or _get_openrouter_key_env()
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        models_url = "https://openrouter.ai/api/v1/models"
        model = cfg.get("model") or DEFAULT_OPENROUTER_MODEL

    return {
        "provider": provider,
        "api_key": api_key,
        "api_url": api_url,
        "models_url": models_url,
        "model": model,
        "temperature": cfg.get("temperature", 0.2),
        "max_tokens": cfg.get("max_tokens", 1000),
    }


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into config.json (writing only known schema keys) and return it."""
    cfg = load_config()
    for key in (
        "provider", "openrouter_key", "nvidia_key", "nvidia_url",
        "model", "nvidia_model", "temperature", "max_tokens",
    ):
        if key in updates:
            cfg[key] = updates[key]
    cfg["provider"] = normalize_provider(cfg.get("provider"))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def _get_openrouter_key_env() -> str | None:
    """Get OpenRouter key from environment or claudish active key file."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
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


def _get_nvidia_nim_key_env() -> str | None:
    return os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")


# ── Backwards-compatible module-level snapshot ───────────────────────────
# Some code (utils.py, older imports) still reads these directly. They're
# computed once at import time as a best-effort fallback, but anything that
# talks to the API should prefer get_runtime() so config.json changes (e.g.
# from the web UI) apply immediately without restarting the process.
_snapshot = get_runtime()
API_PROVIDER = _snapshot["provider"]
API_KEY = _snapshot["api_key"]
API_URL = _snapshot["api_url"]
API_MODELS_URL = _snapshot["models_url"]
DEFAULT_MODEL = _snapshot["model"]

OPENROUTER_KEY = _get_openrouter_key_env()
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

NVIDIA_NIM_KEY = _get_nvidia_nim_key_env()
NVIDIA_NIM_BASE_URL = DEFAULT_NVIDIA_NIM_BASE_URL
NVIDIA_NIM_API_URL = f"{NVIDIA_NIM_BASE_URL}/chat/completions"
NVIDIA_NIM_MODELS_URL = f"{NVIDIA_NIM_BASE_URL}/models"

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
    str(HOME / ".ssh") + os.sep,
    str(HOME / ".gnupg") + os.sep,
    str(HOME / ".password-store") + os.sep,
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