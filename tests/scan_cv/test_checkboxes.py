# tests/scan_cv/test_checkboxes.py
"""Tests engine/scan_cv/checkboxes.py against the golden corpus.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_checkboxes.py
"""
import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import deskew
from engine.scan_cv.checkboxes import detect_checkboxes

GOLDEN = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "golden"
COORD_TOLERANCE_PT = 3.0


def _expected_checkbox(fixture_json):
    matches = [r for r in fixture_json["rects"] if r["fill"] and not r["stroke"]]
    assert len(matches) == 1
    return matches[0]


def _expected_checkbox_post_render(fixture_json):
    """The interface spec (2026-09-12-scan-cv-algorithm-design.md, "Pipeline
    architecture" step 6) requires every detected point to be mapped back to
    the *post-render* (pre-deskew) pixel space via invertAffineTransform(M)
    -- not to the pre-rotation "true content" space -- because real OCR runs
    on the post-render bitmap and CV rects must land in the same frame to
    line up with OCR words. fixture_rotated.json's own `rects` reuse the
    pre-rotation coordinates unchanged (see eval/scan_cv/generate.py), which
    happens to still satisfy Task 4's line test because a near-full-width
    line's two endpoints sit almost symmetric about the rotation center, so
    their rotation-induced errors cancel on averaging. A checkbox is a small
    shape localized off-center, so that cancellation does not happen and
    verifying against the unrotated truth is off by several points -- a
    hand-verified, mathematically exact effect of rotating an off-center
    point by `true_skew_deg`, not a detector imprecision. This helper
    computes the actually-correct expectation: the true rect's corners,
    forward-rotated by the fixture's own recorded skew (the same rotation
    `eval/scan_cv/generate.py` applied to build the bitmap), which is what a
    correct post-render-space detection must match.
    """
    box = _expected_checkbox(fixture_json)
    scale = fixture_json["dpi"] / 72.0
    skew = fixture_json["true_skew_deg"]
    img = cv2.imread(str(GOLDEN / "fixture_rotated.png"))
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), skew, 1.0)
    corners = np.array([
        [box["x0"] * scale, box["top"] * scale, 1.0],
        [box["x1"] * scale, box["top"] * scale, 1.0],
        [box["x0"] * scale, box["bottom"] * scale, 1.0],
        [box["x1"] * scale, box["bottom"] * scale, 1.0],
    ])
    mapped = corners @ M.T
    xs, ys = mapped[:, 0] / scale, mapped[:, 1] / scale
    return {"x0": xs.min(), "x1": xs.max(), "top": ys.min(), "bottom": ys.max()}


def _deskewed(fixture_name):
    img = cv2.imread(str(GOLDEN / f"{fixture_name}.png"))
    binary = preprocess(img)
    return deskew(binary)  # (deskewed, M)


class TestCheckboxes(unittest.TestCase):
    def test_detects_the_checkbox_unrotated(self):
        data = json.loads((GOLDEN / "fixture_checkbox.json").read_text())
        exp = _expected_checkbox(data)
        deskewed, M = _deskewed("fixture_checkbox")
        detected = detect_checkboxes(deskewed, M, data["dpi"])
        self.assertEqual(len(detected), 1)
        d = detected[0]
        self.assertAlmostEqual(d["x0"], exp["x0"], delta=COORD_TOLERANCE_PT)
        self.assertAlmostEqual(d["x1"], exp["x1"], delta=COORD_TOLERANCE_PT)
        self.assertAlmostEqual(d["top"], exp["top"], delta=COORD_TOLERANCE_PT)
        self.assertAlmostEqual(d["bottom"], exp["bottom"], delta=COORD_TOLERANCE_PT)

    def test_always_emits_fill_true_stroke_false(self):
        data = json.loads((GOLDEN / "fixture_checkbox.json").read_text())
        deskewed, M = _deskewed("fixture_checkbox")
        for d in detect_checkboxes(deskewed, M, data["dpi"]):
            self.assertTrue(d["fill"])
            self.assertFalse(d["stroke"])

    def test_detects_two_adjacent_checkboxes_independently(self):
        # A real, common form pattern ("Yes [ ]  No [ ]") that no existing
        # fixture exercises -- every golden fixture has exactly one checkbox.
        # Two squares close together are a real risk for a contour-based
        # detector: _erase_long_lines' morphological kernel is on the order
        # of a checkbox's own size, so it could plausibly bridge a narrow gap
        # and merge both squares into one blob that fails the 4-corner test.
        scale = 300 / 72.0
        img = np.full((round(100 * scale), round(400 * scale), 3), 255, dtype=np.uint8)
        x0 = 50.0
        for _ in range(2):
            x1 = x0 + 20.0
            cv2.rectangle(
                img, (round(x0 * scale), round(30 * scale)),
                (round(x1 * scale), round(50 * scale)), (0, 0, 0), 2)
            x0 = x1 + 15.0  # a typical tight real-world checkbox spacing
        binary = preprocess(img)
        deskewed, M = deskew(binary)
        detected = detect_checkboxes(deskewed, M, dpi=300)
        self.assertEqual(len(detected), 2)
        xs = sorted(d["x0"] for d in detected)
        self.assertGreater(xs[1] - xs[0], 20.0)  # two distinct boxes, not one merged blob

    def test_detects_the_checkbox_on_the_rotated_fixture_ignoring_lines(self):
        data = json.loads((GOLDEN / "fixture_rotated.json").read_text())
        exp = _expected_checkbox_post_render(data)
        deskewed, M = _deskewed("fixture_rotated")
        detected = detect_checkboxes(deskewed, M, data["dpi"])
        self.assertEqual(len(detected), 1)
        d = detected[0]
        self.assertAlmostEqual(d["x0"], exp["x0"], delta=COORD_TOLERANCE_PT)
        self.assertAlmostEqual(d["top"], exp["top"], delta=COORD_TOLERANCE_PT)


if __name__ == "__main__":
    unittest.main()
