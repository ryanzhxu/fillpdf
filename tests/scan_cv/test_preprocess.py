"""Tests engine/scan_cv/preprocess.py's binarization step against the
golden corpus fixtures from eval/scan_cv/generate.py.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_preprocess.py
"""
import unittest
from pathlib import Path

import cv2
import numpy as np

from engine.scan_cv.preprocess import preprocess

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"


class TestPreprocess(unittest.TestCase):
    def test_output_is_single_channel_binary(self):
        img = cv2.imread(str(GOLDEN / "fixture_lines.png"))
        out = preprocess(img)
        self.assertEqual(out.ndim, 2)  # single channel
        self.assertEqual(out.shape, img.shape[:2])
        unique = set(np.unique(out).tolist())
        self.assertTrue(unique <= {0, 255})  # strictly binary

    def test_foreground_pixels_exist_and_background_is_majority(self):
        img = cv2.imread(str(GOLDEN / "fixture_checkbox.png"))
        out = preprocess(img)
        foreground = int((out == 255).sum())
        total = out.size
        self.assertGreater(foreground, 0)
        self.assertLess(foreground, total * 0.5)  # a checkbox is a small mark


if __name__ == "__main__":
    unittest.main()
