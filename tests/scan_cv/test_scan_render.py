"""Tests engine/scan_cv/render.py against the real target scan
(eval/scan_cv/samples/agm_proxy_form.pdf), part 3 of AUTOPILOT.md's
decomposition (real page_backend, rendering piece).
"""
import unittest
from pathlib import Path

import numpy as np
import pdfplumber

from engine.scan_cv.pipeline import detect_lines_and_boxes
from engine.scan_cv.render import render_page_to_bitmap

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "eval" / "scan_cv" / "samples" / "agm_proxy_form.pdf"
)


class TestRenderPageToBitmap(unittest.TestCase):
    def test_renders_bgr_bitmap_at_approximately_requested_dpi(self):
        with pdfplumber.open(SAMPLE) as pdf:
            page = pdf.pages[0]
            width_pt, height_pt = page.width, page.height

        bitmap = render_page_to_bitmap(SAMPLE, 0, dpi=300)

        self.assertEqual(bitmap.ndim, 3)
        self.assertEqual(bitmap.shape[2], 3)
        self.assertEqual(bitmap.dtype, np.uint8)
        expected_w = width_pt * 300 / 72
        expected_h = height_pt * 300 / 72
        self.assertLess(abs(bitmap.shape[1] - expected_w), 2)
        self.assertLess(abs(bitmap.shape[0] - expected_h), 2)

    def test_output_feeds_detect_lines_and_boxes_without_crashing(self):
        bitmap = render_page_to_bitmap(SAMPLE, 0, dpi=300)

        rects = detect_lines_and_boxes(bitmap, dpi=300)

        self.assertIsInstance(rects, list)


if __name__ == "__main__":
    unittest.main()
