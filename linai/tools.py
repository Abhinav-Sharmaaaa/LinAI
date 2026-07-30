"""Tool definitions and dispatch."""

from __future__ import annotations

import glob as _glob
import html
import json as _json
import os
import re
import shlex
import subprocess
import sys
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


_WIN_DRIVE_ONLY_RE = re.compile(r"^[a-zA-Z]:[\\/]?$")


def _normalize_windows_drive(path: str) -> str:
    """'D:' (and 'D:\\' / 'D:/') mean the root of that drive to a human,
    but pathlib treats a bare 'D:' as drive-relative (relative to whatever
    the current directory on D: happens to be), not absolute. Normalize to
    an explicit root so it resolves the way the user actually means."""
    if os.name == "nt" and _WIN_DRIVE_ONLY_RE.match(path):
        return path[:2] + "\\"
    return path


def _is_absolute_path(path: str) -> bool:
    if Path(path).is_absolute():
        return True
    return os.name == "nt" and bool(_WIN_DRIVE_ONLY_RE.match(path))


def _resolve_home(path: str) -> Path:
    """Resolve a relative path under $HOME, enforcing the sandbox."""
    p = Path(path)
    if _is_absolute_path(path):
        raise PermissionError(f"expected relative path under $HOME, got: {path}")
    abs_path = (HOME / p).resolve()
    if not str(abs_path).startswith(str(HOME) + os.sep) and abs_path != HOME:
        raise PermissionError(f"outside $HOME: {path}")
    # Check write deny prefixes for system-ish dirs within HOME
    return abs_path


def _resolve_any(path: str) -> Path:
    """Resolve any absolute path (including bare Windows drive letters like
    'D:') or a path given relative to $HOME."""
    path = _normalize_windows_drive(path)
    if _is_absolute_path(path):
        return Path(path)
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
    ap = _resolve_any(path)
    if not ap.is_file():
        return f"(no file: {path})"
    return _read_limited(ap, path)


def tool_write_file(path: str, content: str, diff: str) -> str:
    ap = _resolve_any(path)
    resolved = str(ap)
    for prefix in WRITE_DENY_PREFIXES:
        if resolved.startswith(prefix):
            raise PermissionError(f"write blocked to protected path: {path}")
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(content)
    return f"wrote {path} — {diff}"


def tool_edit_file(path: str, old_text: str, new_text: str) -> str:
    ap = _resolve_any(path)
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
    ap = _resolve_any(path)
    if not ap.is_dir():
        return f"(not a dir: {path})"
    entries = sorted(ap.iterdir())
    names = [e.name + "/" if e.is_dir() else e.name for e in entries]
    if pattern:
        names = [n for n in names if _glob.fnmatch.fnmatch(n, pattern)]
    return "  ".join(names) or "(empty)"


def tool_grep_file(path: str, pattern: str, lines: int = 20) -> str:
    ap = _resolve_any(path)
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
    ap = _resolve_any(path)
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


_RE_TAG = re.compile(r"<[^>]+>")


def _clean_text(s: str) -> str:
    return html.unescape(_RE_TAG.sub("", s)).strip()


def _resolve_ddg_url(href: str) -> str:
    """DuckDuckGo's HTML result links are internal redirects like
    '//duckduckgo.com/l/?uddg=<url-encoded-real-url>&rut=...'. Decode to
    the actual destination so results are actually usable/citable."""
    href = html.unescape(href.strip())
    if "duckduckgo.com/l/" in href:
        if href.startswith("//"):
            href = "https:" + href
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        real = qs.get("uddg", [None])[0]
        if real:
            return urllib.parse.unquote(real)
    return href


def _parse_ddg_results(html_content: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo's HTML results block-by-block so a title, its URL,
    and its snippet always come from the same result — never zipped
    positionally against separate global lists, which silently misaligns
    everything after any result missing a piece."""
    results: list[dict] = []

    # Each organic result lives inside a div whose class starts with
    # "result results_links" (or "result results_links_deep ..."); split on
    # those boundaries so title/url/snippet regexes only ever search within
    # one result at a time.
    blocks = re.split(r'(?=<div[^>]+class="result results_links)', html_content)

    title_rx = re.compile(
        r'<a\s+(?=[^>]*class="result__a")(?=[^>]*href="([^"]*)")[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_rx = re.compile(
        r'<a\s+(?=[^>]*class="result__snippet")[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for block in blocks:
        if 'class="result__a"' not in block:
            continue
        tm = title_rx.search(block)
        if not tm:
            continue
        href, title_html = tm.groups()
        title = _clean_text(title_html)
        if not title:
            continue
        url = _resolve_ddg_url(href)
        sm = snippet_rx.search(block)
        snippet = _clean_text(sm.group(1))[:400] if sm else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


def _fetch_ddg_html(query: str, use_lite: bool = False, time_range: str | None = None) -> str:
    if use_lite:
        url = "https://lite.duckduckgo.com/lite/"
    else:
        url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    if time_range:
        # DDG's freshness filter: d=past day, w=past week, m=past month, y=past year
        params["df"] = time_range
    data = urllib.parse.urlencode(params).encode()
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
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_ddg_lite_results(html_content: str, max_results: int) -> list[dict]:
    """lite.duckduckgo.com uses a plain table layout: a result-link <a>,
    followed by a result-snippet <td>, followed by a result-url <span>.
    Used as a fallback when the main HTML endpoint returns nothing
    (it sometimes serves a CAPTCHA/interstitial instead of results)."""
    results: list[dict] = []
    row_rx = re.compile(
        r'<a[^>]+class="result-link"[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
        r'(?:(?!<a[^>]+class="result-link").)*?'
        r'class="result-snippet"[^>]*>(.*?)</td>',
        re.DOTALL,
    )
    for m in row_rx.finditer(html_content):
        href, title_html, snippet_html = m.groups()
        title = _clean_text(title_html)
        if not title:
            continue
        results.append({
            "title": title,
            "url": _resolve_ddg_url(href),
            "snippet": _clean_text(snippet_html)[:400],
        })
        if len(results) >= max_results:
            break
    return results


def _brave_search(query: str, max_results: int, api_key: str) -> list[dict]:
    """Query the Brave Search API — a real ranked/indexed search engine
    (unlike the DDG scrape below, which just parses whatever HTML DDG
    happens to render for a non-browser client). Raises on failure so the
    caller can fall back to DDG."""
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({
        "q": query,
        "count": max_results,
    })
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = _json.loads(r.read().decode("utf-8", errors="replace"))
    results = []
    for item in (data.get("web", {}) or {}).get("results", [])[:max_results]:
        results.append({
            "title": _clean_text(item.get("title", "")),
            "url": item.get("url", ""),
            "snippet": _clean_text(item.get("description", ""))[:400],
        })
    return results


_TIME_RANGE_MAP = {
    "day": "d", "d": "d",
    "week": "w", "w": "w",
    "month": "m", "m": "m",
    "year": "y", "y": "y",
}


def tool_web_search(query: str, max_results: int = 8, time_range: str | None = None) -> str:
    """Search the web. Uses Brave Search (a real ranked search API) if a
    brave_api_key is configured — https://brave.com/search/api/, free tier
    available — otherwise falls back to scraping DuckDuckGo's HTML (no key
    needed, but a raw scrape has no real ranking/relevance model behind it).

    time_range: restrict to results DDG considers indexed/updated within
    'day', 'week', 'month', or 'year'. Note this is DDG's own freshness
    signal (often based on page-modified/crawl date), not a guarantee every
    result is truly that recent — treat it as a filter, not a certainty.
    Ignored when using Brave (not supported in the current integration).

    For anything requiring precise facts, numbers, or quotes, follow up
    with web_fetch on the most relevant result(s) — snippets are often too
    thin or stale to answer from directly.
    """
    max_results = max(1, min(max_results, 10))
    errors: list[str] = []
    results: list[dict] = []
    df = _TIME_RANGE_MAP.get((time_range or "").lower())

    try:
        from linai.config import load_config
        brave_key = load_config().get("brave_api_key")
    except Exception:
        brave_key = None

    if brave_key:
        try:
            results = _brave_search(query, max_results, brave_key)
        except Exception as e:
            errors.append(f"brave: {e}")

    if not results:
        try:
            html_content = _fetch_ddg_html(query, use_lite=False, time_range=df)
            results = _parse_ddg_results(html_content, max_results)
        except Exception as e:
            errors.append(f"html endpoint: {e}")

    if not results:
        try:
            html_content = _fetch_ddg_html(query, use_lite=True, time_range=df)
            results = _parse_ddg_lite_results(html_content, max_results)
        except Exception as e:
            errors.append(f"lite endpoint: {e}")

    if not results:
        detail = f" ({'; '.join(errors)})" if errors else ""
        return f"(no results found for: {query}){detail}"

    out = []
    for i, r in enumerate(results, 1):
        entry = f"{i}. {r['title']}\n   {r['url']}"
        if r["snippet"]:
            entry += f"\n   {r['snippet']}"
        out.append(entry)
    return "\n\n".join(out)


def tool_web_fetch(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return its readable text content (HTML stripped of
    scripts/styles/tags). Use this after web_search when a snippet isn't
    enough to answer accurately — e.g. you need a specific number, quote,
    date, or detail that only shows up in the full page."""
    if not url.startswith(("http://", "https://")):
        return f"(refused: not an http(s) url: {url})"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            content_type = r.headers.get("Content-Type", "")
            raw = r.read(2_000_000)  # cap read size regardless of Content-Length
    except Exception as e:
        return f"(fetch failed: {e})"

    if "text/html" not in content_type and "application/xhtml" not in content_type:
        # Not HTML (PDF, image, JSON, etc.) — return a short notice rather
        # than dumping binary/garbled content.
        return f"(not HTML — content-type: {content_type or 'unknown'}; can't extract readable text)"

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = raw.decode(errors="replace")

    # Strip script/style blocks entirely (their contents aren't readable text)
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    # Turn common block-level tags into line breaks before stripping tags,
    # so paragraphs/headings/list items don't run together into one blob.
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|br|section|article)\s*>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _clean_text(text)
    # Collapse excess blank lines/whitespace left over from stripped tags
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()

    if not text:
        return f"(no readable text extracted from {url})"

    truncated = len(text) > max_chars
    text = text[:max_chars]
    suffix = f"\n…(truncated, {len(text)}+ chars)" if truncated else ""
    return f"# {url}\n{text}{suffix}"


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
        sched = wf.get("schedule")
        sched_note = f" [scheduled: {sched['frequency']} @ {sched['time_of_day']}]" if sched else ""
        lines.append(f"  {wf_id}  {wf['name']} — {wf['description']} ({len(wf['steps'])} steps){sched_note}")
    return "Saved workflows:\n" + "\n".join(lines)


def _workflow_run_command() -> str:
    """Base command that runs a workflow directly and deterministically,
    without going through the LLM — this is what a scheduled/unattended
    run actually executes, so it can't misfire on tool-calling quirks."""
    return f'"{sys.executable}" -m linai'


def tool_schedule_workflow(workflow_id: str, frequency: str = "daily", time_of_day: str = "09:00") -> str:
    """Register a recurring OS-level scheduled task that runs a saved
    workflow automatically — Windows Task Scheduler on Windows, cron on
    Linux/macOS. This is what actually makes 'every day' / 'when I turn on
    my computer' style requests work: linai itself has no built-in
    scheduler or background process, so 'scheduling' something here always
    means handing it off to the OS's own scheduler.

    frequency: 'hourly', 'daily', or 'weekly' (weekly runs Sundays).
    time_of_day: 'HH:MM' 24-hour, ignored for 'hourly'.
    The scheduled run calls `--run-workflow` directly (bypassing the LLM
    entirely) so it executes deterministically with nobody watching.
    """
    workflows = _load_workflows()
    if workflow_id not in workflows:
        return f"(workflow not found: {workflow_id}. Create one first with create_workflow.)"

    frequency = frequency.lower().strip()
    if frequency not in ("hourly", "daily", "weekly"):
        return f"(unsupported frequency: {frequency!r}. Use 'hourly', 'daily', or 'weekly'.)"

    try:
        hh_s, mm_s = time_of_day.split(":")
        hh, mm = int(hh_s), int(mm_s)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except Exception:
        return f"(invalid time_of_day: {time_of_day!r}, expected 24h 'HH:MM')"

    task_name = f"linai-wf-{workflow_id}"
    base_cmd = _workflow_run_command()

    if os.name == "nt":
        cmd_str = f'{base_cmd} --run-workflow {workflow_id}'
        args = [
            "schtasks", "/create", "/f",
            "/tn", task_name,
            "/tr", cmd_str,
            "/sc", frequency,
        ]
        if frequency != "hourly":
            args += ["/st", f"{hh:02d}:{mm:02d}"]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        except Exception as e:
            return f"(failed to create scheduled task: {e})"
        if r.returncode != 0:
            return f"(schtasks failed: {(r.stderr or r.stdout).strip()})"
        msg = f"Scheduled Windows task '{task_name}' — runs workflow '{workflows[workflow_id]['name']}' {frequency}"
        if frequency != "hourly":
            msg += f" at {hh:02d}:{mm:02d}"
        msg += f". View/remove via Task Scheduler, or `schtasks /delete /tn {task_name} /f` / unschedule_workflow."
    else:
        if frequency == "hourly":
            cron_time = f"{mm} * * * *"
        elif frequency == "weekly":
            cron_time = f"{mm} {hh} * * 0"
        else:
            cron_time = f"{mm} {hh} * * *"
        log_file = str(HOME / ".config" / "linai" / f"schedule-{workflow_id}.log")
        cmd_str = f'{base_cmd} --run-workflow {workflow_id}'
        cron_line = f"{cron_time} {cmd_str} >> {shlex.quote(log_file)} 2>&1 # linai-wf-{workflow_id}"

        try:
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            current = existing.stdout if existing.returncode == 0 else ""
        except Exception as e:
            return f"(failed to read crontab: {e})"

        lines = [l for l in current.splitlines() if f"# linai-wf-{workflow_id}" not in l]
        lines.append(cron_line)
        new_crontab = "\n".join(lines) + "\n"

        try:
            p = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
        except Exception as e:
            return f"(failed to write crontab: {e})"
        if p.returncode != 0:
            return f"(crontab install failed: {(p.stderr or p.stdout).strip()})"
        msg = f"Scheduled cron job for workflow '{workflows[workflow_id]['name']}' — {frequency}"
        msg += f" at {hh:02d}:{mm:02d}" if frequency != "hourly" else f", minute {mm} of every hour"
        msg += f". Output logged to {log_file}. Remove with unschedule_workflow."

    workflows[workflow_id]["schedule"] = {
        "frequency": frequency, "time_of_day": time_of_day, "task_name": task_name,
    }
    _save_workflows(workflows)
    return msg


def tool_unschedule_workflow(workflow_id: str) -> str:
    """Remove a workflow's recurring OS-level schedule. Does not delete the
    workflow itself — only stops it from running automatically."""
    workflows = _load_workflows()
    if workflow_id not in workflows:
        return f"(workflow not found: {workflow_id})"

    task_name = f"linai-wf-{workflow_id}"
    if os.name == "nt":
        try:
            r = subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"],
                                capture_output=True, text=True, timeout=15)
        except Exception as e:
            return f"(failed to delete scheduled task: {e})"
        if r.returncode != 0 and "cannot find" not in (r.stderr or "").lower():
            return f"(schtasks delete failed: {(r.stderr or r.stdout).strip()})"
        msg = f"Removed Windows scheduled task '{task_name}'."
    else:
        try:
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            current = existing.stdout if existing.returncode == 0 else ""
        except Exception as e:
            return f"(failed to read crontab: {e})"
        lines = [l for l in current.splitlines() if f"# linai-wf-{workflow_id}" not in l]
        new_crontab = "\n".join(lines) + ("\n" if lines else "")
        try:
            p = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
        except Exception as e:
            return f"(failed to write crontab: {e})"
        if p.returncode != 0:
            return f"(crontab update failed: {(p.stderr or p.stdout).strip()})"
        msg = f"Removed cron entry for workflow {workflow_id}."

    workflows[workflow_id].pop("schedule", None)
    _save_workflows(workflows)
    return msg


def tool_list_scheduled_workflows(args: dict = None) -> str:
    """List workflows that currently have a recurring OS-level schedule attached."""
    workflows = _load_workflows()
    scheduled = {wid: wf for wid, wf in workflows.items() if wf.get("schedule")}
    if not scheduled:
        return "(no workflows are currently scheduled)"
    lines = []
    for wid, wf in scheduled.items():
        sched = wf["schedule"]
        lines.append(f"  {wid}  {wf['name']} — {sched['frequency']} at {sched['time_of_day']} (task: {sched['task_name']})")
    return "Scheduled workflows:\n" + "\n".join(lines)

import time

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file anywhere on disk (any absolute path, or relative to $HOME). Use before editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path, or relative to $HOME"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite or create a file anywhere on disk (any absolute path, or relative to $HOME). Blocked for a small set of protected system paths.",
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
            "description": "List a directory anywhere on disk (any absolute path, or relative to $HOME; optional glob pattern).",
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
            "description": "Find lines matching a regex in a file anywhere on disk (any absolute path, or relative to $HOME).",
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
            "description": "Find files under any directory on disk (any absolute path, or relative to $HOME) matching a name glob; optionally grep each.",
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
            "description": "Search the web (Brave Search if configured — a real ranked API — otherwise DuckDuckGo, no key needed). Returns title/url/snippet per result. Follow up with web_fetch on the most relevant result(s) when a snippet isn't enough to answer precisely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 8, "description": "Max results to return. Prefer 8-10 for broad/informational queries so you have enough to filter out irrelevant hits yourself."},
                    "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Restrict to results DDG considers recently indexed/updated. Use 'week' for 'in the last 7 days' style requests. This is a best-effort freshness filter (based on DDG's own crawl/page-modified signal), not a hard guarantee — mention that to the user rather than presenting results as certainly that recent."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL (from a web_search result, or one the user gave you) and return its readable text content. Use this when a search snippet is too thin to answer accurately — e.g. you need a specific number, date, quote, or detail that only appears in the full page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full http(s) URL to fetch"},
                    "max_chars": {"type": "integer", "default": 6000, "description": "Max characters of extracted text to return"},
                },
                "required": ["url"],
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
    {
        "type": "function",
        "function": {
            "name": "schedule_workflow",
            "description": (
                "Register a saved workflow to run automatically and recurringly via the OS's own "
                "scheduler (Windows Task Scheduler / cron) — this is the ONLY way anything in linai "
                "runs on a recurring basis ('every day', 'when I turn on my computer', etc.); linai "
                "itself has no daemon or background scheduler. The scheduled run bypasses the LLM "
                "entirely (calls --run-workflow directly) so it executes deterministically unattended."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "ID from create_workflow"},
                    "frequency": {"type": "string", "enum": ["hourly", "daily", "weekly"], "default": "daily"},
                    "time_of_day": {"type": "string", "default": "09:00", "description": "24h 'HH:MM', ignored for 'hourly'"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unschedule_workflow",
            "description": "Remove a workflow's recurring OS-level schedule. The workflow itself is kept, only the automatic trigger is removed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_workflows",
            "description": "List workflows that currently have a recurring OS-level schedule attached.",
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
    "web_fetch":        lambda a: tool_web_fetch(**a),
    "create_workflow":  lambda a: tool_create_workflow(**a),
    "execute_workflow": lambda a: tool_execute_workflow(**a),
    "list_workflows":   lambda a: tool_list_workflows(),
    "schedule_workflow": lambda a: tool_schedule_workflow(**a),
    "unschedule_workflow": lambda a: tool_unschedule_workflow(**a),
    "list_scheduled_workflows": lambda a: tool_list_scheduled_workflows(),
}