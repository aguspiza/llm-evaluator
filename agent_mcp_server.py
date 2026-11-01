"""MCP stdio server that serves agent_tools.py to the claude CLI for agent_eval.py.

  python agent_mcp_server.py <state.json>

The world lives in state.json: every call loads it, runs the tool, logs the call
and writes it back, so agent_eval.py can grade the final state afterwards.
"""

import asyncio
import json
import pathlib
import sys

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

import agent_tools

STATE = pathlib.Path(sys.argv[1])
server = Server("agent")


@server.list_tools()
async def list_tools():
    return [types.Tool(name=s["name"], description=s["description"], inputSchema=s["parameters"])
            for s in agent_tools.TOOL_SPECS]


@server.call_tool(validate_input=False)
async def call_tool(name, arguments):
    world = json.loads(STATE.read_text(encoding="utf-8"))
    result = agent_tools.run_tool(world, name, arguments or {})
    STATE.write_text(json.dumps(world, ensure_ascii=False), encoding="utf-8")
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
