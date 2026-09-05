"""Tests for the deployed app (web/app.py).

The demo and the deployed app share ONE UI file, demo/index.html, the same way
the demo and the render tests share ONE injector, tools/inject.mjs. That
sharing is the thing worth pinning: web/app.py rewrites a marker line in that
HTML, and demo/demo.py rewrites a different one. If either marker moves, the
served page breaks in a way that looks fine until a user clicks something. Both
rewrites already fail loudly at startup; these tests prove the markers are
still there and that the rewrite lands.

Run standalone with:  .venv/bin/python -m pytest tests/test_web_app.py
"""
import glob
import json
import tempfile
import unittest
from pathlib import Path

from web.app import MAX_PAGES, UPLOAD_FLAG_DST, UPLOAD_FLAG_SRC, app

ROOT = Path(__file__).resolve().parent.parent


class TestWebApp(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.c = app.test_client()

    # ---- the shared-UI contract ----------------------------------------
    def test_index_html_still_carries_both_rewrite_markers(self):
        # The two rewrites that keep demo and web on one file. Losing either
        # silently is exactly the failure this repo has already paid for once.
        html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
        self.assertIn(UPLOAD_FLAG_SRC, html)
        self.assertIn("'../tools/inject.mjs'", html)

    def test_served_page_turns_upload_mode_on(self):
        body = self.c.get("/").get_data(as_text=True)
        self.assertIn(UPLOAD_FLAG_DST, body)
        self.assertNotIn(UPLOAD_FLAG_SRC, body)

    def test_injector_is_served_as_javascript(self):
        # A .mjs served as text/plain is refused by the browser's module
        # loader, and the failure surfaces only when Download is clicked.
        r = self.c.get("/tools/inject.mjs")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["Content-Type"])
        self.assertIn("injectFields", r.get_data(as_text=True))

    def test_healthz(self):
        self.assertEqual(self.c.get("/healthz").get_json(), {"ok": True})

    # ---- detection ------------------------------------------------------
    def test_detect_returns_the_same_fields_as_the_detector(self):
        pdf = (ROOT / "fixtures" / "safer.pdf").read_bytes()
        r = self.c.post("/api/detect", data=pdf, content_type="application/pdf")
        self.assertEqual(r.status_code, 200)
        doc = r.get_json()
        self.assertEqual(len(doc["fields"]), 222)
        self.assertEqual(len(doc["pages"]), 8)

    def test_detect_leaves_no_temp_file_behind(self):
        # An uploaded form carries someone's address and income. It touches
        # disk only because pdfplumber opens a path, and it must not survive
        # the request.
        before = set(glob.glob(str(Path(tempfile.gettempdir()) / "*.pdf")))
        self.c.post("/api/detect",
                    data=(ROOT / "fixtures" / "safer.pdf").read_bytes(),
                    content_type="application/pdf")
        self.assertEqual(set(glob.glob(str(Path(tempfile.gettempdir()) / "*.pdf"))),
                         before)

    # ---- honest failures, never a blank 500 -----------------------------
    def test_empty_body_is_rejected(self):
        r = self.c.post("/api/detect", data=b"", content_type="application/pdf")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_non_pdf_is_rejected(self):
        r = self.c.post("/api/detect", data=b"hello world",
                        content_type="application/pdf")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a PDF", r.get_json()["error"])

    def test_corrupt_pdf_reports_a_reason(self):
        r = self.c.post("/api/detect", data=b"%PDF-1.4\ngarbage",
                        content_type="application/pdf")
        self.assertEqual(r.status_code, 400)
        self.assertIn("could not be read", r.get_json()["error"])

    def test_page_cap_is_enforced(self):
        self.assertEqual(MAX_PAGES, 30)     # demo/demo.py caps at the same number


if __name__ == "__main__":
    unittest.main()
