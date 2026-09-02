"""Tests for eval/report.py. Run with:
    .venv/bin/python eval/test_report.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from eval.report import report


def _metrics(truth=100, detected=100, matched=90, precision=0.9, recall=0.9, f1=0.9, near_miss=0):
    return {
        "truth": truth, "detected": detected, "matched": matched,
        "precision": precision, "recall": recall, "f1": f1, "near_miss": near_miss,
    }


def _scores(git_sha="sha1", overall=None, holdout=None, per_rule=None, per_family=None,
            per_form=None, guards=None, failures=None):
    return {
        "version": 1, "git_sha": git_sha, "started_at": "2026-09-01T00:00:00Z",
        "overall": overall if overall is not None else _metrics(),
        "holdout": holdout if holdout is not None else _metrics(truth=0, detected=0, matched=0,
                                                                 precision=0.0, recall=0.0, f1=0.0),
        "per_rule": per_rule if per_rule is not None else {"R1": _metrics(), "R2": _metrics()},
        "per_family": per_family if per_family is not None else {"famA": _metrics()},
        "per_form": per_form if per_form is not None else {"form_a.pdf": _metrics(f1=0.5),
                                                             "form_b.pdf": _metrics(f1=0.95)},
        "guards": guards,
        "failures": failures if failures is not None else [],
    }


def _write(tmpdir, name, doc):
    p = Path(tmpdir) / name
    p.write_text(json.dumps(doc))
    return str(p)


class TestReport(unittest.TestCase):
    def test_no_baseline_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            scores_path = _write(tmp, "scores.json", _scores())
            text = report(scores_path)
        self.assertIn("FORMFILL DETECTION EVAL REPORT", text)
        self.assertIn("OVERALL", text)
        self.assertIn("PER-RULE", text)
        self.assertIn("PER-FAMILY", text)
        self.assertIn("WORST", text)
        self.assertIn("GUARDS", text)
        self.assertIn("FAILURES", text)
        self.assertNotIn("REGRESSION", text)

    def test_baseline_shows_signed_deltas_and_regression(self):
        base_guards = {"box_over_ink": 0.05, "box_over_ink_offenders": [],
                        "glyph_coverage": 1.0, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        cand_guards = {"box_over_ink": 0.09, "box_over_ink_offenders": [],
                        "glyph_coverage": 0.95, "whitespace_fit": {"too_small_fraction": 0.0, "stacked_fraction": 0.0}}
        baseline_doc = _scores(git_sha="base", overall=_metrics(precision=0.95, recall=0.90, f1=0.924),
                                guards=base_guards)
        candidate_doc = _scores(git_sha="cand", overall=_metrics(precision=0.90, recall=0.95, f1=0.924),
                                 guards=cand_guards)
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = _write(tmp, "baseline.json", baseline_doc)
            candidate_path = _write(tmp, "candidate.json", candidate_doc)
            text = report(candidate_path, baseline_path)

        self.assertIn("+0.05", text.replace("+0.0500", "+0.05"))  # recall delta present, sign explicit
        self.assertIn("-0.05", text.replace("-0.0500", "-0.05"))  # precision delta present, sign explicit
        self.assertIn("REGRESSION", text)  # precision drop and glyph_coverage drop must be flagged

    def test_worst_forms_sorted_ascending_by_f1(self):
        per_form = {f"form_{i}.pdf": _metrics(f1=i / 100.0) for i in range(1, 15)}
        with tempfile.TemporaryDirectory() as tmp:
            scores_path = _write(tmp, "scores.json", _scores(per_form=per_form))
            text = report(scores_path)
        worst_section = text.split("WORST 10 FORMS BY F1")[1].split("GUARDS")[0]
        self.assertIn("form_1.pdf", worst_section)
        self.assertNotIn("form_14.pdf", worst_section)

    def test_no_guards_data_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            scores_path = _write(tmp, "scores.json", _scores(guards=None))
            text = report(scores_path)
        self.assertIn("no guards data", text)


if __name__ == "__main__":
    unittest.main()
