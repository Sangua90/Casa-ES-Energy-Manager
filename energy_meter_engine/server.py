"""Authenticated internal planning endpoint, bounded request size."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import hmac
import json
import os

from engine import Engine


def main():
    options = json.loads(Path("/data/options.json").read_text())
    token = options.get("api_token", "")
    if len(token) < 32:
        raise RuntimeError("Configure an API token of at least 32 characters")
    engine = Engine("/data/models.json")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, code, body):
            raw = json.dumps(body, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.answer(200 if self.path == "/health" else 404,
                        {"schema": 1, "status": "ready", "version": "0.1.1"})

        def do_POST(self):
            if self.path != "/v1/plan":
                return self.answer(404, {"error": "not_found"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.answer(401, {"error": "unauthorized"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1048576:
                    return self.answer(413, {"error": "request_size"})
                payload = json.loads(self.rfile.read(size))
                result = engine.plan(payload)
            except (ValueError, KeyError, TypeError, OverflowError):
                return self.answer(400, {"error": "invalid_snapshot"})
            self.answer(200, result)

    server = HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8099"))), Handler)
    server.timeout = 10
    print("Energy Meter Engine 0.1.1 ready; no device service access", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

