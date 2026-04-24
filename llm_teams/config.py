"""Load llm-teams configuration from ~/.config/llm-teams/config.yaml or env vars."""
import os
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG_DIR = Path.home() / ".config" / "llm-teams"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"

_DEFAULTS: dict[str, Any] = {
    "provider": "github",
    "client_id": None,
    "api_base_url": None,       # optional backend API
    "anthropic_model": "claude-sonnet-4-6",
    "login_timeout": 120,       # seconds to wait for browser callback
}


def load() -> dict[str, Any]:
    cfg: dict[str, Any] = dict(_DEFAULTS)

    if _CONFIG_FILE.exists():
        with _CONFIG_FILE.open() as fh:
            file_cfg = yaml.safe_load(fh) or {}
        cfg.update(file_cfg)

    # Environment variables override everything
    for key in (
        "LLM_TEAMS_PROVIDER",
        "LLM_TEAMS_CLIENT_ID",
        "LLM_TEAMS_API_BASE_URL",
        "LLM_TEAMS_ANTHROPIC_MODEL",
        "LLM_TEAMS_CUSTOM_AUTH_URL",
        "LLM_TEAMS_CUSTOM_TOKEN_URL",
        "LLM_TEAMS_CUSTOM_USERINFO_URL",
    ):
        val = os.environ.get(key)
        if val:
            cfg_key = key.removeprefix("LLM_TEAMS_").lower()
            cfg[cfg_key] = val

    # ANTHROPIC_API_KEY is read directly by the SDK — no need to mirror it here
    return cfg


def get(key: str, fallback: Optional[Any] = None) -> Any:
    return load().get(key, fallback)


def config_path() -> Path:
    return _CONFIG_FILE
