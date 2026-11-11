"""
Local AI Transparent Proxy for Token Optimizer.
Intercepts LLM requests from any agent (Cursor, Claude Code, Windsurf, Aider, Cline).
Performs in-flight prompt compaction, output streaming, tool loop detection, and token tracking.
Endpoint: http://127.0.0.1:8765/v1
"""

import sys
import os
import json
import queue
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from src.compactor import PromptCompactor
from src.metrics import MetricsTracker
from src.dashboard import Dashboard
from src.loop_detector import LoopDetector
from src.secret_redactor import SecretRedactor
from src.graph_engine import GraphEngine
from src.cache_normalizer import PromptCacheNormalizer
from src.governor import ContextGovernor
from src.paths import resolve_data_dir

DEFAULT_PORT = 8765
DEFAULT_HOST = os.environ.get("HOST", os.environ.get("TOKENSAFE_HOST", "0.0.0.0"))
DEFAULT_UPSTREAM = "https://api.deepseek.com/v1"

class EventBroadcaster:
    """Thread-safe pub/sub for Server-Sent Events (SSE) streaming."""
    def __init__(self):
        self._subscribers: List[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_type: str, data: Dict[str, Any]):
        payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for d in dead:
                self._subscribers.remove(d)

BROADCASTER = EventBroadcaster()

class TokenSafeProxyHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests and proxies /v1/chat/completions with token optimization."""

    def log_message(self, format, *args):
        # Mute noisy default server logs
        pass

    def _validate_origin(self) -> bool:
        """Protects against DNS rebinding and cross-origin attacks from arbitrary browser origins."""
        origin = self.headers.get("Origin", "")
        if origin:
            allowed_prefixes = ["http://127.0.0.1", "http://localhost", "http://0.0.0.0", "https://tokensafe.dev"]
            custom_origins = os.environ.get("TOKENSAFE_ALLOWED_ORIGINS", "")
            if custom_origins:
                allowed_prefixes.extend([o.strip() for o in custom_origins.split(",") if o.strip()])
            if not any(origin.startswith(a) for a in allowed_prefixes):
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Forbidden: Untrusted cross-origin request rejected"}')
                return False
        return True

    def do_GET(self):
        if not self._validate_origin():
            return

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

        elif self.path in ("/api/events", "/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q = BROADCASTER.subscribe()
            try:
                initial_summary = tracker.get_summary()
                init_msg = f"event: init\ndata: {json.dumps(initial_summary, ensure_ascii=False)}\n\n"
                self.wfile.write(init_msg.encode("utf-8"))
                self.wfile.flush()

                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                BROADCASTER.unsubscribe(q)
            return

        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "active", "proxy": "tokensafe"}).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._validate_origin():
            return

        if self.path.startswith("/v1/chat/completions"):
            self._touch_proxy_heartbeat()
            self.handle_chat_completions()
        else:
            self.send_response(404)
            self.end_headers()

    @staticmethod
    def _touch_proxy_heartbeat():
        """Marks the proxy as actively serving agent traffic.

        Lifecycle hooks read this marker to skip their own prompt compaction:
        when the proxy is in the loop it REPLACES the last user message with the
        TOCP form, so hook-side injection would double-count the same prompt."""
        try:
            marker = os.path.join(resolve_data_dir(), ".proxy_active")
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(int(__import__("time").time())))
        except Exception:
            pass

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

        proxy_tracker = MetricsTracker()

        # 1. Local Secret Redaction: Scrub credentials across all messages before dispatch
        messages = payload.get("messages", [])
        if messages:
            messages, secrets_scrubbed = SecretRedactor.redact_messages(messages)
            if secrets_scrubbed > 0:
                print(f"[TokenSafe] Scrubbed {secrets_scrubbed} sensitive secret(s) from payload before transmission.")

        # 2. Ingress Optimization: REPLACE the last user message with its TOCP form.
        #    This is the real replace-mode: the upstream model never sees the verbose
        #    original, so the delta is literally measured (see METRICS.md).
        target_symbol = None
        if messages:
            for idx in reversed(range(len(messages))):
                msg = messages[idx]
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    raw_user = msg["content"]
                    compaction = PromptCompactor.compact(raw_user, enable_deepseek=False)
                    if compaction["saved_tokens"] > 5:
                        msg["content"] = compaction["compact_text"]
                        try:
                            proxy_tracker.record_event(
                                session_id="proxy_session",
                                event_type="PROMPT",
                                original_tokens=compaction["original_tokens"],
                                optimized_tokens=compaction["compact_tokens"],
                                details={
                                    "mode": "proxy_replace",
                                    "action": compaction.get("action"),
                                    "symbol": compaction.get("symbol"),
                                    "tier": compaction.get("tier", "REGEX"),
                                },
                                measurement="measured",  # bytes literally swapped
                            )
                        except Exception:
                            pass
                    target_symbol = compaction.get("symbol")
                    break

        # 3. Automatic Architecture Awareness (God Nodes Injection)
        if messages:
            try:
                graph = GraphEngine.get_instance(os.getcwd())
                banner = graph.format_topology_banner()
                first_msg = messages[0]
                if banner and isinstance(first_msg.get("content"), str) and "[TokenSafe Architecture" not in first_msg["content"]:
                    if first_msg.get("role") == "system":
                        first_msg["content"] = f"{banner}\n{first_msg['content']}"
            except Exception:
                pass

        # 4. Prompt Cache Prefix Normalization (100% Cache Hits)
        if messages:
            messages, cache_meta = PromptCacheNormalizer.normalize_messages(messages)
            payload["messages"] = messages

        # 5. Context Window Governor (Budget Autothrottle)
        if messages:
            messages, gov_stats = ContextGovernor.govern_messages(messages, session_id="proxy_session")
            payload["messages"] = messages
            if gov_stats.get("throttled"):
                BROADCASTER.publish("governor_throttle", gov_stats)

        # 6. Resolve Multi-Provider Upstream & API Key
        auth_header = self.headers.get("Authorization", "")
        custom_url = os.environ.get("TOKENSAFE_UPSTREAM_URL", "")

        # Supported keys from environment or config
        data_dir = resolve_data_dir()
        conf_keys = {}
        config_path = os.path.join(data_dir, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    conf_keys = json.load(f)
            except Exception:
                pass

        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "") or conf_keys.get("deepseek_api_key", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "") or conf_keys.get("openai_api_key", "")
        gemini_key = os.environ.get("GEMINI_API_KEY", "") or conf_keys.get("gemini_api_key", "")
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "") or conf_keys.get("anthropic_api_key", "")
        generic_upstream_key = os.environ.get("TOKENSAFE_UPSTREAM_KEY", "")

        # Infer upstream URL if not provided
        pref = conf_keys.get("preferred_provider", "").lower()
        if not custom_url:
            if (pref == "gemini" and gemini_key) or (gemini_key and not deepseek_key and not openai_key):
                upstream_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                resolved_key = gemini_key
            elif openai_key and not deepseek_key:
                upstream_url = "https://api.openai.com/v1/chat/completions"
                resolved_key = openai_key
            elif anthropic_key and not deepseek_key:
                upstream_url = "https://api.anthropic.com/v1/messages"
                resolved_key = anthropic_key
            else:
                upstream_url = "https://api.deepseek.com/chat/completions"
                resolved_key = deepseek_key or generic_upstream_key
        else:
            upstream_url = custom_url
            if "openai.com" in upstream_url:
                resolved_key = openai_key or generic_upstream_key or deepseek_key
            elif "anthropic.com" in upstream_url:
                resolved_key = anthropic_key or generic_upstream_key
            elif "googleapis.com" in upstream_url:
                resolved_key = gemini_key or generic_upstream_key
            else:
                resolved_key = deepseek_key or generic_upstream_key or openai_key

        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        elif resolved_key:
            if "anthropic.com" in upstream_url:
                headers["x-api-key"] = resolved_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {resolved_key}"

        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
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

                # Broadcast live telemetry update to SSE dashboard subscribers
                try:
                    tracker = MetricsTracker()
                    summary = tracker.get_summary()
                    BROADCASTER.publish("stats_update", summary)
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Upstream proxy error: {str(e)}"}).encode("utf-8"))

def run_proxy(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST):
    # Auto-initialize Knowledge Graph & API Surface on startup
    hubs_summary = "active"
    try:
        graph = GraphEngine.get_instance(os.getcwd())
        graph.generate_api_surface()
        god_nodes = graph.get_god_nodes(3)
        if god_nodes:
            hubs_summary = ", ".join([f"{g['file']} ({g['dependents']} deps)" for g in god_nodes])
    except Exception as e:
        hubs_summary = f"initialized ({e})"

    server_address = (host, port)
    httpd = HTTPServer(server_address, TokenSafeProxyHandler)
    display_host = "localhost" if host in ("0.0.0.0", "", "127.0.0.1") else host
    print(f"\n⚡ TokenSafe Local AI Proxy running on: http://{display_host}:{port} (bound to {host})")
    print(f"   Dashboard: http://{display_host}:{port}/dashboard")
    print(f"   OpenAI-compatible URL: http://{display_host}:{port}/v1")
    print(f"   Auto-Graph: {hubs_summary}")
    print(f"   API Surface: tokensafe-out/API_SURFACE.md")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy stopped.")

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run_proxy(port)

if __name__ == "__main__":
    main()
