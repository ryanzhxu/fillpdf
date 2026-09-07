"""Tests for R18's curve-drawn checkbox/radio candidates in engine.detect.

Some form producers draw a checkbox or radio button as a rounded-rectangle
Bezier path (pdfplumber object_type "curve") rather than a plain filled rect.
R18 only ever read page.rects, so every such box was silently invisible no
matter how it was captioned -- confirmed on 13 of 16 curve-heavy real fetched
PDFs that eval.blind flagged as "structured, zero fields found" (see
_rect_like_curves in engine/detect/rules.py).

Run standalone with:  .venv/bin/python -m pytest tests/test_curve_checkbox.py
"""
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from engine.detect import detect

# The real file that motivated this rule (see module docstring). Not part of
# this worktree's tracked fixtures -- eval/corpus/real is gitignored, real
# corpus PDFs live only in the main repo checkout -- so this test skips if it
# is not present rather than failing a clean checkout. Same pattern as
# eval/test_guards.py's REAL_BAD_PDF.
REAL_MOTIVATING_PDF = "/Users/ryan.xu/Developer/formfill/eval/corpus/real/1bdaa5e8fd5eaace.pdf"


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


def _radio_pair_pdf():
    """A Yes/No question whose two option boxes are rounded-rect curves,
    not plain rects -- the shape R18 previously could not see at all."""
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 700
    c.drawString(72, y, "Do you smoke?")
    c.roundRect(200, y - 3, 16, 16, 3, stroke=0, fill=1)
    c.drawString(222, y, "Yes")
    c.roundRect(270, y - 3, 16, 16, 3, stroke=0, fill=1)
    c.drawString(292, y, "No")
    c.showPage()
    c.save()
    return buf.getvalue()


def _lone_icon_pdf():
    """A same-size rounded-rect curve sits near a Yes/No group, but a THIRD,
    same-size curve sits far down the page next to unrelated boilerplate
    text -- a phone/service icon badge, the real false positive measured on
    the tuning corpus while building this rule (an "Translating and
    Interpreting Service" icon). It must not be read as a checkbox just
    because a same-sized pair happens to exist elsewhere on the page."""
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 700
    c.drawString(72, y, "Do you smoke?")
    c.roundRect(200, y - 3, 16, 16, 3, stroke=0, fill=1)
    c.drawString(222, y, "Yes")
    c.roundRect(270, y - 3, 16, 16, 3, stroke=0, fill=1)
    c.drawString(292, y, "No")
    c.roundRect(72, 100, 16, 16, 3, stroke=0, fill=1)
    c.drawString(94, 104, "Call us: 1800 000 000")
    c.showPage()
    c.save()
    return buf.getvalue()


def _duplicate_paint_icon_pdf():
    """One icon, painted twice at IDENTICAL coordinates (a duplicate fill
    pass -- also measured on the real tuning-corpus false positive). The
    repeated paint must not count as its own sibling."""
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.roundRect(72, 700, 16, 16, 3, stroke=0, fill=1)
    c.roundRect(72, 700, 16, 16, 3, stroke=0, fill=1)
    c.drawString(94, 704, "Call us: 1800 000 000")
    c.showPage()
    c.save()
    return buf.getvalue()


class TestCurveCheckbox(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_curve_drawn_radio_pair_is_detected(self):
        path = _write(self.tmp, "radio.pdf", _radio_pair_pdf())
        out = detect(path)
        labels = sorted(f["label"] for f in out["fields"])
        self.assertEqual(labels, ["No", "Yes"])
        self.assertTrue(all(f["type"] == "checkbox" for f in out["fields"]))

    def test_lone_same_size_icon_elsewhere_on_page_is_not_a_checkbox(self):
        path = _write(self.tmp, "icon.pdf", _lone_icon_pdf())
        out = detect(path)
        labels = sorted(f["label"] for f in out["fields"])
        self.assertEqual(labels, ["No", "Yes"])  # the icon must not appear

    def test_duplicate_painted_icon_is_not_a_checkbox(self):
        path = _write(self.tmp, "dup_icon.pdf", _duplicate_paint_icon_pdf())
        out = detect(path)
        self.assertEqual(out["fields"], [])

    def test_real_fixture_field_count_is_unchanged(self):
        # safer.pdf's checkboxes are plain rects; this rule must not alter it.
        out = detect("fixtures/safer.pdf")
        self.assertEqual(len(out["fields"]), 222)

    @unittest.skipUnless(Path(REAL_MOTIVATING_PDF).exists(), "real corpus not present in this worktree")
    def test_real_fetched_form_finds_all_20_curve_drawn_options(self):
        # Hand-verified against the PDF itself (a 4-page ACC-style
        # application): page 2 draws a title pick-list (Mr/Mrs/Ms/Miss), two
        # Yes/No questions, an ID-confirmation pair, and a gender pick-list
        # (Male/Female/Gender diverse) as rounded-rect curves; page 3 draws
        # three more Yes/No pairs the same way. Before this rule existed the
        # file scored 0 fields.
        out = detect(REAL_MOTIVATING_PDF)
        fields = out["fields"]
        self.assertEqual(len(fields), 20)
        self.assertTrue(all(f["type"] == "checkbox" and f["rule"] == "R18" for f in fields))
        labels = {f["label"] for f in fields}
        self.assertEqual(
            labels,
            {"Mr", "Mrs", "Ms", "Miss", "Yes", "No",
             "The name I wrote in Question 1", "The name I wrote in Question 2", "Other",
             "Male", "Female", "Gender diverse"},
        )
        self.assertEqual(sum(1 for f in fields if f["page"] == 2), 14)
        self.assertEqual(sum(1 for f in fields if f["page"] == 3), 6)


if __name__ == "__main__":
    unittest.main()
