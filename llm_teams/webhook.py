"""Local HTTPS server for Microsoft Graph change notifications (push mode).

Graph requires a publicly reachable HTTPS URL. Two strategies are supported:

  1. ngrok tunnel (auto-detected if `ngrok` is on PATH)
  2. Explicit public URL provided by the caller (e.g. localtunnel, Cloudflare, etc.)

Graph sends a POST to the notification_url with a JSON payload.
Each notification is validated (clientState check) and put on a queue for the consumer.
"""
import hashlib
import hmac
import json
import queue
import socket
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse


_CLIENT_STATE = "llm-teams"


# ------------------------------------------------------------------ #
# Self-signed cert (Graph needs HTTPS even for localhost via ngrok)
# ------------------------------------------------------------------ #

def _ensure_selfsigned_cert() -> tuple[Path, Path]:
    """Return (cert_path, key_path), generating them once in a temp dir."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "llm-teams-local"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        d = Path(tempfile.mkdtemp())
        cert_path = d / "cert.pem"
        key_path = d / "key.pem"

        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return cert_path, key_path

    except ImportError:
        raise RuntimeError(
            "Install `cryptography` for webhook push mode: pip install cryptography"
        )


# ------------------------------------------------------------------ #
# HTTP handler
# ------------------------------------------------------------------ #

class _NotificationHandler(BaseHTTPRequestHandler):
    def __init__(self, event_queue: "queue.Queue[dict]", *args, **kwargs):
        self._q = event_queue
        super().__init__(*args, **kwargs)

    def do_POST(self):
        # Graph sends a validation request first (GET or POST with validationToken)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "validationToken" in qs:
            token = qs["validationToken"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(token.encode())
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        self.send_response(202)  # Graph expects 202 Accepted within 3s
        self.end_headers()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return

        for notification in payload.get("value", []):
            if notification.get("clientState") != _CLIENT_STATE:
                continue  # reject unknown sources
            self._q.put(notification)

    def do_GET(self):
        # Validation handshake (some Graph scenarios use GET)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "validationToken" in qs:
            token = qs["validationToken"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(token.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


# ------------------------------------------------------------------ #
# Server lifecycle
# ------------------------------------------------------------------ #

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_notification_server(
    public_url: Optional[str] = None,
) -> tuple["HTTPServer", int, "queue.Queue[dict]", str]:
    """Start the local notification server.

    Returns (server, port, event_queue, public_url).
    If public_url is None, tries to auto-start an ngrok tunnel.
    """
    event_queue: "queue.Queue[dict]" = queue.Queue()
    port = _free_port()

    handler = lambda *a, **kw: _NotificationHandler(event_queue, *a, **kw)
    server = HTTPServer(("0.0.0.0", port), handler)

    # Wrap with TLS (Graph requires HTTPS)
    cert_path, key_path = _ensure_selfsigned_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if not public_url:
        public_url = _start_ngrok(port)

    return server, port, event_queue, public_url


def _start_ngrok(port: int) -> str:
    """Try to start an ngrok tunnel. Returns the public HTTPS URL."""
    import subprocess, time

    try:
        # ngrok must already be authenticated: `ngrok authtoken <token>`
        proc = subprocess.Popen(
            ["ngrok", "http", "--scheme=https", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)  # give ngrok time to establish the tunnel

        # Query ngrok's local API for the tunnel URL
        import httpx
        resp = httpx.get("http://localhost:4040/api/tunnels", timeout=5)
        tunnels = resp.json().get("tunnels", [])
        for t in tunnels:
            if t.get("proto") == "https":
                return t["public_url"]

    except Exception as exc:
        raise RuntimeError(
            f"Could not start ngrok tunnel: {exc}\n"
            "Install ngrok (https://ngrok.com) and run `ngrok authtoken <token>` first, "
            "or pass --notification-url with a public HTTPS URL."
        )

    raise RuntimeError("ngrok started but no HTTPS tunnel found.")
