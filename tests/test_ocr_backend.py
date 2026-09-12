"""Tests for detect()'s optional page_backend hook
(docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md).

Reuses the exact FakeSyntheticPage fixture from tests/test_synthetic_page.py
as a stub backend's return value -- this test file is about detect()'s own
orchestration (notice precedence, origin tagging, ID assignment), not about
re-proving the rules work on synthetic geometry (that's test_synthetic_page.py).

Run standalone with:  .venv/bin/python -m pytest tests/test_ocr_backend.py
"""
import io
import unittest

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from engine.detect import detect
from tests.test_synthetic_page import _make_fixture


def _image_only_pdf(n_pages=1):
    """A PDF whose pages are each a single page-filling image, no text.

    Identical to tests/test_scanned.py's own helper of the same name --
    duplicated rather than imported, matching that file's existing pattern
    of each test file owning its small PDF-building helpers.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    img = ImageReader(Image.new("RGB", (600, 800), (235, 235, 235)))
    for _ in range(n_pages):
        c.drawImage(img, 0, 0, width=w, height=h)
        c.showPage()
    c.save()
    return buf.getvalue()


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


class TestOcrBackend(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_no_backend_preserves_todays_scanned_notice(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path)
        self.assertEqual(out["notice"]["code"], "scanned")
        self.assertEqual(out["fields"], [])

    def test_backend_produces_ocr_tagged_fields(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        self.assertEqual(len(out["fields"]), 2)
        for f in out["fields"]:
            self.assertEqual(f["origin"], "ocr")

    def test_backend_sets_ocr_assisted_notice(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        self.assertEqual(out["notice"]["code"], "ocr_assisted")

    def test_backend_declining_falls_back_to_scanned(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: None)
        self.assertEqual(out["notice"]["code"], "scanned")
        self.assertEqual(out["fields"], [])

    def test_backend_ids_follow_the_normal_scheme(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        ids = {f["id"] for f in out["fields"]}
        self.assertEqual(ids, {"p1_name", "p1_chk"})

    def test_real_text_pdf_is_never_sent_to_the_backend(self):
        # A backend that always raises must never be called on a real,
        # non-scanned page -- _page_is_scanned() gates every call.
        def _boom(pg, i):
            raise AssertionError("backend called on a non-scanned page")
        out = detect("fixtures/safer.pdf", page_backend=_boom)
        self.assertNotIn("notice", out)


if __name__ == "__main__":
    unittest.main()
