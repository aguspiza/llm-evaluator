"""Tests for slop_eval.py (effectiveness metrics of the Jev slop score)."""
import slop_eval as se


def _row(category, ref_score, slop, text="x" * 100):
    return {"category": category, "ref_score": ref_score, "jev_slop": slop, "model_response": text}


# --- Helper / internal coverage -------------------------------------------

def test_summarize_saved_separates_bare_number_from_extra_text():
    rows = [
        _row("seguimiento_instrucciones", 10, 0.0, "8"),
        _row("seguimiento_instrucciones", 10, 0.5, "8"),
        _row("seguimiento_instrucciones", 5, 6.0, "Claro, la respuesta es 8, espero que sirva"),
        _row("generacion_codigo", 10, 1.0, "def f(): pass" * 40),
    ]
    s = se.summarize_saved(rows)
    assert s["instruction_bare_number_slop"] == 0.25 and s["instruction_bare_n"] == 2
    assert s["instruction_extra_text_slop"] == 6.0 and s["instruction_extra_n"] == 1
    assert s["by_category"]["generacion_codigo"] == {"n": 1, "mean_slop": 1.0}
    assert "pearson_slop_vs_length" in s


def test_summarize_pairs_counts_known_order():
    pairs = [
        dict(slop_tight=0.0, slop_orig=1.0, slop_padded=6.0, grade_tight=9, grade_orig=9, grade_padded=8,
             len_tight=80, len_orig=100, len_padded=200),
        dict(slop_tight=2.0, slop_orig=1.0, slop_padded=5.0, grade_tight=9, grade_orig=9, grade_padded=9,
             len_tight=90, len_orig=100, len_padded=210),
    ]
    s = se.summarize_pairs(pairs)
    assert s["n"] == 2
    assert s["padded_gt_original"] == 1.0
    assert s["original_ge_tight"] == 0.5
    assert s["full_order"] == 0.5
    assert s["mean_slop"] == {"tight": 1.0, "original": 1.0, "padded": 5.5}


# --- Logic / interface coverage -------------------------------------------

class FakeJudge:
    def evaluate(self, test_prompt, model_response, evaluation_criteria):
        return {"jev_slop": float(len(model_response) > 50) * 5, "jev_slop_confidence": 0.8, "jev_score": 9.0}


def test_slop_of_returns_only_slop_fields():
    out = se.slop_of(FakeJudge(), {"test_prompt": "p", "model_response": "x" * 60, "evaluation_criteria": "e"})
    assert out == {"jev_slop": 5.0, "jev_slop_confidence": 0.8, "jev_score": 9.0}
