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

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from engine.detect import detect


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


if __name__ == "__main__":
    unittest.main()
