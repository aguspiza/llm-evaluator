"""Measure whether the Jev "slop" score means what we want.

    uv run python slop_eval.py saved results/results-2026-09-1*.json
        Slop over already-saved responses: separation on the instruction tests (10/10 answers
        must be ~0 slop, answers with extra text > 0), correlation with length, slop by category.

    uv run python slop_eval.py pairs results/results-2026-09-1*.json --n 30
        Known-order pairs: for good responses, Opus writes a padded twin and a tightened twin;
        report how often slop(padded) > slop(original) > slop(tight).
"""
import json
import os
import random
import statistics
import subprocess
import time
from typing import List

import typer
import yaml
from rich.table import Table

from compare_judges import load_judgments, pearson, spearman
from llm_evaluator.config_loader import resolve_env_vars
from llm_evaluator.evaluator import Evaluator
from llm_evaluator.reporter import console

app = typer.Typer(add_completion=False)

PAD_PROMPT = (
    "Rewrite the following answer so it says exactly the same things and reaches the same conclusions, "
    "but padded the way a verbose chatbot would: add an opening pleasantry, restate the question, add hedging "
    "and generic disclaimers, repeat one point in different words, add commentary about the answer itself, "
    "and a closing offer to help further. Keep the language of the original. Roughly double the length. "
    "Output only the rewritten answer.\n\n--- ANSWER ---\n"
)
TIGHT_PROMPT = (
    "Rewrite the following answer keeping every fact, step and conclusion, but remove every sentence that "
    "carries no information: pleasantries, restating the question, hedging, repetition, commentary about the "
    "answer itself. Keep the language of the original. Output only the rewritten answer.\n\n--- ANSWER ---\n"
)


def _judge() -> Evaluator:
    with open("config.yaml", encoding="utf-8") as f:
        return Evaluator(None, resolve_env_vars(yaml.safe_load(f))["judges"]["typesafe"])


def slop_of(judge: Evaluator, item: dict) -> dict:
    ev = judge.evaluate(item["test_prompt"], item["model_response"], item["evaluation_criteria"])
    return {"jev_slop": ev["jev_slop"], "jev_slop_confidence": ev["jev_slop_confidence"], "jev_score": ev["jev_score"]}


def rewrite(text: str, prompt: str, model: str = "claude-opus-5") -> str:
    r = subprocess.run(["claude", "--print", "--output-format", "text", "--model", model],
                       input=prompt + text, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def _mean(xs):
    return round(statistics.fmean(xs), 2) if xs else None


def summarize_saved(rows: List[dict]) -> dict:
    """rows: items + jev_slop. Instruction tests give free ground truth: 10/10 = bare number."""
    instr = [r for r in rows if r["category"] == "seguimiento_instrucciones"]
    bare = [r["jev_slop"] for r in instr if r["ref_score"] == 10]
    padded = [r["jev_slop"] for r in instr if r["ref_score"] < 10]
    lengths = [len(r["model_response"]) for r in rows]
    slops = [r["jev_slop"] for r in rows]
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["jev_slop"])
    return {
        "n": len(rows),
        "instruction_bare_number_slop": _mean(bare), "instruction_bare_n": len(bare),
        "instruction_extra_text_slop": _mean(padded), "instruction_extra_n": len(padded),
        "pearson_slop_vs_length": pearson(slops, lengths),
        "spearman_slop_vs_length": spearman(slops, lengths),
        "spearman_slop_vs_ref_score": spearman(slops, [r["ref_score"] for r in rows]),
        "by_category": {c: {"n": len(v), "mean_slop": _mean(v)} for c, v in sorted(by_cat.items())},
    }


def summarize_pairs(pairs: List[dict]) -> dict:
    pad_gt_orig = sum(p["slop_padded"] > p["slop_orig"] for p in pairs)
    orig_ge_tight = sum(p["slop_orig"] >= p["slop_tight"] for p in pairs)
    full_order = sum(p["slop_padded"] > p["slop_orig"] >= p["slop_tight"] for p in pairs)
    return {
        "n": len(pairs),
        "padded_gt_original": round(pad_gt_orig / len(pairs), 2),
        "original_ge_tight": round(orig_ge_tight / len(pairs), 2),
        "full_order": round(full_order / len(pairs), 2),
        "mean_slop": {"tight": _mean([p["slop_tight"] for p in pairs]),
                      "original": _mean([p["slop_orig"] for p in pairs]),
                      "padded": _mean([p["slop_padded"] for p in pairs])},
        "mean_grade": {"tight": _mean([p["grade_tight"] for p in pairs]),
                       "original": _mean([p["grade_orig"] for p in pairs]),
                       "padded": _mean([p["grade_padded"] for p in pairs])},
        "mean_len": {"tight": _mean([p["len_tight"] for p in pairs]),
                     "original": _mean([p["len_orig"] for p in pairs]),
                     "padded": _mean([p["len_padded"] for p in pairs])},
    }


def _print_dict(title: str, d: dict):
    t = Table(title=title)
    t.add_column("metric"); t.add_column("value")
    for k, v in d.items():
        t.add_row(k, json.dumps(v, ensure_ascii=False))
    console.print(t)


@app.command()
def saved(files: List[str] = typer.Argument(...), output: str = typer.Option("results/slop-saved.json", "-o")):
    judge = _judge()
    items = load_judgments(files)
    print(f"Scoring slop on {len(items)} saved responses")
    rows = []
    for i, it in enumerate(items, 1):
        rows.append(dict(it, **slop_of(judge, it)))
        print(f"  [{i}/{len(items)}] {it['test_id']:16} slop={rows[-1]['jev_slop']:5.2f} len={len(it['model_response'])}")
    summary = summarize_saved(rows)
    _print_dict("Slop on saved responses", summary)
    worst = sorted(rows, key=lambda r: -r["jev_slop"])[:8]
    t = Table(title="Highest slop")
    for c in ("Model", "Test", "ref", "slop", "len", "head"):
        t.add_column(c)
    for r in worst:
        t.add_row(r["model_name"][:24], r["test_id"], str(r["ref_score"]), f"{r['jev_slop']:.1f}",
                  str(len(r["model_response"])), r["model_response"][:90].replace("\n", " "))
    console.print(t)
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2, ensure_ascii=False)
    print(f"Saved {output}")


@app.command()
def pairs(files: List[str] = typer.Argument(...), n: int = typer.Option(30, "--n"),
          seed: int = typer.Option(1, "--seed"), output: str = typer.Option("results/slop-pairs.json", "-o")):
    judge = _judge()
    good = [it for it in load_judgments(files)
            if it["ref_score"] >= 9 and 200 <= len(it["model_response"]) <= 2500]
    random.Random(seed).shuffle(good)
    chosen = good[:n]
    print(f"Building {len(chosen)} padded/tight twins with Opus (from {len(good)} candidates)")
    out = []
    for i, it in enumerate(chosen, 1):
        try:
            padded = rewrite(it["model_response"], PAD_PROMPT)
            tight = rewrite(it["model_response"], TIGHT_PROMPT)
        except Exception as e:
            print(f"  [{i}] rewrite failed: {e}")
            continue
        variants = {"orig": it["model_response"], "padded": padded, "tight": tight}
        rec = {"model_name": it["model_name"], "test_id": it["test_id"], "category": it["category"]}
        for k, text in variants.items():
            s = slop_of(judge, dict(it, model_response=text))
            rec[f"slop_{k}"] = s["jev_slop"]; rec[f"grade_{k}"] = s["jev_score"]; rec[f"len_{k}"] = len(text)
            rec[f"text_{k}"] = text
        out.append(rec)
        print(f"  [{i}/{len(chosen)}] {it['test_id']:16} slop tight/orig/padded = "
              f"{rec['slop_tight']:.1f} / {rec['slop_orig']:.1f} / {rec['slop_padded']:.1f}")
    summary = summarize_pairs(out)
    _print_dict("Known-order pairs", summary)
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "pairs": out}, f, indent=2, ensure_ascii=False)
    print(f"Saved {output}")


if __name__ == "__main__":
    app()
