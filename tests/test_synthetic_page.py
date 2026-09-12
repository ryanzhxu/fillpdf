"""Proves _detect_page() (rules.py) runs unmodified against a synthetic
DetectablePage -- the whole point of the interface in
docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.

No OCR or image-processing library is used here. FakeSyntheticPage is
hand-built with geometry chosen to exercise two rules that read different
parts of the DetectablePage surface:
  - R5 (engine/detect/rules.py, "runs of underscores are write-on lines")
    reads only `chars` and `extract_words()`.
  - R18 ("a checkbox drawn as a filled square, not a glyph") reads only
    `rects` and `extract_words()`.
Together they cover every DetectablePage member except `curves`, which the
spec says may legitimately stay empty for a v1 backend.

Run standalone with:  .venv/bin/python -m pytest tests/test_synthetic_page.py
"""
import unittest

from engine.detect.page_protocol import DetectablePage
from engine.detect.rules import detect as detect_page


class FakeSyntheticPage:
    """A hand-built DetectablePage, standing in for a future OCR/CV backend."""

    def __init__(self, width, height, chars, rects, curves, words):
        self.width = width
        self.height = height
        self.chars = chars
        self.rects = rects
        self.curves = curves
        self._words = words

    def extract_words(self):
        return self._words


def _make_fixture():
    """One page: a "Name" write-on line (R5) and an "Agree" checkbox (R18).

    Coordinates use pdfplumber's convention: `top`/`bottom` measured down
    from the page's top edge; page height H = 100, width W = 200.
    """
    H, W = 100.0, 200.0

    # "Name" label immediately followed by a 30pt-wide run of 10 underscore
    # characters -- long enough (>=25pt) to skip R5's short-run gates, so
    # the label comes straight from the word sitting on the same baseline
    # ending at (or just before) the run's own start.
    name_word = {"text": "Name", "x0": 10, "x1": 40, "top": 10, "bottom": 20}
    underscore_chars = []
    x = 42
    for _ in range(10):
        underscore_chars.append(
            {"text": "_", "x0": x, "x1": x + 3, "top": 10, "bottom": 20})
        x += 3

    # A 20x20 filled, unstroked square (inside R18's 18-32pt band, square
    # within its 8pt tolerance) with an "Agree" caption on the same
    # vertical midline (within R18's 2pt line tolerance) and a 5pt gap
    # (within its 11pt max caption gap).
    chk_rect = {"x0": 10, "x1": 30, "top": 50, "bottom": 70,
                "width": 20, "height": 20, "fill": True, "stroke": False}
    agree_word = {"text": "Agree", "x0": 35, "x1": 65, "top": 55, "bottom": 65}

    return FakeSyntheticPage(
        width=W, height=H,
        chars=underscore_chars,
        rects=[chk_rect],
        curves=[],
        words=[name_word, agree_word],
    )


class TestSyntheticPage(unittest.TestCase):
    def test_fixture_satisfies_the_protocol(self):
        self.assertIsInstance(_make_fixture(), DetectablePage)

    def test_r5_write_on_line_from_synthetic_chars(self):
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        r5 = [f for f in fields if f["rule"] == "R5"]
        self.assertEqual(len(r5), 1)
        self.assertEqual(r5[0]["type"], "text")
        self.assertEqual(r5[0]["label"], "Name")
        self.assertEqual(r5[0]["page"], 1)
        self.assertEqual(r5[0]["rect"], [43, 81.0, 71, 101.0])

    def test_r18_checkbox_from_synthetic_rects(self):
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        r18 = [f for f in fields if f["rule"] == "R18"]
        self.assertEqual(len(r18), 1)
        self.assertEqual(r18[0]["type"], "checkbox")
        self.assertEqual(r18[0]["label"], "Agree")
        self.assertEqual(r18[0]["page"], 1)
        self.assertEqual(r18[0]["rect"], [10, 30.0, 30, 50.0])

    def test_exactly_two_fields_total(self):
        # Guards against the fixture accidentally tripping an unrelated rule.
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        self.assertEqual(len(fields), 2)


if __name__ == "__main__":
    unittest.main()
