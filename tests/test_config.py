"""Tests for llm_teams.config — configuration loading."""
import pytest
import yaml

import llm_teams.config as cfg_mod


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config file to a temp dir and clear relevant env vars."""
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(cfg_mod, "_CONFIG_FILE", cfg_path)
    monkeypatch.setattr(cfg_mod, "_CONFIG_DIR", tmp_path)

    env_vars = [
        "LLM_TEAMS_PROVIDER", "LLM_TEAMS_CLIENT_ID", "LLM_TEAMS_TEAM_ID",
        "LLM_TEAMS_CHANNEL_ID", "LLM_TEAMS_CHAT_ID", "LLM_TEAMS_POLL_INTERVAL",
        "LLM_TEAMS_POLL_TIMEOUT",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)

    return cfg_path


class TestDefaults:
    def test_default_provider(self):
        assert cfg_mod.load()["provider"] == "microsoft"

    def test_default_client_id_is_none(self):
        assert cfg_mod.load()["client_id"] is None

    def test_default_poll_interval(self):
        assert cfg_mod.load()["poll_interval"] == 5

    def test_default_poll_timeout(self):
        assert cfg_mod.load()["poll_timeout"] == 300

    def test_teams_ids_none_by_default(self):
        cfg = cfg_mod.load()
        assert cfg["teams_team_id"] is None
        assert cfg["teams_channel_id"] is None
        assert cfg["teams_chat_id"] is None


class TestFileOverrides:
    def test_file_overrides_provider(self, isolated_config):
        isolated_config.write_text(yaml.dump({"provider": "github"}))
        assert cfg_mod.load()["provider"] == "github"

    def test_file_overrides_client_id(self, isolated_config):
        isolated_config.write_text(yaml.dump({"client_id": "my-client"}))
        assert cfg_mod.load()["client_id"] == "my-client"

    def test_file_sets_team_id(self, isolated_config):
        isolated_config.write_text(yaml.dump({"teams_team_id": "team-123"}))
        assert cfg_mod.load()["teams_team_id"] == "team-123"

    def test_empty_file_uses_defaults(self, isolated_config):
        isolated_config.write_text("")
        assert cfg_mod.load()["provider"] == "microsoft"

    def test_missing_file_uses_defaults(self):
        assert cfg_mod.load()["provider"] == "microsoft"


class TestEnvVarOverrides:
    def test_env_overrides_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_TEAMS_PROVIDER", "google")
        assert cfg_mod.load()["provider"] == "google"

    def test_env_overrides_client_id(self, monkeypatch):
        monkeypatch.setenv("LLM_TEAMS_CLIENT_ID", "env-client-id")
        assert cfg_mod.load()["client_id"] == "env-client-id"

    def test_env_overrides_team_id(self, monkeypatch):
        monkeypatch.setenv("LLM_TEAMS_TEAM_ID", "env-team")
        assert cfg_mod.load()["teams_team_id"] == "env-team"

    def test_env_overrides_channel_id(self, monkeypatch):
        monkeypatch.setenv("LLM_TEAMS_CHANNEL_ID", "env-chan")
        assert cfg_mod.load()["teams_channel_id"] == "env-chan"

    def test_env_overrides_file(self, isolated_config, monkeypatch):
        isolated_config.write_text(yaml.dump({"provider": "github"}))
        monkeypatch.setenv("LLM_TEAMS_PROVIDER", "google")
        assert cfg_mod.load()["provider"] == "google"


class TestGetHelper:
    def test_get_existing_key(self):
        assert cfg_mod.get("provider") == "microsoft"

    def test_get_missing_key_returns_fallback(self):
        assert cfg_mod.get("nonexistent_key", "fallback") == "fallback"

    def test_get_missing_key_returns_none_by_default(self):
        assert cfg_mod.get("nonexistent_key") is None
