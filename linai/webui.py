"""Web-based configuration UI for linai."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from linai.config import (
        DEFAULT_CONFIG,
        CONFIG_FILE,
        CONFIG_DIR,
        load_config,
        save_config as config_save,
        get_runtime,
        normalize_provider,
    )
except ImportError:
    # Fallback if not installed
    DEFAULT_CONFIG = {"model": "", "temperature": 0.2, "max_tokens": 1000}
    CONFIG_FILE = Path.home() / ".config" / "linai" / "config.json"
    CONFIG_DIR = CONFIG_FILE.parent

    def normalize_provider(p):
        return "nvidia_nim" if p in ("nvidia", "nvidia_nim") else "openrouter"

    def load_config():
        cfg = dict(DEFAULT_CONFIG)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        return cfg

    def config_save(updates):
        cfg = load_config()
        cfg.update(updates)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return cfg

    def get_runtime(cfg=None):
        cfg = cfg or load_config()
        provider = normalize_provider(cfg.get("provider"))
        if provider == "nvidia_nim":
            return {
                "provider": provider,
                "api_key": cfg.get("nvidia_key") or os.environ.get("NVIDIA_API_KEY"),
                "api_url": (cfg.get("nvidia_url") or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions",
                "model": cfg.get("nvidia_model") or "",
            }
        return {
            "provider": provider,
            "api_key": cfg.get("openrouter_key") or os.environ.get("OPENROUTER_API_KEY"),
            "api_url": "https://openrouter.ai/api/v1/chat/completions",
            "model": cfg.get("model") or "",
        }


WEB_DIR = Path(__file__).parent / "web"


class WebUIHandler(SimpleHTTPRequestHandler):
    """HTTP handler for Web UI - serves static files and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)
        print(f"DEBUG: Handler directory = {self.directory}", file=sys.stderr)

    def end_headers(self):
        # Add CORS headers for local development
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_index()
        elif path == "/api/config":
            self.serve_config()
        elif path == "/api/logs":
            self.serve_logs()
        elif path == "/api/status":
            self.serve_status()
        elif path == "/api/workflows":
            self.serve_workflows()
        elif path == "/api/models":
            self.serve_models()
        elif path.startswith("/static/"):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}

        if path == "/api/config":
            self.save_config(data)
        elif path == "/api/test-connection":
            self.test_connection(data)
        elif path == "/api/workflows":
            self.create_workflow(data)
        elif path == "/api/workflows/execute":
            self.execute_workflow(data)
        else:
            self.send_error(404)

    def serve_index(self):
        """Serve the main HTML page from static files."""
        index_file = WEB_DIR / "static" / "index.html"
        if index_file.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open(index_file, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404)

    def serve_config(self):
        """Serve current configuration."""
        config = self.get_config()
        # Don't send actual keys in GET response for security
        safe_config = {k: v for k, v in config.items() if 'key' not in k.lower()}
        runtime = get_runtime(config)
        safe_config["provider"] = runtime["provider"]
        safe_config["openrouter_key_set"] = bool(config.get("openrouter_key"))
        safe_config["nvidia_key_set"] = bool(config.get("nvidia_key"))
        safe_config["nvidia_url"] = config.get("nvidia_url", "")
        safe_config["model"] = config.get("model", "")
        safe_config["nvidia_model"] = config.get("nvidia_model", "")
        safe_config["temperature"] = config.get("temperature", 0.2)
        safe_config["max_tokens"] = config.get("max_tokens", 1000)
        self.send_json(safe_config)

    def serve_status(self):
        """Serve system status."""
        try:
            from linai.utils import get_system_status, get_disk_free_report
            status = {
                "system": get_system_status(),
                "disk": get_disk_free_report(),
                "provider": self.get_provider_info(),
                "api_key_configured": bool(self.get_api_key()),
            }
        except Exception:
            status = {
                "system": {"cpu_percent": 0, "memory_percent": 0},
                "disk": {"percent": 0},
                "provider": self.get_provider_info(),
                "api_key_configured": bool(self.get_api_key()),
            }
        self.send_json(status)

    def serve_logs(self):
        """Serve recent logs with level info."""
        logs = self.get_logs(200)
        self.send_json({"logs": logs})

    def serve_workflows(self):
        """Serve saved workflows."""
        workflows = self.get_workflows()
        self.send_json({"workflows": workflows})

    def serve_models(self):
        """Serve available models with provider info."""
        models = self.get_models()
        self.send_json({"models": models})

    def save_config(self, data):
        """Update configuration from POST data."""
        config = config_save(data)

        # Return safe config (without keys)
        safe_config = {k: v for k, v in config.items() if 'key' not in k.lower()}
        runtime = get_runtime(config)
        safe_config["provider"] = runtime["provider"]
        safe_config["openrouter_key_set"] = bool(config.get("openrouter_key"))
        safe_config["nvidia_key_set"] = bool(config.get("nvidia_key"))

        self.send_json({"success": True, "config": safe_config})

    def create_workflow(self, data):
        """Create a new workflow."""
        import uuid
        workflows_file = CONFIG_DIR / "workflows.json"
        workflows = {}
        if workflows_file.exists():
            try:
                with open(workflows_file) as f:
                    workflows = json.load(f)
            except Exception:
                pass

        workflow_id = str(uuid.uuid4())[:8]
        workflows[workflow_id] = {
            **data,
            "created_at": time.time(),
        }

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(workflows_file, "w") as f:
            json.dump(workflows, f, indent=2)

        self.send_json({"success": True, "workflow_id": workflow_id})

    def execute_workflow(self, data):
        """Execute a workflow."""
        workflow_id = data.get("workflow_id")
        from linai.tools import tool_execute_workflow
        result = tool_execute_workflow({"workflow_id": workflow_id})
        self.send_json({"success": True, "result": result})

    def test_connection(self, data):
        """Test API connection."""
        # Save the test config temporarily, then use get_runtime to resolve
        cfg = config_save(data) if data else load_config()
        runtime = get_runtime(cfg)
        result = self.test_api_connection(runtime["provider"], runtime["api_key"], runtime["model"])
        self.send_json(result)

    def get_config(self):
        return load_config()

    def get_provider_info(self):
        """Get current provider information."""
        runtime = get_runtime()
        return {
            "provider": runtime["provider"],
            "api_url": runtime["api_url"],
            "key_configured": bool(runtime["api_key"]),
            "default_model": runtime["model"],
        }

    def get_api_key(self):
        """Get the current API key."""
        return get_runtime()["api_key"]

    def get_logs(self, lines: int = 200):
        """Get recent log entries with parsed level and time."""
        log_file = Path.home() / ".local" / "share" / "linai" / "logs" / "linai.log"
        if not log_file.exists():
            alt_locations = [
                CONFIG_DIR / "linai.log",
                Path.home() / ".linai.log",
            ]
            for loc in alt_locations:
                if loc.exists():
                    log_file = loc
                    break
            else:
                return [{"time": "", "level": "info", "message": "No log file found"}]

        try:
            with open(log_file) as f:
                all_lines = f.readlines()

            logs = []
            for line in all_lines[-lines:]:
                line = line.rstrip()
                if not line:
                    continue

                # Try to parse timestamp and level from log line
                time_str = ""
                level = "info"
                message = line

                if " - " in line:
                    parts = line.split(" - ", 2)
                    if len(parts) >= 3:
                        time_str = parts[0]
                        level_part = parts[1].upper()
                        message = parts[2]

                        if "ERROR" in level_part:
                            level = "error"
                        elif "WARN" in level_part:
                            level = "warn"
                        elif "INFO" in level_part:
                            level = "info"
                        elif "DEBUG" in level_part:
                            level = "info"
                        elif "SUCCESS" in level_part:
                            level = "success"
                    elif len(parts) == 2:
                        time_str = parts[0]
                        message = parts[1]

                logs.append({"time": time_str, "level": level, "message": message})

            return logs
        except Exception as e:
            return [{"time": "", "level": "error", "message": f"Error reading logs: {e}"}]

    def get_workflows(self):
        """Get saved workflows."""
        workflows_file = CONFIG_DIR / "workflows.json"
        if not workflows_file.exists():
            return []

        try:
            with open(workflows_file) as f:
                workflows = json.load(f)
            return [
                {"id": k, **v}
                for k, v in workflows.items()
            ]
        except Exception:
            return []

    def get_models(self):
        """Fetch available models from the API."""
        runtime = get_runtime()
        API_MODELS_URL, API_KEY = runtime["models_url"], runtime["api_key"]
        provider = runtime["provider"]

        if not API_KEY:
            return []

        try:
            import urllib.request
            req = urllib.request.Request(
                API_MODELS_URL,
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            models = data.get("data", [])
            return [
                {
                    "id": m.get("id", ""),
                    "provider": provider,
                    "pricing": m.get("pricing", {}),
                    "context_length": m.get("context_length", 0),
                }
                for m in models[:100]
            ]
        except Exception:
            return []

    def test_api_connection(self, provider: str, api_key: str, model: str):
        """Test API connection with given credentials."""
        import urllib.request
        import urllib.error

        if provider == "nvidia_nim":
            url = f"https://integrate.api.nvidia.com/v1/chat/completions"
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"

        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 10,
        }).encode()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/Abhinav-Sharmaaaa/LinAI"
            headers["X-Title"] = "linai"

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            content = data["choices"][0]["message"]["content"]
            return {"success": True, "response": content[:100]}
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_json(self, data):
        """Send JSON response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


class WebServer:
    """Web UI Server for linai."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.server = HTTPServer((host, port), WebUIHandler)
        self.thread: threading.Thread | None = None

    def start(self, open_browser: bool = True):
        """Start the web server."""
        print(f"Starting linai Web UI at http://{self.host}:{self.port}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        if open_browser:
            time.sleep(1)
            webbrowser.open(f"http://{self.host}:{self.port}")

        return self

    def stop(self):
        """Stop the web server."""
        self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=2)


def main():
    """Main entry point for web UI."""
    import sys
    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    server = WebServer(port=port)
    try:
        server.start()
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()