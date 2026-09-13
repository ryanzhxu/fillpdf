"""Regression test for a real, precisely-diagnosed detector gap found while
investigating `python -m eval.blind`'s "STRUCTURED BUT ZERO FIELDS" real
PDFs (see .autobuild/PROGRESS.md's "Needs human" entry on CHECK_GLYPHS).

`engine/detect/rules.py`'s `CHECK_GLYPHS = {"", ""}` recognizes
only the Webdings/Wingdings private-use-area checkbox glyphs. It does not
recognize U+2610 BALLOT BOX ("☐"), which several real form producers (fonts
observed: MS-Gothic, Segoe UI Symbol) use instead for real, labelled
checkboxes. Confirmed on real files: eval/corpus/real/9e5fa53418722365.pdf
page 1 ("☐ Existing outdoor lighting on the Project Site is compliant...")
and eval/corpus/real/2833d482ac6db6e1.pdf page 2 (~20 Somali-language
checkbox options) both get 0 detected fields today because of this. It also
already affects 9 files in the SCORED corpus (7 tuning, 2 holdout) that use
this same glyph -- see PROGRESS.md for the exact list and the measured
tuning/holdout F1 effect of adding it.

`engine/detect/rules.py` is locked to crash-safety-only edits per
AUTOPILOT.md this run, so this gap is not fixed here -- only pinned as an
`xfail` so a human fixing it (by adding "☐" to CHECK_GLYPHS) gets an
immediate, unambiguous signal: this test flips from xfail to a passing test,
and `strict=True` means it would otherwise show up as a hard failure
(XPASS) if the underlying behavior changed without this test being updated,
so it cannot go stale silently.

Run standalone with:  .venv/bin/python -m pytest tests/test_check_glyphs_gap.py
"""
import unittest

import pytest

from engine.detect.rules import detect as detect_page


class FakePage:
    """A hand-built DetectablePage, matching tests/test_synthetic_page.py's
    convention exactly."""

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
    """One page: a single U+2610 BALLOT BOX glyph immediately followed by an
    "Agree" label on the same baseline -- the same shape R1 already handles
    for the Webdings/Wingdings glyphs (see tests/test_synthetic_page.py's
    R18 fixture for the equivalent filled-square case)."""
    H, W = 100.0, 200.0
    glyph_char = {"text": "☐", "x0": 10, "x1": 20, "top": 50, "bottom": 60}
    label_word = {"text": "Agree", "x0": 25, "x1": 55, "top": 50, "bottom": 60}
    return FakePage(width=W, height=H, chars=[glyph_char], rects=[],
                    curves=[], words=[label_word])


class TestBallotBoxGlyphGap(unittest.TestCase):
    @pytest.mark.xfail(
        reason="CHECK_GLYPHS (engine/detect/rules.py) does not include "
                "U+2610 BALLOT BOX; rules.py is locked to crash-safety-only "
                "edits this run, see .autobuild/PROGRESS.md Needs human",
        strict=True,
    )
    def test_ballot_box_glyph_detected_as_checkbox(self):
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        r1 = [f for f in fields if f["rule"] == "R1"]
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0]["type"], "checkbox")
        self.assertEqual(r1[0]["label"], "Agree")


if __name__ == "__main__":
    unittest.main()
