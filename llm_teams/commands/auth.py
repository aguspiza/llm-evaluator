"""auth commands: login, logout, status, token, init-config."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from llm_teams import config as cfg_mod
from llm_teams.auth import flow, providers, session as sess_mod

app = typer.Typer(help="Manage authentication and sessions.")
console = Console()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _resolve_provider(provider_id: str):
    """Return a Provider; handle the 'custom' case via config/env vars."""
    cfg = cfg_mod.load()

    if provider_id == "custom":
        from llm_teams.auth.providers import Provider
        for key in ("custom_auth_url", "custom_token_url", "custom_userinfo_url"):
            if not cfg.get(key):
                console.print(
                    f"[red]Missing config key '{key}' for custom provider.[/]\n"
                    f"Set it in [cyan]{cfg_mod.config_path()}[/] or via env var "
                    f"[cyan]LLM_TEAMS_{key.upper()}[/]."
                )
                raise typer.Exit(1)
        return Provider(
            id="custom",
            name=cfg.get("custom_name", "Custom SSO"),
            auth_url=cfg["custom_auth_url"],
            token_url=cfg["custom_token_url"],
            userinfo_url=cfg["custom_userinfo_url"],
            default_scopes=cfg.get("custom_scopes", ["openid", "email", "profile"]),
            pkce=cfg.get("custom_pkce", True),
        )

    return providers.get(provider_id)


def _resolve_client_id(provider_id: str, override: Optional[str]) -> str:
    if override:
        return override
    cfg = cfg_mod.load()
    client_id = cfg.get("client_id") or os.environ.get("LLM_TEAMS_CLIENT_ID")
    if not client_id:
        console.print(
            "[red]No client_id found.[/]\n"
            f"Set [cyan]client_id[/] in [cyan]{cfg_mod.config_path()}[/] "
            "or export [cyan]LLM_TEAMS_CLIENT_ID=…[/]"
        )
        raise typer.Exit(1)
    return client_id


# ------------------------------------------------------------------ #
# Commands
# ------------------------------------------------------------------ #

@app.command()
def login(
    provider: Annotated[
        str,
        typer.Option("--provider", "-p", help=f"OAuth2 provider ({', '.join(providers.names())})"),
    ] = None,
    client_id: Annotated[Optional[str], typer.Option("--client-id", help="OAuth2 client ID")] = None,
    timeout: Annotated[int, typer.Option(help="Seconds to wait for browser callback")] = 120,
):
    """Authenticate via browser SSO and store a local session."""
    cfg = cfg_mod.load()
    provider_id = provider or cfg.get("provider", "github")

    prov = _resolve_provider(provider_id)
    cid = _resolve_client_id(provider_id, client_id)

    try:
        session = flow.login(prov, cid, timeout=timeout)
    except TimeoutError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    expires_dt = datetime.fromtimestamp(session.expires_at, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    console.print()
    console.print(
        Panel(
            f"[bold green]Logged in![/]\n\n"
            f"  User:     [cyan]{session.display_name}[/]\n"
            f"  Provider: [cyan]{session.provider}[/]\n"
            f"  Expires:  [dim]{expires_dt}[/]",
            title="[bold]llm-teams[/]",
            border_style="green",
        )
    )


@app.command()
def logout():
    """Remove the stored session."""
    sess_mod.clear()
    console.print("[bold]Logged out.[/] Session cleared.")


@app.command()
def status():
    """Show the current session status."""
    session = sess_mod.load()

    if session is None:
        console.print("[yellow]No session found.[/] Run [cyan]llm-teams auth login[/].")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("User", session.display_name)
    table.add_row("Email", session.email or "—")
    table.add_row("Provider", session.provider)
    table.add_row("Subject", session.sub or "—")
    table.add_row("Team", session.team_id or "—")

    if session.is_expired:
        expires_str = "[red]EXPIRED[/]"
    else:
        h, rem = divmod(session.expires_in, 3600)
        m = rem // 60
        expires_str = f"[green]{h}h {m}m remaining[/]"
    table.add_row("Session", expires_str)

    console.print(Panel(table, title="[bold]Session Status[/]", border_style="blue"))


@app.command()
def token():
    """Print the raw access token (for scripting / piping to an agent)."""
    session = sess_mod.require()
    # Print with no markup so it can be captured cleanly
    typer.echo(session.access_token)


@app.command("init-config")
def init_config(
    provider: Annotated[str, typer.Option(help="Default OAuth2 provider")] = "github",
    client_id: Annotated[Optional[str], typer.Option(help="OAuth2 client ID")] = None,
):
    """Create ~/.config/llm-teams/config.yaml with sensible defaults."""
    path = cfg_mod.config_path()
    if path.exists():
        overwrite = typer.confirm(f"{path} already exists. Overwrite?", default=False)
        if not overwrite:
            raise typer.Exit(0)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict = {"provider": provider}
    if client_id:
        doc["client_id"] = client_id

    with path.open("w") as fh:
        yaml.dump(doc, fh, default_flow_style=False)

    console.print(f"[green]Config written to[/] [cyan]{path}[/]")
    console.print("Edit it to add your [bold]client_id[/] and other settings.")
