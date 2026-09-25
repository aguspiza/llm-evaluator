"""Other ways to use CLM-8B as a judge, on responses already judged by Opus 5 / Sonnet 4.6.

    uv run python clm_eval.py rank
        Best-of-N: per test, rank all saved responses with clm-serve /v1/rank and compare the
        order with the reference judge; Jev's and CLM's absolute scores (from the saved
        judge-compare files) are ranked the same way as baselines.

    uv run python clm_eval.py anchors
        Per-test calibration: two responses per test with known reference scores act as anchors
        and the saved `prompts` scores are rescaled linearly onto them; measured on the rest.

    uv run python clm_eval.py prompts
        The Opus 5 set re-judged with question wordings closer to CLM's training data
        (yes/no, and levels that describe the response instead of the grade).

Needs clm-serve at --url (see config.yaml `judges.clm`).
"""
import json
import random
import statistics
import time
from collections import defaultdict
from itertools import combinations
from typing import List, Optional

import httpx
import typer
from rich.table import Table

from compare_judges import pearson, spearman
from llm_evaluator.reporter import console

app = typer.Typer(add_completion=False)

REF_SETS = {"opus5": "results/judge-compare-opus5.json", "sonnet46": "results/judge-compare-sonnet46.json"}
CLM_ABS = {"opus5": "results/judge-compare-clm-opus5.json"}

RANK_QUESTION = "Which response completes the task best according to the evaluation criteria?"

VARIANTS = {
    "noul": {"type": "noul", "instructions":
             "Does `model_response` correctly answer `test_prompt` and meet every requirement in `evaluation_criteria`?"},
    "noul_described": {"type": "noul", "instructions":
                       "Is `model_response` a correct answer to `test_prompt` according to `evaluation_criteria`?",
                       "criteria": {"true": "The response is correct and meets the criteria.",
                                    "false": "The response is wrong, incomplete or ignores the criteria."}},
    "levels_response": {"type": "score", "instructions":
                        "How good is `model_response` as an answer to `test_prompt`, judged by `evaluation_criteria`?",
                        "criteria": ["The response is wrong, empty or ignores the task.",
                                     "The response is mostly wrong.",
                                     "The response is partly correct but misses important requirements.",
                                     "The response is correct but has notable flaws.",
                                     "The response is correct with only minor imperfections.",
                                     "The response is fully correct and meets every requirement."]},
}


def pairwise_accuracy(ref: List[float], pred: List[float]) -> Optional[float]:
    """Share of pairs with different reference scores that `pred` orders the same way (ties = 0.5)."""
    hits = [1.0 if (r1 - r2) * (p1 - p2) > 0 else 0.5 if p1 == p2 else 0.0
            for (r1, p1), (r2, p2) in combinations(zip(ref, pred), 2) if r1 != r2]
    return statistics.fmean(hits) if hits else None


def top1_hit(ref: List[float], pred: List[float]) -> bool:
    """Is the response `pred` ranks first among those the reference scores highest?"""
    return ref[max(range(len(pred)), key=pred.__getitem__)] == max(ref)


def group_by_test(rows: List[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r["test_id"]].append(r)
    return groups


def _load_rows(path: str) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def _key(r: dict) -> tuple:
    return r["file"], r["test_id"], r["model_name"]


def _clm_rank(url: str, rows: List[dict]) -> List[float]:
    first = rows[0]
    context = f"Task given to a model:\n{first['test_prompt']}\n\nEvaluation criteria:\n{first['evaluation_criteria']}"
    answers = [r["model_response"] or "(empty response)" for r in rows]
    res = httpx.post(f"{url}/v1/rank", timeout=600,
                     json={"context": context, "question": RANK_QUESTION, "answers": answers}).json()["ranked"]
    prob = {r["candidate"]: r["prob"] for r in res}
    return [prob[a] for a in answers]


@app.command()
def rank(url: str = typer.Option("http://127.0.0.1:8700", help="clm-serve base URL"),
         output: str = typer.Option("results/clm-eval-rank.json", "--output", "-o")):
    out = {}
    for ref_name, path in REF_SETS.items():
        rows = _load_rows(path)
        clm_abs = {_key(r): r["jev_score"] for r in _load_rows(CLM_ABS[ref_name])} if ref_name in CLM_ABS else {}
        per_test = []
        for test_id, group in sorted(group_by_test(rows).items()):
            ref = [r["ref_score"] for r in group]
            start = time.time()
            preds = {"clm_rank": _clm_rank(url, group), "jev_abs": [r["jev_score"] for r in group]}
            elapsed = round(time.time() - start, 2)
            if clm_abs:
                preds["clm_abs"] = [clm_abs[_key(r)] for r in group]
            per_test.append({"test_id": test_id, "n": len(group), "clm_rank_time": elapsed, "ref": ref, "preds": preds,
                             **{f"{m}_pairwise": pairwise_accuracy(ref, p) for m, p in preds.items()},
                             **{f"{m}_top1": top1_hit(ref, p) for m, p in preds.items()}})
            print(f"  [{ref_name}] {test_id:16} n={len(group)} " + "  ".join(
                f"{m}={per_test[-1][f'{m}_pairwise'] if per_test[-1][f'{m}_pairwise'] is None else round(per_test[-1][f'{m}_pairwise'], 2)}"
                for m in preds))
        out[ref_name] = per_test

    t = Table(title="Best-of-N ranking vs reference judge (pairwise acc = mean over tests with informative pairs)")
    for col in ("ref", "method", "pairwise acc", "top-1 hits", "tests"):
        t.add_column(col, justify="right")
    for ref_name, per_test in out.items():
        for m in per_test[0]["preds"]:
            accs = [p[f"{m}_pairwise"] for p in per_test if p[f"{m}_pairwise"] is not None]
            tops = sum(p[f"{m}_top1"] for p in per_test)
            t.add_row(ref_name, m, f"{statistics.fmean(accs):.2f}", f"{tops}/{len(per_test)}", str(len(accs)))
    console.print(t)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Saved {output}")


def _variant_score(answer: dict) -> float:
    if answer["type"] == "noul":
        return answer["noul"] * 10
    return answer["score"] * 10 / (len(answer["probabilities"]) - 1)


@app.command()
def prompts(url: str = typer.Option("http://127.0.0.1:8700", help="clm-serve base URL"),
            output: str = typer.Option("results/clm-eval-prompts.json", "--output", "-o")):
    rows = _load_rows(REF_SETS["opus5"])
    baseline = {_key(r): r["jev_score"] for r in _load_rows(CLM_ABS["opus5"])}
    scored = []
    for i, r in enumerate(rows, 1):
        state = {"test_prompt": r["test_prompt"], "model_response": r["model_response"],
                 "evaluation_criteria": r["evaluation_criteria"]}
        res = httpx.post(f"{url}/v1/systemone", timeout=300, json={"state": state, "questions": VARIANTS})
        res.raise_for_status()
        s = {v: round(_variant_score(a), 2) for v, a in res.json()["answers"].items()}
        scored.append({**{k: r[k] for k in ("file", "test_id", "model_name", "ref_score")},
                       "scores": {"rubric_baseline": baseline[_key(r)], "jev": r["jev_score"], **s}})
        print(f"  [{i}/{len(rows)}] {r['test_id']:16} ref={r['ref_score']:2}  " + "  ".join(f"{k}={v:5.2f}" for k, v in s.items()))

    ref = [r["ref_score"] for r in scored]
    t = Table(title="CLM question wordings vs Opus 5 (n=%d)" % len(scored))
    for col in ("variant", "MAE", "pearson", "spearman", "pairwise acc (within test)", "range"):
        t.add_column(col, justify="right")
    summary = {}
    for v in scored[0]["scores"]:
        pred = [r["scores"][v] for r in scored]
        groups = group_by_test([{"test_id": r["test_id"], "i": i} for i, r in enumerate(scored)])
        accs = [a for g in groups.values()
                if (a := pairwise_accuracy([ref[x["i"]] for x in g], [pred[x["i"]] for x in g])) is not None]
        summary[v] = {"mae": round(statistics.fmean(abs(p - q) for p, q in zip(pred, ref)), 2),
                      "pearson": pearson(ref, pred), "spearman": spearman(ref, pred),
                      "pairwise": round(statistics.fmean(accs), 3), "min": min(pred), "max": max(pred)}
        s = summary[v]
        t.add_row(v, str(s["mae"]), str(s["pearson"]), str(s["spearman"]), f"{s['pairwise']:.2f}",
                  f"{s['min']:.1f}-{s['max']:.1f}")
    console.print(t)
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": scored}, f, indent=2, ensure_ascii=False)
    print(f"Saved {output}")


def calibrate(scores: List[float], anchors: tuple) -> List[float]:
    """Map `scores` linearly so each anchor's score lands on its reference score, clipped to 0-10."""
    (s1, r1), (s2, r2) = anchors
    if s1 == s2:
        return [(r1 + r2) / 2] * len(scores)
    return [min(10.0, max(0.0, r1 + (s - s1) * (r2 - r1) / (s2 - s1))) for s in scores]


def _anchored_metrics(groups: dict, variant: str, pick) -> dict:
    """Calibrate each test on the two anchors `pick` chooses; metrics over the non-anchor responses."""
    ref, pred = [], []
    for g in groups.values():
        a, b = pick(g)
        rest = [r for i, r in enumerate(g) if i not in (a, b)]
        anchors = ((g[a]["scores"][variant], g[a]["ref_score"]), (g[b]["scores"][variant], g[b]["ref_score"]))
        ref += [r["ref_score"] for r in rest]
        pred += calibrate([r["scores"][variant] for r in rest], anchors)
    return {"n": len(ref), "mae": statistics.fmean(abs(p - q) for p, q in zip(pred, ref)),
            "pearson": pearson(ref, pred) or 0.0, "spearman": spearman(ref, pred) or 0.0}


def _extremes(g: List[dict]) -> tuple:
    order = sorted(range(len(g)), key=lambda i: g[i]["ref_score"])
    return order[0], order[-1]


def _random_pair(rng: random.Random):
    def pick(g: List[dict]) -> tuple:
        pairs = [(i, j) for i, j in combinations(range(len(g)), 2) if g[i]["ref_score"] != g[j]["ref_score"]]
        return rng.choice(pairs) if pairs else (0, 1)
    return pick


@app.command()
def anchors(scores_file: str = typer.Option("results/clm-eval-prompts.json", help="Output of `prompts`"),
            seeds: int = typer.Option(50, help="Random anchor draws to average"),
            output: str = typer.Option("results/clm-eval-anchors.json", "--output", "-o")):
    with open(scores_file, encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    groups = group_by_test(rows)
    t = Table(title="Per-test anchor calibration vs Opus 5 (metrics on non-anchor responses)")
    for col in ("variant", "anchors", "n", "MAE", "pearson", "spearman"):
        t.add_column(col, justify="right")
    summary = {}
    for v in rows[0]["scores"]:
        rng = random.Random(0)
        draws = [_anchored_metrics(groups, v, _random_pair(rng)) for _ in range(seeds)]
        summary[v] = {"extremes": _anchored_metrics(groups, v, _extremes),
                      "random": {k: statistics.fmean(d[k] for d in draws) for k in draws[0]}}
        for kind, m in summary[v].items():
            t.add_row(v, kind, f"{m['n']:.0f}", f"{m['mae']:.2f}", f"{m['pearson']:.2f}", f"{m['spearman']:.2f}")
    console.print(t)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved {output}")


if __name__ == "__main__":
    app()
