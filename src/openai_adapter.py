import json, time, uuid, secrets, hmac, os, logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from antigravity_pro_bridge import Bridge, discover, rpc

ROOT = Path(__file__).parent
KEY = (ROOT / "adapter.key").read_text().strip()
bridge = Bridge()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.info(fmt, *args)

    def authorized(self):
        return hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + KEY
        )

    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.path == "/health":
                models = bridge.models()
                return self.send_json(
                    200, {"ok": True, "hubPid": discover()[2], "models": len(models)}
                )
            if not self.authorized():
                return self.send_json(401, {"error": {"message": "Unauthorized"}})
            if self.path == "/v1/models":
                return self.send_json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": m["modelId"],
                                "object": "model",
                                "owned_by": "antigravity",
                            }
                            for m in bridge.models()
                        ],
                    },
                )
            self.send_json(404, {"error": {"message": "Not found"}})
        except Exception as e:
            self.send_json(503, {"error": {"message": str(e)}})

    def do_POST(self):
        if not self.authorized():
            return self.send_json(401, {"error": {"message": "Unauthorized"}})
        if self.path != "/v1/chat/completions":
            return self.send_json(404, {"error": {"message": "Not found"}})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 < n <= 2097152:
                return self.send_json(
                    413, {"error": {"message": "Invalid request size"}}
                )
            d = json.loads(self.rfile.read(n))
            model = d.get("model", "")
            msg, meta = bridge.complete(
                model,
                d.get("messages", []),
                d.get("tools"),
                d.get("user", self.headers.get("X-OpenClaw-Session-Key", "")),
                d.get("tool_choice"),
            )
            finish = "tool_calls" if msg.get("tool_calls") else "stop"
            rid = "chatcmpl-" + uuid.uuid4().hex
            created = int(time.time())
            logging.info(
                "Hub RPC completed model=%s reused=%s cascade=%s",
                model,
                meta["reused"],
                meta["cascade"],
            )
            if not d.get("stream"):
                return self.send_json(
                    200,
                    {
                        "id": rid,
                        "object": "chat.completion",
                        "created": created,
                        "model": model,
                        "choices": [
                            {"index": 0, "message": msg, "finish_reason": finish}
                        ],
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                    },
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            delta = dict(msg)
            if "tool_calls" in delta:
                delta["tool_calls"] = [
                    dict(call, index=i) for i, call in enumerate(delta["tool_calls"])
                ]
            for payload, reason in [(delta, None), ({}, finish)]:
                chunk = {
                    "id": rid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": payload, "finish_reason": reason}
                    ],
                }
                self.wfile.write(
                    ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode()
                )
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            logging.warning("Client disconnected")
        except Exception as e:
            logging.warning("Request failed: %s", e)
            self.send_json(
                400 if isinstance(e, (ValueError, KeyError)) else 502,
                {"error": {"message": str(e), "type": "adapter_error"}},
            )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 18800), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
