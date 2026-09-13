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

from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import deskew
from engine.scan_cv.lines import detect_lines

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
