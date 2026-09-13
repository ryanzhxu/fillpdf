# tests/scan_cv/test_lines.py
"""Tests engine/scan_cv/lines.py against the golden corpus (both the
unrotated fixture_lines and the rotated fixture_rotated, which shares the
same 4 lines plus a checkbox this module must ignore).

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_lines.py
"""
import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import deskew
from engine.scan_cv.lines import detect_lines, detect_verticals

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"
COORD_TOLERANCE_PT = 3.0


def _expected_lines(fixture_json):
    return [r for r in fixture_json["rects"] if r["stroke"] and not r["fill"]]


def _line_matches(detected, expected, tol=COORD_TOLERANCE_PT):
    return (abs(detected["x0"] - expected["x0"]) <= tol
            and abs(detected["x1"] - expected["x1"]) <= tol
            and abs(detected["top"] - expected["top"]) <= tol
            and abs(detected["bottom"] - expected["bottom"]) <= tol)


def _deskewed(fixture_name):
    img = cv2.imread(str(GOLDEN / f"{fixture_name}.png"))
    binary = preprocess(img)
    return deskew(binary)  # (deskewed, M)


class TestLines(unittest.TestCase):
    def test_detects_all_four_lines_unrotated(self):
        data = json.loads((GOLDEN / "fixture_lines.json").read_text())
        deskewed, M = _deskewed("fixture_lines")
        detected = detect_lines(deskewed, M, data["dpi"])
        expected = _expected_lines(data)
        self.assertEqual(len(detected), len(expected))
        for exp in expected:
            self.assertTrue(any(_line_matches(d, exp) for d in detected),
                             f"no detected line matched {exp}")

    def test_detected_lines_match_the_thin_rect_convention(self):
        data = json.loads((GOLDEN / "fixture_lines.json").read_text())
        deskewed, M = _deskewed("fixture_lines")
        for d in detect_lines(deskewed, M, data["dpi"]):
            self.assertLess(d["height"], 3)
            self.assertGreaterEqual(d["width"], 5)
            self.assertFalse(d["fill"])
            self.assertTrue(d["stroke"])

    def test_keeps_a_table_rule_whose_column_divider_only_brackets_one_end(self):
        # A T-junction (a table's write-on rule meeting a column divider at
        # one end only) is not a closed box -- only a real box has a
        # bracketing vertical at BOTH endpoints. Repro of a real bug: with
        # only one endpoint required, two ordinary table rules sharing one
        # left-hand column divider were both misread as box edges and
        # silently dropped (0 of 2 detected instead of 2 of 2).
        img = np.full((300, 600, 3), 255, dtype=np.uint8)
        cv2.line(img, (100, 150), (300, 150), (0, 0, 0), 2)
        cv2.line(img, (100, 200), (300, 200), (0, 0, 0), 2)
        cv2.line(img, (100, 75), (100, 225), (0, 0, 0), 2)
        binary = preprocess(img)
        deskewed, M = deskew(binary)
        detected = detect_lines(deskewed, M, dpi=72)
        self.assertEqual(len(detected), 2)

    def test_still_drops_a_real_closed_box(self):
        # A genuine closed box (bracketed by a vertical at BOTH endpoints)
        # must still be excluded -- that is detect_checkboxes' job, not a
        # line -- so the fix above must not just always keep lines.
        img = np.full((300, 600, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (100, 100), (160, 160), (0, 0, 0), 2)
        binary = preprocess(img)
        deskewed, M = deskew(binary)
        detected = detect_lines(deskewed, M, dpi=72)
        self.assertEqual(len(detected), 0)

    def test_detects_a_box_sides_as_verticals(self):
        # A boxed heading/instruction panel (all 4 sides drawn, no fill) is
        # exactly the shape detect_lines() alone cannot describe -- see
        # detect_verticals()'s docstring and
        # tests/scan_cv/test_backend.py's regression test on the real
        # motivating fixture. Here: a plain rectangle with one line of text
        # inside it, at a large enough scale that its sides clear
        # BOX_SIDE_MIN_LEN_PT.
        img = np.full((300, 600, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (100, 100), (500, 160), (0, 0, 0), 2)
        cv2.putText(img, "A BOXED HEADING", (150, 138),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        binary = preprocess(img)
        deskewed, M = deskew(binary)
        verticals = detect_verticals(deskewed, M, dpi=72)
        self.assertGreaterEqual(len(verticals), 2)
        xs = sorted(v["x0"] for v in verticals)
        self.assertAlmostEqual(xs[0], 99.0, delta=3)
        self.assertAlmostEqual(xs[-1], 499.0, delta=3)
        for v in verticals:
            self.assertLess(v["width"], 3)
            self.assertGreaterEqual(v["height"], 5)

    def test_ignores_short_vertical_strokes_like_letters(self):
        # An ordinary letter's vertical stroke (well under
        # BOX_SIDE_MIN_LEN_PT at typical form-text sizes) must not register
        # as a box side.
        img = np.full((300, 600, 3), 255, dtype=np.uint8)
        cv2.putText(img, "Illinois", (150, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        binary = preprocess(img)
        deskewed, M = deskew(binary)
        verticals = detect_verticals(deskewed, M, dpi=72)
        self.assertEqual(verticals, [])

    def test_detects_lines_on_the_rotated_fixture_ignoring_the_checkbox(self):
        data = json.loads((GOLDEN / "fixture_rotated.json").read_text())
        deskewed, M = _deskewed("fixture_rotated")
        detected = detect_lines(deskewed, M, data["dpi"])
        expected = _expected_lines(data)
        self.assertEqual(len(detected), len(expected))
        for exp in expected:
            self.assertTrue(any(_line_matches(d, exp) for d in detected),
                             f"no detected line matched {exp}")


if __name__ == "__main__":
    unittest.main()
