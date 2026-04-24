"""LLM agent runner — Claude with tool use, authenticated via the current SSO session."""
import json
from typing import Any, Optional

import anthropic
import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from llm_teams.auth.session import Session

console = Console()

# ------------------------------------------------------------------ #
# Tool definitions (Claude tool_use format)
# ------------------------------------------------------------------ #

TOOLS: list[dict] = [
    {
        "name": "get_session_info",
        "description": (
            "Return information about the currently authenticated user and their session. "
            "Use this first to understand who you are acting on behalf of."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "http_request",
        "description": (
            "Make an authenticated HTTP request to an API endpoint using the user's access token. "
            "Returns the response status code and body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method",
                },
                "url": {"type": "string", "description": "Full URL to request"},
                "body": {
                    "type": "object",
                    "description": "JSON body for POST/PUT/PATCH requests",
                },
                "headers": {
                    "type": "object",
                    "description": "Additional HTTP headers (the Authorization header is added automatically)",
                },
            },
            "required": ["method", "url"],
        },
    },
    {
        "name": "ask_human",
        "description": (
            "Ask the human a question when you need clarification or approval before proceeding. "
            "Use sparingly — only when genuinely blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the human"}
            },
            "required": ["question"],
        },
    },
    {
        "name": "task_done",
        "description": "Signal that you have completed the task. Include a short summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What was accomplished"}
            },
            "required": ["summary"],
        },
    },
]


# ------------------------------------------------------------------ #
# Tool execution
# ------------------------------------------------------------------ #

def _execute_tool(name: str, tool_input: dict, session: Session) -> str:
    if name == "get_session_info":
        return json.dumps(
            {
                "email": session.email,
                "name": session.name,
                "sub": session.sub,
                "provider": session.provider,
                "team_id": session.team_id,
                "expires_in_seconds": session.expires_in,
            }
        )

    if name == "http_request":
        method: str = tool_input["method"]
        url: str = tool_input["url"]
        body: Optional[dict] = tool_input.get("body")
        extra_headers: dict = tool_input.get("headers", {})

        headers = {**session.auth_header(), "Content-Type": "application/json", **extra_headers}
        try:
            resp = httpx.request(method, url, json=body, headers=headers, timeout=20)
            try:
                resp_body = resp.json()
            except Exception:
                resp_body = resp.text
            return json.dumps({"status": resp.status_code, "body": resp_body})
        except httpx.RequestError as exc:
            return json.dumps({"error": str(exc)})

    if name == "ask_human":
        question = tool_input["question"]
        console.print()
        console.print(Rule("[bold yellow]Agent needs input[/]"))
        answer = Prompt.ask(f"[bold yellow]?[/] {question}")
        return answer

    if name == "task_done":
        return "__DONE__"

    return json.dumps({"error": f"Unknown tool: {name}"})


# ------------------------------------------------------------------ #
# Agent loop
# ------------------------------------------------------------------ #

def _system_prompt(session: Session) -> str:
    return (
        f"You are an AI assistant acting on behalf of {session.display_name} "
        f"(provider: {session.provider}).\n\n"
        "You have access to tools to:\n"
        "- Inspect the authenticated user's identity (`get_session_info`)\n"
        "- Make authenticated HTTP requests to APIs (`http_request`)\n"
        "- Ask the human for clarification (`ask_human`)\n"
        "- Signal task completion (`task_done`)\n\n"
        "Work autonomously. Ask the human only when genuinely blocked. "
        "When you have finished the task, call `task_done` with a clear summary."
    )


def run(task: str, session: Session, model: str, max_turns: int = 20) -> None:
    """Run the Claude agent loop until the task is done or max_turns is reached."""
    client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

    console.print()
    console.print(
        Panel(
            f"[bold]Task:[/] {task}\n[dim]Agent: {model} | User: {session.display_name}[/]",
            title="[bold cyan]LLM Agent[/]",
            border_style="cyan",
        )
    )
    console.print()

    for turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_system_prompt(session),
            tools=TOOLS,
            messages=messages,
        )

        # Collect text blocks for display
        text_parts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if text_parts:
            for text in text_parts:
                console.print(Markdown(text))

        # Append assistant message
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn" and not tool_uses:
            break

        if not tool_uses:
            break

        # Execute tools and build tool_result blocks
        tool_results: list[dict[str, Any]] = []
        done = False

        for tu in tool_uses:
            console.print(f"  [dim]→ calling tool:[/] [cyan]{tu.name}[/]")
            raw_result = _execute_tool(tu.name, tu.input, session)

            if raw_result == "__DONE__":
                done = True
                summary = tu.input.get("summary", "Task complete.")
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": "Done."}
                )
                console.print()
                console.print(
                    Panel(
                        f"[bold green]{summary}[/]",
                        title="[bold]Task complete[/]",
                        border_style="green",
                    )
                )
            else:
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": raw_result}
                )

        messages.append({"role": "user", "content": tool_results})

        if done:
            return

    console.print("[yellow]Agent reached maximum turns without calling task_done.[/]")
