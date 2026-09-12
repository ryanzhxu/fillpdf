"""Structural contract for a page `_detect_page()` (rules.py) can run against.

Anything satisfying this -- a real pdfplumber page, or a synthetic one built
from OCR + image-detection output -- can be scored by the exact same rules
in rules.py, with no changes to rules.py itself.

Confirmed by reading every `page.`/`pg.` access across rules.py and
__init__.py: these six things are the entire surface the detector uses.
Nothing font-specific beyond literal character text (CHECK_GLYPHS is a text
check, not a font lookup), nothing else PDF-internal.

All coordinates on a DetectablePage -- every `chars`/`rects`/`curves` entry's
x0/x1/top/bottom, and the page's own width/height -- must be in PDF points,
top-down origin (top of page = 0), matching the SAME real page's actual
width/height as reported in detect()'s `pages[]` entry for that page number.
A backend must rescale into this coordinate space before returning a
DetectablePage; it must never report pixel coordinates or any other scaled
space, or every field rect `detect()` computes will be wrong relative to the
page dimensions callers already have.

See docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class DetectablePage(Protocol):
    width: float
    height: float
    chars: list       # each: {"x0", "x1", "top", "bottom", "text"}
    rects: list        # each: {"x0", "x1", "top", "bottom", "width", "height", "fill", "stroke"}
    curves: list       # [] is valid for a v1 backend (see the spec's "curves
                       # may legitimately be empty" note). If populated, NOT
                       # the same shape as rects: each entry needs "x0",
                       # "x1", "top", "bottom" (as rects do) plus "path" -- a
                       # list of (operator, (x, y)) tuples, operator one of
                       # "m"/"l"/"c"/"h", starting with "m" and ending with
                       # "h" -- describing a rectangle-like outline. See
                       # rules.py's _rect_like_curves().

    def extract_words(self) -> list: ...  # each: {"x0", "x1", "top", "bottom", "text"}
