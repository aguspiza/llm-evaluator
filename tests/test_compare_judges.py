"""Tests for compare_judges.py (re-judging saved results with another judge)."""
import json

import compare_judges as cj


def _result(test_id, score, response="ok", justification="fine", category="c", **extra):
    r = {
        "model_name": "m", "test_id": test_id, "category": category,
        "test_prompt": "p", "evaluation_criteria": "e", "model_response": response,
        "evaluation": {"score": score, "justification": justification, "judge_raw_response": ""},
    }
    r.update(extra)
    return r


# --- Helper / internal coverage -------------------------------------------

def test_pearson_and_spearman_on_perfect_and_inverse_series():
    assert cj.pearson([1, 2, 3], [2, 4, 6]) == 1.0
    assert cj.spearman([1, 2, 3], [3, 2, 1]) == -1.0


def test_spearman_averages_tied_ranks():
    assert cj.ranks([10, 10, 5]) == [2.5, 2.5, 1.0]


def test_correlation_is_none_when_a_series_is_constant():
    assert cj.pearson([1, 1, 1], [1, 2, 3]) is None


def test_load_judgments_skips_benchmarks_errors_and_failed_judgings(tmp_path):
    data = {"results": [
        _result("a", 8),
        _result("bench", -1, benchmark=True),
        _result("err", 0, response="ERROR: timeout"),
        _result("unparsed", 0, justification="Failed to parse judge response: ```"),
        _result("evfail", 0, justification="Evaluation failed: boom"),
    ]}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    items = cj.load_judgments([str(path)])

    assert [i["test_id"] for i in items] == ["a"]
    assert items[0]["ref_score"] == 8
    assert items[0]["file"] == str(path)


def test_summarize_reports_agreement_metrics():
    rows = [
        {"category": "x", "ref_score": 10, "jev_score": 9.0, "jev_confidence": 0.8, "jev_time": 0.2},
        {"category": "x", "ref_score": 0, "jev_score": 2.0, "jev_confidence": 0.6, "jev_time": 0.2},
        {"category": "y", "ref_score": 5, "jev_score": 9.0, "jev_confidence": 0.4, "jev_time": 0.2},
    ]
    s = cj.summarize(rows)
    assert s["n"] == 3
    assert s["mae"] == round((1 + 2 + 4) / 3, 2)
    assert s["bias"] == round((-1 + 2 + 4) / 3, 2)
    assert s["within_1"] == round(1 / 3, 2)
    assert s["within_2"] == round(2 / 3, 2)
    assert s["by_category"]["x"] == {"n": 2, "mae": 1.5}
    assert s["by_category"]["y"] == {"n": 1, "mae": 4.0}


# --- Logic / interface coverage -------------------------------------------

class FakeEvaluator:
    def __init__(self, scores):
        self.scores = iter(scores)

    def evaluate(self, test_prompt, model_response, evaluation_criteria):
        s = next(self.scores)
        return {"score": round(s), "justification": "", "judge_raw_response": "", "jev_score": s, "jev_confidence": 0.5}


def test_compare_rejudges_every_item_and_returns_rows_and_summary():
    items = [dict(_result("a", 8), ref_score=8, ref_justification="fine", file="f"),
             dict(_result("b", 2), ref_score=2, ref_justification="bad", file="f")]

    rows, summary = cj.compare(items, FakeEvaluator([7.0, 2.0]))

    assert [(r["ref_score"], r["jev_score"]) for r in rows] == [(8, 7.0), (2, 2.0)]
    assert all("jev_time" in r for r in rows)
    assert summary["n"] == 2 and summary["mae"] == 0.5
