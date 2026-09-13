"""Tests engine/scan_cv/backend.py's assembly of render.py + ocr.py +
pipeline.py into a real `page_backend`, wired straight into `detect()`.

Run standalone with:  .venv/bin/python -m pytest tests/scan_cv/test_backend.py
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pdfplumber
import pypdf

from engine.detect import detect
from engine.detect.page_protocol import DetectablePage
from engine.scan_cv.backend import make_cv_ocr_backend

SAMPLE = Path(__file__).parent.parent.parent / "eval" / "scan_cv" / "samples" / "agm_proxy_form.pdf"
_CORPUS_REAL = Path(__file__).parent.parent.parent / "eval" / "corpus" / "real"
COURT_FORM_1 = _CORPUS_REAL / "5180e9e5652573e2.pdf"
COURT_FORM_2 = _CORPUS_REAL / "d5a49cf46d75829e.pdf"


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

    def test_assembled_page_drives_a_real_checkbox_rule(self):
        # Same purpose as test_assembled_page_drives_a_real_rule above, but
        # for the OTHER real rule a scan_cv-assembled page can feed: R18,
        # which fires on a fill=True/stroke=False square in the 18-32pt band
        # (engine/scan_cv/checkboxes.py's CHK_MIN_PT/CHK_MAX_PT match R18's
        # own R18_CHK_MIN/R18_CHK_MAX exactly, by design -- see checkboxes.py's
        # module docstring). No prior test ran a CV-detected checkbox rect
        # through the real, unmodified rules.py end to end; test_pipeline.py
        # only checks detect_lines_and_boxes() returns the right rect SHAPE,
        # and the real end-to-end tests below never exercise this path since
        # none of the 3 real scanned corpus files happen to contain a
        # detectable checkbox at production DPI (see PROGRESS.md).
        fake_rects = [{"x0": 10, "x1": 30, "top": 10, "bottom": 30,
                       "width": 20, "height": 20, "fill": True, "stroke": False}]
        fake_words = [{"x0": 35, "x1": 55, "top": 15, "bottom": 25, "text": "Yes"}]
        fake_chars = []
        with mock.patch("engine.scan_cv.backend.render_page_to_bitmap", return_value="bitmap"), \
             mock.patch("engine.scan_cv.backend.detect_lines_and_boxes", return_value=fake_rects), \
             mock.patch("engine.scan_cv.backend.ocr_page", return_value=(fake_words, fake_chars)):
            backend = make_cv_ocr_backend(SAMPLE)
            with pdfplumber.open(SAMPLE) as pdf:
                page = backend(pdf.pages[0], 1)
        from engine.detect.rules import detect as detect_page
        fields, _carry = detect_page(page, pno=1, carry_in=None)
        r18 = [f for f in fields if f["rule"] == "R18"]
        self.assertEqual(len(r18), 1)
        self.assertEqual(r18[0]["type"], "checkbox")
        self.assertEqual(r18[0]["label"], "Yes")

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

    def test_agm_proxy_form_ignores_boxed_headings_and_the_signature_line(self):
        # Regression test for three false positives a person found in
        # production, all real text on this page that is not a field:
        # "PROXY FORM" (the page's own title, printed inside a bordered
        # box) and "*Please attach voting instructions for your proxy if
        # required*" (a single-line bordered instruction strip) were each
        # read as a blank write-on line because the scanned page had no
        # vertical rects at all -- see engine/scan_cv/lines.py's
        # detect_verticals(). "Signed;" was a manufactured R2 cell whose
        # top edge was actually that instruction strip's own bottom
        # border, paired with the real "Owner" signature line below it --
        # see engine.detect.rules.SIGNATURE, broadened to also match a
        # bare "signed" rather than only "signature".
        doc = detect(str(SAMPLE), page_backend=make_cv_ocr_backend(SAMPLE))
        labels = [f["label"] for f in doc["fields"]]
        self.assertNotIn("PROXY FORM", labels)
        self.assertNotIn("Signed;", labels)
        for label in labels:
            self.assertNotIn("voting instructions", label)
        # The real fields on this page must survive all three fixes.
        self.assertIn("appoint", labels)
        self.assertIn("Owner", labels)

    def test_detect_end_to_end_at_non_default_dpi(self):
        # make_cv_ocr_backend(pdf_path, dpi=...) exposes dpi as a real,
        # already-public parameter, but every other end-to-end test in this
        # file calls it with the default (300) only -- so the render.py ->
        # pipeline.py -> ocr.py coordinate chain has never actually been
        # checked at any other scale, despite dpi mismatches being the exact
        # class of bug found repeatedly elsewhere in this backend (the
        # missing --dpi fix, the CropBox/MediaBox scale note). Two prior end-
        # to-end fixtures full runs already timed dpi=300 at ~1-2s; the
        # values below span a plausible real range without adding much
        # wall-clock time to the suite.
        for dpi in (150, 400):
            with self.subTest(dpi=dpi):
                doc = detect(str(SAMPLE), page_backend=make_cv_ocr_backend(SAMPLE, dpi=dpi))
                self.assertEqual(doc["notice"]["code"], "ocr_assisted")
                self.assertGreater(len(doc["fields"]), 0)
                pages_by_number = {p["page"]: p for p in doc["pages"]}
                for f in doc["fields"]:
                    page = pages_by_number[f["page"]]
                    x0, y0, x1, y1 = f["rect"]
                    self.assertGreaterEqual(x0, 0)
                    self.assertGreaterEqual(y0, 0)
                    self.assertLessEqual(x1, page["width"])
                    self.assertLessEqual(y1, page["height"])

    def test_detect_end_to_end_on_multi_page_scanned_pdf(self):
        # Every other end-to-end test in this file (and every prior
        # AUTOPILOT.md pass's manual verification) only ever exercised
        # AGM_PROXY_FORM, which is a single page -- so a per-page state bug
        # (e.g. a stale `carry` from R5's column-tracking leaking across
        # pages, or an off-by-one in render_page_to_bitmap's 0-indexed
        # `page_number` vs detect()'s 1-indexed `page_backend` calls) would
        # never have been caught. Build a 2-page scanned PDF (the same page
        # twice) at runtime -- not a new committed eval/scan_cv/samples file,
        # since AUTOPILOT.md's part 4 names only agm_proxy_form.pdf as the
        # validation target -- and confirm the real backend produces fields
        # on BOTH pages with in-bounds rects, exactly the same mechanical bar
        # test_detect_end_to_end_on_agm_proxy_form already holds page 1 to.
        reader = pypdf.PdfReader(str(SAMPLE))
        writer = pypdf.PdfWriter()
        writer.add_page(reader.pages[0])
        writer.add_page(reader.pages[0])
        with tempfile.TemporaryDirectory() as tmpdir:
            multi_page = Path(tmpdir) / "two_page_scan.pdf"
            with open(multi_page, "wb") as f:
                writer.write(f)

            doc = detect(str(multi_page), page_backend=make_cv_ocr_backend(multi_page))

        self.assertEqual(doc["notice"]["code"], "ocr_assisted")
        fields_by_page = {1: [], 2: []}
        for f in doc["fields"]:
            fields_by_page[f["page"]].append(f)
        self.assertGreater(len(fields_by_page[1]), 0)
        self.assertGreater(len(fields_by_page[2]), 0)
        pages_by_number = {p["page"]: p for p in doc["pages"]}
        for f in doc["fields"]:
            page = pages_by_number[f["page"]]
            x0, y0, x1, y1 = f["rect"]
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page["width"])
            self.assertLessEqual(y1, page["height"])
            self.assertEqual(f["origin"], "ocr")

    def test_detect_end_to_end_on_mixed_real_and_scanned_document(self):
        # Every prior end-to-end test builds its multi-page fixture by
        # duplicating the SAME page (either AGM_PROXY_FORM alone, or that
        # page twice), so an off-by-one between render_page_to_bitmap's
        # 0-indexed page_number and detect()'s 1-indexed page_backend calls
        # could not have been caught: a wrongly-shifted index would just
        # render the identical neighboring page again. A real-world scanned
        # PDF is often mixed (e.g. a text cover page ahead of a scanned
        # form), so build page 1 from fixtures/safer.pdf (a real text page,
        # known to have detectable fields) and page 2 from the scanned
        # AGM_PROXY_FORM, and confirm each page's fields come from the
        # right source: page 1 detected normally, page 2 via real OCR with
        # AGM-specific content (proving the backend rendered page 2, not
        # page 1, for the scanned page).
        text_reader = pypdf.PdfReader("fixtures/safer.pdf")
        scan_reader = pypdf.PdfReader(str(SAMPLE))
        writer = pypdf.PdfWriter()
        writer.add_page(text_reader.pages[1])  # a safer.pdf page with real fields
        writer.add_page(scan_reader.pages[0])
        with tempfile.TemporaryDirectory() as tmpdir:
            mixed = Path(tmpdir) / "mixed.pdf"
            with open(mixed, "wb") as f:
                writer.write(f)

            doc = detect(str(mixed), page_backend=make_cv_ocr_backend(mixed))

        self.assertEqual(doc["notice"]["code"], "ocr_assisted")
        fields_by_page = {1: [], 2: []}
        for f in doc["fields"]:
            fields_by_page[f["page"]].append(f)

        self.assertGreater(len(fields_by_page[1]), 0)
        self.assertTrue(all(f["origin"] == "detected" for f in fields_by_page[1]))

        self.assertGreater(len(fields_by_page[2]), 0)
        self.assertTrue(all(f["origin"] == "ocr" for f in fields_by_page[2]))
        page2_labels = " ".join(f.get("label", "") for f in fields_by_page[2])
        self.assertIn("appoint", page2_labels)

    @unittest.skipUnless(COURT_FORM_1.exists() and COURT_FORM_2.exists(),
                          "real corpus not present in this worktree")
    def test_detect_end_to_end_on_real_world_scanned_court_forms(self):
        # Every other end-to-end test in this file exercises AGM_PROXY_FORM
        # or synthetic fixtures built from it -- never a scanned document the
        # backend hadn't already been tuned against. eval/corpus/real/ holds
        # two such documents no other test or eval/blind.py's own
        # _flat_real_pdfs() filter has ever run through detect(): real
        # Illinois court "Civil Law Citation and Complaint" scans, manifest
        # verdict "scan", 0 extractable chars. Extract one real page from
        # each (hand-picked as a page the full document actually places
        # fields on) rather than the full 16-17 page document, to keep the
        # test fast, and hold the real backend to the same mechanical bar as
        # every other end-to-end test here: no crash, ocr_assisted notice, a
        # non-empty fields list, every rect in-page-bounds. Not a claim the
        # fields are correct -- there is no hand-verified ground truth for
        # either file. Guarded by skipUnless because eval/corpus/ is
        # gitignored (36MB of stripped real forms) and absent on CI and
        # fresh worktrees -- see tests/test_r5b_underline_gap.py for the same
        # pattern.
        cases = [
            (COURT_FORM_1, 13),  # page 14: fields present
            (COURT_FORM_2, 4),  # page 5: fields present
        ]
        for source_path, page_index in cases:
            with self.subTest(source=str(source_path), page_index=page_index):
                reader = pypdf.PdfReader(str(source_path))
                writer = pypdf.PdfWriter()
                writer.add_page(reader.pages[page_index])
                with tempfile.TemporaryDirectory() as tmpdir:
                    single_page = Path(tmpdir) / "one_page.pdf"
                    with open(single_page, "wb") as f:
                        writer.write(f)

                    doc = detect(str(single_page), page_backend=make_cv_ocr_backend(single_page))

                self.assertEqual(doc["notice"]["code"], "ocr_assisted")
                self.assertGreater(len(doc["fields"]), 0)
                pages_by_number = {p["page"]: p for p in doc["pages"]}
                for f in doc["fields"]:
                    page = pages_by_number[f["page"]]
                    x0, y0, x1, y1 = f["rect"]
                    self.assertGreaterEqual(x0, 0)
                    self.assertGreaterEqual(y0, 0)
                    self.assertLessEqual(x1, page["width"])
                    self.assertLessEqual(y1, page["height"])

    @unittest.skipUnless(COURT_FORM_1.exists(), "real corpus not present in this worktree")
    def test_detect_end_to_end_on_full_multi_page_real_scan(self):
        # The previous test extracts one hand-picked page from each real
        # court-form scan to keep the suite fast, which proves correctness
        # on a real page but never exercises AUTOPILOT.md part 4's "runs
        # without crashing or timing out" bar over a genuinely large
        # real-world document -- every OTHER multi-page test in this file
        # uses a 2-page fixture built by duplicating a single page. Run the
        # full, unmodified 17-page scan (all pages OCR'd and CV'd for real,
        # ~24s) straight through detect() and check the same mechanical bar
        # as every other end-to-end test: no crash, ocr_assisted notice, a
        # non-empty fields list, every rect in-page-bounds. Not a claim the
        # fields are correct -- there is no hand-verified ground truth for
        # this file.
        doc = detect(str(COURT_FORM_1), page_backend=make_cv_ocr_backend(COURT_FORM_1))
        self.assertEqual(doc["notice"]["code"], "ocr_assisted")
        self.assertGreater(len(doc["fields"]), 0)
        pages_by_number = {p["page"]: p for p in doc["pages"]}
        for f in doc["fields"]:
            page = pages_by_number[f["page"]]
            x0, y0, x1, y1 = f["rect"]
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page["width"])
            self.assertLessEqual(y1, page["height"])

    @unittest.skipUnless(COURT_FORM_2.exists(), "real corpus not present in this worktree")
    def test_detect_end_to_end_on_full_multi_page_real_scan_second_form(self):
        # The full-multi-page check above only ever ran COURT_FORM_1 --
        # COURT_FORM_2 (a different real 16-page court-form scan) has only
        # ever had a single hand-picked page extracted and tested, never the
        # complete document. Run it whole (all pages OCR'd and CV'd for
        # real, ~20s) through the same mechanical bar as every other
        # end-to-end test here: no crash, ocr_assisted notice, a non-empty
        # fields list, every rect in-page-bounds. Not a claim the fields are
        # correct -- there is no hand-verified ground truth for this file.
        doc = detect(str(COURT_FORM_2), page_backend=make_cv_ocr_backend(COURT_FORM_2))
        self.assertEqual(doc["notice"]["code"], "ocr_assisted")
        self.assertGreater(len(doc["fields"]), 0)
        pages_by_number = {p["page"]: p for p in doc["pages"]}
        for f in doc["fields"]:
            page = pages_by_number[f["page"]]
            x0, y0, x1, y1 = f["rect"]
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page["width"])
            self.assertLessEqual(y1, page["height"])


if __name__ == "__main__":
    unittest.main()
