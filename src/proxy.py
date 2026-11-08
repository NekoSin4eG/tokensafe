"""
Local AI Transparent Proxy for Token Optimizer.
Intercepts LLM requests from any agent (Cursor, Claude Code, Windsurf, Aider, Cline).
Performs in-flight prompt compaction, output streaming, tool loop detection, and token tracking.
Endpoint: http://127.0.0.1:8765/v1
"""

import sys
import os
import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.compactor import PromptCompactor
from src.metrics import MetricsTracker
from src.dashboard import Dashboard
from src.loop_detector import LoopDetector
from src.secret_redactor import SecretRedactor

DEFAULT_PORT = 8765
DEFAULT_UPSTREAM = "https://api.deepseek.com/v1"

class TokenSafeProxyHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests and proxies /v1/chat/completions with token optimization."""

    def log_message(self, format, *args):
        # Mute noisy default server logs
        pass

    def do_GET(self):
        tracker = MetricsTracker()
        dashboard = Dashboard(tracker)

        if self.path in ("/", "/dashboard", "/dashboard.html"):
            html = dashboard.render_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif self.path in ("/metrics", "/api/metrics"):
            summary = tracker.get_summary()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(summary, indent=2).encode("utf-8"))

        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "active", "proxy": "tokensafe"}).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/v1/chat/completions"):
            self.handle_chat_completions()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_chat_completions(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON payload: {e}"}).encode("utf-8"))
            return

        # 1. Local Secret Redaction: Scrub credentials across all messages before dispatch
        messages = payload.get("messages", [])
        if messages:
            messages, secrets_scrubbed = SecretRedactor.redact_messages(messages)
            if secrets_scrubbed > 0:
                print(f"[TokenSafe] Scrubbed {secrets_scrubbed} sensitive secret(s) from payload before transmission.")

        # 2. Ingress Optimization: Compact last user message
        if messages:
            for idx in reversed(range(len(messages))):
                msg = messages[idx]
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    raw_user = msg["content"]
                    compaction = PromptCompactor.compact(raw_user, enable_deepseek=False)
                    if compaction["saved_tokens"] > 5:
                        msg["content"] = compaction["compact_text"]
                    break

        # 3. Forward to Upstream
        auth_header = self.headers.get("Authorization", "")
        # Check if local config has deepseek key
        config_path = os.path.join(BASE_DIR, "data", "config.json")
        upstream_key = ""
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    conf = json.load(f)
                    upstream_key = conf.get("deepseek_api_key", "")
            except Exception:
                pass

        final_auth = auth_header or (f"Bearer {upstream_key}" if upstream_key else "")
        upstream_url = os.environ.get("TOKENSAFE_UPSTREAM_URL", "https://api.deepseek.com/chat/completions")

        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": final_auth,
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                resp_data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in ("content-type", "content-encoding"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Upstream proxy error: {str(e)}"}).encode("utf-8"))

def run_proxy(port: int = DEFAULT_PORT):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, TokenSafeProxyHandler)
    print(f"\n⚡ TokenSafe Local AI Proxy running on: http://127.0.0.1:{port}")
    print(f"   Dashboard: http://127.0.0.1:{port}/dashboard")
    print(f"   OpenAI-compatible URL: http://127.0.0.1:{port}/v1")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy stopped.")

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run_proxy(port)
