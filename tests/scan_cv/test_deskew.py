"""Tests engine/scan_cv/deskew.py against the golden corpus's rotated
fixture (eval/scan_cv/generate.py's fixture_rotated, true skew = 3.0deg).

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_deskew.py
"""
import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import detect_skew_angle, deskew

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"
ANGLE_TOLERANCE_DEG = 0.5

# Synthetic pages below follow eval/scan_cv/generate.py's conventions: a
# 200x100pt page rendered at 300 DPI, black ink on white, then run through
# preprocess() exactly as the golden fixtures are.
SCALE = 300 / 72.0
PAGE_W, PAGE_H = round(200.0 * SCALE), round(100.0 * SCALE)

# A structureless page must land this close to 0.0. The guard returns
# exactly 0.0, so the tolerance only has to be small enough to catch the
# measured regressions (-6.3, +9.45, +10.0 degrees) -- 1.0 degree is far
# below all of them and still well under the skew a scan realistically has.
FLAT_TOLERANCE_DEG = 1.0


def _blank_page():
    return np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)


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

    def test_recovers_skew_that_misses_the_coarse_search_grid(self):
        """2.4deg falls between the coarse sweep's whole-degree samples, so
        reaching it needs the fine pass: the coarse sweep alone answers
        -2.0. Every other angle case here sits on the coarse grid, which
        left the fine pass untested."""
        img = _blank_page()
        for y_pt in (15.0, 35.0, 55.0, 75.0):
            cv2.line(img, (round(10.0 * SCALE), round(y_pt * SCALE)),
                     (round(190.0 * SCALE), round(y_pt * SCALE)), (0, 0, 0), 2)
        M = cv2.getRotationMatrix2D((PAGE_W / 2.0, PAGE_H / 2.0), 2.4, 1.0)
        img = cv2.warpAffine(img, M, (PAGE_W, PAGE_H), borderValue=(255, 255, 255))
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, -2.4, delta=ANGLE_TOLERANCE_DEG)


class TestDeskewOnPagesWithNoDominantDirection(unittest.TestCase):
    """A page with no dominant horizontal direction must report no skew.

    Three of these were measured returning a large, wrong angle when the
    row-sum score was read over the whole frame: the wedges warpAffine
    cuts into the corners grow with |angle| and, with no real horizontal
    feature to outweigh them, become the strongest signal in the image.
    Each test names its own measured value. The rest already answered 0.0
    and are here to keep answering it.
    """

    def test_scattered_dots_report_no_skew(self):
        """Measured -3.000 on this dot layout before the fix. The review
        that found the bug measured -6.300 on a layout of its own, so the
        wrong angle a dotted page lands on is arbitrary, not fixed."""
        img = _blank_page()
        rng = np.random.default_rng(1234)
        xs = rng.integers(20, PAGE_W - 20, size=40)
        ys = rng.integers(20, PAGE_H - 20, size=40)
        for x, y in zip(xs, ys):
            cv2.circle(img, (int(x), int(y)), 3, (0, 0, 0), -1)
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_single_vertical_rule_reports_no_skew(self):
        """Measured -9.95 before the fix (the review measured +9.45; the
        score curve is symmetric here, so the sign is a coin toss). A
        vertical rule is real ink but says nothing about the horizontal
        direction."""
        img = _blank_page()
        cv2.line(img, (PAGE_W // 2, 20), (PAGE_W // 2, PAGE_H - 20), (0, 0, 0), 2)
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_all_foreground_image_reports_no_skew(self):
        """Measured +10.0 before the fix -- pinned at the search bound,
        which is the border artifact by itself."""
        binary = np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)
        angle = detect_skew_angle(binary)
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_blank_page_reports_no_skew(self):
        binary = np.zeros((PAGE_H, PAGE_W), dtype=np.uint8)
        angle = detect_skew_angle(binary)
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_page_of_vertical_rules_only_reports_no_skew(self):
        """This page answered 0.0 before the fix and must keep doing so.
        It is the case that forces the second half of the guard: inside
        the window several vertical rules leave a score curve whose peak
        is 21x its own median, so a ratio check alone would accept them.
        The absolute gain check is what rejects them (their peak sits 200x
        under it)."""
        img = _blank_page()
        for i in range(5):
            x = int(PAGE_W * (i + 1) / 6)
            cv2.line(img, (x, 20), (x, PAGE_H - 20), (0, 0, 0), 2)
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_lone_checkbox_reports_no_skew(self):
        """A sparse page that already answered -0.1 before the fix -- the
        example the old docstring's safe-failure claim rested on. It is
        the weakest kind of evidence for that claim, so it is pinned here
        rather than relied on."""
        img = _blank_page()
        cv2.rectangle(img, (round(10.0 * SCALE), round(50.0 * SCALE)),
                      (round(30.0 * SCALE), round(70.0 * SCALE)), (0, 0, 0), 2)
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, 0.0, delta=FLAT_TOLERANCE_DEG)

    def test_a_single_rule_still_carries_a_real_skew(self):
        """The guard rejects pages with no horizontal direction, not pages
        with a thin one: one horizontal rule is still enough to deskew by."""
        img = _blank_page()
        cv2.line(img, (round(10.0 * SCALE), round(50.0 * SCALE)),
                 (round(190.0 * SCALE), round(50.0 * SCALE)), (0, 0, 0), 2)
        M = cv2.getRotationMatrix2D((PAGE_W / 2.0, PAGE_H / 2.0), 3.0, 1.0)
        img = cv2.warpAffine(img, M, (PAGE_W, PAGE_H), borderValue=(255, 255, 255))
        angle = detect_skew_angle(preprocess(img))
        self.assertAlmostEqual(angle, -3.0, delta=ANGLE_TOLERANCE_DEG)


if __name__ == "__main__":
    unittest.main()
