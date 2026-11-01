import json

import agent_eval
import agent_tasks
import agent_tools


# ---------------------------------------------------------------------------
# Helper / internal coverage
# ---------------------------------------------------------------------------

def test_has_number_reads_spanish_and_english_formats():
    assert agent_tasks.has_number("Pagas 1.930,50 € en total", 1930.5)
    assert agent_tasks.has_number("total: 1930.5 EUR", 1930.5)
    assert agent_tasks.has_number("Son 22 grados", 22)
    assert not agent_tasks.has_number("Son 21 grados", 22)


def test_has_number_reads_thousands_without_decimals():
    assert agent_tasks.has_number("El total es 63.550 €", 63550)
    assert agent_tasks.has_number("ha gastado €4,870 en 2026", 4870)
    assert agent_tasks.has_number("son 138,89 €", 138.89)


def test_new_world_is_independent_per_task():
    a = agent_tools.new_world()
    b = agent_tools.new_world()
    agent_tools.run_tool(a, "add_todo", {"text": "x"})
    assert b["todos"] == []


def test_run_tool_logs_every_call_with_its_result():
    w = agent_tools.new_world()
    result = agent_tools.run_tool(w, "get_weather", {"city": "sevilla"})
    assert result["temp_c"] == 22
    assert w["_calls"] == [{"name": "get_weather", "args": {"city": "sevilla"}, "result": result}]


def test_run_tool_unknown_tool_or_bad_args_returns_error_not_exception():
    w = agent_tools.new_world()
    assert "error" in agent_tools.run_tool(w, "no_such_tool", {})
    assert "error" in agent_tools.run_tool(w, "get_weather", {})


def test_injected_failure_happens_only_the_first_n_times():
    w = agent_tools.new_world()
    w["_fail"] = {"get_order": 1}
    assert "error" in agent_tools.run_tool(w, "get_order", {"order_id": "A-7015"})
    assert agent_tools.run_tool(w, "get_order", {"order_id": "A-7015"})["status"] == "enviado"


def test_book_table_full_slot_returns_alternatives_and_books_nothing():
    w = agent_tools.new_world()
    r = agent_tools.run_tool(w, "book_table", {"restaurant": "La Tagliatella", "date": "2026-11-07",
                                               "time": "21:00", "people": 2})
    assert "error" in r and r["alternatives"] == ["20:30", "22:15"]
    assert w["bookings"] == []


def test_cancel_order_on_a_locked_order_fails_and_cancels_nothing():
    w = agent_tools.new_world()
    w["orders"]["X-1"] = {"customer_id": "9", "date": "2026-10-01", "total": 1.0, "status": "pendiente"}
    w["locked_orders"] = ["X-1"]
    assert "error" in agent_tools.run_tool(w, "cancel_order", {"order_id": "X-1"})
    assert w["cancellations"] == [] and w["orders"]["X-1"]["status"] == "pendiente"


def test_list_customers_is_paginated():
    w = agent_tools.new_world()
    w["customers"] = [{"name": f"c{i}"} for i in range(7)]
    p1 = agent_tools.run_tool(w, "list_customers", {"page": 1})
    p2 = agent_tools.run_tool(w, "list_customers", {"page": 2})
    assert len(p1["customers"]) == 5 and p1["has_more"]
    assert len(p2["customers"]) == 2 and not p2["has_more"]


def test_get_availability_returns_busy_slots_of_another_person():
    w = agent_tools.new_world()
    w["availability"] = {"ana.lopez@acme.es": {"2026-11-04": [{"start": "09:00", "end": "10:00"}]}}
    r = agent_tools.run_tool(w, "get_availability", {"email": "ana.lopez@acme.es", "date": "2026-11-04"})
    assert r["busy"] == [{"start": "09:00", "end": "10:00"}]
    assert agent_tools.run_tool(w, "get_availability", {"email": "x@y.z", "date": "2026-11-04"})["busy"] == []


def test_update_todo_changes_the_text():
    w = agent_tools.new_world()
    agent_tools.run_tool(w, "add_todo", {"text": "leche"})
    agent_tools.run_tool(w, "update_todo", {"todo_id": 1, "text": "leche y pan"})
    assert w["todos"][0]["text"] == "leche y pan"


def test_every_tool_has_a_schema_and_an_implementation():
    names = {t["name"] for t in agent_tools.TOOL_SPECS}
    assert names == set(agent_tools.IMPLS)


# ---------------------------------------------------------------------------
# Logic / interface coverage
# ---------------------------------------------------------------------------

def _tool_call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def test_run_openai_task_executes_tool_calls_and_returns_final_answer():
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("get_weather", {"city": "Sevilla"})]},
        {"role": "assistant", "content": "En Sevilla hace 22 grados y está soleado."},
    ])
    seen = []

    def chat(messages, tools):
        seen.append([m["role"] for m in messages])
        return next(replies), {"completion_tokens": 10}

    task = agent_tasks.TASK_BY_ID["weather_single"]
    run = agent_eval.run_openai_task(task, chat)
    assert run["answers"] == ["En Sevilla hace 22 grados y está soleado."]
    assert [c["name"] for c in run["calls"]] == ["get_weather"]
    assert run["calls"][0]["turn"] == 0
    assert seen[1] == ["system", "user", "assistant", "tool"]
    assert run["tokens"] == 20


def test_run_openai_task_bad_json_arguments_become_a_tool_error():
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{city:"}}]},
        {"role": "assistant", "content": "No pude."},
    ])
    run = agent_eval.run_openai_task(agent_tasks.TASK_BY_ID["weather_single"],
                                     lambda m, t: (next(replies), {}))
    assert "error" in run["calls"][0]["result"]


def test_run_openai_task_stops_after_max_steps():
    loop = {"role": "assistant", "content": "", "tool_calls": [_tool_call("list_todos", {})]}
    run = agent_eval.run_openai_task(agent_tasks.TASK_BY_ID["weather_single"],
                                     lambda m, t: (loop, {}), max_steps=3)
    assert len(run["calls"]) == 3
    assert run["answers"] == [""]


def test_grade_scores_the_fraction_of_passed_checks():
    task = agent_tasks.TASK_BY_ID["no_tool_needed"]
    good = agent_eval.grade(task, {"calls": [], "answers": ["La capital de Portugal es Lisboa."],
                                   "world": agent_tools.new_world()})
    bad = agent_eval.grade(task, {"calls": [{"name": "get_weather", "args": {}, "result": {}, "turn": 0}],
                                  "answers": ["Madrid"], "world": agent_tools.new_world()})
    assert good["score"] == 10 and good["passed"]
    assert bad["score"] == 0 and not bad["passed"]


def test_assign_turns_follows_tool_use_counts_per_turn():
    calls = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert [c["turn"] for c in agent_eval.assign_turns(calls, [2, 0, 1])] == [0, 0, 2]


def test_task_extra_data_is_merged_into_a_fresh_world():
    task = {"fail": {}, "extra": {"files": {"nuevo.txt": "x"}, "customers": [{"name": "a"}]}}
    w = agent_eval._new_task_world(task)
    assert w["files"]["nuevo.txt"] == "x" and "presupuesto_2025.txt" in w["files"]
    assert w["customers"] == [{"name": "a"}]
    assert "nuevo.txt" not in agent_tools.new_world()["files"]


def test_every_task_has_checks_turns_and_a_level():
    for t in agent_tasks.TASKS:
        assert t["turns"] and t["check"] and t["category"] and t["level"] in (1, 2)
    assert len({t["id"] for t in agent_tasks.TASKS}) == len(agent_tasks.TASKS)
