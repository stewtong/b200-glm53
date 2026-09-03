#!/usr/bin/env python3
"""Mock GLM-5.3-Flash-style endpoint for non-GPU tests.

Implements the routes the standing deployment exposes (exact-match paths) with
synthetic content, plus configurable failure modes selected by env var:

  MOCK_MODE=ok        (default) all routes succeed
  MOCK_MODE=http429   chat completions returns 429
  MOCK_MODE=silent    chat completions returns 200 with zero bytes and hangs
  MOCK_MODE=reasoning-only
                      chat completions streams only reasoning_content
  MOCK_MODE=finish-stop
                      chat completions ends with finish_reason "stop"
  MOCK_MODE=chat-empty  non-streaming chat completions has empty content
  MOCK_MODE=malformed-chat  non-streaming chat completions is invalid JSON
  MOCK_MODE=responses-failed  Responses returns status "failed"
  MOCK_MODE=unauthorized  every route returns 401

No real credentials, hosts, or model weights are involved.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("MOCK_MODE", "ok")
ROUTES = [
    "/v1/chat/completions", "/v1/completions", "/v1/models",
    "/v1/messages", "/v1/messages/count_tokens", "/v1/responses", "/ping",
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _raw_json(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        self._json(401, {"error": {"message": "mock: unauthorized"}})

    def do_GET(self):
        if self.path == "/ping":
            if MODE == "unauthorized":
                return self._unauthorized()
            return self._json(200, {"status": "ok"})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [
                {"id": "glm-5.3-flash", "object": "model"}]})
        self._json(404, {"error": {"message": "mock: unknown route"}})

    def do_POST(self):
        # Drain the request body before responding, mirroring a real server.
        self.body = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.body += chunk
        if MODE == "unauthorized":
            return self._unauthorized()
        if self.path == "/v1/messages":
            return self._json(200, {
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": "glm-5.3-flash", "content": [{"type": "text", "text": "mock reply"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 2},
            })
        if self.path == "/v1/messages/count_tokens":
            return self._json(200, {"input_tokens": 42})
        if self.path == "/v1/responses":
            status = "failed" if MODE == "responses-failed" else "completed"
            return self._json(200, {
                "id": "resp_mock", "object": "response", "status": status,
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "mock reply"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            })
        if self.path == "/v1/chat/completions":
            return self._chat_completions()
        self._json(404, {"error": {"message": "mock: unknown route"}})

    def _chat_completions(self):
        if MODE == "http429":
            return self._json(429, {"error": {"message": "mock: rate limited"}})
        try:
            payload = json.loads(self.body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        completion = int(payload.get("max_tokens") or 0)
        if not payload.get("stream"):
            if MODE == "malformed-chat":
                return self._raw_json(200, b"{not-json")
            content = "" if MODE == "chat-empty" else "mock reply"
            return self._json(200, {
                "id": "chatcmpl_mock", "object": "chat.completion",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": content},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            })
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(obj):
            data = b"data: " + json.dumps(obj).encode() + b"\n\n"
            self.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
            self.wfile.flush()

        def done():
            data = b"data: [DONE]\n\n"
            self.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
            self.wfile.flush()

        if MODE == "silent":
            # 200 with no bytes at all, until the client times out.
            time.sleep(30)
            return
        if MODE == "reasoning-only":
            for _ in range(3):
                chunk({"choices": [{"delta": {"reasoning_content": "mock reasoning "},
                                    "finish_reason": None}]})
            chunk({"choices": [{"delta": {}, "finish_reason": "length"}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": completion}})
            done()
        else:
            finish = "stop" if MODE == "finish-stop" else "length"
            for _ in range(3):
                chunk({"choices": [{"delta": {"content": "mock output "},
                                    "finish_reason": None}]})
            chunk({"choices": [{"delta": {}, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": completion}})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"mock endpoint on 127.0.0.1:{port} mode={MODE}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
