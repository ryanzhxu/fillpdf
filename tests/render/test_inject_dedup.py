"""Regression test for silent data loss on a duplicate field name.

tools/inject.mjs turns each field into a real pdf-lib AcroForm widget, and
pdf-lib throws if two widgets share a name. Before the de-dup guard, the
second field -- with whatever the user typed into it -- was dropped from the
output PDF and only counted in `failed`. detect() de-duplicates its ids, but
the demo lets a person rename a box to one that already exists, so a colliding
name still reaches the injector. This test pins that every field lands with a
distinct name and its value intact.

Run with:
    .venv/bin/python -m pytest tests/render/test_inject_dedup.py -v
"""
from __future__ import annotations

from pypdf import PdfReader

import fixture
from inject import inject


def _fields(*fields) -> dict:
    return {
        "version": 1,
        "pages": [{"page": 1, "width": fixture.PAGE_SIZE[0], "height": fixture.PAGE_SIZE[1]}],
        "fields": list(fields),
    }


def _text(fid, value, rect):
    return {"id": fid, "page": 1, "type": "text", "rect": list(rect), "value": value}


def test_duplicate_ids_both_land_with_distinct_names(tmp_path):
    """Two fields sharing an id must both survive injection.

    inject() asserts zero injection failures, so before the de-dup guard this
    call raised (the second 'name' threw FieldAlreadyExists). After it, the
    second field is renamed 'name_2' and its value is preserved.
    """
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    doc = _fields(
        _text("name", "first", fixture.TEXT_RECT),
        _text("name", "second", fixture.MULTILINE_RECT),
    )
    inject(flat, doc, out, flatten=False)

    got = {k: v.get("/V") for k, v in PdfReader(out).get_fields().items()}
    assert set(got) == {"name", "name_2"}
    assert got["name"] == "first"
    assert got["name_2"] == "second"


def test_empty_id_falls_back_instead_of_dropping_the_field(tmp_path):
    """An empty id must not throw a nameless-widget error and lose the field."""
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    doc = _fields(_text("", "kept", fixture.TEXT_RECT))
    inject(flat, doc, out, flatten=False)

    got = {k: v.get("/V") for k, v in PdfReader(out).get_fields().items()}
    assert list(got.values()) == ["kept"]
