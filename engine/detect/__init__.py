"""Field detection. The single entry point every consumer calls."""
from pathlib import Path
from typing import Union

import pdfplumber

from .rules import detect as _detect_page, slug

__all__ = ["detect", "slug"]

# A scanned or image-only PDF carries no real text layer: each page is a raster
# image, so pdfplumber extracts almost no characters. The detector reads a
# form's printed text to place fields, so on a scan it finds nothing and would
# otherwise return an empty result with no explanation. A public app cannot do
# that, so detect() attaches an honest `notice` the UI can show. The check is
# deliberately conservative -- a real flat form carries hundreds of label
# characters per page, far above SCANNED_MAX_CHARS_PER_PAGE -- so it fires only
# on genuine scans, never on the 165-form corpus.
SCANNED_MAX_CHARS_PER_PAGE = 20   # a real flat form carries far more label text
SCANNED_IMAGE_COVERAGE = 0.5      # fraction of page area one image must cover
SCANNED_MESSAGE = (
    "This looks like a scanned or image-only PDF: its pages are images with no "
    "text layer, so there is nothing for FormFill to read and no fields could "
    "be found. Run OCR on it first (for example, export a searchable / "
    "text-layer PDF) and try again."
)


def _page_is_scanned(pg) -> bool:
    """True when a page is a page-filling image with almost no text."""
    non_ws = sum(1 for c in pg.chars if c["text"].strip())
    if non_ws >= SCANNED_MAX_CHARS_PER_PAGE:
        return False
    area = float(pg.width) * float(pg.height)
    if area <= 0:
        return False
    return any(
        (im.get("width", 0) or 0) * (im.get("height", 0) or 0) >= SCANNED_IMAGE_COVERAGE * area
        for im in pg.images
    )


def detect(pdf_path: Union[str, Path]) -> dict:
    """Detect fillable regions in a flat PDF.

    Pure function. No network, no mutation of the input, no global state.
    Returns the shape defined in eval/contracts/fields.schema.json.
    """
    fields, pages = [], []
    carry, prev_width = None, None
    scanned_pages = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            pages.append({"page": i, "width": float(pg.width), "height": float(pg.height)})
            if prev_width is not None and abs(pg.width - prev_width) > 1:
                carry = None      # a page-size/orientation change breaks column geometry
            if _page_is_scanned(pg):
                scanned_pages += 1
            page_fields, carry = _detect_page(pg, i, carry_in=carry)
            fields += page_fields
            prev_width = pg.width

    seen: dict = {}
    for f in fields:
        base = f"p{f['page']}_" + (slug(f["label"]) if f["type"] == "text" else "chk")
        seen[base] = seen.get(base, 0) + 1
        f["id"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
        f["origin"] = "detected"

    out = {"version": 1, "source": {"pages": len(pages)}, "pages": pages, "fields": fields}
    # Flag the whole document when most content pages are scanned images. One
    # image page in an otherwise text PDF is not a scan, so require a majority.
    if pages and scanned_pages / len(pages) >= 0.5:
        out["notice"] = {"code": "scanned", "message": SCANNED_MESSAGE}
    return out
