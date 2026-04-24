"""Session persistence — stored as JSON in ~/.config/llm-teams/session.json (mode 0600)."""
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_CONFIG_DIR = Path.home() / ".config" / "llm-teams"
_SESSION_FILE = _CONFIG_DIR / "session.json"


@dataclass
class Session:
    access_token: str
    token_type: str
    expires_at: float               # unix timestamp
    provider: str
    email: Optional[str] = None
    name: Optional[str] = None
    sub: Optional[str] = None       # subject (user ID from the IdP)
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None
    team_id: Optional[str] = None   # set after team selection

    # ------------------------------------------------------------------ #
    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def expires_in(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def display_name(self) -> str:
        return self.name or self.email or self.sub or "unknown"

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"{self.token_type} {self.access_token}"}


# ------------------------------------------------------------------ #
# Persistence
# ------------------------------------------------------------------ #

def save(session: Session) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(json.dumps(asdict(session), indent=2))
    _SESSION_FILE.chmod(0o600)


def load() -> Optional[Session]:
    if not _SESSION_FILE.exists():
        return None
    try:
        data = json.loads(_SESSION_FILE.read_text())
        return Session(**data)
    except Exception:
        return None


def clear() -> None:
    if _SESSION_FILE.exists():
        _SESSION_FILE.unlink()


def require() -> Session:
    """Return the current session or exit with an actionable message."""
    from rich.console import Console
    session = load()
    if session is None:
        Console().print("[bold red]Not logged in.[/] Run: [cyan]llm-teams auth login[/]")
        raise SystemExit(1)
    if session.is_expired:
        Console().print("[bold red]Session expired.[/] Run: [cyan]llm-teams auth login[/]")
        raise SystemExit(1)
    return session
