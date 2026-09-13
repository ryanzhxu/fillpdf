"""Tests engine/scan_cv/backend.py's assembly of render.py + ocr.py +
pipeline.py into a real `page_backend`, wired straight into `detect()`.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_backend.py
"""
import unittest
from pathlib import Path
from unittest import mock

import pdfplumber

from engine.detect import detect
from engine.detect.page_protocol import DetectablePage
from engine.scan_cv.backend import make_cv_ocr_backend

SAMPLE = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "samples" / "agm_proxy_form.pdf"


class TestBackend(unittest.TestCase):
    def test_assembled_page_drives_a_real_rule(self):
        # Proves backend.py's own wiring -- not render.py's or ocr.py's or
        # pipeline.py's internals, each already covered by their own tests --
        # by faking just those three pieces' outputs (a "Name" write-on line,
        # same shape as tests/test_synthetic_page.py's hand-built fixture)
        # and checking the assembled DetectablePage runs R5 correctly through
        # the real, unmodified rules.py.
        fake_rects = []  # no lines/checkboxes needed for this rule
        fake_words = [{"x0": 10, "x1": 40, "top": 10, "bottom": 20, "text": "Name"}]
        fake_chars = [{"x0": 42 + i * 3, "x1": 45 + i * 3, "top": 10, "bottom": 20,
                       "text": "_"} for i in range(10)]
        with mock.patch("engine.scan_cv.backend.render_page_to_bitmap", return_value="bitmap"), \
             mock.patch("engine.scan_cv.backend.detect_lines_and_boxes", return_value=fake_rects), \
             mock.patch("engine.scan_cv.backend.ocr_page", return_value=(fake_words, fake_chars)):
            backend = make_cv_ocr_backend(SAMPLE)
            with pdfplumber.open(SAMPLE) as pdf:
                page = backend(pdf.pages[0], 1)
        from engine.detect.rules import detect as detect_page
        fields, _carry = detect_page(page, pno=1, carry_in=None)
        r5 = [f for f in fields if f["rule"] == "R5"]
        self.assertEqual(len(r5), 1)
        self.assertEqual(r5[0]["label"], "Name")

    def test_backend_declines_a_page_with_nothing_recovered(self):
        # A blank/near-blank scan (no OCR words, no CV rects) should decline
        # (return None) rather than claim ocr_assisted over an empty page --
        # detect() then keeps its honest "scanned" notice for that page. See
        # engine/detect/__init__.py's page_backend contract.
        with mock.patch("engine.scan_cv.backend.render_page_to_bitmap", return_value="bitmap"), \
             mock.patch("engine.scan_cv.backend.detect_lines_and_boxes", return_value=[]), \
             mock.patch("engine.scan_cv.backend.ocr_page", return_value=([], [])):
            backend = make_cv_ocr_backend(SAMPLE)
            with pdfplumber.open(SAMPLE) as pdf:
                page = backend(pdf.pages[0], 1)
        self.assertIsNone(page)

    def test_backend_returns_a_DetectablePage(self):
        backend = make_cv_ocr_backend(SAMPLE)
        with pdfplumber.open(SAMPLE) as pdf:
            page = backend(pdf.pages[0], 1)
        self.assertIsInstance(page, DetectablePage)
        self.assertEqual(page.width, pdf.pages[0].width)
        self.assertEqual(page.height, pdf.pages[0].height)
        self.assertEqual(page.curves, [])

    def test_detect_end_to_end_on_agm_proxy_form(self):
        # AUTOPILOT.md's part-4 mechanical checks: detect() runs without
        # crashing or timing out, tags the document ocr_assisted (not
        # scanned) -- i.e. the backend actually ran and was not declined --
        # and returns a non-empty fields list (engine/scan_cv/lines.py's
        # morphological-opening fix raised this file's detected line count
        # from 2 to 10, enough for rules.py to now find real fields here;
        # see .autobuild/PROGRESS.md). Every field's rect must still land in
        # bounds, checked below regardless of count. This does NOT assert
        # the fields are the CORRECT ones -- there is no hand-verified
        # ground truth for this file (see AUTOPILOT.md's part 4) -- only
        # that the pipeline produces mechanically-sane, non-empty output.
        doc = detect(str(SAMPLE), page_backend=make_cv_ocr_backend(SAMPLE))
        self.assertEqual(doc["notice"]["code"], "ocr_assisted")
        self.assertGreater(len(doc["fields"]), 0)
        pages_by_number = {p["page"]: p for p in doc["pages"]}
        for f in doc["fields"]:
            page = pages_by_number[f["page"]]
            # rect is [x0, y0, x1, y1] in bottom-up PDF points (see
            # rules.py's "H - bottom"/"H - top" construction).
            x0, y0, x1, y1 = f["rect"]
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page["width"])
            self.assertLessEqual(y1, page["height"])
            self.assertEqual(f["origin"], "ocr")


if __name__ == "__main__":
    unittest.main()
