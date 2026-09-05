"""Tests for Unicode ballot-box checkboxes (U+2610/2611/2612) in engine.detect.

R1 keyed on two PRIVATE-USE codepoints only -- U+F063 (Webdings) and U+F06F
(Wingdings) -- so a producer that draws its checkboxes with the actual Unicode
BALLOT BOX character was invisible to the detector no matter how it was
captioned. Found via eval.blind on eval/corpus/real/9e5fa53418722365.pdf, a
zoning "Outdoor Lighting" checklist whose six real checkboxes (three compliance
statements plus an Owner/Applicant/Agent role row) are MS-Gothic U+2610 and
scored zero fields. Measured across the corpora: 7 tuning files, 2 holdout
files and 15 real fetched files carry these codepoints, 332 glyphs in total.

The same file also proves the NEGATIVE case. `.autobuild/PROGRESS.md` pass 16
guessed its checkboxes were "a SymbolMT PUA glyph"; rendering the page showed
that glyph (U+F8E7) is an em-dash list bullet introducing prose statements
("More information can be found in..."), not a checkbox. Reading it as one
would invent 13 false positives on page 1 alone, so PUA codepoints stay
opt-in by codepoint and are NOT matched as a range.

These fixtures hand-build a PDF carrying a ToUnicode CMap that maps a drawn
Helvetica glyph to the codepoint under test. That is the same mechanism the
real file uses (MS-Gothic plus a ToUnicode CMap), it is what pdfplumber
actually reads, and it needs no font dependency.

Run standalone with:  .venv/bin/python -m pytest tests/test_ballot_box_checkbox.py
"""
import unittest

from engine.detect import detect


def _cmap(mapping):
    """A ToUnicode CMap mapping single source bytes to target codepoints."""
    pairs = "\n".join(f"<{src:02X}> <{dst:04X}>" for src, dst in mapping.items())
    return ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CMapName /Test def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
            f"{len(mapping)} beginbfchar\n{pairs}\nendbfchar\n"
            "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend")


def _pdf(content, mapping):
    """A one-page PDF. /F1 carries the ToUnicode CMap, /F2 is plain text.

    /F1 declares a full-em width so the mark occupies a real 12pt box, as
    MS-Gothic's does on the source document. Without /Widths the glyph
    measures zero wide and no label sits close enough to attach.
    """
    cmap = _cmap(mapping)
    objs = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        "/Resources<</Font<</F1 5 0 R/F2 7 0 R>>>>/Contents 4 0 R>>",
        f"<</Length {len(content)}>>\nstream\n{content}\nendstream",
        "<</Type/Font/Subtype/TrueType/BaseFont/MS-Gothic/FirstChar 65"
        "/LastChar 65/Widths[1000]/FontDescriptor 8 0 R/ToUnicode 6 0 R>>",
        f"<</Length {len(cmap)}>>\nstream\n{cmap}\nendstream",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        "<</Type/FontDescriptor/FontName/MS-Gothic/Flags 4"
        "/FontBBox[0 -200 1000 900]/ItalicAngle 0/Ascent 900/Descent -200"
        "/CapHeight 700/StemV 80>>",
    ]
    out, offs = "%PDF-1.4\n", []
    for i, body in enumerate(objs, start=1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def _marked_options(codepoint):
    """An Owner/Applicant role row, each option marked with `codepoint` --
    the exact shape of 9e5fa53418722365.pdf's signatory row."""
    content = ("BT /F1 12 Tf 72 700 Td (A) Tj ET\n"
               "BT /F2 12 Tf 90 700 Td (Owner) Tj ET\n"
               "BT /F1 12 Tf 200 700 Td (A) Tj ET\n"
               "BT /F2 12 Tf 218 700 Td (Applicant) Tj ET")
    return _pdf(content, {0x41: codepoint})


def _em_dash_bullets():
    """SymbolMT U+F8E7 used as a list bullet before prose statements, as on
    9e5fa53418722365.pdf page 1. It renders as an em dash, not a box."""
    lines = ["Drop-down lenses.", "Mercury vapor lights.",
             "More information can be found in the code."]
    content = "\n".join(
        f"BT /F1 12 Tf 59 {700 - i * 15} Td (A) Tj ET\n"
        f"BT /F2 12 Tf 78 {700 - i * 15} Td ({t}) Tj ET"
        for i, t in enumerate(lines))
    return _pdf(content, {0x41: 0xF8E7})


def _write(tmp, name, data):
    p = tmp / name
    p.write_bytes(data)
    return str(p)


class TestBallotBoxCheckbox(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_empty_ballot_box_is_detected(self):
        path = _write(self.tmp, "u2610.pdf", _marked_options(0x2610))
        out = detect(path)
        self.assertEqual(sorted(f["label"] for f in out["fields"]),
                         ["Applicant", "Owner"])
        self.assertTrue(all(f["type"] == "checkbox" for f in out["fields"]))

    def test_checked_and_crossed_ballot_boxes_are_detected(self):
        # A form distributed with an option already ticked is still a form.
        for cp in (0x2611, 0x2612):
            with self.subTest(codepoint=hex(cp)):
                path = _write(self.tmp, f"u{cp:04x}.pdf", _marked_options(cp))
                out = detect(path)
                self.assertEqual(sorted(f["label"] for f in out["fields"]),
                                 ["Applicant", "Owner"])

    def test_ballot_box_rect_tracks_the_glyph(self):
        path = _write(self.tmp, "rect.pdf", _marked_options(0x2610))
        out = detect(path)
        owner = next(f for f in out["fields"] if f["label"] == "Owner")
        x0, _, x1, _ = owner["rect"]
        self.assertAlmostEqual(x0, 72.0, delta=1.0)
        self.assertLess(x1 - x0, 14.0)      # the glyph box, not a fixed 10pt

    def test_em_dash_bullet_glyph_is_not_a_checkbox(self):
        # The PUA codepoint pass 16 mistook for a checkbox. It is a bullet.
        path = _write(self.tmp, "bullets.pdf", _em_dash_bullets())
        self.assertEqual(detect(path)["fields"], [])

    def test_real_fixture_field_count_is_unchanged(self):
        # safer.pdf uses Webdings/Wingdings boxes; this change must not move it.
        out = detect("fixtures/safer.pdf")
        self.assertEqual(len(out["fields"]), 222)


if __name__ == "__main__":
    unittest.main()
