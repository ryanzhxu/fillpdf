"""Tests for R5b's ON_RULE_TOL_Y guard in engine.detect.

_qualified_write_on_lines() rejects a thin rule as decorative -- not a
blank write-on line -- when text sitting on the rule's own baseline
covers a large fraction of the rule's own width. "Sitting on the rule's
own baseline" is decided by ON_RULE_TOL_Y: a word only counts toward that
coverage check if its bottom sits within ON_RULE_TOL_Y points of the
rule's top. The cut used to be exactly 3, but a real fetched form,
eval/corpus/real/9e5fa53418722365.pdf, draws its heading-underline gap at
a consistent 2.93-3.07pt across 13 separate headings -- half the same
shape landing just inside the old cut, half just outside it by a few
hundredths of a point. The ones that landed outside were never counted as
"covered", so the decorative underline under headings like "PROHIBITED
LIGHTING" was treated as a genuine blank line, and R5b filled in its
caption using words below it -- the next unrelated checklist item -- as a
bogus label.

Run standalone with:  .venv/bin/python -m pytest tests/test_r5b_underline_gap.py
"""
import io
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import pdfplumber

from engine.detect import detect

PAGE_H = 792

# The real file that motivated this fix (see module docstring). Not part of
# this worktree's tracked fixtures -- eval/corpus/real is gitignored -- so
# this test skips if it is not present rather than failing a clean checkout.
REAL_MOTIVATING_PDF = "/Users/ryan.xu/Developer/formfill/eval/corpus/real/9e5fa53418722365.pdf"


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


def _text_bottom(text, font_size=9):
    """The top-down pdfplumber bottom of `text` drawn at (60, 700)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", font_size)
    c.drawString(60, 700, text)
    c.showPage()
    c.save()
    with pdfplumber.open(io.BytesIO(buf.getvalue())) as p:
        return p.pages[0].extract_words()[0]["bottom"]


def _heading_underline_pdf(gap):
    """A heading with a decorative underline `gap` points below its own
    bottom, plus an unrelated checklist line further down -- the exact
    shape of eval/corpus/real/9e5fa53418722365.pdf's "PROHIBITED LIGHTING"
    section, whose underline this rule must not mistake for a blank
    write-on line captioned by the next unrelated line of text."""
    heading_bottom = _text_bottom("PROHIBITED LIGHTING")
    rule_top = heading_bottom + gap
    rect_y = PAGE_H - (rule_top + 0.5)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 9)
    c.drawString(60, 700, "PROHIBITED LIGHTING")
    c.rect(60, rect_y, 150, 0.5, stroke=0, fill=1)
    c.drawString(60, 680, "Drop-down lenses.")
    c.showPage()
    c.save()
    return buf.getvalue()


def _captioned_writeon_line_pdf():
    """A genuine write-on line captioned normally, to its left ("Name"),
    plus an unrelated short word sitting 3.2pt above the SAME rule far to
    the right -- covering only a sliver of it. Must still be found: real
    left-of-line captions never overlap the rule's own x-range, and a
    stray word covering well under the 35% cut must not suppress it
    either, regardless of how close it sits."""
    name_bottom = _text_bottom("Name")
    rule_top = name_bottom - 1
    rect_y = PAGE_H - (rule_top + 0.5)
    heading_y = PAGE_H - (rule_top - 3.2) - 9
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 9)
    c.drawString(60, 700, "Name")
    c.rect(95, rect_y, 205, 0.5, stroke=0, fill=1)
    c.drawString(250, heading_y, "Hi")
    c.showPage()
    c.save()
    return buf.getvalue()


class TestR5bUnderlineGapGuard(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_heading_underline_just_outside_the_old_cut_is_not_a_field(self):
        path = _write(self.tmp, "heading.pdf", _heading_underline_pdf(3.2))
        out = detect(path)
        self.assertEqual(out["fields"], [])

    def test_heading_underline_well_inside_the_old_cut_is_not_a_field(self):
        path = _write(self.tmp, "heading_tight.pdf", _heading_underline_pdf(2.9))
        out = detect(path)
        self.assertEqual(out["fields"], [])

    def test_genuine_captioned_write_on_line_still_found(self):
        path = _write(self.tmp, "writeon.pdf", _captioned_writeon_line_pdf())
        out = detect(path)
        labels = [f["label"] for f in out["fields"]]
        self.assertEqual(labels, ["Name"])

    def test_real_fixture_field_count_is_unchanged(self):
        out = detect("fixtures/safer.pdf")
        self.assertEqual(len(out["fields"]), 222)

    @unittest.skipUnless(Path(REAL_MOTIVATING_PDF).exists(), "real corpus not present in this worktree")
    def test_real_fetched_form_no_longer_has_the_heading_underline_fields(self):
        # Hand-verified: reverting just the tolerance (ON_RULE_TOL_Y 3.5 ->
        # 3.0) reproduces exactly these 4 bogus fields on this file today.
        # This does not claim the file has zero REAL fields -- its
        # checkboxes use a glyph R1 does not yet recognize, a separate,
        # documented, still-open gap -- only that these specific
        # confirmed-bogus fields do not reappear.
        out = detect(REAL_MOTIVATING_PDF)
        bogus_fragments = (
            "Drop-down lenses.",
            "The Site Plan shall demonstrate how the development compli",
            "The applicant shall submit a Lighting Plan for new commerc",
            "(Check at least one of the following",
        )
        for f in out["fields"]:
            label = f.get("label") or ""
            for bad in bogus_fragments:
                self.assertNotIn(bad, label, f"bogus label reappeared: {label!r}")


if __name__ == "__main__":
    unittest.main()
