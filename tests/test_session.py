"""Tests for llm_teams.auth.session — session persistence."""
import json
import time
from pathlib import Path

import pytest

from llm_teams.auth.session import Session, save, load, clear


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    """Redirect session storage to a temp dir."""
    import llm_teams.auth.session as sess_mod
    config_dir = tmp_path / ".config" / "llm-teams"
    session_path = config_dir / "session.json"
    monkeypatch.setattr(sess_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(sess_mod, "_SESSION_FILE", session_path)
    return session_path


@pytest.fixture
def valid_session():
    return Session(
        access_token="tok_abc",
        token_type="Bearer",
        expires_at=time.time() + 3600,
        provider="microsoft",
        email="user@example.com",
        name="Test User",
        sub="sub-123",
    )


@pytest.fixture
def expired_session():
    return Session(
        access_token="tok_old",
        token_type="Bearer",
        expires_at=time.time() - 1,
        provider="microsoft",
    )


class TestSessionProperties:
    def test_not_expired(self, valid_session):
        assert valid_session.is_expired is False

    def test_expired(self, expired_session):
        assert expired_session.is_expired is True

    def test_expires_in_positive(self, valid_session):
        assert valid_session.expires_in > 0

    def test_expires_in_zero_when_past(self, expired_session):
        assert expired_session.expires_in == 0

    def test_display_name_uses_name(self, valid_session):
        assert valid_session.display_name == "Test User"

    def test_display_name_falls_back_to_email(self):
        s = Session("t", "Bearer", time.time() + 100, "ms", email="a@b.com")
        assert s.display_name == "a@b.com"

    def test_display_name_falls_back_to_sub(self):
        s = Session("t", "Bearer", time.time() + 100, "ms", sub="sub-99")
        assert s.display_name == "sub-99"

    def test_display_name_unknown_when_all_none(self):
        s = Session("t", "Bearer", time.time() + 100, "ms")
        assert s.display_name == "unknown"

    def test_auth_header(self, valid_session):
        assert valid_session.auth_header() == {"Authorization": "Bearer tok_abc"}

    def test_auth_header_token_type(self):
        s = Session("t", "Token", time.time() + 100, "github")
        assert s.auth_header() == {"Authorization": "Token t"}


class TestPersistence:
    def test_save_and_load_roundtrip(self, session_file, valid_session):
        save(valid_session)
        loaded = load()
        assert loaded is not None
        assert loaded.access_token == valid_session.access_token
        assert loaded.email == valid_session.email
        assert loaded.provider == valid_session.provider

    def test_save_creates_file(self, session_file, valid_session):
        save(valid_session)
        assert session_file.exists()

    def test_save_restricts_permissions(self, session_file, valid_session):
        save(valid_session)
        mode = oct(session_file.stat().st_mode)[-3:]
        assert mode == "600"

    def test_load_returns_none_when_missing(self, session_file):
        assert load() is None

    def test_load_returns_none_on_corrupt_json(self, session_file):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("not json {{")
        assert load() is None

    def test_load_returns_none_on_missing_fields(self, session_file):
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(json.dumps({"access_token": "x"}))
        assert load() is None

    def test_clear_removes_file(self, session_file, valid_session):
        save(valid_session)
        assert session_file.exists()
        clear()
        assert not session_file.exists()

    def test_clear_is_idempotent(self, session_file):
        clear()  # file doesn't exist — should not raise
