"""Tests for the `typesafe` (Jev) judge provider in llm_evaluator.evaluator."""
from types import SimpleNamespace

from llm_evaluator.evaluator import Evaluator, JEV_LEVELS, grade_from_score_answer


def _answer(score, confidence=0.5, probabilities=None):
    return SimpleNamespace(score=score, confidence=confidence, probabilities=probabilities or {})


# --- Helper / internal coverage -------------------------------------------

def test_levels_are_a_valid_jev_rubric():
    assert 2 <= len(JEV_LEVELS) <= 10


def test_grade_maps_top_level_to_10_and_bottom_to_0():
    top = len(JEV_LEVELS) - 1
    assert grade_from_score_answer(_answer(top))["score"] == 10
    assert grade_from_score_answer(_answer(0))["score"] == 0


def test_grade_keeps_float_and_rounds_int_score():
    ev = grade_from_score_answer(_answer(4.3, confidence=0.61, probabilities={3: 0.2, 4: 0.3, 5: 0.5}))
    assert ev["score"] == 9            # 4.3 * 10/5 = 8.6 -> 9
    assert ev["jev_score"] == 8.6
    assert ev["jev_confidence"] == 0.61
    assert "8.60" in ev["justification"]
    assert isinstance(ev["judge_raw_response"], str)


# --- Logic / interface coverage -------------------------------------------

class FakeTypeSafe:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        return SimpleNamespace(scores={"grade": self.answer})


def test_evaluate_sends_state_and_rubric_and_returns_evaluation_dict():
    fake = FakeTypeSafe(_answer(5.0, confidence=0.9, probabilities={5: 1.0}))
    evaluator = Evaluator(fake, {"provider": "typesafe"})

    result = evaluator.evaluate("What is 2+2?", "4", "Must answer 4.")

    state, questions = fake.calls[0]
    assert state == {"test_prompt": "What is 2+2?", "model_response": "4", "evaluation_criteria": "Must answer 4."}
    assert list(questions) == ["grade"]
    assert list(questions["grade"].criteria) == JEV_LEVELS
    assert result["score"] == 10
    assert set(result) >= {"score", "justification", "judge_raw_response"}
