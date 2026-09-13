"""Tests engine/scan_cv/deskew.py against the golden corpus's rotated
fixture (eval/scan_cv/generate.py's fixture_rotated, true skew = 3.0deg).

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_deskew.py
"""
import json
import unittest
from pathlib import Path

import cv2

from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import detect_skew_angle, deskew

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"
ANGLE_TOLERANCE_DEG = 0.5


class TestDeskew(unittest.TestCase):
    def test_recovers_true_skew_angle_within_tolerance(self):
        img = cv2.imread(str(GOLDEN / "fixture_rotated.png"))
        data = json.loads((GOLDEN / "fixture_rotated.json").read_text())
        binary = preprocess(img)
        angle = detect_skew_angle(binary)
        # The angle that STRAIGHTENS the image is the negative of the skew
        # that was applied to create it.
        self.assertAlmostEqual(angle, -data["true_skew_deg"], delta=ANGLE_TOLERANCE_DEG)

    def test_unrotated_fixture_detects_near_zero_skew(self):
        img = cv2.imread(str(GOLDEN / "fixture_lines.png"))
        binary = preprocess(img)
        angle = detect_skew_angle(binary)
        self.assertAlmostEqual(angle, 0.0, delta=ANGLE_TOLERANCE_DEG)

    def test_deskew_returns_image_and_invertible_matrix(self):
        img = cv2.imread(str(GOLDEN / "fixture_rotated.png"))
        binary = preprocess(img)
        rotated, M = deskew(binary)
        self.assertEqual(rotated.shape, binary.shape)
        M_inv = cv2.invertAffineTransform(M)
        self.assertEqual(M_inv.shape, (2, 3))


if __name__ == "__main__":
    unittest.main()
