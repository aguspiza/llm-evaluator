"""Tests for llm_teams.auth.providers."""
import pytest
from llm_teams.auth.providers import get, names, Provider


class TestGet:
    def test_microsoft_provider(self):
        p = get("microsoft")
        assert p.id == "microsoft"
        assert "login.microsoftonline.com" in p.auth_url
        assert p.pkce is True

    def test_github_provider(self):
        p = get("github")
        assert p.id == "github"
        assert "github.com" in p.auth_url
        assert p.pkce is False  # GitHub doesn't support PKCE

    def test_google_provider(self):
        p = get("google")
        assert p.id == "google"
        assert p.pkce is True

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get("nonexistent")

    def test_error_lists_available_providers(self):
        with pytest.raises(ValueError) as exc_info:
            get("bad")
        assert "microsoft" in str(exc_info.value)


class TestNames:
    def test_includes_builtin_providers(self):
        n = names()
        assert "microsoft" in n
        assert "github" in n
        assert "google" in n

    def test_includes_custom(self):
        assert "custom" in names()

    def test_returns_list(self):
        assert isinstance(names(), list)


class TestMicrosoftScopes:
    def test_has_teams_scopes(self):
        p = get("microsoft")
        scopes_str = " ".join(p.default_scopes)
        assert "ChannelMessage.Send" in scopes_str
        assert "ChannelMessage.Read.All" in scopes_str
        assert "Channel.ReadBasic.All" in scopes_str
        assert "Team.ReadBasic.All" in scopes_str

    def test_has_openid_scopes(self):
        p = get("microsoft")
        assert "openid" in p.default_scopes
        assert "email" in p.default_scopes

    def test_has_offline_access_for_refresh(self):
        assert "offline_access" in get("microsoft").default_scopes
