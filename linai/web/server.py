"""Web UI Server for linai configuration and monitoring."""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from linai.config import (
    API_PROVIDER, API_KEY, API_URL, API_MODELS_URL, DEFAULT_CONFIG,
    OPENROUTER_KEY, NVIDIA_NIM_KEY, NVIDIA_NIM_BASE_URL,
    CONFIG_DIR, CONFIG_FILE, HISTORY_FILE, HOME,
    DEFAULT_OPENROUTER_MODEL, DEFAULT_NVIDIA_NIM_MODEL
)
from linai.utils import get_system_status, get_disk_free_report


class WebUIHandler(SimpleHTTPRequestHandler):
    """HTTP handler for Web UI."""

    def __init__(self, *args, **kwargs):
        # Set the directory to serve static files from
        self.web_root = Path(__file__).parent / "static"
        super().__init__(*args, directory=str(self.web_root), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_index()
        elif path == "/api/config":
            self.serve_config()
        elif path == "/api/status":
            self.serve_status()
        elif path == "/api/logs":
            self.serve_logs()
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

        if path == "/api/config":
            self.update_config()
        elif path == "/api/workflows":
            self.create_workflow()
        elif path == "/api/workflows/execute":
            self.execute_workflow()
        elif path == "/api/test-connection":
            self.test_connection()
        else:
            self.send_error(404)

    def serve_index(self):
        """Serve the main HTML page."""
        index_file = self.web_root / "index.html"
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
        self.send_json(config)

    def serve_status(self):
        """Serve system status."""
        status = {
            "system": get_system_status(),
            "disk": get_disk_free_report(),
            "provider": self.get_provider_info(),
            "api_key_configured": bool(self.get_api_key()),
        }
        self.send_json(status)

    def serve_logs(self):
        """Serve recent logs."""
        logs = self.get_logs()
        self.send_json({"logs": logs})

    def serve_workflows(self):
        """Serve saved workflows."""
        workflows = self.get_workflows()
        self.send_json({"workflows": workflows})

    def serve_models(self):
        """Serve available models."""
        models = self.get_models()
        self.send_json({"models": models})

    def update_config(self):
        """Update configuration from POST data."""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(post_data)

        config = self.get_config()
        config.update(data)

        # Save to config file
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

        self.send_json({"success": True, "config": config})

    def create_workflow(self):
        """Create a new workflow."""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(post_data)

        workflow_id = self.save_workflow(data)
        self.send_json({"success": True, "workflow_id": workflow_id})

    def execute_workflow(self):
        """Execute a workflow."""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(post_data)

        workflow_id = data.get("workflow_id")
        result = self.run_workflow(workflow_id)
        self.send_json({"success": True, "result": result})

    def test_connection(self):
        """Test API connection."""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(post_data)

        provider = data.get("provider", "openrouter")
        api_key = data.get("api_key")
        model = data.get("model")

        result = self.test_api_connection(provider, api_key, model)
        self.send_json(result)

    def get_config(self) -> dict[str, Any]:
        """Get current configuration."""
        config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
            except Exception:
                pass
        # Add defaults for missing keys
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config

    def get_provider_info(self) -> dict[str, Any]:
        """Get current provider information."""
        from linai.config import API_PROVIDER, API_KEY, API_URL, DEFAULT_OPENROUTER_MODEL, DEFAULT_NVIDIA_NIM_MODEL
        return {
            "provider": API_PROVIDER,
            "api_url": API_URL,
            "key_configured": bool(API_KEY),
            "default_model": DEFAULT_NVIDIA_NIM_MODEL if API_PROVIDER == "nvidia_nim" else DEFAULT_OPENROUTER_MODEL,
        }

    def get_api_key(self) -> str | None:
        """Get the current API key."""
        from linai.config import API_KEY
        return API_KEY

    def get_logs(self, lines: int = 100) -> list[str]:
        """Get recent log entries."""
        log_file = Path(HOME) / ".local" / "share" / "linai" / "logs" / "linai.log"
        if not log_file.exists():
            # Try alternative locations
            alt_locations = [
                CONFIG_DIR / "linai.log",
                HOME / ".linai.log",
            ]
            for loc in alt_locations:
                if loc.exists():
                    log_file = loc
                    break
            else:
                return ["No log file found"]

        try:
            with open(log_file) as f:
                all_lines = f.readlines()
            return [line.rstrip() for line in all_lines[-lines:]]
        except Exception as e:
            return [f"Error reading logs: {e}"]

    def get_workflows(self) -> list[dict[str, Any]]:
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

    def save_workflow(self, workflow: dict[str, Any]) -> str:
        """Save a workflow and return its ID."""
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
            **workflow,
            "created_at": time.time(),
        }

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(workflows_file, "w") as f:
            json.dump(workflows, f, indent=2)

        return workflow_id

    def run_workflow(self, workflow_id: str) -> str:
        """Execute a workflow by ID."""
        from linai.tools import tool_execute_workflow
        return tool_execute_workflow({"workflow_id": workflow_id})

    def get_models(self) -> list[dict[str, Any]]:
        """Fetch available models from the API."""
        from linai.config import API_MODELS_URL, API_KEY
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
                    "pricing": m.get("pricing", {}),
                    "context_length": m.get("context_length", 0),
                }
                for m in models[:50]  # Limit to 50
            ]
        except Exception:
            return []

    def test_api_connection(self, provider: str, api_key: str, model: str) -> dict[str, Any]:
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
            headers["HTTP-Referer"] = "https://github.com/linai"
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

    def send_json(self, data: Any):
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