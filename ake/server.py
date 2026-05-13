"""Minimal HTTP server for /health and /ready endpoints.

Runs in a daemon thread alongside the main async loop so that container
orchestrators can probe liveness/readiness independently of the MCP server.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_ready = threading.Event()


def mark_ready() -> None:
    """Call once migrations have completed and the DB pool is warm."""
    _ready.set()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(200, b'{"status":"ok"}')
        elif self.path == "/ready":
            if _ready.is_set():
                self._respond(200, b'{"status":"ready"}')
            else:
                self._respond(503, b'{"status":"starting"}')
        else:
            self._respond(404, b'{"status":"not found"}')

    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # suppress access logs; structured logging handles observability


def start(host: str = "0.0.0.0", port: int = 8000) -> HTTPServer:
    server = HTTPServer((host, port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    return server
