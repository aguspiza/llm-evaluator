"""Tests for the `clm` (Contrastive-LM CLM-8B via clm-serve) judge provider."""
import httpx

from llm_evaluator.evaluator import Evaluator, JEV_LEVELS, JEV_SLOP_LEVELS


def _score(score, confidence=0.5, probabilities=None):
    return {"type": "score", "score": score, "confidence": confidence, "probabilities": probabilities or {}}


class FakePost:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, json, timeout):
        self.calls.append((url, json))
        return httpx.Response(200, json={"model": json["model"], "answers": self.answers},
                              request=httpx.Request("POST", url))


# --- Logic / interface coverage -------------------------------------------

def test_evaluate_posts_systemone_wire_format_and_returns_evaluation_dict(monkeypatch):
    fake = FakePost({"grade": _score(5.0, 0.9, {"5": 1.0}), "slop": _score(0.0, 0.8)})
    monkeypatch.setattr(httpx, "post", fake)
    evaluator = Evaluator(None, {"provider": "clm", "base_url": "http://clm:8700", "model": "clm-latest"})

    result = evaluator.evaluate("What is 2+2?", "4", "Must answer 4.")

    url, body = fake.calls[0]
    assert url == "http://clm:8700/v1/systemone"
    assert body["model"] == "clm-latest"
    assert body["state"] == {"test_prompt": "What is 2+2?", "model_response": "4",
                             "evaluation_criteria": "Must answer 4."}
    assert body["questions"]["grade"]["type"] == "score"
    assert body["questions"]["grade"]["criteria"] == JEV_LEVELS
    assert body["questions"]["slop"]["criteria"] == JEV_SLOP_LEVELS
    assert result["score"] == 10 and result["jev_score"] == 10.0 and result["jev_slop"] == 0.0
    assert result["justification"].startswith("CLM score")
