"""Tests for llm_teams.auth.callback — OAuth2 local callback server."""
import queue
import time
import threading

import httpx
import pytest

from llm_teams.auth.callback import start_callback_server


@pytest.fixture
def server():
    srv, port, q = start_callback_server()
    yield srv, port, q
    srv.shutdown()


class TestCallbackServer:
    def test_starts_and_listens(self, server):
        _, port, _ = server
        # Just verify the port is open
        resp = httpx.get(f"http://localhost:{port}/callback?code=x&state=y", timeout=3)
        assert resp.status_code == 200

    def test_puts_code_on_queue(self, server):
        _, port, q = server
        httpx.get(f"http://localhost:{port}/callback?code=mycode&state=mystate", timeout=3)
        result = q.get(timeout=2)
        assert result["code"] == "mycode"
        assert result["state"] == "mystate"

    def test_puts_error_on_queue(self, server):
        _, port, q = server
        httpx.get(
            f"http://localhost:{port}/callback?error=access_denied&error_description=denied",
            timeout=3,
        )
        result = q.get(timeout=2)
        assert "error" in result

    def test_success_response_contains_html(self, server):
        _, port, _ = server
        resp = httpx.get(f"http://localhost:{port}/callback?code=x&state=y", timeout=3)
        assert b"Authentication successful" in resp.content

    def test_error_response_is_400(self, server):
        _, port, _ = server
        resp = httpx.get(f"http://localhost:{port}/callback?error=denied", timeout=3)
        assert resp.status_code == 400

    def test_two_servers_get_different_ports(self):
        srv1, port1, _ = start_callback_server()
        srv2, port2, _ = start_callback_server()
        try:
            assert port1 != port2
        finally:
            srv1.shutdown()
            srv2.shutdown()
