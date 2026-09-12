"""Structural contract for a page `_detect_page()` (rules.py) can run against.

Anything satisfying this -- a real pdfplumber page, or a synthetic one built
from OCR + image-detection output -- can be scored by the exact same rules
in rules.py, with no changes to rules.py itself.

Confirmed by reading every `page.`/`pg.` access across rules.py and
__init__.py: these five things are the entire surface the detector uses.
Nothing font-specific beyond literal character text (CHECK_GLYPHS is a text
check, not a font lookup), nothing else PDF-internal.

See docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class DetectablePage(Protocol):
    width: float
    height: float
    chars: list       # each: {"x0", "x1", "top", "bottom", "text"}
    rects: list        # each: {"x0", "x1", "top", "bottom", "width", "height", "fill", "stroke"}
    curves: list       # same shape as rects; [] is valid -- see the spec's
                       # "curves may legitimately be empty" note

    def extract_words(self) -> list: ...  # each: {"x0", "x1", "top", "bottom", "text"}
