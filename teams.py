#!/usr/bin/env python3
"""llm-teams — CLI for teams with SSO auth and LLM agent delegation.

Usage:
    python teams.py auth login
    python teams.py auth status
    python teams.py agent run "list all open PRs and summarize them"
    python teams.py agent delegate
"""
import typer

from llm_teams.commands.auth import app as auth_app
from llm_teams.commands.agent import app as agent_app

app = typer.Typer(
    name="llm-teams",
    help=(
        "Team CLI with browser-based SSO authentication.\n\n"
        "Quick start:\n\n"
        "  1. llm-teams auth init-config   # create ~/.config/llm-teams/config.yaml\n"
        "  2. llm-teams auth login          # browser SSO → local session\n"
        "  3. llm-teams agent run <task>    # hand task to Claude agent\n"
    ),
    no_args_is_help=True,
)

app.add_typer(auth_app, name="auth")
app.add_typer(agent_app, name="agent")


@app.command()
def whoami():
    """Print the currently logged-in user."""
    from llm_teams.auth import session as sess_mod
    from rich.console import Console
    session = sess_mod.require()
    Console().print(
        f"[bold]{session.display_name}[/]  "
        f"[dim]({session.provider})[/]"
    )


if __name__ == "__main__":
    app()
