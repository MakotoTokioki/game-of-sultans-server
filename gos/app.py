from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import config
from .handlers import handle_distribute, handle_forward, handle_jwt_action
from .pgstore import init_pg_store
from .store import Store

_store = init_pg_store(Store(), config.PG_DSN)
_log_lock = threading.Lock()
_music_lock = threading.Lock()
_music_cache: bytes | None = None


def _music_bytes() -> bytes:
    """A ~0.5 s silent MP3 (mono 24 kHz, 32 kbps) generated once with ffmpeg."""
    global _music_cache
    with _music_lock:
        if _music_cache is None:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                     "-t", "0.5", "-b:a", "32k", "-f", "mp3", "-y", tmp_path],
                    check=True, capture_output=True,
                )
                with open(tmp_path, "rb") as fh:
                    _music_cache = fh.read()
            finally:
                import os
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if not _music_cache:
                _music_cache = b""
    return _music_cache or b""


def log_event(event: dict) -> None:
    event.setdefault("ts", time.time())
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with _log_lock:
        with open(config.LOG_DIR / "requests.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _brief(value, limit: int = 800):
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


class Handler(BaseHTTPRequestHandler):
    server_version = "GoSDevServer/0.1"
    protocol_version = "HTTP/1.1"

    # quieter default logging; we keep our own JSONL
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.rstrip("/") in ("", "/health"):
            self._send(200, {"ok": True, "service": "game-of-sultans-dev-server", "time": int(time.time())})
        elif "/vocal_music/" in path or path.endswith(".mp3"):
            # musicMod downloads story/tutorial voices from GOS_MUSIC_BASE_URL
            # (advertised as MusicUrl in the 9006 response).  Serve a short
            # silent MP3 so network.download completes and the story dialog
            # advances instead of hanging on "Malformed URL".
            body = _music_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        started = time.time()
        path = urlparse(self.path).path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            log_event({"endpoint": path, "error": f"bad body: {exc!r}"})
            self._send(400, {"error": "bad json"})
            return

        try:
            if path.endswith("/jwt_action"):
                response = handle_jwt_action(body, _store)
                endpoint = "jwt_action"
            elif path.endswith("/distribute"):
                response = handle_distribute(body, _store)
                endpoint = "distribute"
            elif path.endswith("/forward"):
                response = handle_forward(body, _store)
                endpoint = "forward"
            elif path.endswith("/custom_event"):
                # SDK telemetry/custom-event callback; acknowledge and ignore.
                response = {"stat": 1, "data": json.dumps({"Code": 0, "Data": {}})}
                endpoint = "custom_event"
            elif path.endswith("/dis") or path.endswith("/dist") or path.endswith("/distributi"):
                # Same-length aliases used by the patched SDK URLs.
                response = handle_distribute(body, _store)
                endpoint = "distribute"
            elif path.startswith("/frontend/"):
                # Any other SDK/game path is treated as a routed action.
                response = handle_distribute(body, _store)
                endpoint = "distribute"
            else:
                log_event({"endpoint": path, "error": "unknown endpoint"})
                self._send(404, {"error": "unknown endpoint"})
                return
        except Exception as exc:
            log_event({"endpoint": path, "error": repr(exc), "request": _brief(body)})
            self._send(500, {"stat": 0, "info": "server error"})
            return

        elapsed = (time.time() - started) * 1000
        log_event(
            {
                "endpoint": endpoint,
                "action": body.get("actionid") or body.get("act_id"),
                "request": _brief(body),
                "response": _brief(response),
                "ms": round(elapsed, 2),
            }
        )
        self._send(200, response)


def main() -> None:
    addr = (config.HOST, config.PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"Game of Sultans dev server listening on http://{config.HOST}:{config.PORT}")
    print(f"  data: {config.DATA_DIR}")
    print(f"  logs: {config.LOG_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
