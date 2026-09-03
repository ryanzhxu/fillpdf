"""Tests for the no-fields guard in engine.detect.

A real-world PDF that is not a scan can still yield zero detected fields --
either because it genuinely is not a form (an instructions sheet, a cover
letter) or because its layout is one none of the rules in rules.py reach. In
both cases the detector used to return an empty result with no explanation,
the same "silently does nothing" gap the scanned guard closes for scans.

Run standalone with:  .venv/bin/python -m pytest tests/test_no_fields.py
"""
import unittest

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io

from engine.detect import detect


def _prose_pdf():
    """A normal text-layer PDF with no blanks, checkboxes, or ruled table --
    plain prose, the shape of an instructions sheet or a notice."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    lines = [
        "This document explains the application process in general terms.",
        "Please read every section carefully before you begin.",
        "No response is required from the reader on this page.",
        "Further instructions continue on the following pages of the packet.",
    ]
    for i, line in enumerate(lines):
        c.drawString(72, 700 - i * 20, line)
    c.showPage()
    c.save()
    return buf.getvalue()


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


class TestNoFieldsGuard(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_prose_pdf_with_no_fields_gets_a_no_fields_notice(self):
        path = _write(self.tmp, "prose.pdf", _prose_pdf())
        out = detect(path)
        self.assertEqual(len(out["fields"]), 0)
        self.assertIn("notice", out)
        self.assertEqual(out["notice"]["code"], "no_fields")
        self.assertTrue(out["notice"]["message"].strip())

    def test_real_fixture_with_fields_is_not_flagged(self):
        # safer.pdf detects real fields, so it must never get this notice.
        out = detect("fixtures/safer.pdf")
        self.assertGreater(len(out["fields"]), 0)
        self.assertNotIn("notice", out)

    def test_no_fields_and_scanned_are_mutually_exclusive(self):
        # A scanned PDF also has zero fields, but the more specific "scanned"
        # notice must win -- a person should not see two contradictory
        # explanations for the same blank page.
        import io as _io
        from PIL import Image
        from reportlab.lib.utils import ImageReader

        buf = _io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter
        img = ImageReader(Image.new("RGB", (600, 800), (235, 235, 235)))
        c.drawImage(img, 0, 0, width=w, height=h)
        c.showPage()
        c.save()
        path = _write(self.tmp, "scan.pdf", buf.getvalue())
        out = detect(path)
        self.assertEqual(out["notice"]["code"], "scanned")


if __name__ == "__main__":
    unittest.main()
