"""End-to-end test of engine/scan_cv/pipeline.py's public
detect_lines_and_boxes() against the full golden corpus.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_pipeline.py
"""
import json
import unittest
from pathlib import Path

import cv2

from engine.scan_cv.pipeline import detect_lines_and_boxes

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"


class TestPipeline(unittest.TestCase):
    def test_rotated_fixture_produces_all_five_expected_rects(self):
        img = cv2.imread(str(GOLDEN / "fixture_rotated.png"))
        data = json.loads((GOLDEN / "fixture_rotated.json").read_text())
        result = detect_lines_and_boxes(img, data["dpi"])
        self.assertEqual(len(result), len(data["rects"]))
        lines = [r for r in result if r["stroke"]]
        boxes = [r for r in result if r["fill"]]
        self.assertEqual(len(lines), 4)
        self.assertEqual(len(boxes), 1)

    def test_result_entries_are_DetectablePage_rects_shaped(self):
        img = cv2.imread(str(GOLDEN / "fixture_lines.png"))
        data = json.loads((GOLDEN / "fixture_lines.json").read_text())
        for r in detect_lines_and_boxes(img, data["dpi"]):
            for key in ("x0", "x1", "top", "bottom", "width", "height", "fill", "stroke"):
                self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
