"""Web-based configuration UI for linai."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from linai.config import (
        API_PROVIDER,
        API_KEY,
        DEFAULT_CONFIG,
        CONFIG_FILE,
        CONFIG_DIR,
        NVIDIA_NIM_KEY,
        OPENROUTER_KEY,
    )
except ImportError:
    # Fallback if not installed
    API_PROVIDER = os.environ.get("LINAI_API_PROVIDER", "openrouter")
    API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    DEFAULT_CONFIG = {"model": "", "temperature": 0.2, "max_tokens": 1000}
    CONFIG_FILE = Path.home() / ".config" / "linai" / "config.json"
    CONFIG_DIR = CONFIG_FILE.parent
    NVIDIA_NIM_KEY = os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_NIM_API_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")

WEB_DIR = Path(__file__).parent / "web"

HTML_TEMPLATE = """
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
                    <input type="password" id="openrouter-key" placeholder="sk-or-v1-..." value="{openrouter_key}">
                </div>
            </div>
            <div class="card">
                <h2>Model Settings</h2>
                <div class="field-row">
                    <div class="field">
                        <label>Model</label>
                        <input type="text" id="model" value="{model}">
                    </div>
                    <div class="field">
                        <label>Temperature</label>
                        <input type="number" step="0.1" min="0" max="2" id="temperature" value="{temperature}">
                    </div>
                </div>
                <div class="field">
                    <label>Max Tokens</label>
                    <input type="number" id="max_tokens" value="{max_tokens}">
                </div>
            </div>
        </div>

        <div id="nvidia-tab" class="tab-content">
            <div class="card">
                <h2>NVIDIA NIM Configuration</h2>
                <div class="field">
                    <label>NVIDIA API Key</label>
                    <input type="password" id="nvidia-key" placeholder="nvapi-..." value="{nvidia_key}">
                </div>
                <div class="field">
                    <label>NIM API Base URL</label>
                    <input type="text" id="nvidia-url" placeholder="https://integrate.api.nvidia.com/v1" value="{nvidia_url}">
                </div>
            </div>
            <div class="card">
                <h2>Model Settings</h2>
                <div class="field-row">
                    <div class="field">
                        <label>Model</label>
                        <input type="text" id="nvidia-model" value="{nvidia_model}">
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
                if (data.openrouter_key) document.getElementById('openrouter-key').value = data.openrouter_key;
                if (data.nvidia_key) document.getElementById('nvidia-key').value = data.nvidia_key;
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
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[data-tab="${provider}"]`).classList.add('active');
            document.getElementById(provider + '-tab').classList.add('active');
        }

        // Save config
        document.getElementById('save-btn').addEventListener('click', async () => {
            const btn = document.getElementById('save-btn');
            btn.innerHTML = '<span class="spinner"></span> Saving...';
            btn.disabled = true;

            const provider = document.querySelector('.tab.active').dataset.tab;
            const config = {
                provider,
                openrouter_key: document.getElementById('openrouter-key').value,
                nvidia_key: document.getElementById('nvidia-key').value,
                nvidia_url: document.getElementById('nvidia-url').value,
                model: document.getElementById('model').value,
                nvidia_model: document.getElementById('nvidia-model').value,
                temperature: parseFloat(document.getElementById('temperature').value),
                max_tokens: parseInt(document.getElementById('max_tokens').value),
            };

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

            try {
                const res = await fetch('/api/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider })
                });
                const data = await res.json();
                showStatus(data.message || (data.success ? 'Connection successful!' : 'Connection failed'), data.success);
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
                    `<div class="log-line ${l.level}">[${l.time}] ${l.message}</div>`
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
"""

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

        if self.path == '/api/config':
            self.save_config(data)
        elif self.path == '/api/test':
            self.test_connection(data)
        else:
            self.send_error(404)

    def send_html(self):
        # Load current config values
        config = self.load_saved_config()

        html = HTML_TEMPLATE.format(
            openrouter_key=config.get('openrouter_key', ''),
            nvidia_key=config.get('nvidia_key', ''),
            nvidia_url=config.get('nvidia_url', 'https://integrate.api.nvidia.com/v1'),
            model=config.get('model', 'google/gemma-4-26b-a4b-it:free'),
            nvidia_model=config.get('nvidia_model', 'nvidia/nemotron-3-ultra'),
            temperature=config.get('temperature', 0.2),
            max_tokens=config.get('max_tokens', 1000),
        )

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def load_saved_config(self):
        config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
            except:
                pass
        return config

    def send_config(self):
        config = self.load_saved_config()
        config['provider'] = os.environ.get('LINAI_API_PROVIDER', 'openrouter')
        self.send_json(config)

    def save_config(self, data):
        # Update environment for current session
        if data.get('provider'):
            os.environ['LINAI_API_PROVIDER'] = data['provider']
        if data.get('openrouter_key'):
            os.environ['OPENROUTER_API_KEY'] = data['openrouter_key']
        if data.get('nvidia_key'):
            os.environ['NVIDIA_API_KEY'] = data['nvidia_key']
        if data.get('nvidia_url'):
            os.environ['NVIDIA_NIM_API_URL'] = data['nvidia_url']
        if data.get('model'):
            os.environ['LINAI_MODEL'] = data['model']
        if data.get('nvidia_model'):
            os.environ['LINAI_NVIDIA_NIM_MODEL'] = data['nvidia_model']
        if data.get('temperature'):
            os.environ['LINAI_TEMPERATURE'] = str(data['temperature'])
        if data.get('max_tokens'):
            os.environ['LINAI_MAX_TOKENS'] = str(data['max_tokens'])

        # Save to config file
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        saved = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    saved = json.load(f)
            except:
                pass

        saved.update({
            'provider': data.get('provider', 'openrouter'),
            'model': data.get('model'),
            'nvidia_model': data.get('nvidia_model'),
            'temperature': data.get('temperature'),
            'max_tokens': data.get('max_tokens'),
        })

        with open(CONFIG_FILE, 'w') as f:
            json.dump(saved, f, indent=2)

        self.send_json({'success': True, 'message': 'Configuration saved. Restart linai to apply changes.'})

    def test_connection(self, data):
        provider = data.get('provider', 'openrouter')
        try:
            if provider == 'nvidia_nim':
                # Test NVIDIA NIM
                import urllib.request
                key = os.environ.get('NVIDIA_API_KEY') or os.environ.get('NVIDIA_NIM_API_KEY')
                url = os.environ.get('NVIDIA_NIM_API_URL', 'https://integrate.api.nvidia.com/v1')
                if not key:
                    self.send_json({'success': False, 'message': 'NVIDIA_API_KEY not set'})
                    return
                req = urllib.request.Request(
                    f"{url}/models",
                    headers={'Authorization': f'Bearer {key}'}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.load(r)
                    self.send_json({'success': True, 'message': f'NVIDIA NIM connected. {len(data.get("data", []))} models available.'})
            else:
                # Test OpenRouter
                import urllib.request
                key = os.environ.get('OPENROUTER_API_KEY')
                if not key:
                    self.send_json({'success': False, 'message': 'OPENROUTER_API_KEY not set'})
                    return
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/models',
                    headers={'Authorization': f'Bearer {key}'}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.load(r)
                    self.send_json({'success': True, 'message': f'OpenRouter connected. {len(data.get("data", []))} models available.'})
        except Exception as e:
            self.send_json({'success': False, 'message': f'Connection failed: {str(e)}'})

    def send_logs(self):
        logs = []
        # Read linai log file if exists
        log_file = CONFIG_DIR / 'linai.log'
        if log_file.exists():
            try:
                with open(log_file) as f:
                    for line in f.readlines()[-100:]:
                        parts = line.strip().split(' ', 2)
                        if len(parts) >= 3:
                            logs.append({'time': parts[0], 'level': parts[1], 'message': parts[2]})
            except:
                pass
        self.send_json({'logs': logs})

    def send_status(self):
        status = {
            'provider': API_PROVIDER,
            'has_openrouter_key': bool(OPENROUTER_KEY),
            'has_nvidia_key': bool(NVIDIA_NIM_KEY),
            'default_model': DEFAULT_CONFIG.get('model', ''),
        }
        self.send_json(status)

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def run_server(port=8765):
    server = HTTPServer(('127.0.0.1', port), WebUIHandler)
    print(f"linai Web UI running at http://127.0.0.1:{port}")
    webbrowser.open(f'http://127.0.0.1:{port}')
    server.serve_forever()


def main():
    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            pass
    run_server(port)


if __name__ == '__main__':
    main()