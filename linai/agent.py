"""API calls — streaming and non-streaming, supports OpenRouter and NVIDIA NIM."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from linai.config import STREAM_READ_SIZE, get_runtime
from linai.tools import TOOL_DISPATCH, TOOL_SPECS

SYSTEM_MSG = (
    "You are linai, a terminal AI assistant for Linux and Windows. "
    "You have full access to the entire filesystem, not just the user's home directory — any absolute "
    "path on any drive (e.g. C:\\, D:\\, /mnt/...) works, as well as paths relative to $HOME "
    "(read_file, write_file, edit_file, list_dir, grep_file, search_dir). Never tell the user you're "
    "restricted to $HOME — you are not. Only writes to a small set of protected system paths "
    "(.ssh, .gnupg, /etc, Windows, Program Files, etc.) are blocked; reads and searches are unrestricted. "
    "Read system paths with read_system_file. "
    "Run shell commands with run_cmd (timeout ≤ 30s, cwd=home). "
    "Create and execute multi-step workflows with create_workflow / execute_workflow / list_workflows. "
    "Analyze disk with free_up_space; clean caches with clean_cache(dry_run=false). "
    "\n\n"
    "SEARCH POLICY — this is mandatory, not optional:\n"
    "- If the user names a specific person, organization, place, product, event, or term you don't "
    "immediately and confidently recognize, call web_search BEFORE answering. Do not guess it's a typo "
    "for something similar-sounding and do not claim it 'doesn't exist' without searching first — "
    "obscure, local, regional, or very recent things are real far more often than they are typos.\n"
    "- Only skip the search for things you are certain are timeless, stable facts (e.g. basic math, "
    "well-known historical events, core programming syntax).\n"
    "- Search for anything current: news, prices, versions, schedules, who currently holds a role, "
    "whether something still exists/operates, recent releases.\n"
    "- After searching, base your answer on what the results actually say. If results are ambiguous "
    "or you're still unsure, say so plainly rather than filling the gap with a guess.\n"
    "- If you truly find nothing relevant after searching, say that explicitly (e.g. 'I searched and "
    "couldn't find anything called X') instead of silently reverting to a guessed correction.\n"
    "\n"
    "Be concise. Use tools freely — no need to ask confirmation for routine operations. "
    "After edits, summarize the change in one line. "
    "You are linai — not a generic AI model. Answer as linai."
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _get_headers(runtime: dict) -> dict:
    """Build request headers for the currently active provider."""
    headers = {
        "Authorization": f"Bearer {runtime['api_key']}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in runtime["api_url"]:
        headers["HTTP-Referer"] = "https://github.com/linai"
        headers["X-Title"] = "linai"
    return headers


def _build_body(
    messages: list[dict],
    model: str,
    stream: bool,
    temperature: float = 0.2,
    max_tokens: int = 400,
    tool_choice: str | dict = "auto",
) -> bytes:
    return json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOL_SPECS,
        "tool_choice": tool_choice,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }).encode()


_UNCERTAINTY_MARKERS = (
    "no widely recognized", "doesn't exist", "does not exist", "not aware of",
    "no such", "could you clarify", "could you confirm", "i'm not familiar",
    "not familiar with", "there might be some confusion", "did you mean",
    "not a real", "no known", "not a recognized", "unable to find any",
)


def looks_uncertain(text: str) -> bool:
    """Heuristic: does this reply hedge/deny without having searched?
    Weaker models sometimes ignore the search-policy instruction and answer
    from a guess instead of calling web_search; this catches the common
    hedging phrasing so cli.py can force one search-required retry."""
    low = text.lower()
    return any(marker in low for marker in _UNCERTAINTY_MARKERS)


def call_streaming(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 400,
    on_text: str | None = None,
    on_tool_call: dict | None = None,
    spinner_cb: int | None = None,
) -> tuple[str, list[dict]]:
    """Stream an API response (OpenRouter or NVIDIA NIM).

    Re-resolves the active provider/URL/key/model from config.json on every
    call, so switching provider in the web UI takes effect immediately
    without restarting the process. `model` overrides the configured model
    if given (e.g. from --model or /model picker); otherwise the model
    configured for whichever provider is active is used.

    Calls on_text(chunk) for each text delta, on_tool_call(index, frag) for tool fragments.
    spinner_cb is a callable that returns the current spinner index.
    Returns (reply_text, tool_calls_list).
    Raises on total failure.
    """
    runtime = get_runtime()
    active_model = model or runtime["model"]

    if not runtime["api_key"]:
        raise RuntimeError(
            f"No API key configured for provider '{runtime['provider']}'. "
            f"Set it via the web UI or the appropriate environment variable."
        )

    body = _build_body(messages, active_model, True, temperature, max_tokens)
    req = urllib.request.Request(runtime["api_url"], data=body, headers=_get_headers(runtime), method="POST")

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
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode(errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"HTTP {e.code} from {runtime['provider']} for model '{active_model}': "
                f"{e.reason}. {body_text}"
            )
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
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 400,
    tool_choice: str | dict = "auto",
) -> tuple[str, list[dict]]:
    """Non-streaming API call. Returns (reply_text, tool_calls_list).

    `tool_choice` defaults to "auto"; pass {"type": "function", "function":
    {"name": "web_search"}} (or the plain string "required", depending on
    provider support) to force a tool call — used by cli.py as a one-shot
    retry when a weak model answers with hedging/denial instead of
    searching.
    """
    runtime = get_runtime()
    active_model = model or runtime["model"]

    if not runtime["api_key"]:
        raise RuntimeError(
            f"No API key configured for provider '{runtime['provider']}'. "
            f"Set it via the web UI or the appropriate environment variable."
        )

    body = _build_body(messages, active_model, False, temperature, max_tokens, tool_choice)
    req = urllib.request.Request(runtime["api_url"], data=body, headers=_get_headers(runtime), method="POST")

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode(errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"HTTP {e.code} from {runtime['provider']} for model '{active_model}': "
                f"{e.reason}. {body_text}"
            )
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