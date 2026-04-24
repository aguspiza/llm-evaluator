"""teams commands — browse, message, and watch Microsoft Teams teams/channels."""
import json
import sys
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from llm_teams import config as cfg_mod
from llm_teams.auth import session as sess_mod
from llm_teams.graph import GraphClient, delta_event_stream, extract_text
from llm_teams.teams_link import parse as parse_link

app = typer.Typer(help="Browse and message Microsoft Teams teams and channels.")
console = Console()
err = Console(stderr=True)


def _graph() -> GraphClient:
    session = sess_mod.require()
    return GraphClient(session.access_token)


# ------------------------------------------------------------------ #
# teams use-link  — parse a Teams URL and save IDs to config
# ------------------------------------------------------------------ #

@app.command("use-link")
def use_link(
    url: Annotated[str, typer.Argument(help="Teams channel/chat link (from 'Get link to channel')")],
):
    """Parse a Teams deep link and save the IDs to ~/.config/llm-teams/config.yaml.

    In Teams: right-click a channel → Get link to channel → paste here.

    \b
    Example:
        python teams.py teams use-link "https://teams.microsoft.com/l/channel/19%3Axxx.../..."
    """
    import yaml

    try:
        link = parse_link(url)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    cfg_path = cfg_mod.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if cfg_path.exists():
        with cfg_path.open() as fh:
            existing = yaml.safe_load(fh) or {}

    if link.team_id:
        existing["teams_team_id"] = link.team_id
    if link.channel_id:
        existing["teams_channel_id"] = link.channel_id
    if link.chat_id:
        existing["teams_chat_id"] = link.chat_id

    # Remove chat_id if we now have a channel destination (and vice-versa)
    if link.is_channel and "teams_chat_id" in existing:
        existing.pop("teams_chat_id")
    if link.is_chat and "teams_team_id" in existing:
        existing.pop("teams_team_id")
        existing.pop("teams_channel_id", None)

    with cfg_path.open("w") as fh:
        yaml.dump(existing, fh, default_flow_style=False)

    console.print(f"[green]Config updated:[/] [cyan]{cfg_path}[/]")
    if link.is_channel:
        console.print(f"  teams_team_id:    [cyan]{link.team_id}[/]")
        console.print(f"  teams_channel_id: [cyan]{link.channel_id}[/]")
    elif link.is_chat:
        console.print(f"  teams_chat_id: [cyan]{link.chat_id}[/]")


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


# ------------------------------------------------------------------ #
# teams watch  — live streaming view (delta polling)
# ------------------------------------------------------------------ #

@app.command()
def watch(
    team_id: Annotated[Optional[str], typer.Option("--team-id", "-T")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id", "-C")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id", "-H")] = None,
    poll_interval: Annotated[int, typer.Option("--poll-interval", "-i",
        help="Seconds between delta polls")] = None,
    json_output: Annotated[bool, typer.Option("--json",
        help="Emit newline-delimited JSON events to stdout (for piping)")] = False,
    skip_self: Annotated[bool, typer.Option("--skip-self / --no-skip-self",
        help="Skip messages sent by the authenticated user")] = True,
    push: Annotated[bool, typer.Option("--push",
        help="Use Graph change notifications instead of polling (requires ngrok or --notification-url)")] = False,
    notification_url: Annotated[Optional[str], typer.Option("--notification-url",
        help="Public HTTPS URL for Graph to POST notifications to (--push mode)")] = None,
):
    """Watch a Teams channel or chat for new messages in real time.

    Two modes:
      Default (--no-push): uses delta queries — polls Graph efficiently every
      poll_interval seconds, emitting only new messages.

      Push (--push): registers a Graph change notification subscription.
      Graph POSTs events to a local HTTPS server exposed via ngrok (or a URL
      you supply with --notification-url). Near-real-time with no polling.

    With --json, each new message is printed as a single JSON line on stdout
    so the harness can consume it via a pipe. Without --json, messages are
    printed as a live rich table to the terminal.

    Press Ctrl+C to stop.
    """
    cfg = cfg_mod.load()
    t_id = team_id or cfg.get("teams_team_id")
    c_id = channel_id or cfg.get("teams_channel_id")
    ch_id = chat_id or cfg.get("teams_chat_id")

    if not ch_id and not (t_id and c_id):
        err.print("[red]Specify --team-id + --channel-id or --chat-id.[/]")
        raise typer.Exit(1)

    interval = poll_interval or int(cfg.get("poll_interval", 5))

    graph = _graph()
    skip_id: Optional[str] = None
    if skip_self:
        try:
            skip_id = graph.me().get("id")
        except Exception:
            pass

    dest = f"chat {ch_id}" if ch_id else f"channel {c_id}"

    if push:
        _watch_push(graph, t_id, c_id, ch_id, notification_url, json_output, skip_id, dest)
    else:
        _watch_delta(graph, t_id, c_id, ch_id, interval, json_output, skip_id, dest)


def _watch_delta(graph, t_id, c_id, ch_id, interval, json_output, skip_id, dest):
    err.print(f"[cyan]Watching[/] {dest}  [dim](delta, every {interval}s — Ctrl+C to stop)[/]")

    try:
        for msg in delta_event_stream(
            graph,
            team_id=t_id, channel_id=c_id, chat_id=ch_id,
            poll_interval=interval, skip_user_id=skip_id,
        ):
            _emit(msg, json_output)
    except KeyboardInterrupt:
        err.print("\n[yellow]Stopped.[/]")


def _watch_push(graph, t_id, c_id, ch_id, notification_url, json_output, skip_id, dest):
    from llm_teams.webhook import start_notification_server
    import signal, time as _time

    err.print(f"[cyan]Starting push listener for[/] {dest}…")

    server, port, event_queue, pub_url = start_notification_server(notification_url)
    err.print(f"[green]Notification URL:[/] {pub_url}")

    # Register Graph subscription
    resource = (
        f"/chats/{ch_id}/messages" if ch_id
        else f"/teams/{t_id}/channels/{c_id}/messages"
    )
    sub = graph.create_subscription(resource, pub_url)
    sub_id = sub["id"]
    err.print(f"[green]Subscription registered[/] (id: {sub_id}, expires in 60 min)")

    err.print(f"[dim]Listening for push events — Ctrl+C to stop[/]")

    try:
        while True:
            try:
                notification = event_queue.get(timeout=1)
            except Exception:
                continue

            # notification contains resourceData with the message ID;
            # fetch the full message for consistency with delta mode
            resource_data = notification.get("resourceData", {})
            msg_id = resource_data.get("id")
            if msg_id and not json_output:
                # Rich display: fetch full message for text
                try:
                    if ch_id:
                        msg = graph._get(f"/chats/{ch_id}/messages/{msg_id}")
                    else:
                        msg = graph._get(f"/teams/{t_id}/channels/{c_id}/messages/{msg_id}")
                    if skip_id and msg.get("from", {}).get("user", {}).get("id") == skip_id:
                        continue
                    _emit(msg, json_output)
                except Exception:
                    pass
            else:
                # JSON mode: emit raw notification
                if json_output:
                    _emit_json(notification)

    except KeyboardInterrupt:
        err.print("\n[yellow]Stopping — deleting subscription…[/]")
        try:
            graph.delete_subscription(sub_id)
        except Exception:
            pass
        server.shutdown()
        err.print("[yellow]Done.[/]")


def _emit(msg: dict, json_output: bool) -> None:
    if json_output:
        _emit_json(msg)
    else:
        sender = msg.get("from", {}).get("user", {}).get("displayName", "?")
        ts = msg.get("createdDateTime", "")[:16].replace("T", " ")
        text = extract_text(msg)
        console.print(
            f"[dim]{ts}[/]  [bold cyan]{sender}[/]  {text}"
        )


def _emit_json(data: dict) -> None:
    """Print one JSON line to stdout (for harness pipe consumption)."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()
