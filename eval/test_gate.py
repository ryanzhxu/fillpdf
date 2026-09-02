"""Tests for eval/gate.py. Run with:
    .venv/bin/python eval/test_gate.py
"""
import unittest

from eval.gate import gate


def _metrics(truth=100, detected=100, matched=90, precision=0.9, recall=0.9, f1=0.9,
             label_accuracy=None, label_pairs=0):
    m = {
        "truth": truth, "detected": detected, "matched": matched,
        "precision": precision, "recall": recall, "f1": f1,
    }
    # Only set when a test asks for it -- omitting these two keys reproduces
    # a real run where label_accuracy could not be measured at all, which
    # must keep passing every other pre-existing gate test unchanged.
    if label_accuracy is not None:
        m["label_accuracy"] = label_accuracy
        m["label_pairs"] = label_pairs
    return m


def _scores(overall=None, holdout=None, per_family=None, failures=None, guards=None):
    return {
        "version": 1, "git_sha": "deadbeef", "started_at": "2026-09-01T00:00:00Z",
        "overall": overall if overall is not None else _metrics(),
        "holdout": holdout if holdout is not None else _metrics(truth=0, detected=0, matched=0,
                                                                 precision=0.0, recall=0.0, f1=0.0),
        "per_rule": {},
        "per_family": per_family if per_family is not None else {},
        "per_form": {},
        "guards": guards,
        "failures": failures if failures is not None else [],
    }


class TestGate(unittest.TestCase):
    def test_identical_passes_clean(self):
        baseline = _scores()
        candidate = _scores()
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])
        self.assertEqual(result["reasons"], [])

    def test_overall_f1_down_fails(self):
        baseline = _scores(overall=_metrics(f1=0.90))
        candidate = _scores(overall=_metrics(f1=0.85))
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("overall f1" in r for r in result["reasons"]), result["reasons"])

    def test_holdout_f1_down_fails(self):
        holdout_base = _metrics(truth=50, detected=50, matched=45, precision=0.9, recall=0.9, f1=0.90)
        holdout_cand = _metrics(truth=50, detected=50, matched=40, precision=0.8, recall=0.8, f1=0.85)
        baseline = _scores(holdout=holdout_base)
        candidate = _scores(holdout=holdout_cand)
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("holdout" in r for r in result["reasons"]), result["reasons"])

    def test_family_drop_fails_while_overall_improves(self):
        base_family = {
            "famA": _metrics(f1=0.90),
            "famB": _metrics(f1=0.90),
        }
        cand_family = {
            "famA": _metrics(f1=0.90),
            "famB": _metrics(f1=0.85),  # down 0.05, over the 0.02 limit
        }
        baseline = _scores(overall=_metrics(f1=0.90), per_family=base_family)
        candidate = _scores(overall=_metrics(f1=0.92), per_family=cand_family)
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("famB" in r for r in result["reasons"]), result["reasons"])

    def test_precision_down_recall_up_f1_up_fails(self):
        baseline = _scores(overall=_metrics(precision=0.95, recall=0.80, f1=0.868))
        candidate = _scores(overall=_metrics(precision=0.85, recall=0.95, f1=0.897))
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("precision" in r for r in result["reasons"]), result["reasons"])

    def test_new_timeout_failure_fails(self):
        baseline = _scores(failures=[])
        candidate = _scores(failures=[{"form": "weird.pdf", "reason": "timeout", "detail": "exceeded 30s"}])
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("timeout" in r for r in result["reasons"]), result["reasons"])

    def test_preexisting_failure_does_not_fail(self):
        failure = {"form": "weird.pdf", "reason": "crash", "detail": "boom"}
        baseline = _scores(failures=[failure])
        candidate = _scores(failures=[dict(failure)])
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])

    def test_box_over_ink_up_with_new_offenders_fails(self):
        base_guards = {"box_over_ink": 0.05, "box_over_ink_offenders": [{"id": "f1", "page": 1, "coverage": 0.2}],
                        "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        cand_guards = {"box_over_ink": 0.08, "box_over_ink_offenders": [
            {"id": "f1", "page": 1, "coverage": 0.2}, {"id": "f2", "page": 2, "coverage": 0.3}],
            "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        baseline = _scores(guards=base_guards)
        candidate = _scores(guards=cand_guards)
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("f2" in r for r in result["reasons"]), result["reasons"])

    def test_box_over_ink_up_no_new_offenders_warns_not_fails(self):
        base_guards = {"box_over_ink": 0.05, "box_over_ink_offenders": [{"id": "f1", "page": 1, "coverage": 0.2}],
                        "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        cand_guards = {"box_over_ink": 0.08, "box_over_ink_offenders": [{"id": "f1", "page": 1, "coverage": 0.35}],
                        "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        baseline = _scores(guards=base_guards)
        candidate = _scores(guards=cand_guards)
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])
        self.assertTrue(any("box_over_ink" in w for w in result["warnings"]), result["warnings"])

    def test_glyph_coverage_below_floor_fails(self):
        base_guards = {"box_over_ink": 0.05, "box_over_ink_offenders": [],
                        "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        cand_guards = {"box_over_ink": 0.05, "box_over_ink_offenders": [],
                        "glyph_coverage": 0.95, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        baseline = _scores(guards=base_guards)
        candidate = _scores(guards=cand_guards)
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("glyph_coverage" in r for r in result["reasons"]), result["reasons"])

    def test_empty_holdout_both_sides_warns_and_does_not_count_as_passed_check(self):
        baseline = _scores()  # default holdout truth=0
        candidate = _scores()
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])
        self.assertTrue(any("HOLDOUT CHECK DID NOT RUN" in w for w in result["warnings"]), result["warnings"])

    def test_label_accuracy_drop_fails_when_measurable_in_both(self):
        baseline = _scores(overall=_metrics(f1=0.90, label_accuracy=0.98, label_pairs=200))
        candidate = _scores(overall=_metrics(f1=0.90, label_accuracy=0.93, label_pairs=200))
        result = gate(candidate, baseline)
        self.assertFalse(result["passed"])
        self.assertTrue(any("label_accuracy" in r for r in result["reasons"]), result["reasons"])

    def test_label_accuracy_small_drop_within_tolerance_passes(self):
        baseline = _scores(overall=_metrics(f1=0.90, label_accuracy=0.98, label_pairs=200))
        candidate = _scores(overall=_metrics(f1=0.90, label_accuracy=0.97, label_pairs=200))
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])

    def test_label_accuracy_unavailable_in_candidate_skips_check_and_warns(self):
        # e.g. a run scored only against real stripped forms, which carry no
        # truth labels at all -- label_pairs is 0 and label_accuracy is absent.
        baseline = _scores(overall=_metrics(f1=0.90, label_accuracy=0.98, label_pairs=200))
        candidate = _scores(overall=_metrics(f1=0.90))
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])
        self.assertTrue(any("LABEL ACCURACY CHECK DID NOT RUN" in w for w in result["warnings"]),
                        result["warnings"])

    def test_label_accuracy_literal_unavailable_string_skips_check_and_warns(self):
        baseline = _scores(overall=_metrics(f1=0.90, label_accuracy="UNAVAILABLE", label_pairs=0))
        candidate = _scores(overall=_metrics(f1=0.90, label_accuracy=0.5, label_pairs=50))
        result = gate(candidate, baseline)
        self.assertTrue(result["passed"])
        self.assertTrue(any("LABEL ACCURACY CHECK DID NOT RUN" in w for w in result["warnings"]),
                        result["warnings"])


if __name__ == "__main__":
    unittest.main()
