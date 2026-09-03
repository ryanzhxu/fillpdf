"""Regression test for total download failure on a non-WinAnsi value.

tools/inject.mjs draws each field's appearance with the standard PDF font
(Helvetica / WinAnsi) at save() time. A value containing any character outside
WinAnsi -- CJK, emoji, Cyrillic -- cannot be encoded by that font, and because
pdf-lib regenerates every field's appearance on save(), ONE such character in
ONE field made the WHOLE doc.save() throw: the user could not download their
form at all, and every typed value was lost with it. The fix defers appearance
generation to the PDF viewer (NeedAppearances) so the save succeeds and every
value (/V) is preserved. These tests pin that behavior.

Run with:
    .venv/bin/python -m pytest tests/render/test_inject_unicode.py -v
"""
from __future__ import annotations

from pypdf import PdfReader

import fixture
from inject import inject

CJK = "中文 name"   # "中文 name" -- outside WinAnsi


def _doc(*fields) -> dict:
    return {
        "version": 1,
        "pages": [{"page": 1, "width": fixture.PAGE_SIZE[0], "height": fixture.PAGE_SIZE[1]}],
        "fields": list(fields),
    }


def _text(fid, value, rect):
    return {"id": fid, "page": 1, "type": "text", "rect": list(rect), "value": value}


def test_non_winansi_value_survives_injection(tmp_path):
    """A CJK value must not crash the save and must round-trip intact.

    inject() asserts the CLI exited 0, so before the fix this call raised
    (doc.save() threw 'WinAnsi cannot encode'). After it, the save succeeds via
    NeedAppearances and the value is read straight back out of /V.
    """
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    inject(flat, _doc(_text("name", CJK, fixture.TEXT_RECT)), out, flatten=False)

    got = {k: v.get("/V") for k, v in PdfReader(out).get_fields().items()}
    assert got["name"] == CJK


def test_winansi_and_non_winansi_fields_both_land(tmp_path):
    """A CJK value in one field must not take an adjacent ASCII field down with it."""
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    inject(
        flat,
        _doc(
            _text("ascii", "Jane Public", fixture.TEXT_RECT),
            _text("cjk", CJK, fixture.MULTILINE_RECT),
        ),
        out,
        flatten=False,
    )

    got = {k: v.get("/V") for k, v in PdfReader(out).get_fields().items()}
    assert got["ascii"] == "Jane Public"
    assert got["cjk"] == CJK


def test_flatten_with_non_winansi_falls_back_to_fillable_not_data_loss(tmp_path):
    """Flatten cannot bake a CJK value without a Unicode font.

    Rather than fail the whole download, the injector saves an un-flattened
    (still fillable) form so the value survives -- the field stays in the PDF
    with its value, instead of being flattened away or lost to a crash.
    """
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    inject(flat, _doc(_text("name", CJK, fixture.TEXT_RECT)), out, flatten=True)

    fields = PdfReader(out).get_fields()
    assert fields, "flatten fallback dropped the field instead of keeping it fillable"
    assert fields["name"].get("/V") == CJK
