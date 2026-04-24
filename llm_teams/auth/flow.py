"""Full SSO login flow: open browser → catch localhost callback → exchange code → store session."""
import time
import webbrowser
from urllib.parse import urlencode
from typing import Optional

import httpx
from rich.console import Console
from rich.status import Status

from .callback import start_callback_server
from .pkce import generate_code_challenge, generate_code_verifier, generate_state
from .providers import Provider
from .session import Session, save

console = Console()


def _build_auth_url(provider: Provider, client_id: str, redirect_uri: str,
                    state: str, verifier: Optional[str]) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(provider.default_scopes),
        "state": state,
    }
    if provider.pkce and verifier:
        params["code_challenge"] = generate_code_challenge(verifier)
        params["code_challenge_method"] = "S256"
    return f"{provider.auth_url}?{urlencode(params)}"


def _exchange_code(provider: Provider, client_id: str, code: str,
                   redirect_uri: str, verifier: Optional[str]) -> dict:
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if provider.pkce and verifier:
        payload["code_verifier"] = verifier

    headers = {"Accept": "application/json"}
    response = httpx.post(provider.token_url, data=payload, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def _fetch_userinfo(provider: Provider, access_token: str) -> dict:
    try:
        response = httpx.get(
            provider.userinfo_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def login(provider: Provider, client_id: str, timeout: int = 120) -> Session:
    """Run the browser-based SSO login flow and return the resulting session."""

    verifier = generate_code_verifier() if provider.pkce else None
    state = generate_state()

    server, port, result_queue = start_callback_server()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_url = _build_auth_url(provider, client_id, redirect_uri, state, verifier)

    console.print()
    console.print(f"[bold]Logging in with {provider.name}[/]")
    console.print(f"Opening browser… if it doesn't open, visit:\n  [cyan]{auth_url}[/]")
    console.print()

    webbrowser.open(auth_url)

    with Status("[dim]Waiting for browser authentication…[/]", console=console):
        try:
            result = result_queue.get(timeout=timeout)
        except Exception:
            server.shutdown()
            raise TimeoutError(
                f"No response received within {timeout}s. Did you complete the login in the browser?"
            )

    server.shutdown()

    if "error" in result:
        raise RuntimeError(f"OAuth error: {result['error']}")

    if result.get("state") != state:
        raise RuntimeError("State mismatch — possible CSRF attack. Aborting.")

    code = result["code"]

    with Status("[dim]Exchanging authorization code for tokens…[/]", console=console):
        token_data = _exchange_code(provider, client_id, code, redirect_uri, verifier)

    with Status("[dim]Fetching user info…[/]", console=console):
        userinfo = _fetch_userinfo(provider, token_data["access_token"])

    expires_in = int(token_data.get("expires_in", 3600))
    session = Session(
        access_token=token_data["access_token"],
        token_type=token_data.get("token_type", "Bearer").capitalize(),
        expires_at=time.time() + expires_in,
        provider=provider.id,
        email=userinfo.get("email") or userinfo.get("login"),      # GitHub uses 'login'
        name=userinfo.get("name"),
        sub=str(userinfo.get("id") or userinfo.get("sub", "")),
        refresh_token=token_data.get("refresh_token"),
        id_token=token_data.get("id_token"),
    )

    save(session)
    return session
