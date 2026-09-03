"""Tests for the scanned / image-only PDF guard in engine.detect.

A public app will certainly be handed a scan. Before this guard the detector
returned an empty result with no explanation. It must instead attach an honest
`notice` the UI can show, without ever flagging a real text-layer form.

Run standalone with:  .venv/bin/python -m pytest tests/test_scanned.py
"""
import io
import unittest

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from engine.detect import detect


def _image_only_pdf(n_pages=1):
    """A PDF whose pages are each a single page-filling image, no text."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    img = ImageReader(Image.new("RGB", (600, 800), (235, 235, 235)))
    for _ in range(n_pages):
        c.drawImage(img, 0, 0, width=w, height=h)
        c.showPage()
    c.save()
    return buf.getvalue()


def _text_pdf():
    """A flat form-like PDF with a normal printed text layer, no image."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for line, y in enumerate(range(720, 300, -24)):
        c.drawString(72, y, f"Field label {line}: ____________________")
    c.showPage()
    c.save()
    return buf.getvalue()


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


class TestScannedGuard(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_image_only_pdf_gets_a_scanned_notice(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path)
        self.assertIn("notice", out)
        self.assertEqual(out["notice"]["code"], "scanned")
        self.assertTrue(out["notice"]["message"].strip())

    def test_multi_page_scan_is_flagged(self):
        path = _write(self.tmp, "scan3.pdf", _image_only_pdf(n_pages=3))
        out = detect(path)
        self.assertEqual(out.get("notice", {}).get("code"), "scanned")

    def test_ordinary_text_form_is_not_flagged(self):
        path = _write(self.tmp, "text.pdf", _text_pdf())
        out = detect(path)
        self.assertNotIn("notice", out)

    def test_real_fixture_is_not_flagged(self):
        # safer.pdf carries a real text layer; it must never be called a scan.
        out = detect("fixtures/safer.pdf")
        self.assertNotIn("notice", out)

    def test_notice_does_not_change_the_base_shape(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path)
        for key in ("version", "source", "pages", "fields"):
            self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
