"""UnrealMCP plugin bridge.

Runs inside Unreal Editor and exposes:
- GET /health
- POST /tool/<name>

When executed outside Unreal (for local validation), it still serves /health.
"""

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

try:
    import unreal  # type: ignore
except Exception:  # pragma: no cover - allows local smoke test without Unreal
    class _UnrealFallback:
        @staticmethod
        def log(msg: str):
            print(msg)

    unreal = _UnrealFallback()  # type: ignore

PORT = 8765
LOG_LINES: list[str] = []
_server_thread: threading.Thread | None = None
_httpd: HTTPServer | None = None


def ok(action: str, result: Any) -> dict:
    return {
        "success": True,
        "action": action,
        "result": result,
        "logs": LOG_LINES[-50:],
        "errors": [],
    }


def err(action: str, message: str, tb: str = "") -> dict:
    return {
        "success": False,
        "action": action,
        "result": None,
        "logs": LOG_LINES[-50:],
        "errors": [message, tb] if tb else [message],
    }


def tool_ping(_args: dict) -> dict:
    return ok("ping", {"status": "ok"})


TOOLS = {
    "ping": tool_ping,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def _respond(self, status: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "plugin": "UnrealMCP"})
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            self._respond(400, err("parse", f"Bad JSON: {e}"))
            return

        if self.path.startswith("/tool/"):
            tool_name = self.path[len("/tool/"):]
            handler = TOOLS.get(tool_name)
            if not handler:
                self._respond(404, err(tool_name, f"Unknown tool: {tool_name}"))
                return
            try:
                result = handler(body.get("args", {}))
                self._respond(200, result)
            except Exception as e:
                self._respond(500, err(tool_name, str(e), traceback.format_exc()))
            return

        self._respond(404, err("route", f"Unknown path: {self.path}"))


def start():
    global _server_thread, _httpd
    if _server_thread and _server_thread.is_alive():
        unreal.log("UnrealMCP: server already running")
        return

    try:
        _httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        _httpd = None
        unreal.log(f"UnrealMCP: failed to start HTTP bridge on 127.0.0.1:{PORT}: {e}")
        return
    _server_thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    _server_thread.start()
    unreal.log(f"UnrealMCP: HTTP bridge listening on 127.0.0.1:{PORT}")


def stop():
    global _httpd, _server_thread
    if _httpd:
        httpd = _httpd
        server_thread = _server_thread
        try:
            httpd.shutdown()
            httpd.server_close()
            if (
                server_thread
                and server_thread.is_alive()
                and server_thread is not threading.current_thread()
            ):
                server_thread.join(timeout=5)
        finally:
            _httpd = None
            _server_thread = None
        unreal.log("UnrealMCP: HTTP bridge stopped")


start()
