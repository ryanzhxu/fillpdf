"""Tests for eval/guards.py. Run with:
    .venv/bin/python eval/test_guards.py
"""
import unittest

from engine.detect import detect
from eval.guards import box_over_ink, glyph_coverage, whitespace_fit

PDF = "fixtures/safer.pdf"


class TestBoxOverInk(unittest.TestCase):
    def test_guard_still_catches_an_injected_bad_box_on_safer(self):
        """The guard must catch a box on real printed text.

        This test used to assert that the live detector put boxes on the "4c."
        heading of safer.pdf. Rule R9 now removes those, so asserting the bug
        still exists would assert a regression. Instead, inject a box over that
        same heading and prove the guard still finds it.
        """
        from engine.detect import detect
        import pdfplumber

        with pdfplumber.open(PDF) as pdf:
            page = pdf.pages[2]
            H = page.height
            hit = [c for c in page.chars if c["text"].strip()]
            xs = [c["x0"] for c in hit][:1] or [100.0]
            words = page.extract_words()
            target = next((w for w in words if w["text"].startswith("4c")), words[0])
            rect = [target["x0"], H - target["bottom"] - 1,
                    target["x0"] + 220, H - target["top"] + 1]

        injected = [{"id": "injected_on_heading", "page": 3, "type": "text",
                     "rect": rect, "label": "", "origin": "user_added"}]
        result = box_over_ink(PDF, injected)
        self.assertGreater(result["fraction"], 0.0,
                           "guard failed to flag a box placed on printed text")

    def test_r9_keeps_the_known_safer_false_positives_gone(self):
        """Regression guard for the two worst real false positives on safer.pdf.

        Both sat on printed headings and were removed by R9. If either returns,
        R9 has regressed.
        """
        from engine.detect import detect
        ids = {f["id"] for f in detect(PDF)["fields"]}
        self.assertNotIn("p3_4c_if_you_or_your_spouse_were", ids)
        self.assertNotIn("p6_scan_and_upload", ids)

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
