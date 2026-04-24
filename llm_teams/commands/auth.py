"""auth commands — designed to be called by an agent harness as subprocess tools.

All commands support --json for machine-readable output.
"""
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from llm_teams import config as cfg_mod
from llm_teams.auth import flow, providers, session as sess_mod

app = typer.Typer(help="Manage SSO authentication and sessions.")
console = Console()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _out(data: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data))
    # rich output already printed by caller


def _resolve_provider(provider_id: str):
    cfg = cfg_mod.load()
    if provider_id == "custom":
        from llm_teams.auth.providers import Provider
        for key in ("custom_auth_url", "custom_token_url", "custom_userinfo_url"):
            if not cfg.get(key):
                console.print(
                    f"[red]Missing '{key}' for custom provider.[/] "
                    f"Set it in {cfg_mod.config_path()} or via LLM_TEAMS_{key.upper()}"
                )
                raise typer.Exit(1)
        return providers.Provider(
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
            "[red]No client_id found.[/] "
            f"Set client_id in {cfg_mod.config_path()} or export LLM_TEAMS_CLIENT_ID=…"
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
        typer.Option("--provider", "-p", help=f"SSO provider ({', '.join(providers.names())})"),
    ] = None,
    client_id: Annotated[Optional[str], typer.Option("--client-id")] = None,
    timeout: Annotated[int, typer.Option(help="Seconds to wait for browser callback")] = 120,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")] = False,
):
    """Open browser SSO, wait for callback, persist session.

    The harness calls this when an authenticated session is needed.
    Exits 0 on success with session info in stdout (--json) or rich display.
    Exits 1 on failure with an error message.
    """
    cfg = cfg_mod.load()
    provider_id = provider or cfg.get("provider", "github")
    prov = _resolve_provider(provider_id)
    cid = _resolve_client_id(provider_id, client_id)

    try:
        session = flow.login(prov, cid, timeout=timeout)
    except (TimeoutError, RuntimeError) as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    expires_dt = datetime.fromtimestamp(session.expires_at, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    if json_output:
        typer.echo(json.dumps({
            "ok": True,
            "email": session.email,
            "name": session.name,
            "sub": session.sub,
            "provider": session.provider,
            "expires_at": session.expires_at,
            "expires_in": session.expires_in,
        }))
    else:
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
def logout(
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Clear the stored session."""
    sess_mod.clear()
    if json_output:
        typer.echo(json.dumps({"ok": True}))
    else:
        console.print("[bold]Logged out.[/]")


@app.command()
def status(
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Return current session state.

    Exit code: 0 = valid session, 1 = no session or expired.
    """
    session = sess_mod.load()

    if session is None:
        if json_output:
            typer.echo(json.dumps({"authenticated": False, "reason": "no_session"}))
        else:
            console.print("[yellow]No session.[/] Run: [cyan]llm-teams auth login[/]")
        raise typer.Exit(1)

    if session.is_expired:
        if json_output:
            typer.echo(json.dumps({"authenticated": False, "reason": "expired"}))
        else:
            console.print("[red]Session expired.[/] Run: [cyan]llm-teams auth login[/]")
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps({
            "authenticated": True,
            "email": session.email,
            "name": session.name,
            "sub": session.sub,
            "provider": session.provider,
            "team_id": session.team_id,
            "expires_at": session.expires_at,
            "expires_in": session.expires_in,
        }))
    else:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("User", session.display_name)
        table.add_row("Email", session.email or "—")
        table.add_row("Provider", session.provider)
        table.add_row("Sub", session.sub or "—")
        table.add_row("Team", session.team_id or "—")
        h, rem = divmod(session.expires_in, 3600)
        table.add_row("Expires", f"[green]{h}h {rem // 60}m remaining[/]")
        console.print(Panel(table, title="[bold]Session[/]", border_style="blue"))


@app.command()
def token():
    """Print the raw access token to stdout (for harness injection via env or header).

    Exits 1 if not authenticated. No --json flag: the token IS the output.
    """
    session = sess_mod.require()
    typer.echo(session.access_token)


@app.command("init-config")
def init_config(
    provider: Annotated[str, typer.Option()] = "microsoft",
    client_id: Annotated[Optional[str], typer.Option(help="Azure AD app client ID")] = None,
    teams_link: Annotated[Optional[str], typer.Option(
        "--teams-link", "-l",
        help="Teams channel/chat link — parses team_id and channel_id automatically",
    )] = None,
):
    """Create ~/.config/llm-teams/config.yaml.

    \b
    Quickest setup:
        python teams.py auth init-config \\
            --client-id <azure-app-id> \\
            --teams-link "https://teams.microsoft.com/l/channel/..."
    """
    from llm_teams.teams_link import parse as parse_link

    path = cfg_mod.config_path()
    if path.exists():
        if not typer.confirm(f"{path} already exists. Overwrite?", default=False):
            raise typer.Exit(0)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict = {"provider": provider}
    if client_id:
        doc["client_id"] = client_id

    if teams_link:
        try:
            link = parse_link(teams_link)
        except ValueError as exc:
            console.print(f"[red]Bad Teams link: {exc}[/]")
            raise typer.Exit(1)
        if link.team_id:
            doc["teams_team_id"] = link.team_id
        if link.channel_id:
            doc["teams_channel_id"] = link.channel_id
        if link.chat_id:
            doc["teams_chat_id"] = link.chat_id

    with path.open("w") as fh:
        yaml.dump(doc, fh, default_flow_style=False)

    console.print(f"[green]Config written to[/] [cyan]{path}[/]")
    if teams_link and (doc.get("teams_team_id") or doc.get("teams_chat_id")):
        console.print(f"  teams_team_id:    [cyan]{doc.get('teams_team_id', '—')}[/]")
        console.print(f"  teams_channel_id: [cyan]{doc.get('teams_channel_id', '—')}[/]")
