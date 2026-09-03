"""Regression test for mutually-exclusive (Yes/No) checkboxes.

engine.detect tags a Yes/No question's two options with a shared `group`, and
tools/inject.mjs must turn a group into ONE AcroForm radio field so a person
cannot tick both answers. Before this, the two options were independent
checkboxes and both could be checked -- wrong in law, not just in software.
This test pins that a group becomes one radio field with both options and only
the ticked one selected.

Run with:
    .venv/bin/python -m pytest tests/render/test_inject_radio.py -v
"""
from __future__ import annotations

from pypdf import PdfReader

import fixture
from inject import inject

YES_RECT = (110.0, 175.0, 132.0, 197.0)
NO_RECT = (150.0, 175.0, 172.0, 197.0)


def _doc(*fields) -> dict:
    return {
        "version": 1,
        "pages": [{"page": 1, "width": fixture.PAGE_SIZE[0], "height": fixture.PAGE_SIZE[1]}],
        "fields": list(fields),
    }


def test_grouped_checkboxes_become_one_radio_with_the_ticked_option(tmp_path):
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    doc = _doc(
        {"id": "q_yes", "page": 1, "type": "checkbox", "group": "grp_1", "rect": list(YES_RECT), "value": True},
        {"id": "q_no", "page": 1, "type": "checkbox", "group": "grp_1", "rect": list(NO_RECT), "value": False},
    )
    inject(flat, doc, out)

    reader = PdfReader(out)
    fields = reader.get_fields()
    # The two options collapse into ONE field (the radio group), not two.
    assert set(fields) == {"grp_1"}
    fld = fields["grp_1"]
    assert fld.get("/FT") == "/Btn"
    assert fld.get("/Ff", 0) & (1 << 15)        # the radio (mutually-exclusive) flag
    assert list(fld.get("/Opt", [])) == ["q_yes", "q_no"]
    assert len(fld.get("/Kids", [])) == 2       # one widget per option
    # Exactly the ticked option is selected: /V is the appearance-state index,
    # and index 0 is the first /Opt entry, "q_yes" -- the box whose value was True.
    assert str(fld.get("/V")) == "/0"


def test_ungrouped_checkbox_still_injects_as_a_plain_checkbox(tmp_path):
    """A checkbox without a group must keep the independent-checkbox behaviour."""
    flat = tmp_path / "flat.pdf"
    fixture.build_flat_pdf(flat)
    out = tmp_path / "out.pdf"

    doc = _doc(
        {"id": "agree", "page": 1, "type": "checkbox", "rect": list(YES_RECT), "value": True},
    )
    inject(flat, doc, out)

    fields = PdfReader(out).get_fields()
    assert set(fields) == {"agree"}
    assert fields["agree"].get("/FT") == "/Btn"
