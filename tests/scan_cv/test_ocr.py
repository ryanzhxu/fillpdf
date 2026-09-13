# tests/scan_cv/test_ocr.py
"""Tests engine/scan_cv/ocr.py: the OCR piece of AUTOPILOT.md's part 3 (real
page_backend), following render.py's rendering piece. Two checks:

  - A synthetic bitmap with a known word at a known pixel position, DPI 300,
    proves recognized text and PDF-point coordinates round-trip correctly,
    and that `chars` splits each word into one entry per character.
  - The real target scan (eval/scan_cv/samples/agm_proxy_form.pdf) proves
    OCR runs end-to-end on a real scanned page without crashing and returns
    words within the page's own bounds -- the same mechanical bar
    AUTOPILOT.md part 4 holds the eventual full pipeline to.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_ocr.py
"""
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
import pdfplumber

from engine.scan_cv.ocr import ocr_page
from engine.scan_cv.render import render_page_to_bitmap

DPI = 300
SCALE = DPI / 72.0
SAMPLE = (
    Path(__file__).parent.parent.parent
    / "eval" / "scan_cv" / "samples" / "agm_proxy_form.pdf"
)


def _synthetic_bitmap_with_word(word, x0_pt, y0_pt):
    """A blank 200x100pt page (rendered at DPI) with `word` drawn in large
    black text whose baseline sits at (x0_pt, y0_pt) in PDF points."""
    w, h = round(200.0 * SCALE), round(100.0 * SCALE)
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    origin = (round(x0_pt * SCALE), round(y0_pt * SCALE))
    cv2.putText(img, word, origin, cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3,
                cv2.LINE_AA)
    return img


class TestOcrPage(unittest.TestCase):
    def test_recognizes_known_word_at_known_position(self):
        bitmap = _synthetic_bitmap_with_word("NAME", 20.0, 40.0)

        words, chars = ocr_page(bitmap, dpi=DPI)

        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["text"], "NAME")
        # cv2.putText's origin is the text baseline, so the recognized box
        # sits above and around it -- assert it lands in the right
        # neighborhood, not an exact pixel match.
        self.assertTrue(10.0 < words[0]["x0"] < 30.0)
        self.assertTrue(20.0 < words[0]["top"] < 40.0)
        self.assertTrue(words[0]["x1"] > words[0]["x0"])
        self.assertTrue(words[0]["bottom"] > words[0]["top"])

    def test_chars_split_evenly_one_per_character(self):
        bitmap = _synthetic_bitmap_with_word("NAME", 20.0, 40.0)

        words, chars = ocr_page(bitmap, dpi=DPI)

        self.assertEqual(len(chars), len("NAME"))
        self.assertEqual([c["text"] for c in chars], list("NAME"))
        # Sub-boxes tile the word's box left to right with no gaps/overlaps.
        word = words[0]
        self.assertAlmostEqual(chars[0]["x0"], word["x0"], places=6)
        self.assertAlmostEqual(chars[-1]["x1"], word["x1"], places=6)
        for a, b in zip(chars, chars[1:]):
            self.assertAlmostEqual(a["x1"], b["x0"], places=6)
            self.assertEqual(a["top"], word["top"])
            self.assertEqual(a["bottom"], word["bottom"])

    def test_blank_page_yields_no_words(self):
        img = np.full((round(100.0 * SCALE), round(200.0 * SCALE), 3), 255,
                      dtype=np.uint8)

        words, chars = ocr_page(img, dpi=DPI)

        self.assertEqual(words, [])
        self.assertEqual(chars, [])

    def test_subprocess_timeout_yields_no_words_instead_of_hanging(self):
        # pytesseract's documented failure mode for a stuck tesseract
        # subprocess is to kill it and raise RuntimeError once `timeout`
        # elapses (pytesseract.pytesseract.timeout_manager). ocr_page must
        # turn that into "no words recognized" rather than letting it
        # propagate and crash detect() -- see AUTOPILOT.md part 4's "without
        # crashing or timing out" bar.
        bitmap = _synthetic_bitmap_with_word("NAME", 20.0, 40.0)
        with mock.patch("pytesseract.image_to_data",
                        side_effect=RuntimeError("Tesseract process timeout")):
            words, chars = ocr_page(bitmap, dpi=DPI, timeout=1)

        self.assertEqual(words, [])
        self.assertEqual(chars, [])

    def test_zero_confidence_word_is_still_kept(self):
        # A real recognized word can legitimately score exactly 0 confidence
        # (measured on real corpus scans: "But", "City", "VIOLATION" all
        # scored 0 while being correctly recognized) -- only tesseract's
        # -1-confidence block/line summary rows (which always carry empty
        # text, already excluded by the `not text` check) should be dropped.
        bitmap = _synthetic_bitmap_with_word("NAME", 20.0, 40.0)
        fake_data = {
            "text": ["", "NAME"],
            "left": [0, 30],
            "top": [0, 60],
            "width": [0, 100],
            "height": [0, 30],
            "conf": [-1, 0],
        }
        with mock.patch("pytesseract.image_to_data", return_value=fake_data):
            words, chars = ocr_page(bitmap, dpi=DPI)

        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["text"], "NAME")

    def test_runs_on_the_real_target_scan_without_crashing(self):
        with pdfplumber.open(SAMPLE) as pdf:
            page = pdf.pages[0]
            width_pt, height_pt = page.width, page.height

        bitmap = render_page_to_bitmap(SAMPLE, 0, dpi=DPI)
        words, chars = ocr_page(bitmap, dpi=DPI)

        self.assertGreater(len(words), 0)
        for w in words:
            self.assertGreaterEqual(w["x0"], -1.0)
            self.assertGreaterEqual(w["top"], -1.0)
            self.assertLessEqual(w["x1"], width_pt + 1.0)
            self.assertLessEqual(w["bottom"], height_pt + 1.0)


if __name__ == "__main__":
    unittest.main()
