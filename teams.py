#!/usr/bin/env python3
"""llm-teams — Teams CLI for agent harnesses.

The human authenticates via browser SSO (Microsoft Azure AD).
The agent harness then uses these commands to interact with humans
through Microsoft Teams channels and chats.

Quick start:
  1. python teams.py auth init-config
  2. python teams.py auth login
  3. python teams.py teams list
  4. # from the harness:
     python teams.py agent ask-human "Should I proceed?" --json
     python teams.py agent notify "Task complete" --level success --json
"""
import typer

from llm_teams.commands.auth import app as auth_app
from llm_teams.commands.agent import app as agent_app
from llm_teams.commands.teams import app as teams_app

app = typer.Typer(
    name="llm-teams",
    help=(
        "Teams CLI — SSO auth + agent ↔ human bridge via Microsoft Teams.\n\n"
        "Designed to run inside an agent harness. "
        "The harness authenticates the human once via browser SSO, "
        "then uses the agent commands to send/receive messages through Teams."
    ),
    no_args_is_help=True,
)

app.add_typer(auth_app, name="auth")
app.add_typer(agent_app, name="agent")
app.add_typer(teams_app, name="teams")


@app.command()
def whoami(
    json_output: bool = typer.Option(False, "--json"),
):
    """Print the currently authenticated user."""
    import json as json_mod
    from llm_teams.auth import session as sess_mod
    from rich.console import Console

    session = sess_mod.require()
    if json_output:
        typer.echo(json_mod.dumps({
            "email": session.email,
            "name": session.name,
            "sub": session.sub,
            "provider": session.provider,
        }))
    else:
        Console().print(
            f"[bold]{session.display_name}[/]  [dim]({session.provider})[/]"
        )


if __name__ == "__main__":
    app()
