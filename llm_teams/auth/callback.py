"""Local HTTP server that catches the OAuth2 redirect and extracts the auth code."""
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

_HTML_SUCCESS = b"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Authenticated</title>
<style>
  body{font-family:system-ui,sans-serif;display:flex;align-items:center;
       justify-content:center;height:100vh;margin:0;background:#f0fdf4}
  .card{background:#fff;border-radius:12px;padding:40px 48px;
        box-shadow:0 4px 24px rgba(0,0,0,.08);text-align:center}
  h2{color:#16a34a;margin:0 0 8px}
  p{color:#6b7280;margin:0}
</style>
</head>
<body>
<div class="card">
  <h2>&#10003; Authentication successful</h2>
  <p>You can close this tab and return to the terminal.</p>
</div>
<script>setTimeout(()=>window.close(),2000)</script>
</body></html>
"""

_HTML_ERROR = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Auth error</title>
<style>
  body{{font-family:system-ui,sans-serif;display:flex;align-items:center;
       justify-content:center;height:100vh;margin:0;background:#fef2f2}}
  .card{{background:#fff;border-radius:12px;padding:40px 48px;
         box-shadow:0 4px 24px rgba(0,0,0,.08);text-align:center}}
  h2{{color:#dc2626;margin:0 0 8px}}
  p{{color:#6b7280;margin:0}}
</style>
</head>
<body>
<div class="card">
  <h2>&#10007; Authentication failed</h2>
  <p>{error}</p>
</div>
</body></html>
"""


class _CallbackHandler(BaseHTTPRequestHandler):
    def __init__(self, result_queue: "queue.Queue[dict]", *args, **kwargs):
        self._q = result_queue
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query, keep_blank_values=True)

        if "code" in params:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML_SUCCESS)
            self._q.put(
                {
                    "code": params["code"][0],
                    "state": params.get("state", [""])[0],
                }
            )
        else:
            error = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            body = _HTML_ERROR.format(error=error).encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            self._q.put({"error": error})

    def log_message(self, *_):
        pass  # suppress server logs in terminal


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def start_callback_server() -> tuple[HTTPServer, int, "queue.Queue[dict]"]:
    """Start a one-shot local server.  Returns (server, port, result_queue)."""
    result_queue: "queue.Queue[dict]" = queue.Queue(maxsize=1)
    port = _free_port()

    handler = lambda *a, **kw: _CallbackHandler(result_queue, *a, **kw)
    server = HTTPServer(("localhost", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, result_queue
