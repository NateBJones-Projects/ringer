#!/usr/bin/env python3
"""Local capture sink for probing what an OpenAI-compatible client puts on the wire.

Listens on 127.0.0.1:<port>, appends one JSON row per request (path, headers
lower-cased, parsed JSON body) to <capture_file>, and answers with a minimal
non-streaming chat completion. No model is involved anywhere.

Usage: header_sink.py <port> <capture_file>
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Sink(BaseHTTPRequestHandler):
    capture_path = ""

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8", "replace")) if raw else None
        except json.JSONDecodeError:
            body = {"_unparsed": raw.decode("utf-8", "replace")[:2000]}
        row = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        with open(self.capture_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        reply = json.dumps(
            {
                "id": "chatcmpl-probe",
                "object": "chat.completion",
                "created": 0,
                "model": "probe/sink",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:  # silence per-request stderr noise
        pass


def main() -> int:
    port, capture = int(sys.argv[1]), sys.argv[2]
    Sink.capture_path = capture
    HTTPServer(("127.0.0.1", port), Sink).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
