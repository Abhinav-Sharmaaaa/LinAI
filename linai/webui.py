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
from string import Template
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

HTML_TEMPLATE = Template("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>linai Configuration</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #00d9ff; margin-bottom: 24px; font-size: 28px; }
        .card { background: #16213e; border-radius: 12px; padding: 24px; margin-bottom: 20px; border: 1px solid #0f3460; }
        h2 { color: #00d9ff; margin-bottom: 16px; font-size: 18px; }
        .field { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; color: #aaa; font-size: 14px; }
        input, select { width: 100%; padding: 10px 12px; background: #0f3460; border: 1px solid #0f3460; border-radius: 6px; color: #eee; font-size: 14px; }
        input:focus, select:focus { outline: none; border-color: #00d9ff; }
        .btn { padding: 12px 24px; background: #00d9ff; color: #1a1a2e; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; transition: background 0.2s; }
        .btn:hover { background: #00b8d4; }
        .btn-secondary { background: #0f3460; color: #eee; }
        .btn-secondary:hover { background: #1a5c8a; }
        .btn-group { display: flex; gap: 12px; margin-top: 20px; }
        .status { padding: 12px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
        .status.ok { background: #0f3460; border: 1px solid #00d9ff; color: #00d9ff; }
        .status.error { background: #3d0f1a; border: 1px solid #ff3366; color: #ff3366; }
        .provider-tabs { display: flex; gap: 8px; margin-bottom: 20px; }
        .tab { padding: 10px 20px; background: #0f3460; border: 1px solid #0f3460; border-radius: 6px; color: #aaa; cursor: pointer; transition: all 0.2s; }
        .tab.active { background: #00d9ff; color: #1a1a2e; border-color: #00d9ff; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .logs { background: #0f0f1a; border: 1px solid #0f3460; border-radius: 8px; padding: 16px; max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.5; }
        .log-line { margin-bottom: 4px; }
        .log-line.error { color: #ff3366; }
        .log-line.warn { color: #ffaa00; }
        .log-line.info { color: #00d9ff; }
        .log-line.success { color: #00ff88; }
        .hidden { display: none !important; }
        .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        @media (max-width: 600px) { .field-row { grid-template-columns: 1fr; } }
        .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #0f3460; border-top-color: #00d9ff; border-radius: 50%; animation: spin 1s linear infinite; margin-right: 8px; vertical-align: middle; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>linai Configuration</h1>

        <div id="status"></div>

        <div class="provider-tabs">
            <button class="tab active" data-tab="openrouter">OpenRouter</button>
            <button class="tab" data-tab="nvidia">NVIDIA NIM</button>
        </div>

        <div id="openrouter-tab" class="tab-content active">
            <div class="card">
                <h2>OpenRouter Configuration</h2>
                <div class="field">
                    <label>API Key</label>
                    <input type="password" id="openrouter-key" placeholder="sk-or-v1-..." value="$openrouter_key">
                </div>
            </div>
            <div class="card">
                <h2>Model Settings</h2>
                <div class="field-row">
                    <div class="field">
                        <label>Model</label>
                        <input type="text" id="model" value="$model">
                    </div>
                    <div class="field">
                        <label>Temperature</label>
                        <input type="number" step="0.1" min="0" max="2" id="temperature" value="$temperature">
                    </div>
                </div>
                <div class="field">
                    <label>Max Tokens</label>
                    <input type="number" id="max_tokens" value="$max_tokens">
                </div>
            </div>
        </div>

        <div id="nvidia-tab" class="tab-content">
            <div class="card">
                <h2>NVIDIA NIM Configuration</h2>
                <div class="field">
                    <label>NVIDIA API Key</label>
                    <input type="password" id="nvidia-key" placeholder="nvapi-..." value="$nvidia_key">
                </div>
                <div class="field">
                    <label>NIM API Base URL</label>
                    <input type="text" id="nvidia-url" placeholder="https://integrate.api.nvidia.com/v1" value="$nvidia_url">
                </div>
            </div>
            <div class="card">
                <h2>Model Settings</h2>
                <div class="field-row">
                    <div class="field">
                        <label>Model</label>
                        <input type="text" id="nvidia-model" value="$nvidia_model">
                    </div>
                </div>
            </div>
        </div>

        <div class="btn-group">
            <button class="btn" id="save-btn">Save Configuration</button>
            <button class="btn btn-secondary" id="test-btn">Test Connection</button>
        </div>

        <div class="card">
            <h2>Application Logs</h2>
            <div class="logs" id="logs">Loading logs...</div>
            <button class="btn btn-secondary" id="refresh-logs" style="margin-top: 12px;">Refresh Logs</button>
        </div>
    </div>

    <script>
        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById(tab.dataset.tab + '-tab').classList.add('active');
            });
        });

        // Load saved config
        async function loadConfig() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                if (data.openrouter_key_set) document.getElementById('openrouter-key').placeholder = '•••••••• (saved)';
                if (data.nvidia_key_set) document.getElementById('nvidia-key').placeholder = '•••••••• (saved)';
                if (data.nvidia_url) document.getElementById('nvidia-url').value = data.nvidia_url;
                if (data.model) document.getElementById('model').value = data.model;
                if (data.nvidia_model) document.getElementById('nvidia-model').value = data.nvidia_model;
                if (data.temperature) document.getElementById('temperature').value = data.temperature;
                if (data.max_tokens) document.getElementById('max_tokens').value = data.max_tokens;
                updateProviderUI(data.provider || 'openrouter');
            } catch (e) {
                console.error('Failed to load config:', e);
            }
        }

        function updateProviderUI(provider) {
            // Backend normalizes to 'nvidia_nim'; the tab buttons use 'nvidia'.
            const tabId = (provider === 'nvidia_nim' || provider === 'nvidia') ? 'nvidia' : 'openrouter';
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector('.tab[data-tab="' + tabId + '"]').classList.add('active');
            document.getElementById(tabId + '-tab').classList.add('active');
        }

        // Save config
        document.getElementById('save-btn').addEventListener('click', async () => {
            const btn = document.getElementById('save-btn');
            btn.innerHTML = '<span class="spinner"></span> Saving...';
            btn.disabled = true;

            const provider = document.querySelector('.tab.active').dataset.tab;
            const orKey = document.getElementById('openrouter-key').value;
            const nvKey = document.getElementById('nvidia-key').value;
            const config = {
                provider,
                nvidia_url: document.getElementById('nvidia-url').value,
                model: document.getElementById('model').value,
                nvidia_model: document.getElementById('nvidia-model').value,
                temperature: parseFloat(document.getElementById('temperature').value),
                max_tokens: parseInt(document.getElementById('max_tokens').value),
            };
            // Only send keys if the user actually typed something — an empty
            // password field just means "unchanged", not "clear the key".
            if (orKey) config.openrouter_key = orKey;
            if (nvKey) config.nvidia_key = nvKey;

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                const data = await res.json();
                showStatus(data.message || 'Saved successfully', true);
            } catch (e) {
                showStatus('Failed to save: ' + e.message, false);
            }
            btn.innerHTML = 'Save Configuration';
            btn.disabled = false;
        });

        // Test connection
        document.getElementById('test-btn').addEventListener('click', async () => {
            const btn = document.getElementById('test-btn');
            btn.innerHTML = '<span class="spinner"></span> Testing...';
            btn.disabled = true;

            const provider = document.querySelector('.tab.active').dataset.tab;
            const orKey = document.getElementById('openrouter-key').value;
            const nvKey = document.getElementById('nvidia-key').value;
            const testConfig = {
                provider,
                nvidia_url: document.getElementById('nvidia-url').value,
                model: document.getElementById('model').value,
                nvidia_model: document.getElementById('nvidia-model').value,
            };
            if (orKey) testConfig.openrouter_key = orKey;
            if (nvKey) testConfig.nvidia_key = nvKey;

            try {
                const res = await fetch('/api/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(testConfig)
                });
                const data = await res.json();
                showStatus(data.message || data.error || (data.success ? 'Connection successful!' : 'Connection failed'), data.success);
            } catch (e) {
                showStatus('Test failed: ' + e.message, false);
            }
            btn.innerHTML = 'Test Connection';
            btn.disabled = false;
        });

        // Refresh logs
        document.getElementById('refresh-logs').addEventListener('click', loadLogs);

        async function loadLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                const logsEl = document.getElementById('logs');
                logsEl.innerHTML = data.logs.map(l =>
                    '<div class="log-line ' + l.level + '">[' + l.time + '] ' + l.message + '</div>'
                ).join('');
                logsEl.scrollTop = logsEl.scrollHeight;
            } catch (e) {
                console.error('Failed to load logs:', e);
            }
        }

        function showStatus(message, success) {
            const statusEl = document.getElementById('status');
            statusEl.className = 'status ' + (success ? 'ok' : 'error');
            statusEl.textContent = message;
            statusEl.style.display = 'block';
            setTimeout(() => { statusEl.style.display = 'none'; }, 5000);
        }

        // Initialize
        loadConfig();
        loadLogs();

        // Auto-refresh logs every 10 seconds
        setInterval(loadLogs, 10000);
    </script>
</body>
</html>
""")

WEB_DIR = Path(__file__).parent / "web"


class WebUIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            self.send_html()
        elif path == '/api/config':
            self.send_config()
        elif path == '/api/logs':
            self.send_logs()
        elif path == '/api/status':
            self.send_status()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}

        if path == '/api/config':
            self.save_config(data)
        elif path == '/api/test':
            self.test_connection(data)
        else:
            self.send_error(404)

    def send_html(self):
        config = self.get_config()
        provider = normalize_provider(config.get('provider'))

        # Prepare template variables
        template_vars = {
            'openrouter_key': config.get('openrouter_key', ''),
            'nvidia_key': config.get('nvidia_key', ''),
            'nvidia_url': config.get('nvidia_url', '') or 'https://integrate.api.nvidia.com/v1',
            'model': config.get('model', '') or DEFAULT_CONFIG.get('model', ''),
            'nvidia_model': config.get('nvidia_model', '') or DEFAULT_CONFIG.get('nvidia_model', ''),
            'temperature': config.get('temperature', DEFAULT_CONFIG.get('temperature', 0.2)),
            'max_tokens': config.get('max_tokens', DEFAULT_CONFIG.get('max_tokens', 1000)),
        }

        # Use Template.substitute which uses $variable syntax
        html = HTML_TEMPLATE.substitute(template_vars)

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def get_config(self):
        return load_config()

    def send_config(self):
        config = self.get_config()
        # Don't send actual keys in GET response for security, but DO send
        # whether a key is present so the UI can show a placeholder/state.
        safe_config = {k: v for k, v in config.items() if 'key' not in k.lower()}
        safe_config['provider'] = normalize_provider(config.get('provider'))
        safe_config['openrouter_key_set'] = bool(config.get('openrouter_key'))
        safe_config['nvidia_key_set'] = bool(config.get('nvidia_key'))
        self.send_json(safe_config)

    def get_provider(self):
        return normalize_provider(self.get_config().get('provider'))

    def send_logs(self):
        log_file = Path.home() / ".local" / "share" / "linai" / "logs" / "linai.log"
        if not log_file.exists():
            log_file = CONFIG_DIR / "linai.log"
        if not log_file.exists():
            log_file = Path.home() / ".linai.log"

        logs = []
        if log_file.exists():
            try:
                with open(log_file) as f:
                    for line in f.readlines()[-100:]:
                        line = line.strip()
                        if line:
                            # Try to parse timestamp and level
                            level = "info"
                            if "ERROR" in line.upper():
                                level = "error"
                            elif "WARN" in line.upper():
                                level = "warn"
                            elif "SUCCESS" in line.upper():
                                level = "success"
                            logs.append({"time": "", "level": level, "message": line})
            except Exception:
                pass

        self.send_json({"logs": logs})

    def send_status(self):
        try:
            from linai.utils import get_system_status, get_disk_free_report
            status = {
                "system": get_system_status(),
                "disk": get_disk_free_report(),
                "provider": self.get_provider(),
            }
        except ImportError:
            status = {"system": "N/A", "disk": "N/A", "provider": self.get_provider()}
        self.send_json(status)

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def save_config(self, data):
        config_save(data)
        self.send_json({"success": True, "message": "Configuration saved"})

    def test_connection(self, data):
        # Persist whatever was just entered so get_runtime() sees it, then
        # resolve using the *saved* config rather than re-deriving ad hoc
        # provider/url/key logic here (that duplication is what caused the
        # NVIDIA tab to silently test against an OpenRouter model before).
        cfg = config_save(data) if data else load_config()
        runtime = get_runtime(cfg)

        api_key = runtime["api_key"]
        url = runtime["api_url"]
        model = runtime["model"]
        provider = runtime["provider"]

        if not api_key:
            self.send_json({"success": False, "error": f"No API key configured for {provider}"})
            return
        if not model:
            self.send_json({"success": False, "error": f"No model configured for {provider}"})
            return

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
            self.send_json({"success": True, "message": f"Connection successful! Response: {content[:50]}"})
        except urllib.error.HTTPError as e:
            self.send_json({"success": False, "error": f"HTTP {e.code}: {e.reason}"})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})


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