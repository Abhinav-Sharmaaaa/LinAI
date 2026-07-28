"""API calls — streaming and non-streaming, supports OpenRouter and NVIDIA NIM."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from linai.config import API_URL, API_KEY, STREAM_READ_SIZE
from linai.tools import TOOL_DISPATCH, TOOL_SPECS

SYSTEM_MSG = (
    "You are linai, a terminal AI assistant for Linux and Windows. "
    "You have full access to the user's home directory (read_file, write_file, edit_file, list_dir, grep_file, search_dir). "
    "Read system paths with read_system_file. "
    "Run shell commands with run_cmd (timeout ≤ 30s, cwd=home). "
    "Search the web with web_search when you need current info or don't know something. "
    "Create and execute multi-step workflows with create_workflow / execute_workflow / list_workflows. "
    "Analyze disk with free_up_space; clean caches with clean_cache(dry_run=false). "
    "Be concise. Use tools freely — no need to ask confirmation for routine operations. "
    "After edits, summarize the change in one line. "
    "You are linai — not a generic AI model. Answer as linai."
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _get_headers() -> dict:
    """Get fresh headers with current API key."""
    from linai.config import API_KEY
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    # OpenRouter-specific headers
    if "openrouter.ai" in API_URL:
        headers["HTTP-Referer"] = "https://github.com/linai"
        headers["X-Title"] = "linai"
    return headers


def _build_body(
    messages: list[dict],
    model: str,
    stream: bool,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> bytes:
    return json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOL_SPECS,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }).encode()


def call_streaming(
    messages: list[dict],
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
    on_text: str | None = None,
    on_tool_call: dict | None = None,
    spinner_cb: int | None = None,
) -> tuple[str, list[dict]]:
    """Stream an API response (OpenRouter or NVIDIA NIM).

    Calls on_text(chunk) for each text delta, on_tool_call(index, frag) for tool fragments.
    spinner_cb is a callable that returns the current spinner index.
    Returns (reply_text, tool_calls_list).
    Raises on total failure.
    """
    body = _build_body(messages, model, True, temperature, max_tokens)
    req = urllib.request.Request(API_URL, data=body, headers=_get_headers(), method="POST")

    reply_chunks: list[str] = []
    tool_calls: dict[int, dict] = {}

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buf = b""
                while True:
                    chunk = resp.read(STREAM_READ_SIZE)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        line_str = line_bytes.decode(errors="replace").strip()
                        if not line_str or line_str == "data: [DONE]":
                            continue
                        if line_str.startswith("data: "):
                            line_str = line_str[6:]
                        try:
                            obj = json.loads(line_str)
                        except json.JSONDecodeError:
                            continue
                        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                        t = delta.get("content")
                        if t:
                            reply_chunks.append(t)
                            if on_text:
                                on_text(t)
                        for tc in delta.get("tool_calls") or []:
                            idx = tc["index"]
                            frag = tool_calls.setdefault(
                                idx, {"id": tc.get("id", ""), "name": "", "args": ""},
                            )
                            frag["name"] += (tc.get("function") or {}).get("name", "")
                            frag["args"] += (tc.get("function") or {}).get("arguments", "")

                            if on_tool_call:
                                on_tool_call(idx, frag)
            break
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise

    # Build tool call list
    tcs = []
    for i in sorted(tool_calls):
        frag = tool_calls[i]
        try:
            args = json.loads(frag["args"])
        except json.JSONDecodeError:
            args = {}
        tcs.append({
            "id": frag["id"],
            "type": "function",
            "function": {"name": frag["name"], "arguments": json.dumps(args)},
        })

    return "".join(reply_chunks), tcs


def call_nonstreaming(
    messages: list[dict],
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> tuple[str, list[dict]]:
    """Non-streaming API call. Returns (reply_text, tool_calls_list)."""
    body = _build_body(messages, model, False, temperature, max_tokens)
    req = urllib.request.Request(API_URL, data=body, headers=_get_headers(), method="POST")

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            break
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise

    msg = (data.get("choices") or [{}])[0].get("message") or {}
    if not msg:
        text = (data.get("choices") or [{}])[0].get("text") or ""
        if text:
            return text, []
        return f"(no model response)", []

    raw_tcs = msg.get("tool_calls") or []
    tcs = []
    for tc in raw_tcs:
        try:
            args = json.loads((tc.get("function") or {}).get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        tcs.append({
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": (tc.get("function") or {}).get("name", ""),
                "arguments": json.dumps(args),
            },
        })

    return msg.get("content") or "", tcs


def execute_tool(tc: dict) -> str:
    """Execute a single tool call. Returns result string."""
    name = tc["function"]["name"]
    try:
        args = json.loads(tc["function"]["arguments"])
    except json.JSONDecodeError:
        args = {}
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return f"(unknown tool: {name})"
    try:
        return str(fn(args) if args else fn())
    except PermissionError as e:
        return f"(blocked: {e})"
    except Exception as e:
        return f"(error: {e})"