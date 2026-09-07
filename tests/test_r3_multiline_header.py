"""Tests for R3's multi-line-header guard in engine.detect.

grid_cells() only splits a column into separate cells where a real
horizontal rule exists. A producer that draws per-item checkboxes but no
full-width rule between them leaves several unrelated text lines merged
into ONE tall cell. R3 used to read that cell's words sorted purely by x0
and, if the joined string happened to land in its ordinary 2-60-char
header-length window, accept it as a column header and hand it to an
unrelated blank row below as a bogus field label -- confirmed on a real
fetched form, eval/corpus/real/ebe4beb36bad2b41.pdf (see _row_gap_exceeds
in engine/detect/rules.py).

Run standalone with:  .venv/bin/python -m pytest tests/test_r3_multiline_header.py
"""
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from engine.detect import detect

# The real file that motivated this fix (see module docstring). Not part of
# this worktree's tracked fixtures -- eval/corpus/real is gitignored -- so
# this test skips if it is not present rather than failing a clean checkout.
REAL_MOTIVATING_PDF = "/Users/ryan.xu/Developer/formfill/eval/corpus/real/ebe4beb36bad2b41.pdf"


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


def _one_column_table(lines):
    """A single-column ruled table (thin filled rects, the same convention
    grid_cells() reads elsewhere): a top-of-table rule at y=700, a rule at
    y=600 separating the header cell from the row below, and a rule at
    y=550 closing that row. `lines` are (y, text) placed inside the header
    cell; the row below is left blank so a header, if wrongly set, is the
    only way it can ever gain a field."""
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.rect(50, 550, 1, 150, stroke=0, fill=1)
    c.rect(250, 550, 1, 150, stroke=0, fill=1)
    c.rect(50, 700, 200, 1, stroke=0, fill=1)
    c.rect(50, 600, 200, 1, stroke=0, fill=1)
    c.rect(50, 550, 200, 1, stroke=0, fill=1)
    c.setFont("Helvetica", 9)
    for y, text in lines:
        c.drawString(60, y, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _merged_checklist_pdf():
    """Four short, unrelated checklist-style lines ~20pt apart (line
    height 9pt, gap ratio ~2.2) -- the shape that collapsed into one cell
    on the motivating real file and produced a garbled label."""
    return _one_column_table([
        (685, "Alpha"), (665, "Beta item"), (645, "Gamma"), (625, "Delta"),
    ])


def _wrapped_header_pdf():
    """A real 3-line wrapped header, tightly spaced (~12pt apart, ratio
    ~1.3) like fixtures/safer.pdf's "Relationship" / "to" / "Applicant" --
    must still be read as one header and passed to the row below."""
    return _one_column_table([
        (685, "Relationship"), (673, "to"), (661, "Applicant"),
    ])


class TestR3MultilineHeaderGuard(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_merged_unruled_checklist_rows_do_not_become_a_header(self):
        path = _write(self.tmp, "merged.pdf", _merged_checklist_pdf())
        out = detect(path)
        self.assertEqual(out["fields"], [])

    def test_tightly_wrapped_header_still_reaches_its_field(self):
        path = _write(self.tmp, "wrapped.pdf", _wrapped_header_pdf())
        out = detect(path)
        labels = [f["label"] for f in out["fields"]]
        self.assertEqual(labels, ["Relationship to Applicant"])

    def test_real_fixture_field_count_is_unchanged(self):
        out = detect("fixtures/safer.pdf")
        self.assertEqual(len(out["fields"]), 222)

    @unittest.skipUnless(Path(REAL_MOTIVATING_PDF).exists(), "real corpus not present in this worktree")
    def test_real_fetched_form_no_longer_has_the_scrambled_header_field(self):
        # Hand-verified: reverting just this guard (_row_gap_exceeds forced
        # to always return False) reproduces exactly one bogus field on this
        # file today -- a 573pt-wide text box on page 2 whose label is
        # several unrelated Korean checklist lines merged into one cell and
        # sorted by x0 into a scrambled string starting "팀의 정보 ...".
        # This does not claim the file has zero REAL fields -- its
        # checkboxes are a separate, documented, still-open gap (see
        # AUTOPILOT.md / .autobuild/PROGRESS.md pass 12) -- only that this
        # specific confirmed-bogus field does not reappear.
        out = detect(REAL_MOTIVATING_PDF)
        bogus_fragment = "팀의 정보 숫자 의사소통"
        for f in out["fields"]:
            self.assertNotIn(bogus_fragment, f.get("label") or "")


if __name__ == "__main__":
    unittest.main()
