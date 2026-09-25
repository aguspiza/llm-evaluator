"""Tests for the ranking metrics in clm_eval.py."""
from clm_eval import group_by_test, pairwise_accuracy, top1_hit


# --- Helper / internal coverage -------------------------------------------

def test_pairwise_accuracy_counts_only_pairs_with_different_reference():
    assert pairwise_accuracy([1, 5, 9], [0.1, 0.5, 0.9]) == 1.0
    assert pairwise_accuracy([1, 5, 9], [0.9, 0.5, 0.1]) == 0.0
    assert pairwise_accuracy([5, 5, 9], [0.9, 0.1, 0.5]) == 0.5     # (5,9) right, (5,9) wrong, (5,5) skipped


def test_pairwise_accuracy_gives_half_credit_to_predicted_ties():
    assert pairwise_accuracy([1, 9], [0.5, 0.5]) == 0.5


def test_pairwise_accuracy_is_none_without_informative_pairs():
    assert pairwise_accuracy([7, 7, 7], [0.1, 0.2, 0.3]) is None


def test_top1_hit_accepts_any_response_tied_for_best():
    assert top1_hit([9, 3, 9], [0.1, 0.0, 0.8]) is True
    assert top1_hit([9, 3, 9], [0.1, 0.8, 0.0]) is False


def test_group_by_test_keeps_reference_sets_apart():
    rows = [{"test_id": "a", "file": "f1"}, {"test_id": "a", "file": "f2"}, {"test_id": "b", "file": "f1"}]
    assert {k: len(v) for k, v in group_by_test(rows).items()} == {"a": 2, "b": 1}


def test_calibrate_maps_anchor_scores_onto_their_reference_scores():
    from clm_eval import calibrate
    assert calibrate([2.0, 6.0, 4.0], anchors=((2.0, 0), (6.0, 10))) == [0.0, 10.0, 5.0]


def test_calibrate_clips_to_0_10_and_handles_flat_anchors():
    from clm_eval import calibrate
    assert calibrate([1.0, 9.0], anchors=((4.0, 2), (6.0, 8))) == [0.0, 10.0]
    assert calibrate([3.0, 7.0], anchors=((5.0, 4), (5.0, 8))) == [6.0, 6.0]   # no spread: anchor mean
