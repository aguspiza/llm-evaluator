"""Agent eval: tool-use tasks (agent_tasks.py; level 1 = 22 basic, level 2 = 12 harder) on simulated tools (agent_tools.py).

Two runners see the same tasks, system prompt and tools:
- an OpenAI-compatible server (llama-server --jinja) through native tool_calls;
- Claude through the `claude` CLI (Gus's subscription), with the tools served by
  agent_mcp_server.py and Claude Code's built-in tools turned off.
Scoring is automatic: each task's checks look at the calls made, the final state
and the key facts of the answer. Score = 10 x passed checks / checks.

  uv run python agent_eval.py --model lfm3 --base-url http://127.0.0.1:8097 -o results/agent-lfm3.json
  uv run python agent_eval.py --model claude-haiku-5-5 -o results/agent-haiku.json
"""

import argparse
import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import time

import httpx

import agent_tasks
import agent_tools

HERE = pathlib.Path(__file__).resolve().parent
OPENAI_TOOLS = [{"type": "function", "function": spec} for spec in agent_tools.TOOL_SPECS]


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw, None
    try:
        parsed = json.loads(raw or "{}")
        return (parsed, None) if isinstance(parsed, dict) else ({}, "los argumentos no son un objeto JSON")
    except json.JSONDecodeError as e:
        return {}, f"JSON de argumentos no válido: {e}"


def _new_task_world(task):
    """Fresh world plus the task's extra data: dicts are merged, everything else replaced."""
    world = agent_tools.new_world()
    world["_fail"] = dict(task["fail"])
    for key, value in copy.deepcopy(task.get("extra", {})).items():
        world[key] = {**world[key], **value} if isinstance(value, dict) else value
    return world


def run_openai_task(task, chat, max_steps=10):
    """Play the task's turns through chat(messages, tools) -> (message, usage)."""
    world = _new_task_world(task)
    messages = [{"role": "system", "content": agent_tasks.SYSTEM}]
    answers, tokens = [], 0
    for turn, user_text in enumerate(task["turns"]):
        messages.append({"role": "user", "content": user_text})
        answer = ""
        for _ in range(max_steps):
            msg, usage = chat(messages, OPENAI_TOOLS)
            tokens += usage.get("completion_tokens", 0)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                answer = msg.get("content") or ""
                messages.append({"role": "assistant", "content": answer})
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                args, err = _parse_args(tc["function"].get("arguments"))
                if err:
                    result = {"error": err}
                    world["_calls"].append({"name": tc["function"]["name"], "args": {}, "result": result})
                else:
                    result = agent_tools.run_tool(world, tc["function"]["name"], args)
                world["_calls"][-1]["turn"] = turn
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": json.dumps(result, ensure_ascii=False)})
        answers.append(answer)
    return {"calls": world["_calls"], "answers": answers, "world": world, "tokens": tokens}


def make_openai_chat(base_url, temperature):
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=1800)

    def chat(messages, tools):
        r = client.post("/v1/chat/completions",
                        json={"messages": messages, "tools": tools, "temperature": temperature})
        r.raise_for_status()
        body = r.json()
        return body["choices"][0]["message"], body.get("usage", {})
    return chat


def assign_turns(calls, tool_uses_per_turn):
    """Tag the MCP server's call log with the turn each call came from, in order."""
    turns = [t for t, n in enumerate(tool_uses_per_turn) for _ in range(n)]
    return [{**c, "turn": turns[i] if i < len(turns) else len(tool_uses_per_turn) - 1}
            for i, c in enumerate(calls)]


def run_claude_task(task, model):
    """Play the task through the claude CLI with only our MCP tools available."""
    work = pathlib.Path(tempfile.mkdtemp(prefix="agent-eval-"))
    state = work / "state.json"
    state.write_text(json.dumps(_new_task_world(task), ensure_ascii=False), encoding="utf-8")
    mcp = work / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"agent": {
        "command": "uv",
        "args": ["run", "--no-project", "--with", "mcp<2", "python",
                 str(HERE / "agent_mcp_server.py"), str(state)]}}}), encoding="utf-8")
    cmd = ["claude", "-p", "--model", model, "--system-prompt", agent_tasks.SYSTEM, "--tools", "",
           "--setting-sources", "", "--mcp-config", str(mcp), "--strict-mcp-config",
           "--allowedTools", "mcp__agent", "--no-session-persistence",
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", cwd=work)
    answers, uses, tokens = [], [], 0
    # One turn at a time: a message queued while Claude is still working gets
    # merged into the running turn, so the next one is sent only after `result`.
    for text in task["turns"]:
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}},
                                    ensure_ascii=False) + "\n")
        proc.stdin.flush()
        n = 0
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "assistant":
                n += sum(1 for c in e["message"]["content"] if c.get("type") == "tool_use")
            elif e.get("type") == "result":
                answers.append(e.get("result") or "")
                uses.append(n)
                tokens += e.get("usage", {}).get("output_tokens", 0)
                break
    proc.stdin.close()
    proc.wait(timeout=120)
    if len(answers) != len(task["turns"]):
        print(f"    ! claude devolvió {len(answers)} turnos de {len(task['turns'])}")
    world = json.loads(state.read_text(encoding="utf-8"))
    return {"calls": assign_turns(world["_calls"], uses), "answers": answers, "world": world, "tokens": tokens}


def grade(task, run):
    checks = task["check"](run)
    ok = sum(1 for _, passed in checks if passed)
    return {"checks": [{"criterion": c, "ok": bool(p)} for c, p in checks],
            "score": round(10 * ok / len(checks), 2), "passed": ok == len(checks)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, help="nombre para el informe; si empieza por 'claude' usa el CLI")
    p.add_argument("--base-url", help="servidor OpenAI-compatible (llama-server)")
    p.add_argument("--temperature", type=float, default=0.1)
    p.add_argument("--task", help="solo esta tarea")
    p.add_argument("--level", type=int, choices=[1, 2], help="solo las tareas de este nivel")
    p.add_argument("-o", "--output", required=True)
    a = p.parse_args()

    use_claude = a.model.startswith("claude")
    if not use_claude and not a.base_url:
        sys.exit("--base-url es obligatorio para modelos que no son claude")
    chat = None if use_claude else make_openai_chat(a.base_url, a.temperature)
    tasks = [agent_tasks.TASK_BY_ID[a.task]] if a.task else         [t for t in agent_tasks.TASKS if a.level is None or t["level"] == a.level]

    results = []
    for i, task in enumerate(tasks, 1):
        t0 = time.time()
        run = run_claude_task(task, a.model) if use_claude else run_openai_task(task, chat)
        g = grade(task, run)
        elapsed = round(time.time() - t0, 1)
        fails = "; ".join(c["criterion"] for c in g["checks"] if not c["ok"])
        print(f"[{i}/{len(tasks)}] {task['id']:18} {g['score']:5.1f}  {elapsed:6.1f}s  "
              f"{len(run['calls'])} llamadas  {fails and 'FALLA: ' + fails}", flush=True)
        world = {k: v for k, v in run["world"].items() if k in
                 ("emails_sent", "bookings", "events", "tickets", "refunds", "approvals", "cancellations", "todos")}
        results.append({"task": task["id"], "category": task["category"], **g, "time_s": elapsed,
                        "tokens": run["tokens"], "answers": run["answers"], "calls": run["calls"],
                        "final_state": world})
        pathlib.Path(a.output).write_text(json.dumps({"model": a.model, "results": results},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")

    mean = sum(r["score"] for r in results) / len(results)
    full = sum(r["passed"] for r in results)
    print(f"\n{a.model}: media {mean:.2f}/10, tareas perfectas {full}/{len(results)}, "
          f"tiempo total {sum(r['time_s'] for r in results) / 60:.1f} min")


if __name__ == "__main__":
    main()
