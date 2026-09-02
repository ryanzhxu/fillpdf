"""Tests for eval/guards.py. Run with:
    .venv/bin/python eval/test_guards.py
"""
import unittest

from engine.detect import detect
from eval.guards import box_over_ink, glyph_coverage, whitespace_fit

PDF = "fixtures/safer.pdf"


class TestBoxOverInk(unittest.TestCase):
    def test_finds_known_bad_boxes_on_safer_pdf(self):
        """The current detector puts a box over the '4c.' heading text on
        page 3 of safer.pdf. box_over_ink must catch it with no answer key."""
        result = detect(PDF)
        report = box_over_ink(PDF, result["fields"])

        print("\n--- box_over_ink on fixtures/safer.pdf (current detector) ---")
        print(f"fraction of boxes over ink: {report['fraction']:.4f}")
        print(f"worst offenders ({len(report['offenders'])}):")
        for o in report["offenders"]:
            print(f"  {o['id']:45s} page {o['page']}  coverage {o['coverage']:.4f}")

        self.assertGreater(
            len(report["offenders"]), 0,
            "box_over_ink found zero offenders on safer.pdf -- the guard is "
            "broken, not the detector. Known bad box is on page 3 (the '4c.' heading).",
        )
        offender_ids = {o["id"] for o in report["offenders"]}
        self.assertIn(
            "p3_4c_if_you_or_your_spouse_were", offender_ids,
            "box_over_ink did not flag the known bad box over the '4c.' heading on page 3.",
        )

    def test_box_over_dense_paragraph_scores_high(self):
        # Page 1, right column, three dense lines of body text.
        box = {"id": "hand_dense", "page": 1, "type": "text", "rect": [218, 630, 582, 687]}
        report = box_over_ink(PDF, [box])
        coverage = report["offenders"][0]["coverage"] if report["offenders"] else 0.0
        self.assertGreater(coverage, 0.3, f"dense paragraph box scored only {coverage}")

    def test_box_in_whitespace_scores_near_zero(self):
        # Page 1, a verified empty region (no chars underneath).
        box = {"id": "hand_white", "page": 1, "type": "text", "rect": [410, 202, 560, 262]}
        report = box_over_ink(PDF, [box])
        self.assertEqual(report["fraction"], 0.0)
        self.assertEqual(report["offenders"], [])

    def test_box_on_checkbox_glyph_scores_low_because_excluded(self):
        # Exact rect of a real checkbox glyph on page 3 (Webdings box char).
        box = {"id": "hand_chk", "page": 3, "type": "checkbox", "rect": [306.05, 715.488, 316.05, 725.488]}
        report = box_over_ink(PDF, [box])
        self.assertEqual(
            report["fraction"], 0.0,
            "checkbox glyph counted as ink -- the exclusion list is not working",
        )


class TestGlyphCoverage(unittest.TestCase):
    def test_current_detector_finds_almost_all_checkbox_glyphs(self):
        result = detect(PDF)
        report = glyph_coverage(PDF, result["fields"])
        print(f"\nglyph_coverage: {report['fraction']:.4f} "
              f"({report['covered_glyphs']}/{report['total_glyphs']} glyphs)")
        self.assertGreater(report["fraction"], 0.9)


class TestWhitespaceFit(unittest.TestCase):
    def test_flags_a_3pt_tall_box(self):
        box = {"id": "hand_tiny", "page": 1, "type": "text", "rect": [100, 100, 200, 103]}
        report = whitespace_fit(PDF, [box])
        self.assertEqual(report["too_small_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
