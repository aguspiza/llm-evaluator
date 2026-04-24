"""teams commands — browse and message Microsoft Teams teams/channels."""
import json
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from llm_teams.auth import session as sess_mod
from llm_teams.graph import GraphClient, extract_text

app = typer.Typer(help="Browse and message Microsoft Teams teams and channels.")
console = Console()


def _graph() -> GraphClient:
    session = sess_mod.require()
    return GraphClient(session.access_token)


# ------------------------------------------------------------------ #
# teams list
# ------------------------------------------------------------------ #

@app.command("list")
def list_teams(
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """List Teams the authenticated user belongs to."""
    graph = _graph()
    teams = graph.list_joined_teams()

    if json_output:
        typer.echo(json.dumps(teams))
        return

    table = Table(title="Your Teams", show_lines=True)
    table.add_column("Name", style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Description")

    for t in teams:
        table.add_row(
            t.get("displayName", ""),
            t.get("id", ""),
            (t.get("description") or "")[:60],
        )
    console.print(table)


# ------------------------------------------------------------------ #
# teams channels
# ------------------------------------------------------------------ #

@app.command()
def channels(
    team_id: Annotated[str, typer.Argument(help="Team ID (from `teams list`)")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """List channels in a Team."""
    graph = _graph()
    chans = graph.list_channels(team_id)

    if json_output:
        typer.echo(json.dumps(chans))
        return

    table = Table(title=f"Channels in {team_id}", show_lines=True)
    table.add_column("Name", style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Type")

    for c in chans:
        table.add_row(
            c.get("displayName", ""),
            c.get("id", ""),
            c.get("membershipType", "standard"),
        )
    console.print(table)


# ------------------------------------------------------------------ #
# teams send
# ------------------------------------------------------------------ #

@app.command()
def send(
    message: Annotated[str, typer.Argument(help="Message text to send")],
    team_id: Annotated[Optional[str], typer.Option("--team-id", "-T")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id", "-C")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id", "-H")] = None,
    subject: Annotated[Optional[str], typer.Option("--subject", "-s")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Send a message to a Teams channel or chat.

    Either --team-id + --channel-id (channel message) or --chat-id (chat message).
    Falls back to config if not specified.
    """
    from llm_teams import config as cfg_mod
    cfg = cfg_mod.load()

    t_id = team_id or cfg.get("teams_team_id")
    c_id = channel_id or cfg.get("teams_channel_id")
    ch_id = chat_id or cfg.get("teams_chat_id")

    if not ch_id and not (t_id and c_id):
        console.print(
            "[red]Specify --team-id + --channel-id or --chat-id.[/]"
        )
        raise typer.Exit(1)

    graph = _graph()
    if ch_id:
        sent = graph.send_chat_message(ch_id, message)
    else:
        sent = graph.send_channel_message(t_id, c_id, message, subject=subject)

    if json_output:
        typer.echo(json.dumps({"ok": True, "message_id": sent.get("id")}))
    else:
        console.print(f"[green]Message sent[/] (id: {sent.get('id')})")


# ------------------------------------------------------------------ #
# teams messages  (read recent messages)
# ------------------------------------------------------------------ #

@app.command()
def messages(
    team_id: Annotated[Optional[str], typer.Option("--team-id", "-T")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id", "-C")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id", "-H")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Read recent messages from a Teams channel or chat."""
    from llm_teams import config as cfg_mod
    cfg = cfg_mod.load()

    t_id = team_id or cfg.get("teams_team_id")
    c_id = channel_id or cfg.get("teams_channel_id")
    ch_id = chat_id or cfg.get("teams_chat_id")

    graph = _graph()

    if ch_id:
        msgs = graph.list_chat_messages(ch_id)
    elif t_id and c_id:
        msgs = graph.list_channel_messages(t_id, c_id)
    else:
        console.print("[red]Specify --team-id + --channel-id or --chat-id.[/]")
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps(msgs))
        return

    table = Table(title="Recent messages", show_lines=True)
    table.add_column("From", style="bold", width=20)
    table.add_column("Time", width=20)
    table.add_column("Text")

    for m in reversed(msgs):
        sender = m.get("from", {}).get("user", {}).get("displayName", "—")
        ts = m.get("createdDateTime", "")[:16].replace("T", " ")
        text = extract_text(m)[:120]
        table.add_row(sender, ts, text)

    console.print(table)
