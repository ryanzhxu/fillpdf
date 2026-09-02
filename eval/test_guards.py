"""Tests for eval/guards.py. Run with:
    .venv/bin/python eval/test_guards.py
"""
import unittest
from pathlib import Path

from engine.detect import detect
from eval.guards import box_over_ink, glyph_coverage, whitespace_fit, label_plausibility

PDF = "fixtures/safer.pdf"

# The real form the known-bad fragment labels ("ADDRESS OF" / "BEING RENTED
# TO TENANT(s)") were observed on -- see eval/guards.py's label_plausibility
# docstring. Not part of this worktree's tracked fixtures (real corpus PDFs
# live only in the main repo), so this test skips if it is not present
# rather than failing a clean checkout.
REAL_BAD_PDF = "/Users/ryan.xu/Developer/formfill/eval/corpus/tuning/0b7c460527c6.pdf"


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


class TestLabelPlausibility(unittest.TestCase):
    """See eval/guards.py's label_plausibility docstring for the three
    signals (provenance, contiguity, sentence-fragment shape) and why each
    was kept.
    """

    @unittest.skipUnless(Path(REAL_BAD_PDF).exists(), "real corpus not present in this worktree")
    def test_flags_the_known_bad_real_fragments(self):
        """The exact known-bad case: "ADDRESS OF" and "BEING RENTED TO
        TENANT(s)" are real, contiguous, normally-spaced words on
        0b7c460527c6.pdf's page 1 -- sliced out of one run-on heading
        ("ADDRESS OF PLACE BEING RENTED TO TENANT(s) called the 'rental
        unit' in this agreement:"). Neither box_over_ink nor label_accuracy
        (real forms carry no truth label) can see this; label_plausibility
        must, via the sentence-fragment (truncation) signal.
        """
        fields = [
            {"id": "f1", "page": 1, "type": "text", "rect": [33, 227, 100, 244],
             "label": "ADDRESS OF", "rule": "R3"},
            {"id": "f2", "page": 1, "type": "text", "rect": [230, 227, 290, 244],
             "label": "BEING RENTED TO TENANT(s)", "rule": "R3"},
        ]
        report = label_plausibility(REAL_BAD_PDF, fields)
        self.assertEqual(report["flagged"], 2, report["offenders"])
        reasons_by_id = {o["id"]: o["reasons"] for o in report["offenders"]}
        self.assertIn("truncated_right", reasons_by_id["f1"])
        self.assertIn("truncated_left", reasons_by_id["f2"])
        self.assertIn("truncated_right", reasons_by_id["f2"])

    @unittest.skipUnless(Path(REAL_BAD_PDF).exists(), "real corpus not present in this worktree")
    def test_spares_the_real_column_headers_next_to_the_known_bad_case(self):
        """The genuine column headers directly below the bad heading on the
        same form ("unit number", "city") must NOT be flagged."""
        fields = [
            {"id": "g1", "page": 1, "type": "text", "rect": [39, 792 - 605, 90, 792 - 587],
             "label": "unit number", "rule": "R3"},
            {"id": "g2", "page": 1, "type": "text", "rect": [300, 792 - 605, 330, 792 - 587],
             "label": "city", "rule": "R3"},
        ]
        report = label_plausibility(REAL_BAD_PDF, fields)
        self.assertEqual(report["flagged"], 0, report["offenders"])

    def test_flags_a_synthetic_column_crossing_fragment(self):
        """A label built from two words either side of safer.pdf's page-1
        two-column gutter ("...Submit completed" / "The Shelter Aid...")
        must be flagged for the wide internal gap, mirroring the real
        run-on-sentence-across-a-column-boundary bug."""
        fields = [{"id": "hand_cross", "page": 1, "type": "text",
                   "rect": [46, 792 - 121, 240, 792 - 107],
                   "label": "completed The", "rule": "R3"}]
        report = label_plausibility(PDF, fields)
        self.assertEqual(report["flagged"], 1)
        self.assertIn("internal_gap", report["offenders"][0]["reasons"])

    def test_flags_a_synthetic_mid_sentence_fragment(self):
        """"Shelter Aid" is a real, contiguous, normally-spaced two-word
        slice out of the middle of "The Shelter Aid for Elderly Renters
        (SAFER) program..." -- a normally-spaced word sits on the page
        immediately either side of it, excluded from the label."""
        fields = [{"id": "hand_mid", "page": 1, "type": "text",
                   "rect": [220, 792 - 121, 280, 792 - 107],
                   "label": "Shelter Aid", "rule": "R3"}]
        report = label_plausibility(PDF, fields)
        self.assertEqual(report["flagged"], 1)
        self.assertIn("truncated_left", report["offenders"][0]["reasons"])
        self.assertIn("truncated_right", report["offenders"][0]["reasons"])

    def test_flags_a_label_whose_words_are_out_of_page_order(self):
        """A label whose words do not occur in that order anywhere on the
        page (here: reversed) cannot be located -- provenance fails."""
        fields = [{"id": "hand_reversed", "page": 1, "type": "text",
                   "rect": [220, 792 - 121, 400, 792 - 107],
                   "label": "Renters Shelter", "rule": "R3"}]
        report = label_plausibility(PDF, fields)
        self.assertEqual(report["flagged"], 1)
        self.assertIn("not_found_on_page", report["offenders"][0]["reasons"])

    def test_spares_the_real_page_title(self):
        """"Application Form" is safer.pdf's actual page-1 title -- a
        complete label with nothing contiguous before or after it."""
        fields = [{"id": "hand_title", "page": 1, "type": "text",
                   "rect": [340, 792 - 47, 530, 792 - 34],
                   "label": "Application Form", "rule": "R3"}]
        report = label_plausibility(PDF, fields)
        self.assertEqual(report["flagged"], 0, report["offenders"])

    def test_spares_a_real_multiline_column_header(self):
        """"Account Number" is a genuine, complete table-column header on
        safer.pdf page 7, positioned right at its own box -- must not be
        flagged even though the same two words also occur, unrelated,
        inside an earlier body-text sentence on the same page."""
        fields = [{"id": "hand_acct", "page": 7, "type": "text",
                   "rect": [387, 792 - 285, 470, 792 - 273],
                   "label": "Account Number", "rule": "R3"}]
        report = label_plausibility(PDF, fields)
        self.assertEqual(report["flagged"], 0, report["offenders"])

    def test_spares_ordinary_labels_the_live_detector_produces(self):
        """The live detector's own labels on safer.pdf must overwhelmingly
        pass clean -- this guard watches for a regression that makes
        plausible-sounding output newly fragment-heavy, not a fixed target."""
        fields = detect(PDF)["fields"]
        report = label_plausibility(PDF, fields)
        self.assertLess(report["fraction"], 0.30, report["offenders"])

    def test_finds_the_real_cross_column_header_merge_bug(self):
        """The live detector currently merges two separate column headers
        ("Landlord Phone #" and "Date:") across a real ~91pt column gap on
        safer.pdf page 7 into one label. This is a genuine existing defect
        this guard is meant to surface -- not injected, not fixed here (the
        detector is out of scope for this change)."""
        fields = detect(PDF)["fields"]
        report = label_plausibility(PDF, fields)
        offenders_by_label = {o["label"]: o["reasons"] for o in report["offenders"]}
        self.assertIn("Landlord Phone # Date:", offenders_by_label)
        self.assertIn("internal_gap", offenders_by_label["Landlord Phone # Date:"])

    def test_duplication_is_measured_not_judged(self):
        """A label repeated across several boxes on one page (here:
        0b7c460527c6.pdf page 6's "last name" column, inherited by three
        separate rows) must be counted, but must never by itself cause a
        flag."""
        if not Path(REAL_BAD_PDF).exists():
            self.skipTest("real corpus not present in this worktree")
        # Three real "last name" row positions, page 6 (PDF-point rects,
        # bottom-left origin, converted from pdfplumber top/bottom).
        rows = [(550.39, 559.39), (497.11, 506.11), (413.59, 422.59)]
        fields = [
            {"id": f"hand_dup{i}", "page": 6, "type": "text",
             "rect": [34.2, y0, 73.2, y1], "label": "last name", "rule": "R3"}
            for i, (y0, y1) in enumerate(rows)
        ]
        report = label_plausibility(REAL_BAD_PDF, fields)
        self.assertEqual(report["duplication"]["max_repeat"], 3)
        self.assertEqual(report["flagged"], 0, report["offenders"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
