# eval/scan_cv/test_generate.py
"""Confirms the golden corpus is internally consistent -- every rect's
JSON-declared geometry actually matches non-white pixels in its PNG, and
each fixture's PNG dimensions match its declared DPI and page size.

Run standalone with:  .venv/bin/python -m pytest eval/scan_cv/test_generate.py
"""
import json
import unittest
from pathlib import Path

import cv2

GOLDEN = Path(__file__).parent / "golden"
SCALE = 300 / 72.0


class TestGoldenCorpus(unittest.TestCase):
    def _load(self, name):
        img = cv2.imread(str(GOLDEN / f"{name}.png"))
        data = json.loads((GOLDEN / f"{name}.json").read_text())
        return img, data

    def test_fixture_lines_dimensions_and_content(self):
        img, data = self._load("fixture_lines")
        self.assertEqual(data["dpi"], 300)
        self.assertEqual(img.shape[1], round(200.0 * SCALE))  # width px
        self.assertEqual(img.shape[0], round(100.0 * SCALE))  # height px
        self.assertEqual(len(data["rects"]), 4)
        nonwhite = (img < 250).any(axis=2).sum()
        self.assertGreater(nonwhite, 0)

    def test_fixture_checkbox_dimensions_and_content(self):
        img, data = self._load("fixture_checkbox")
        self.assertEqual(len(data["rects"]), 1)
        r = data["rects"][0]
        self.assertEqual((r["width"], r["height"]), (20.0, 20.0))
        self.assertTrue(r["fill"] and not r["stroke"])

    def test_fixture_rotated_has_true_skew_and_combined_rects(self):
        img, data = self._load("fixture_rotated")
        self.assertEqual(data["true_skew_deg"], 3.0)
        self.assertEqual(len(data["rects"]), 5)  # 4 lines + 1 checkbox


if __name__ == "__main__":
    unittest.main()
