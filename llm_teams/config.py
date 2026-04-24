"""Load llm-teams configuration from ~/.config/llm-teams/config.yaml or env vars."""
import os
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG_DIR = Path.home() / ".config" / "llm-teams"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"

_DEFAULTS: dict[str, Any] = {
    # Auth
    "provider": "microsoft",    # default: Microsoft Teams SSO
    "client_id": None,

    # Teams destination for agent ↔ human messages
    "teams_team_id": None,      # which Team to use
    "teams_channel_id": None,   # which channel to use (within that Team)
    "teams_chat_id": None,      # alternative: 1:1 or group chat ID

    # Polling
    "poll_interval": 5,         # seconds between Graph API polls
    "poll_timeout": 300,        # seconds before ask-human gives up

    # Custom OIDC
    "custom_auth_url": None,
    "custom_token_url": None,
    "custom_userinfo_url": None,
    "custom_name": "Custom SSO",
    "custom_scopes": ["openid", "email", "profile"],
    "custom_pkce": True,

    "login_timeout": 120,
}


def load() -> dict[str, Any]:
    cfg: dict[str, Any] = dict(_DEFAULTS)

    if _CONFIG_FILE.exists():
        with _CONFIG_FILE.open() as fh:
            file_cfg = yaml.safe_load(fh) or {}
        cfg.update(file_cfg)

    # Env vars override config file
    _env_map = {
        "LLM_TEAMS_PROVIDER": "provider",
        "LLM_TEAMS_CLIENT_ID": "client_id",
        "LLM_TEAMS_TEAM_ID": "teams_team_id",
        "LLM_TEAMS_CHANNEL_ID": "teams_channel_id",
        "LLM_TEAMS_CHAT_ID": "teams_chat_id",
        "LLM_TEAMS_POLL_INTERVAL": "poll_interval",
        "LLM_TEAMS_POLL_TIMEOUT": "poll_timeout",
        "LLM_TEAMS_CUSTOM_AUTH_URL": "custom_auth_url",
        "LLM_TEAMS_CUSTOM_TOKEN_URL": "custom_token_url",
        "LLM_TEAMS_CUSTOM_USERINFO_URL": "custom_userinfo_url",
    }
    for env_key, cfg_key in _env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    return cfg


def get(key: str, fallback: Optional[Any] = None) -> Any:
    return load().get(key, fallback)


def config_path() -> Path:
    return _CONFIG_FILE
