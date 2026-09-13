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


class TestDeskewOnDenseAndEdgeContent(unittest.TestCase):
    """Real pages whose ink is not thin rules on mostly-white paper.

    Every golden fixture is a handful of hairlines on a white page, which
    is the easiest case there is. These are the two shapes that a guard
    tuned only on that easy case gets wrong, and both were live bugs:
    a dense page (the score's ceiling drops as more rows carry ink) and a
    page whose structure sits outside the middle of the frame.
    """

    def _bars(self, skew_deg, pitch_pt=4.0, bar_pt=1.6):
        """Solid rules on a fixed pitch. The row-sum profile of dense
        single-spaced text looks like this, and 1.6pt of ink per 4.0pt of
        pitch is an ordinary duty cycle for it."""
        img = _blank_page()
        y = 10.0
        while y <= 90.0:
            cv2.rectangle(img, (round(15.0 * SCALE), round(y * SCALE)),
                          (round(185.0 * SCALE), round((y + bar_pt) * SCALE)),
                          (0, 0, 0), -1)
            y += pitch_pt
        M = cv2.getRotationMatrix2D((PAGE_W / 2.0, PAGE_H / 2.0), skew_deg, 1.0)
        img = cv2.warpAffine(img, M, (PAGE_W, PAGE_H), borderValue=(255, 255, 255))
        return preprocess(img)

    def test_dense_text_page_recovers_small_skew(self):
        """A guard that required the peak to beat the sweep median by a
        fixed absolute margin answered 0.0 here -- silently, on a real
        skew. The score tops out near 1.2 on a page this dense, so the
        margin has to be a ratio, never a fixed quantity."""
        angle = detect_skew_angle(self._bars(0.75))
        self.assertAlmostEqual(angle, -0.75, delta=ANGLE_TOLERANCE_DEG)

    def test_dense_text_page_recovers_larger_skew(self):
        """Same page at 3.5deg. The absolute-margin guard failed here too,
        and non-monotonically -- 2.0 and 5.0 passed while 0.75 and 3.5 did
        not -- which is what exposed it as noise-floor behavior."""
        angle = detect_skew_angle(self._bars(3.5))
        self.assertAlmostEqual(angle, -3.5, delta=ANGLE_TOLERANCE_DEG)

    def test_dense_text_page_at_several_duty_cycles(self):
        for bar_pt in (0.4, 0.8, 1.2, 1.6, 2.0, 2.4):
            with self.subTest(bar_pt=bar_pt):
                angle = detect_skew_angle(self._bars(3.0, bar_pt=bar_pt))
                self.assertAlmostEqual(angle, -3.0, delta=ANGLE_TOLERANCE_DEG)

    def _edge_rules(self, ys_pt, skew_deg):
        img = _blank_page()
        for y_pt in ys_pt:
            cv2.line(img, (round(10.0 * SCALE), round(y_pt * SCALE)),
                     (round(190.0 * SCALE), round(y_pt * SCALE)), (0, 0, 0), 2)
        M = cv2.getRotationMatrix2D((PAGE_W / 2.0, PAGE_H / 2.0), skew_deg, 1.0)
        img = cv2.warpAffine(img, M, (PAGE_W, PAGE_H), borderValue=(255, 255, 255))
        return preprocess(img)

    def test_rules_only_near_the_page_edges_recover_skew(self):
        """A header rule and a footer rule with nothing between them.
        Scoring a centered sub-window instead of the whole frame read
        these off the fragments that clipped into the window, answering
        as far as 9.0 degrees out on a true 3.0."""
        for ys in ((1.0, 99.0), (2.0, 98.0), (5.0, 95.0), (8.0, 92.0),
                   (2.0, 4.0, 96.0, 98.0), (5.0, 10.0, 90.0, 95.0)):
            with self.subTest(rules=ys):
                angle = detect_skew_angle(self._edge_rules(ys, 3.0))
                self.assertAlmostEqual(angle, -3.0, delta=ANGLE_TOLERANCE_DEG)

    def test_rules_only_near_the_page_edges_report_no_skew_when_straight(self):
        for ys in ((1.0, 99.0), (2.0, 98.0), (5.0, 95.0), (8.0, 92.0)):
            with self.subTest(rules=ys):
                angle = detect_skew_angle(self._edge_rules(ys, 0.0))
                self.assertAlmostEqual(angle, 0.0, delta=ANGLE_TOLERANCE_DEG)


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
        Its profile is the same at every angle, so the sweep is flat and
        the guard rejects it."""
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
