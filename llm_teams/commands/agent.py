"""agent commands: run, delegate (interactive), session-info."""
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.prompt import Prompt

from llm_teams import config as cfg_mod
from llm_teams.auth import session as sess_mod

app = typer.Typer(help="Delegate tasks to an LLM agent using your current session.")
console = Console()


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="Task description for the agent")],
    model: Annotated[Optional[str], typer.Option("--model", "-m", help="Claude model ID")] = None,
    max_turns: Annotated[int, typer.Option(help="Maximum agent turns")] = 20,
):
    """Run the LLM agent with a task, using your SSO session as identity context."""
    from llm_teams.agent.runner import run as agent_run

    session = sess_mod.require()
    cfg = cfg_mod.load()
    resolved_model = model or cfg.get("anthropic_model", "claude-sonnet-4-6")

    agent_run(task, session, resolved_model, max_turns=max_turns)


@app.command()
def delegate(
    model: Annotated[Optional[str], typer.Option("--model", "-m")] = None,
    max_turns: Annotated[int, typer.Option(help="Maximum agent turns")] = 20,
):
    """Interactively describe a task and hand it off to the agent."""
    session = sess_mod.require()
    cfg = cfg_mod.load()
    resolved_model = model or cfg.get("anthropic_model", "claude-sonnet-4-6")

    console.print(f"[bold cyan]Agent delegate mode[/] — logged in as [cyan]{session.display_name}[/]")
    console.print("[dim]Describe the task you want the agent to perform. Ctrl+C to cancel.[/]\n")

    try:
        task = Prompt.ask("[bold]Task")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Cancelled.[/]")
        raise typer.Exit(0)

    if not task.strip():
        console.print("[red]No task provided.[/]")
        raise typer.Exit(1)

    from llm_teams.agent.runner import run as agent_run
    agent_run(task, session, resolved_model, max_turns=max_turns)


@app.command("session-info")
def session_info():
    """Print the current session as JSON (for piping into external agents or scripts)."""
    import json
    from dataclasses import asdict

    session = sess_mod.require()
    data = asdict(session)
    # Omit raw tokens from stdout unless explicitly requested
    for key in ("access_token", "refresh_token", "id_token"):
        if data.get(key):
            data[key] = "<redacted>"
    typer.echo(json.dumps(data, indent=2))
