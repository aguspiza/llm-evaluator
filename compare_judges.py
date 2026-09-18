"""Re-judge saved results with another judge and measure agreement with the stored scores.

    uv run python compare_judges.py --label opus5 results/results-2026-09-13-*.json
    uv run python compare_judges.py --label gemma4 --judge typesafe -o results/judge-compare-gemma4.json results/...

Only the saved model responses are needed: no model servers are started.
"""
import json
import os
import statistics
import time
from datetime import datetime
from typing import List, Optional

import typer
import yaml
from rich.table import Table

from llm_evaluator.config_loader import resolve_env_vars
from llm_evaluator.evaluator import Evaluator
from llm_evaluator.reporter import console

app = typer.Typer(add_completion=False)


def _judged_ok(r: dict) -> bool:
    ev = r.get("evaluation") or {}
    just = ev.get("justification", "")
    return (
        not r.get("benchmark")
        and not r.get("model_response", "").startswith("ERROR:")
        and ev.get("score", -1) >= 0
        and not just.startswith(("Failed to parse", "Evaluation failed", "Test failed"))
    )


def load_judgments(paths: List[str]) -> List[dict]:
    """Flatten results files into items carrying the reference judge's score."""
    items = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("results", []):
            if not _judged_ok(r):
                continue
            items.append({
                "file": path,
                "model_name": r["model_name"],
                "test_id": r["test_id"],
                "category": r["category"],
                "test_prompt": r["test_prompt"],
                "model_response": r["model_response"],
                "evaluation_criteria": r["evaluation_criteria"],
                "ref_score": r["evaluation"]["score"],
                "ref_justification": r["evaluation"]["justification"],
            })
    return items


def ranks(xs: List[float]) -> List[float]:
    """Fractional ranks (1-based), ties get the average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx * syy) ** 0.5, 3)


def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    return pearson(ranks(xs), ranks(ys))


def summarize(rows: List[dict]) -> dict:
    ref = [r["ref_score"] for r in rows]
    jev = [r["jev_score"] for r in rows]
    diffs = [j - r for j, r in zip(jev, ref)]
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(abs(r["jev_score"] - r["ref_score"]))
    return {
        "n": len(rows),
        "mae": round(statistics.fmean(abs(d) for d in diffs), 2),
        "bias": round(statistics.fmean(diffs), 2),
        "pearson": pearson(ref, jev),
        "spearman": spearman(ref, jev),
        "within_1": round(sum(abs(d) <= 1 for d in diffs) / len(diffs), 2),
        "within_2": round(sum(abs(d) <= 2 for d in diffs) / len(diffs), 2),
        "ref_mean": round(statistics.fmean(ref), 2),
        "jev_mean": round(statistics.fmean(jev), 2),
        "jev_confidence_mean": round(statistics.fmean(r["jev_confidence"] for r in rows), 3),
        "jev_time_total": round(sum(r["jev_time"] for r in rows), 2),
        "by_category": {c: {"n": len(v), "mae": round(statistics.fmean(v), 2)} for c, v in sorted(by_cat.items())},
    }


def compare(items: List[dict], evaluator) -> tuple:
    rows = []
    for i, item in enumerate(items, 1):
        start = time.time()
        ev = evaluator.evaluate(item["test_prompt"], item["model_response"], item["evaluation_criteria"])
        row = dict(item, jev_score=ev["jev_score"], jev_confidence=ev["jev_confidence"],
                   jev_time=round(time.time() - start, 3), jev_raw=ev["judge_raw_response"])
        rows.append(row)
        print(f"  [{i}/{len(items)}] {item['test_id']:16} ref={item['ref_score']:2}  jev={ev['jev_score']:5.2f}  "
              f"conf={ev['jev_confidence']:.2f}  {row['jev_time']:.2f}s")
    return rows, summarize(rows)


def _print_report(label: str, rows: List[dict], summary: dict):
    t = Table(title=f"Jev vs {label}: agreement")
    for col in ("n", "MAE", "bias (jev-ref)", "pearson", "spearman", "within ±1", "within ±2",
                "ref mean", "jev mean", "conf mean", "jev time"):
        t.add_column(col, justify="right")
    t.add_row(str(summary["n"]), str(summary["mae"]), str(summary["bias"]), str(summary["pearson"]),
              str(summary["spearman"]), str(summary["within_1"]), str(summary["within_2"]),
              str(summary["ref_mean"]), str(summary["jev_mean"]), str(summary["jev_confidence_mean"]),
              f"{summary['jev_time_total']:.1f}s")
    console.print(t)

    t = Table(title="MAE by category")
    t.add_column("Category")
    t.add_column("n", justify="right")
    t.add_column("MAE", justify="right")
    for cat, v in summary["by_category"].items():
        t.add_row(cat, str(v["n"]), str(v["mae"]))
    console.print(t)

    t = Table(title="Largest disagreements")
    for col in ("Model", "Test", "ref", "jev", "conf", f"{label} justification"):
        t.add_column(col)
    worst = sorted(rows, key=lambda r: -abs(r["jev_score"] - r["ref_score"]))[:10]
    for r in worst:
        t.add_row(r["model_name"][:28], r["test_id"], str(r["ref_score"]), f"{r['jev_score']:.1f}",
                  f"{r['jev_confidence']:.2f}", r["ref_justification"][:110])
    console.print(t)


@app.command()
def main(
    files: List[str] = typer.Argument(..., help="Results JSON files already judged by the reference judge"),
    label: str = typer.Option(..., "--label", "-l", help="Name of the reference judge (for the report)"),
    judge: str = typer.Option("typesafe", "--judge", "-j", help="Judge preset from config.yaml to re-judge with"),
    output: str = typer.Option(None, "--output", "-o", help="Comparison JSON (default results/judge-compare-<label>.json)"),
):
    with open("config.yaml", encoding="utf-8") as f:
        judge_config = resolve_env_vars(yaml.safe_load(f))["judges"][judge]
    evaluator = Evaluator(None, judge_config)

    items = load_judgments(files)
    print(f"Re-judging {len(items)} responses from {len(files)} files with '{judge}' (reference: {label})")
    rows, summary = compare(items, evaluator)
    _print_report(label, rows, summary)

    output = output or os.path.join("results", f"judge-compare-{label}.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(timespec="seconds"), "reference": label,
                   "judge": judge, "summary": summary, "rows": rows}, f, indent=2, ensure_ascii=False)
    print(f"Saved {output}")


if __name__ == "__main__":
    app()
