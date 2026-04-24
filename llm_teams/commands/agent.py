"""Agent ↔ human bridge over Microsoft Teams chats / channels.

The harness calls these commands to:
  - Send a message to the human (notify)
  - Ask the human a question and block until they reply (ask-human)
  - Listen for ANY incoming message, yielding JSON events (listen)
  - Export the session for the harness context (session-export)

All commands:
  - Exit 0 on success
  - Exit 1 on error / timeout / cancelled
  - Support --json for machine-readable stdout
  - Block until the human responds (ask-human, confirm, listen --count 1)
"""
import json
import sys
from dataclasses import asdict
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from llm_teams import config as cfg_mod
from llm_teams.auth import session as sess_mod
from llm_teams.graph import GraphClient, extract_text, now_iso

app = typer.Typer(
    help=(
        "Agent ↔ human bridge via Microsoft Teams. "
        "The harness calls these to send/receive messages through Teams channels or chats."
    )
)
console = Console(stderr=True)   # progress/status goes to stderr; data goes to stdout


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _graph() -> GraphClient:
    session = sess_mod.require()
    return GraphClient(session.access_token)


def _destination(cfg: dict, team_id_opt: Optional[str], channel_id_opt: Optional[str],
                 chat_id_opt: Optional[str]) -> tuple:
    """Resolve (team_id, channel_id, chat_id) from opts + config."""
    team_id = team_id_opt or cfg.get("teams_team_id")
    channel_id = channel_id_opt or cfg.get("teams_channel_id")
    chat_id = chat_id_opt or cfg.get("teams_chat_id")

    if not chat_id and not (team_id and channel_id):
        console.print(
            "[red]No destination configured.[/] "
            "Set [cyan]teams_team_id + teams_channel_id[/] or [cyan]teams_chat_id[/] in "
            f"{cfg_mod.config_path()} or via env vars LLM_TEAMS_TEAM_ID / LLM_TEAMS_CHANNEL_ID / LLM_TEAMS_CHAT_ID"
        )
        raise typer.Exit(1)

    return team_id, channel_id, chat_id


def _send(graph: GraphClient, team_id: Optional[str], channel_id: Optional[str],
          chat_id: Optional[str], text: str, subject: Optional[str] = None) -> dict:
    if chat_id:
        return graph.send_chat_message(chat_id, text)
    return graph.send_channel_message(team_id, channel_id, text, subject=subject)


# ------------------------------------------------------------------ #
# notify — agent → human, no reply expected
# ------------------------------------------------------------------ #

@app.command()
def notify(
    message: Annotated[str, typer.Argument(help="Message to send to the human")],
    title: Annotated[Optional[str], typer.Option("--title", "-t")] = None,
    level: Annotated[str, typer.Option("--level", "-l", help="info|success|warning|error")] = "info",
    team_id: Annotated[Optional[str], typer.Option("--team-id")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Send a message from the agent to the human via Teams. No reply expected."""
    cfg = cfg_mod.load()
    t_id, c_id, ch_id = _destination(cfg, team_id, channel_id, chat_id)

    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    icon = icons.get(level, "ℹ️")
    header = f"**{title}**\n\n" if title else ""
    full_text = f"{icon} {header}{message}"

    graph = _graph()
    sent = _send(graph, t_id, c_id, ch_id, full_text)

    if json_output:
        typer.echo(json.dumps({
            "ok": True,
            "message_id": sent.get("id"),
            "created": sent.get("createdDateTime"),
        }))
    else:
        console.print(f"[green]Message sent[/] → Teams {'chat' if ch_id else 'channel'}")


# ------------------------------------------------------------------ #
# ask-human — agent sends question, blocks until human replies
# ------------------------------------------------------------------ #

@app.command("ask-human")
def ask_human(
    question: Annotated[str, typer.Argument(help="Question to ask the human in Teams")],
    team_id: Annotated[Optional[str], typer.Option("--team-id")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id")] = None,
    timeout: Annotated[int, typer.Option("--timeout", "-t")] = None,
    poll_interval: Annotated[int, typer.Option("--poll-interval")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Send a question to the human via Teams and wait for their reply.

    Blocks until the human responds in the channel/chat.
    Prints the reply text to stdout (or JSON with --json).
    Exit 1 on timeout or error.
    """
    cfg = cfg_mod.load()
    t_id, c_id, ch_id = _destination(cfg, team_id, channel_id, chat_id)
    _timeout = timeout or int(cfg.get("poll_timeout", 300))
    _poll = poll_interval or int(cfg.get("poll_interval", 5))

    graph = _graph()

    # Get bot's own user ID to exclude its messages from the reply poll
    try:
        me = graph.me()
        bot_user_id = me.get("id")
    except Exception:
        bot_user_id = None

    sent_at = now_iso()
    full_question = f"❓ **Agent question**\n\n{question}"
    console.print(f"[cyan]Sending question to Teams…[/]")
    sent = _send(graph, t_id, c_id, ch_id, full_question)
    message_id = sent.get("id")

    console.print(
        f"[dim]Waiting for human reply (timeout {_timeout}s, polling every {_poll}s)…[/]"
    )

    reply = graph.poll_for_reply(
        team_id=t_id,
        channel_id=c_id,
        message_id=message_id if (t_id and c_id) else None,
        chat_id=ch_id,
        after_iso=sent_at,
        poll_interval=_poll,
        timeout=_timeout,
        bot_user_id=bot_user_id,
    )

    if reply is None:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": "timeout"}))
        else:
            console.print("[red]Timeout: no reply received.[/]")
        raise typer.Exit(1)

    text = extract_text(reply)
    sender = reply.get("from", {}).get("user", {}).get("displayName", "Human")
    console.print(f"[green]Reply received from {sender}[/]")

    if json_output:
        typer.echo(json.dumps({
            "ok": True,
            "answer": text,
            "sender": sender,
            "message_id": reply.get("id"),
            "created": reply.get("createdDateTime"),
        }))
    else:
        typer.echo(text)


# ------------------------------------------------------------------ #
# confirm — yes/no question via Teams
# ------------------------------------------------------------------ #

@app.command()
def confirm(
    question: Annotated[str, typer.Argument(help="Yes/no question for the human")],
    team_id: Annotated[Optional[str], typer.Option("--team-id")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id")] = None,
    timeout: Annotated[int, typer.Option("--timeout", "-t")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Ask a yes/no question in Teams. Exit 0 = yes, 2 = no, 1 = timeout/error."""
    cfg = cfg_mod.load()
    t_id, c_id, ch_id = _destination(cfg, team_id, channel_id, chat_id)
    _timeout = timeout or int(cfg.get("poll_timeout", 300))
    _poll = int(cfg.get("poll_interval", 5))

    graph = _graph()
    try:
        me = graph.me()
        bot_user_id = me.get("id")
    except Exception:
        bot_user_id = None

    sent_at = now_iso()
    full_question = f"✋ **Agent confirmation needed**\n\n{question}\n\nReply **yes** or **no**."
    sent = _send(graph, t_id, c_id, ch_id, full_question)
    message_id = sent.get("id")

    console.print(f"[dim]Waiting for yes/no from human (timeout {_timeout}s)…[/]")

    reply = graph.poll_for_reply(
        team_id=t_id,
        channel_id=c_id,
        message_id=message_id if (t_id and c_id) else None,
        chat_id=ch_id,
        after_iso=sent_at,
        poll_interval=_poll,
        timeout=_timeout,
        bot_user_id=bot_user_id,
    )

    if reply is None:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": "timeout"}))
        raise typer.Exit(1)

    text = extract_text(reply).strip().lower()
    confirmed = text in ("yes", "y", "si", "sí", "ok", "true", "1", "sure", "confirm")

    if json_output:
        typer.echo(json.dumps({
            "ok": True,
            "confirmed": confirmed,
            "raw_answer": text,
            "sender": reply.get("from", {}).get("user", {}).get("displayName"),
        }))
    else:
        typer.echo("yes" if confirmed else "no")

    raise typer.Exit(0 if confirmed else 2)


# ------------------------------------------------------------------ #
# session-export — give harness the session context
# ------------------------------------------------------------------ #

@app.command("session-export")
def session_export(
    include_token: Annotated[bool, typer.Option("--include-token")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Export current session so the harness can inject it into the agent context.

    The access token is redacted by default; use --include-token when the harness
    needs to forward it to Graph API calls itself.
    """
    session = sess_mod.require()
    data = asdict(session)
    if not include_token:
        for key in ("access_token", "refresh_token", "id_token"):
            if data.get(key):
                data[key] = "<redacted>"

    if json_output:
        typer.echo(json.dumps(data))
    else:
        import rich
        rich.print_json(json.dumps(data))


# ------------------------------------------------------------------ #
# listen — subscribe to incoming messages, emit JSON events to stdout
# ------------------------------------------------------------------ #

@app.command()
def listen(
    team_id: Annotated[Optional[str], typer.Option("--team-id")] = None,
    channel_id: Annotated[Optional[str], typer.Option("--channel-id")] = None,
    chat_id: Annotated[Optional[str], typer.Option("--chat-id")] = None,
    poll_interval: Annotated[int, typer.Option("--poll-interval",
        help="Seconds between delta polls")] = None,
    count: Annotated[int, typer.Option("--count", "-n",
        help="Stop after receiving N messages (0 = run forever)")] = 0,
    skip_self: Annotated[bool, typer.Option("--skip-self / --no-skip-self",
        help="Ignore messages sent by the authenticated bot user")] = True,
    push: Annotated[bool, typer.Option("--push",
        help="Use Graph change notifications (requires ngrok or --notification-url)")] = False,
    notification_url: Annotated[Optional[str], typer.Option("--notification-url")] = None,
    timeout: Annotated[int, typer.Option("--timeout", "-t",
        help="Overall timeout in seconds (0 = no timeout)")] = 0,
):
    """Subscribe to a Teams channel/chat and emit one JSON line per incoming message.

    Designed for the agent harness to consume via stdout pipe:

        python teams.py agent listen --count 1 | head -1 | python harness.py

    Modes:
      Default: delta polling (no infra required, slightly delayed by poll_interval).
      --push:  Graph change notifications via ngrok (near real-time).

    Output format (one line per message):
        {"event": "message", "id": "...", "sender": "...", "text": "...",
         "created": "...", "raw": {...}}

    Exit codes:
      0 — received `count` messages and stopped cleanly
      1 — timeout before receiving any message
    """
    from llm_teams.graph import delta_event_stream, extract_text

    cfg = cfg_mod.load()
    t_id = team_id or cfg.get("teams_team_id")
    c_id = channel_id or cfg.get("teams_channel_id")
    ch_id = chat_id or cfg.get("teams_chat_id")

    if not ch_id and not (t_id and c_id):
        console.print(
            "[red]No destination.[/] Set teams_team_id + teams_channel_id or teams_chat_id "
            f"in {cfg_mod.config_path()} or via env vars."
        )
        raise typer.Exit(1)

    interval = poll_interval or int(cfg.get("poll_interval", 5))

    graph = _graph()
    skip_id: Optional[str] = None
    if skip_self:
        try:
            skip_id = graph.me().get("id")
        except Exception:
            pass

    received = 0
    import time as _time
    deadline = _time.time() + timeout if timeout > 0 else None

    console.print(
        f"[cyan]Listening[/] {'(push)' if push else f'(delta, {interval}s interval)'}  "
        f"{'count=' + str(count) if count else 'no limit'}  "
        f"{'timeout=' + str(timeout) + 's' if timeout else 'no timeout'}  "
        "[dim]— Ctrl+C to stop[/]",
        file=sys.stderr,
    )

    def _should_stop() -> bool:
        if deadline and _time.time() >= deadline:
            return True
        return False

    def _emit(msg: dict) -> None:
        text = extract_text(msg)
        sender = msg.get("from", {}).get("user", {}).get("displayName", "")
        event = {
            "event": "message",
            "id": msg.get("id"),
            "sender": sender,
            "sender_id": msg.get("from", {}).get("user", {}).get("id"),
            "text": text,
            "created": msg.get("createdDateTime"),
            "channel_id": msg.get("channelIdentity", {}).get("channelId"),
            "team_id": msg.get("channelIdentity", {}).get("teamId"),
            "chat_id": msg.get("chatId"),
            "raw": msg,
        }
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()

    try:
        if push:
            _listen_push(graph, t_id, c_id, ch_id, notification_url, skip_id,
                         count, deadline, _emit)
        else:
            for msg in delta_event_stream(
                graph,
                team_id=t_id, channel_id=c_id, chat_id=ch_id,
                poll_interval=interval, skip_user_id=skip_id,
            ):
                if _should_stop():
                    console.print("[yellow]Timeout reached.[/]", file=sys.stderr)
                    raise typer.Exit(1)

                _emit(msg)
                received += 1
                if count and received >= count:
                    raise typer.Exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]", file=sys.stderr)
        raise typer.Exit(0)


def _listen_push(graph, t_id, c_id, ch_id, notification_url, skip_id,
                 count, deadline, emit_fn):
    from llm_teams.webhook import start_notification_server
    import time as _time

    server, port, event_queue, pub_url = start_notification_server(notification_url)

    resource = (
        f"/chats/{ch_id}/messages" if ch_id
        else f"/teams/{t_id}/channels/{c_id}/messages"
    )
    sub = graph.create_subscription(resource, pub_url)
    sub_id = sub["id"]
    console.print(f"[green]Subscription:[/] {sub_id}", file=sys.stderr)

    received = 0
    try:
        while True:
            if deadline and _time.time() >= deadline:
                console.print("[yellow]Timeout.[/]", file=sys.stderr)
                raise typer.Exit(1)
            try:
                notification = event_queue.get(timeout=1)
            except Exception:
                continue

            resource_data = notification.get("resourceData", {})
            msg_id = resource_data.get("id")
            if not msg_id:
                continue
            try:
                if ch_id:
                    msg = graph.get_chat_message(ch_id, msg_id)
                else:
                    msg = graph.get_channel_message(t_id, c_id, msg_id)
            except Exception:
                continue

            if skip_id and msg.get("from", {}).get("user", {}).get("id") == skip_id:
                continue

            emit_fn(msg)
            received += 1
            if count and received >= count:
                raise typer.Exit(0)
    finally:
        try:
            graph.delete_subscription(sub_id)
        except Exception:
            pass
        server.shutdown()


# ------------------------------------------------------------------ #
# set-team — associate session with a Teams team
# ------------------------------------------------------------------ #

@app.command("set-team")
def set_team(
    team_id: Annotated[str, typer.Argument(help="Teams team ID to associate with this session")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Attach a Teams team ID to the current session (persisted to disk)."""
    session = sess_mod.require()
    session.team_id = team_id
    sess_mod.save(session)
    if json_output:
        typer.echo(json.dumps({"ok": True, "team_id": team_id}))
    else:
        console.print(f"[green]Team set:[/] [cyan]{team_id}[/]")
