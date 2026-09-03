"""Fixture PDF + fields.json for the golden-image render tests.

A small, purpose-built page (not the 8-page SAFER form) so the goldens stay
small and a failure is easy to read. Each field's on-page guide box IS its
`rect` -- the same rect a real detector (engine.detect.detect()) would
emit -- so a crop taken at that rect shows directly whether the injected
widget landed on its box, the way it would need to on a real scanned form
that already has boxes printed on it.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas as _canvas

PAGE_SIZE = (420.0, 260.0)  # points -- small on purpose

TEXT_RECT = (110.0, 210.0, 330.0, 232.0)
CHECKBOX_RECT = (110.0, 175.0, 132.0, 197.0)
MULTILINE_RECT = (110.0, 40.0, 390.0, 160.0)

TEXT_VALUE = "Jane Public"
MULTILINE_VALUE = (
    "This is a multi-line comment used to test that the appearance "
    "stream wraps text within the field box instead of clipping it."
)


def _xywh(rect):
    x0, y0, x1, y1 = rect
    return (x0, y0, x1 - x0, y1 - y0)


def build_flat_pdf(path: Path) -> None:
    """Write the flat (no AcroForm yet) source page: labels + guide boxes."""
    c = _canvas.Canvas(str(path), pagesize=PAGE_SIZE)
    c.setFont("Helvetica", 10)
    c.drawString(20, 222, "Name:")
    c.rect(*_xywh(TEXT_RECT))
    c.drawString(20, 187, "Agree:")
    c.rect(*_xywh(CHECKBOX_RECT))
    c.drawString(20, 152, "Comments:")
    c.rect(*_xywh(MULTILINE_RECT))
    c.save()


def fields_doc(filled: bool) -> dict:
    """The fields.json-shaped document tools/inject.mjs consumes.

    Matches eval/contracts/fields.schema.json exactly (checked by
    test_fixture_matches_contract_schema) so the injector is exercised with
    the same shape engine.detect.detect() actually emits, not a hand-wavy
    lookalike.

    filled=False -> blank text/multiline, unchecked box.
    filled=True  -> values set, box checked.
    """
    text = {"id": "name", "page": 1, "type": "text", "rect": list(TEXT_RECT)}
    checkbox = {"id": "agree", "page": 1, "type": "checkbox", "rect": list(CHECKBOX_RECT)}
    multiline = {"id": "comments", "page": 1, "type": "multiline", "rect": list(MULTILINE_RECT)}
    if filled:
        text["value"] = TEXT_VALUE
        checkbox["value"] = True
        multiline["value"] = MULTILINE_VALUE
    return {
        "version": 1,
        "pages": [{"page": 1, "width": PAGE_SIZE[0], "height": PAGE_SIZE[1]}],
        "fields": [text, checkbox, multiline],
    }


# name -> rect, used by tests to know what to crop for each field type
FIELD_RECTS = {
    "text": TEXT_RECT,
    "checkbox": CHECKBOX_RECT,
    "multiline": MULTILINE_RECT,
}
