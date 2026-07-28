"""Tool definitions and dispatch."""

from __future__ import annotations

import glob as _glob
import html
import json as _json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from linai.config import (
    DENY_PATTERNS,
    HOME,
    MAX_TOOL_OUTPUT,
    WRITE_DENY_PREFIXES,
)
from linai.utils import cap, strip_ansi

# Compiled deny regex
_RE_DENY = re.compile("|".join(DENY_PATTERNS))

WORKFLOWS_FILE = HOME / ".config" / "linai" / "workflows.json"


def tool_web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo HTML (no API key needed)."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"(web search error: {e})"

    # Parse results from DuckDuckGo HTML
    results = []
    # Pattern for result snippets
    import re as _re
    # Find result blocks
    result_blocks = _re.findall(r'class="result__snippet".*?>(.*?)</a>', html, _re.DOTALL)
    for i, block in enumerate(result_blocks[:max_results]):
        # Clean HTML tags
        clean = _re.sub(r"<[^>]+>", "", block)
        clean = html.unescape(clean.strip())
        if clean:
            results.append(f"{i+1}. {clean}")

    # Also try to get titles/links
    title_blocks = _re.findall(r'class="result__title".*?>(.*?)</a>', html, _re.DOTALL)
    link_blocks = _re.findall(r'class="result__url".*?>(.*?)</a>', html, _re.DOTALL)

    if not results and title_blocks:
        for i, (title, link) in enumerate(zip(title_blocks[:max_results], link_blocks[:max_results])):
            clean_title = _re.sub(r"<[^>]+>", "", title)
            clean_link = _re.sub(r"<[^>]+>", "", link)
            results.append(f"{i+1}. {html.unescape(clean_title.strip())} — {html.unescape(clean_link.strip())}")

    return "\n".join(results) if results else "(no results found)"


def _resolve_home(path: str) -> Path:
    """Resolve a relative path under $HOME, enforcing the sandbox."""
    p = Path(path)
    if p.is_absolute():
        raise PermissionError(f"expected relative path under $HOME, got: {path}")
    abs_path = (HOME / p).resolve()
    if not str(abs_path).startswith(str(HOME) + "/") and abs_path != HOME:
        raise PermissionError(f"outside $HOME: {path}")
    # Check write deny prefixes for system-ish dirs within HOME
    return abs_path


def _resolve_any(path: str) -> Path:
    """Resolve any absolute path or relative-to-home path."""
    p = Path(path)
    if p.is_absolute():
        return p
    return _resolve_home(path)


def _read_limited(abs_path: Path, disp: str, limit: int = 4000) -> str:
    try:
        size = abs_path.stat().st_size
    except OSError:
        size = 0
    try:
        data = abs_path.read_text(errors="replace")[:limit]
    except Exception as e:
        return f"(err reading {disp}: {e})"
    suffix = "" if size <= limit else f"\n…(truncated, {size} bytes)"
    return f"# {disp} ({size} bytes)\n{data}{suffix}"


def tool_read_file(path: str) -> str:
    ap = _resolve_home(path)
    if not ap.is_file():
        return f"(no file: {path})"
    return _read_limited(ap, path)


def tool_write_file(path: str, content: str, diff: str) -> str:
    ap = _resolve_home(path)
    resolved = str(ap)
    for prefix in WRITE_DENY_PREFIXES:
        if resolved.startswith(prefix):
            raise PermissionError(f"write blocked to protected path: {path}")
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(content)
    return f"wrote {path} — {diff}"


def tool_edit_file(path: str, old_text: str, new_text: str) -> str:
    ap = _resolve_home(path)
    src = ap.read_text(errors="replace")
    if old_text not in src:
        return f"(edit failed: old_text not found in {path})"
    ap.write_text(src.replace(old_text, new_text, 1))
    return f"edited {path} ({len(old_text)}→{len(new_text)} chars)"


def tool_run_cmd(cmd: str, timeout: int = 15) -> str:
    if _RE_DENY.search(cmd):
        return f"(refused dangerous: {cmd})"
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=min(timeout, 30), cwd=str(HOME),
        )
        out = (r.stdout + r.stderr).strip()
        if not out:
            return f"(exit {r.returncode})"
        return f"(exit {r.returncode})\n{cap(out)}"
    except subprocess.TimeoutExpired:
        return f"(timeout {min(timeout, 30)}s)"
    except Exception as e:
        return f"(err: {e})"


def tool_list_dir(path: str, pattern: str | None = None) -> str:
    ap = _resolve_home(path)
    if not ap.is_dir():
        return f"(not a dir: {path})"
    entries = sorted(ap.iterdir())
    names = [e.name + "/" if e.is_dir() else e.name for e in entries]
    if pattern:
        names = [n for n in names if _glob.fnmatch.fnmatch(n, pattern)]
    return "  ".join(names) or "(empty)"


def tool_grep_file(path: str, pattern: str, lines: int = 20) -> str:
    p = Path(path)
    if p.is_absolute() and any(str(p).startswith(prefix) for prefix in ("/etc/", "/var/", "/usr/", "/proc/", "/sys/")):
        ap = p
    else:
        ap = _resolve_home(path)
    try:
        src = ap.read_text(errors="replace")
    except Exception as e:
        return f"(err: {e})"
    rx = re.compile(pattern)
    hits = []
    for i, line in enumerate(src.splitlines(), 1):
        if rx.search(line):
            hits.append(f"{i:>4}: {line.rstrip()}")
        if len(hits) >= lines:
            break
    return "\n".join(hits) or f"(no hits for /{pattern}/)"


def tool_search_dir(
    path: str,
    name_glob: str,
    content_pattern: str | None = None,
    max_files: int = 20,
) -> str:
    import fnmatch
    ap = _resolve_home(path)
    content_rx = re.compile(content_pattern) if content_pattern else None
    found: list[str] = []
    for dirpath, _, files in os.walk(str(ap)):
        for f in files:
            if fnmatch.fnmatch(f, name_glob):
                full = Path(dirpath) / f
                if content_rx:
                    try:
                        data = full.read_text(errors="replace")[:4096]
                        if not content_rx.search(data):
                            continue
                    except Exception:
                        continue
                found.append(str(full))
                if len(found) >= max_files:
                    break
        if len(found) >= max_files:
            break
    return "\n".join(found) or "(no matches)"


def tool_read_system_file(path: str) -> str:
    if not any(path.startswith(p) for p in ("/etc/", "/var/", "/usr/", "/proc/", "/sys/")):
        return f"(not a system path, use read_file for {path})"
    return _read_limited(Path(path), path)


def tool_free_up_space() -> str:
    from linai.utils import get_disk_free_report
    return get_disk_free_report()


def tool_clean_cache(dry_run: bool = True) -> str:
    """Clean common cache directories. Set dry_run=false to actually remove files."""
    import shutil

    cleaned: list[str] = []
    errors: list[str] = []

    # Clean ~/.cache/* (thumbnails, yay, etc)
    cache_dir = HOME / ".cache"
    if cache_dir.exists():
        if dry_run:
            total_size = sum(
                sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                for d in cache_dir.iterdir() if d.is_dir()
            )
            cleaned.append(f"~/.cache/: {total_size / (1024*1024):.1f}M")
        else:
            # Remove cache subdirs but keep the parent
            for item in cache_dir.iterdir():
                if item.is_dir() and item.name not in (".", ".."):
                    try:
                        shutil.rmtree(item)
                        cleaned.append(f"~/.cache/{item.name}: cleaned")
                    except Exception as e:
                        errors.append(f"~/.cache/{item.name}: {e}")

    # Clean npm cache
    npm_cache = HOME / ".npm/_cacache"
    if npm_cache.exists():
        if dry_run:
            try:
                size = sum(f.stat().st_size for f in npm_cache.rglob("*") if f.is_file())
                cleaned.append(f"npm cache: {size / (1024*1024):.1f}M")
            except Exception:
                cleaned.append("npm cache: (exists)")
        else:
            try:
                r = subprocess.run(
                    ["npm", "cache", "clean", "--force"],
                    capture_output=True, text=True, timeout=60,
                )
                cleaned.append("npm cache: cleaned" if r.returncode == 0 else f"npm cache: {r.stderr.strip()}")
            except Exception as e:
                errors.append(f"npm cache: {e}")

    # Clean trash
    trash_dir = HOME / ".local/share/Trash/files"
    if trash_dir.exists():
        if dry_run:
            try:
                size = sum(f.stat().st_size for f in trash_dir.rglob("*") if f.is_file())
                cleaned.append(f"trash: {size / (1024*1024):.1f}M")
            except Exception:
                cleaned.append("trash: (exists)")
        else:
            try:
                shutil.rmtree(trash_dir)
                trash_dir.mkdir(parents=True, exist_ok=True)
                cleaned.append("trash: cleaned")
            except Exception as e:
                errors.append(f"trash: {e}")

    if dry_run:
        result = "Dry run - reclaimable areas:\n" + "\n".join(f"  {c}" for c in cleaned)
        if errors:
            result += "\n\nErrors:\n" + "\n".join(f"  {e}" for e in errors)
    else:
        result = "Cleaned:\n" + "\n".join(f"  {c}" for c in cleaned)
        if errors:
            result += "\n\nErrors:\n" + "\n".join(f"  {e}" for e in errors)

    result += "\n\n(Note: pacman cache requires sudo, journalctl needs sudo)"

    return result


def tool_web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo HTML. No API key needed."""
    try:
        # Use POST to html.duckduckgo.com to avoid bot detection
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://duckduckgo.com/",
                "Origin": "https://duckduckgo.com",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            html_content = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"(web search failed: {e})"

    # Parse results from DuckDuckGo HTML
    results = []
    import re
    title_pattern = re.compile(
        r'class="result__title".*?href="(.*?)".*?>(.*?)</a>',
        re.DOTALL
    )
    snippet_pattern = re.compile(
        r'class="result__snippet".*?>(.*?)</a>',
        re.DOTALL
    )

    titles = title_pattern.findall(html_content)
    snippets = snippet_pattern.findall(html_content)

    for i, (url, title) in enumerate(titles[:max_results]):
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        clean_url = html.unescape(url).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]
        results.append(f"{i+1}. {clean_title}\n   {clean_url}\n   {snippet}")

    if not results:
        return "(no results found)"

    return "\n\n".join(results)


# ── Workflow system ──────────────────────────────────────────────────────────
import uuid
import json as _json

WORKFLOWS_FILE = HOME / ".config" / "linai" / "workflows.json"

def _load_workflows() -> dict:
    if WORKFLOWS_FILE.exists():
        try:
            return _json.loads(WORKFLOWS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_workflows(workflows: dict) -> None:
    WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_FILE.write_text(_json.dumps(workflows, indent=2))

def tool_create_workflow(name: str, description: str, steps: list[dict]) -> str:
    """Create a multi-step workflow plan."""
    workflow_id = str(uuid.uuid4())[:8]
    workflows = _load_workflows()
    workflows[workflow_id] = {
        "name": name,
        "description": description,
        "steps": steps,
        "created_at": str(int(time.time())),
    }
    _save_workflows(workflows)
    return f"Created workflow '{name}' (id: {workflow_id}) with {len(steps)} steps. Execute with execute_workflow(workflow_id='{workflow_id}')."

def tool_execute_workflow(workflow_id: str, continue_on_error: bool = False) -> str:
    """Execute a workflow by ID."""
    workflows = _load_workflows()
    if workflow_id not in workflows:
        return f"(workflow not found: {workflow_id})"

    wf = workflows[workflow_id]
    results = [f"Executing workflow: {wf['name']} ({len(wf['steps'])} steps)\n"]

    for i, step in enumerate(wf['steps'], 1):
        step_name = step.get('name', f'step_{i}')
        tool_name = step.get('tool')
        args = step.get('args', {})
        step_desc = step.get('description', '')

        results.append(f"\n--- Step {i}: {step_name} ---")
        results.append(f"Description: {step_desc}")
        results.append(f"Tool: {tool_name}({args})")

        fn = TOOL_DISPATCH.get(tool_name)
        if not fn:
            err = f"(unknown tool: {tool_name})"
            results.append(f"Result: {err}")
            if not continue_on_error:
                results.append("\n[Stopped: continue_on_error=false]")
                break
            continue

        try:
            result = fn(args)
            results.append(f"Result:\n{result}")
        except Exception as e:
            err = f"(error: {e})"
            results.append(f"Result: {err}")
            if not continue_on_error:
                results.append("\n[Stopped: continue_on_error=false]")
                break

    return "\n".join(results)

def tool_list_workflows(args: dict = None) -> str:
    """List all saved workflows."""
    workflows = _load_workflows()
    if not workflows:
        return "(no workflows saved)"

    lines = []
    for wf_id, wf in workflows.items():
        lines.append(f"  {wf_id}  {wf['name']} — {wf['description']} ({len(wf['steps'])} steps)")
    return "Saved workflows:\n" + "\n".join(lines)

import time

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": f"Read a file in {HOME}. Use before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path under $HOME"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": f"Overwrite or create a file in {HOME}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "diff": {"type": "string", "description": "One-line change summary"},
                },
                "required": ["path", "content", "diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file (old must match exactly).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cmd",
            "description": "Run a short shell command (non-interactive). timeout <= 30s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 15},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": f"List a directory in {HOME} (optional glob pattern).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "description": "Glob, e.g. *.py"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_file",
            "description": "Find lines matching a regex in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "lines": {"type": "integer", "default": 20},
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_dir",
            "description": "Find files under a dir matching a name glob; optionally grep each.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "name_glob": {"type": "string", "description": "Name glob, e.g. *.log"},
                    "content_pattern": {"type": "string", "description": "Optional regex to search inside files"},
                    "max_files": {"type": "integer", "default": 20},
                },
                "required": ["path", "name_glob"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_system_file",
            "description": "Read-only access to system paths (/etc, /var, /usr, /proc, /sys).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "free_up_space",
            "description": "Analyze disk usage and return reclaimable areas with estimated space. Does not delete anything.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_cache",
            "description": "Clean common cache directories. Set dry_run=false to actually remove files. Safe for user home only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean", "default": True},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via DuckDuckGo. Use whenever you need current info, docs, prices, news, etc. No API key needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 5, "description": "Max results to return"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_workflow",
            "description": "Create a multi-step workflow plan. Returns a workflow_id to execute.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workflow name"},
                    "description": {"type": "string", "description": "What this workflow accomplishes"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Step name"},
                                "tool": {"type": "string", "description": "Tool to call (e.g., write_file, run_cmd, web_search)"},
                                "args": {"type": "object", "description": "Arguments for the tool"},
                                "description": {"type": "string", "description": "What this step does"},
                            },
                            "required": ["name", "tool", "args", "description"],
                        },
                    },
                },
                "required": ["name", "description", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_workflow",
            "description": "Execute a workflow by ID. Runs each step sequentially, stops on error unless continue_on_error=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID returned by create_workflow"},
                    "continue_on_error": {"type": "boolean", "default": False, "description": "Continue if a step fails"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflows",
            "description": "List all saved workflows.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_DISPATCH: dict[str, Any] = {
    "read_file":        lambda a: tool_read_file(**a),
    "write_file":       lambda a: tool_write_file(**a),
    "edit_file":        lambda a: tool_edit_file(**a),
    "run_cmd":          lambda a: tool_run_cmd(**a),
    "list_dir":         lambda a: tool_list_dir(**a),
    "grep_file":        lambda a: tool_grep_file(**a),
    "search_dir":       lambda a: tool_search_dir(**a),
    "read_system_file": lambda a: tool_read_system_file(**a),
    "free_up_space":    tool_free_up_space,
    "clean_cache":      lambda a: tool_clean_cache(**a),
    "web_search":       lambda a: tool_web_search(**a),
    "create_workflow":  lambda a: tool_create_workflow(**a),
    "execute_workflow": lambda a: tool_execute_workflow(**a),
    "list_workflows":   lambda a: tool_list_workflows(),
}
