"""
Local AI Transparent Proxy for Token Optimizer.
Intercepts LLM requests from any agent (Cursor, Claude Code, Windsurf, Aider, Cline).
Performs in-flight prompt compaction, output streaming, tool loop detection, and token tracking.
Endpoint: http://127.0.0.1:8765/v1
"""

import sys
import os
import json
import time
import queue
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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
        # Live relay: anonymized metadata copy to the cloud live view (best-effort)
        try:
            from src.live_relay import LIVE_RELAY
            LIVE_RELAY.record(event_type, data if isinstance(data, dict) else {})
        except Exception:
            pass
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


# ---------------------------------------------------------------------------
# OpenAI <-> Anthropic payload translation.
# The proxy accepts OpenAI /v1/chat/completions bodies from any agent; when the
# resolved upstream is Anthropic (/v1/messages) the body, response, SSE stream,
# and error envelopes must be converted in-flight, otherwise the raw OpenAI
# shape is rejected by the Anthropic API (missing max_tokens, system role, etc).
# ---------------------------------------------------------------------------

_ANTHROPIC_FINISH_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}

_OPENAI_ONLY_FIELDS = (
    "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs", "n",
    "response_format", "seed", "stream_options", "user", "parallel_tool_calls",
    "prompt_cache_key", "logit_bias", "service_tier",
)


def _blocks_from_content(content: Any, role: str = "user") -> List[Dict[str, Any]]:
    """Converts one OpenAI message content (str or block list) into Anthropic content blocks."""
    blocks: List[Dict[str, Any]] = []
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
        return blocks
    if not isinstance(content, list):
        return blocks
    for blk in content:
        if not isinstance(blk, dict):
            continue
        btype = blk.get("type")
        if btype == "text" and isinstance(blk.get("text"), str):
            nb = {"type": "text", "text": blk["text"]}
            if isinstance(blk.get("cache_control"), dict):
                nb["cache_control"] = blk["cache_control"]
            blocks.append(nb)
        elif btype == "image_url":
            url = (blk.get("image_url") or {}).get("url", "") if isinstance(blk.get("image_url"), dict) else ""
            if url.startswith("data:"):
                head, _, b64 = url.partition(",")
                media = head[5:].split(";", 1)[0] or "image/png"
                if b64:
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
            elif url:
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


def _collapse_plain_text(blocks: List[Dict[str, Any]]) -> Any:
    """Collapses pure-text blocks (no cache_control) back into a plain string —
    Anthropic accepts string content, and it keeps payloads closest to the client's shape."""
    if not blocks or any(b.get("type") != "text" or "cache_control" in b for b in blocks):
        return blocks
    texts = [b.get("text", "") for b in blocks]
    return texts[0] if len(texts) == 1 else "\n\n".join(texts)


def _merge_same_role(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Anthropic requires alternating roles; folds consecutive same-role messages."""
    merged: List[Dict[str, Any]] = []
    for msg in messages:
        if merged and merged[-1].get("role") == msg.get("role"):
            prev = merged[-1]
            prev_c = prev.get("content")
            cur_c = msg.get("content")
            prev_blocks = prev_c if isinstance(prev_c, list) else ([{"type": "text", "text": prev_c}] if prev_c else [])
            cur_blocks = cur_c if isinstance(cur_c, list) else ([{"type": "text", "text": cur_c}] if cur_c else [])
            prev["content"] = prev_blocks + cur_blocks
        else:
            merged.append(msg)
    return merged


def _translate_openai_payload_to_anthropic(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Converts an OpenAI chat-completions payload into an Anthropic /v1/messages payload."""
    src_messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []

    system_parts: List[Dict[str, Any]] = []
    out_messages: List[Dict[str, Any]] = []
    for msg in src_messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("system", "developer"):
            for blk in _blocks_from_content(msg.get("content"), role):
                system_parts.append(blk)
            continue

        if role == "assistant":
            blocks: List[Dict[str, Any]] = _blocks_from_content(msg.get("content"), "assistant")
            for tc in (msg.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                try:
                    tool_input = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(tool_input, dict):
                        tool_input = {"_raw": tool_input}
                except Exception:
                    # Self-healing: repair malformed argument JSON instead of
                    # silently dropping the tool call's inputs.
                    from src.json_repair import parse_tool_arguments
                    tool_input = parse_tool_arguments(fn.get("arguments")) or {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or "toolu_call",
                    "name": fn.get("name") or "unknown",
                    "input": tool_input,
                })
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            out_messages.append({"role": "assistant", "content": _collapse_plain_text(blocks) if len(blocks) == 1 and blocks[0].get("type") == "text" else blocks})
        elif role == "tool":
            tc_content = msg.get("content")
            text = tc_content if isinstance(tc_content, str) else json.dumps(tc_content, ensure_ascii=False)
            out_messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id") or "toolu_call", "content": [{"type": "text", "text": text}]}],
            })
        elif role == "user":
            out_messages.append({"role": "user", "content": _collapse_plain_text(_blocks_from_content(msg.get("content"), "user")) or [{"type": "text", "text": ""}]})

    out: Dict[str, Any] = {
        "model": payload.get("model") or "claude-3-5-sonnet-latest",
        "max_tokens": payload.get("max_tokens") if isinstance(payload.get("max_tokens"), int) and payload["max_tokens"] > 0 else 4096,
        "messages": _merge_same_role(out_messages),
    }
    if system_parts:
        out["system"] = system_parts if len(system_parts) > 1 or system_parts[0].get("cache_control") else system_parts[0]["text"]
    if isinstance(payload.get("temperature"), (int, float)):
        out["temperature"] = payload["temperature"]
    if isinstance(payload.get("top_p"), (int, float)):
        out["top_p"] = payload["top_p"]
    if payload.get("stop"):
        out["stop_sequences"] = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
    if isinstance(payload.get("stream"), bool):
        out["stream"] = payload["stream"]

    # Tools: OpenAI {type:function, function:{...}} -> Anthropic schema.
    # Tools already in Anthropic shape ({name, input_schema}) pass through untouched.
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        ant_tools = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            if isinstance(t.get("input_schema"), dict) and t.get("name"):
                at = dict(t)
            else:
                fn = t.get("function") if isinstance(t.get("function"), dict) else t
                name = fn.get("name")
                if not name:
                    continue
                at = {
                    "name": name,
                    "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}},
                }
                if isinstance(t.get("cache_control"), dict):
                    at["cache_control"] = t["cache_control"]
            ant_tools.append(at)
        if ant_tools:
            out["tools"] = ant_tools
            tc = payload.get("tool_choice")
            if tc == "auto" or (isinstance(tc, dict) and tc.get("type") == "auto"):
                out["tool_choice"] = {"type": "auto"}
            elif tc == "required" or (isinstance(tc, dict) and tc.get("type") == "required"):
                out["tool_choice"] = {"type": "any"}
            elif isinstance(tc, dict) and tc.get("type") == "function":
                fname = (tc.get("function") or {}).get("name")
                if fname:
                    out["tool_choice"] = {"type": "tool", "name": fname}

    for k in _OPENAI_ONLY_FIELDS:
        out.pop(k, None)
    return out


def _translate_anthropic_json_to_openai(resp_json: Dict[str, Any], fallback_model: str = "") -> Dict[str, Any]:
    """Converts a non-streaming Anthropic /v1/messages response into an OpenAI chat.completion."""
    if not isinstance(resp_json, dict) or resp_json.get("type") not in (None, "message"):
        return resp_json  # already OpenAI-shaped or unknown envelope: pass through
    blocks = resp_json.get("content") if isinstance(resp_json.get("content"), list) else []
    text_parts: List[str] = []
    tool_calls = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        if blk.get("type") == "text" and isinstance(blk.get("text"), str):
            text_parts.append(blk["text"])
        elif blk.get("type") == "tool_use":
            tool_calls.append({
                "id": blk.get("id") or f"call_{blk.get('name', 'tool')}",
                "type": "function",
                "function": {
                    "name": blk.get("name") or "unknown",
                    "arguments": json.dumps(blk.get("input") or {}, ensure_ascii=False),
                },
            })
    usage = resp_json.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    finish = _ANTHROPIC_FINISH_MAP.get(resp_json.get("stop_reason"), "stop" if resp_json.get("stop_reason") else None)
    message: Dict[str, Any] = {"role": "assistant", "content": "\n\n".join(text_parts) if text_parts else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    translated = {
        "id": resp_json.get("id") or f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resp_json.get("model") or fallback_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "prompt_tokens_details": {"cached_tokens": cache_read},
        },
    }
    return translated


def _translate_anthropic_error_body(err_bytes: bytes) -> bytes:
    """Maps an Anthropic error envelope {type:error,error:{type,message}} onto the OpenAI one."""
    try:
        data = json.loads(err_bytes.decode("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("error"), dict) and data.get("type") == "error":
            inner = data["error"]
            return json.dumps({"error": {"message": inner.get("message", "Upstream error"), "type": inner.get("type", "api_error"), "code": None}}).encode("utf-8")
    except Exception:
        pass
    return err_bytes


class _AnthropicSSETranslator:
    """Converts an Anthropic /v1/messages SSE event stream into OpenAI chat.completion.chunk SSE lines."""

    def __init__(self, model: str = ""):
        self.model = model or "claude"
        self._buf = b""
        self._tool_blocks: Dict[int, Dict[str, Any]] = {}
        self._chunk_id = f"chatcmpl-{int(time.time() * 1000)}"
        self._done = False
        self._usage_in = 0

    def _chunk(self, delta: Dict[str, Any], finish: Any = None) -> bytes:
        payload = {
            "id": self._chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return (b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n")

    def _handle_event(self, event: str, data: Dict[str, Any]) -> bytes:
        out = b""
        if event == "message_start":
            msg = data.get("message") or {}
            if msg.get("model"):
                self.model = msg["model"]
            self._usage_in = int((msg.get("usage") or {}).get("input_tokens") or 0)
            out += self._chunk({"role": "assistant", "content": ""})
        elif event == "content_block_start":
            blk = data.get("content_block") or {}
            if blk.get("type") == "tool_use":
                idx = int(data.get("index") or 0)
                self._tool_blocks[idx] = {"id": blk.get("id") or "toolu_call", "name": blk.get("name") or "unknown"}
                out += self._chunk({"tool_calls": [{"index": idx, "id": self._tool_blocks[idx]["id"], "type": "function", "function": {"name": self._tool_blocks[idx]["name"], "arguments": ""}}]})
        elif event == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                out += self._chunk({"content": delta["text"]})
            elif delta.get("type") == "input_json_delta":
                idx = int(data.get("index") or 0)
                out += self._chunk({"tool_calls": [{"index": idx, "function": {"arguments": delta.get("partial_json") or ""}}]})
        elif event == "message_delta":
            delta = data.get("delta") or {}
            finish = _ANTHROPIC_FINISH_MAP.get(delta.get("stop_reason"))
            if finish or data.get("usage"):
                out += self._chunk({}, finish=finish)
        elif event == "message_stop" and not self._done:
            self._done = True
            out += b"data: [DONE]\n\n"
        return out

    def feed(self, chunk: bytes) -> bytes:
        """Feeds raw upstream bytes; returns translated OpenAI SSE bytes (may be empty)."""
        if not chunk:
            return b""
        self._buf += chunk
        out = b""
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = self._buf[:nl].rstrip(b"\r")
            self._buf = self._buf[nl + 1:]
            if not line:
                continue
            if line.startswith(b"event:"):
                self._pending_event = line[6:].strip().decode("utf-8", "replace")
            elif line.startswith(b"data:"):
                try:
                    data = json.loads(line[5:].strip().decode("utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict):
                    out += self._handle_event(getattr(self, "_pending_event", ""), data)
        return out

    def flush(self) -> bytes:
        """Emits a final [DONE] if the upstream stream ended without message_stop."""
        if self._done:
            return b""
        self._done = True
        return self._chunk({}, finish="stop") + b"data: [DONE]\n\n"


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

        if self.path == "/health":
            payload_bytes = json.dumps({"status": "active", "proxy": "tokensafe"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload_bytes)))
            self.end_headers()
            self.wfile.write(payload_bytes)
            return

        if self.path in ("/", "/dashboard", "/dashboard.html"):
            tracker = MetricsTracker()
            html_bytes = Dashboard(tracker).render_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        elif self.path in ("/metrics", "/api/metrics"):
            tracker = MetricsTracker()
            summary = tracker.get_summary()
            summary_bytes = json.dumps(summary, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(summary_bytes)))
            self.end_headers()
            self.wfile.write(summary_bytes)

        elif self.path in ("/api/events", "/events"):
            tracker = MetricsTracker()
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

        else:
            err_bytes = b'{"error": "Not found"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_bytes)))
            self.end_headers()
            self.wfile.write(err_bytes)

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

    def _read_request_body(self) -> bytes:
        te = str(self.headers.get("Transfer-Encoding", "")).lower()
        if "chunked" in te:
            chunks: List[bytes] = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size_str = line.split(b";", 1)[0].strip()
                chunk_size = int(size_str, 16)
                if chunk_size == 0:
                    while True:
                        trailer = self.rfile.readline()
                        if not trailer or trailer in (b"\r\n", b"\n"):
                            break
                    break
                chunks.append(self.rfile.read(chunk_size))
                self.rfile.readline()  # trailing CRLF
            return b"".join(chunks)
        content_len = max(0, int(self.headers.get("Content-Length", 0)))
        return self.rfile.read(content_len)

    def _send_json_error(self, status_code: int, message: str):
        err_bytes = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(err_bytes)))
        self.end_headers()
        self.wfile.write(err_bytes)

    def handle_chat_completions(self):
        try:
            body = self._read_request_body()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except Exception as e:
            self._send_json_error(400, f"Invalid JSON payload: {e}")
            return

        proxy_tracker = MetricsTracker()
        from src.license_manager import LicenseManager
        quota = LicenseManager.check_quota(proxy_tracker)
        is_quota_exceeded = bool(quota.get("is_quota_exceeded", False))

        # 1. Local Secret Redaction: Scrub credentials across all messages before dispatch (always active)
        messages = payload.get("messages", [])
        if messages:
            messages, secrets_scrubbed = SecretRedactor.redact_messages(messages)
            if secrets_scrubbed > 0:
                print(f"[TokenSafe] Scrubbed {secrets_scrubbed} sensitive secret(s) from payload before transmission.")

        # 2. Ingress Optimization: REPLACE the last user message with its TOCP form.
        #    Paused when Community 500k monthly measured quota is exceeded.
        target_symbol = None
        if messages and not is_quota_exceeded:
            for idx in reversed(range(len(messages))):
                msg = messages[idx]
                if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                    raw_user = msg["content"]
                    compaction = PromptCompactor.compact(raw_user, enable_distill=False)
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
                            from src.telemetry_sync import TelemetrySync
                            TelemetrySync.sync_background(throttle_seconds=20.0)
                        except Exception:
                            pass
                    target_symbol = compaction.get("symbol")
                    break

        # 3. Automatic Architecture Awareness (God Nodes Injection)
        if messages and not is_quota_exceeded:
            try:
                graph = GraphEngine.get_instance(os.getcwd())
                banner = graph.format_topology_banner()
                first_msg = messages[0]
                if banner and isinstance(first_msg.get("content"), str) and "[TokenSafe Architecture" not in first_msg["content"]:
                    if first_msg.get("role") == "system":
                        first_msg["content"] = f"{banner}\n{first_msg['content']}"
            except Exception:
                pass

        # 4. Resolve Multi-Provider Upstream & API Key (needed before provider-aware cache breakpoints)
        auth_header = self.headers.get("Authorization", "")
        custom_url = os.environ.get("TOKENSAFE_UPSTREAM_URL", "")

        data_dir = resolve_data_dir()
        conf_keys = {}
        config_path = os.path.join(data_dir, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    conf_keys = json.load(f)
            except Exception:
                pass

        upstream_url, resolved_key = self._resolve_upstream(conf_keys, custom_url)
        provider = self._detect_provider(upstream_url)

        # 5. Epoch-Aware Context Governor (runs BEFORE cache normalization so breakpoints stay valid)
        if messages and not is_quota_exceeded:
            messages, gov_stats = ContextGovernor.govern_messages(
                messages,
                session_id="proxy_session",
                model=str(payload.get("model") or ""),
                append_only=True,
            )
            payload["messages"] = messages
            if gov_stats.get("throttled"):
                BROADCASTER.publish("governor_throttle", gov_stats)

        # 6. Prompt Cache Prefix & Tool Normalization (multi-breakpoint + deterministic ordering)
        if not is_quota_exceeded:
            if messages:
                messages, cache_meta = PromptCacheNormalizer.normalize_messages(
                    messages,
                    provider=provider,
                    tools=payload.get("tools") if isinstance(payload.get("tools"), list) else None,
                )
                payload["messages"] = messages
                if cache_meta.get("normalized_tools") is not None:
                    payload["tools"] = cache_meta["normalized_tools"]
                if provider == "openai" and cache_meta.get("prompt_cache_key"):
                    payload.setdefault("prompt_cache_key", cache_meta["prompt_cache_key"])
            elif "tools" in payload and isinstance(payload["tools"], list):
                payload["tools"] = PromptCacheNormalizer.normalize_tools(
                    payload["tools"], provider=provider, inject_cache_control=False
                )

        if provider == "anthropic":
            payload = _translate_openai_payload_to_anthropic(payload)

        headers = {"Content-Type": "application/json"}
        if provider == "anthropic":
            x_api_key = self.headers.get("x-api-key", "") if hasattr(self.headers, "get") else ""
            bearer_key = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()
            api_key_val = x_api_key or bearer_key or resolved_key
            if api_key_val:
                headers["x-api-key"] = api_key_val
            headers["anthropic-version"] = "2023-06-01"
        elif auth_header:
            headers["Authorization"] = auth_header
        elif resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"

        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"),
            headers=headers
        )

        is_stream = bool(payload.get("stream"))
        req_timeout = 120.0 if is_stream else 30.0
        try:
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in ("content-type", "content-encoding"):
                        self.send_header(k, v)
                if is_quota_exceeded:
                    self.send_header("X-TokenSafe-Quota", "exceeded")

                if is_stream:
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    sse_t = _AnthropicSSETranslator(str(payload.get("model") or "")) if provider == "anthropic" else None
                    reader = getattr(resp, "read1", None) or resp.read
                    while True:
                        try:
                            chunk = reader(4096)
                        except TypeError:
                            chunk = resp.read()
                            if chunk:
                                out = sse_t.feed(chunk) if sse_t else chunk
                                if out:
                                    self.wfile.write(out)
                                    self.wfile.flush()
                            break
                        if not chunk:
                            break
                        out = sse_t.feed(chunk) if sse_t else chunk
                        if out:
                            self.wfile.write(out)
                            self.wfile.flush()
                    if sse_t:
                        tail = sse_t.flush()
                        if tail:
                            self.wfile.write(tail)
                            self.wfile.flush()
                else:
                    resp_data = resp.read()
                    if provider == "anthropic":
                        try:
                            raw_json = json.loads(resp_data.decode("utf-8"))
                            resp_data = json.dumps(
                                _translate_anthropic_json_to_openai(raw_json, str(payload.get("model") or "")),
                                ensure_ascii=False,
                            ).encode("utf-8")
                        except Exception:
                            pass
                    self.send_header("Content-Length", str(len(resp_data)))
                    self.end_headers()
                    self.wfile.write(resp_data)

                    try:
                        resp_json = json.loads(resp_data.decode("utf-8"))
                        usage = resp_json.get("usage") or {}
                        cached = (
                            (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                            or usage.get("cache_read_input_tokens")
                            or usage.get("prompt_cache_hit_tokens")
                            or 0
                        )
                        if cached:
                            BROADCASTER.publish("cache_stats", {"provider": provider, "cached_tokens": int(cached)})
                    except Exception:
                        pass

                # Broadcast live telemetry update to SSE dashboard subscribers
                try:
                    summary = proxy_tracker.get_summary()
                    BROADCASTER.publish("stats_update", summary)
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            with e:
                err_body = e.read()
                ctype = e.headers.get("Content-Type", "application/json") if e.headers else "application/json"
            if provider == "anthropic":
                err_body = _translate_anthropic_error_body(err_body)
            self.send_response(e.code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self._send_json_error(502, f"Upstream proxy error: {str(e)}")

    @staticmethod
    def _detect_provider(upstream_url: str) -> str:
        url = (upstream_url or "").lower()
        if "anthropic.com" in url:
            return "anthropic"
        if "openai.com" in url:
            return "openai"
        if "googleapis.com" in url or "generativelanguage" in url:
            return "gemini"
        if "deepseek.com" in url:
            return "deepseek"
        return "generic"

    @staticmethod
    def _resolve_upstream(conf_keys: Dict[str, Any], custom_url: str):
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "") or conf_keys.get("deepseek_api_key", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "") or conf_keys.get("openai_api_key", "")
        gemini_key = os.environ.get("GEMINI_API_KEY", "") or conf_keys.get("gemini_api_key", "")
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "") or conf_keys.get("anthropic_api_key", "")
        generic_upstream_key = os.environ.get("TOKENSAFE_UPSTREAM_KEY", "")

        pref = str(conf_keys.get("preferred_provider", "")).lower()
        if not custom_url:
            if (pref == "gemini" and gemini_key) or (gemini_key and not deepseek_key and not openai_key):
                return "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", gemini_key
            if openai_key and not deepseek_key:
                return "https://api.openai.com/v1/chat/completions", openai_key
            if anthropic_key and not deepseek_key:
                return "https://api.anthropic.com/v1/messages", anthropic_key
            return "https://api.deepseek.com/chat/completions", (deepseek_key or generic_upstream_key)

        if "openai.com" in custom_url:
            return custom_url, (openai_key or generic_upstream_key or deepseek_key)
        if "anthropic.com" in custom_url:
            return custom_url, (anthropic_key or generic_upstream_key)
        if "googleapis.com" in custom_url:
            return custom_url, (gemini_key or generic_upstream_key)
        return custom_url, (deepseek_key or generic_upstream_key or openai_key)

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
    class _TokenSafeThreadingServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = _TokenSafeThreadingServer(server_address, TokenSafeProxyHandler)
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
