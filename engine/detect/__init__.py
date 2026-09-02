"""Field detection. The single entry point every consumer calls."""
from pathlib import Path
from typing import Union

import pdfplumber

from .rules import detect as _detect_page, slug

__all__ = ["detect", "slug"]


def detect(pdf_path: Union[str, Path]) -> dict:
    """Detect fillable regions in a flat PDF.

    Pure function. No network, no mutation of the input, no global state.
    Returns the shape defined in eval/contracts/fields.schema.json.
    """
    fields, pages = [], []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            pages.append({"page": i, "width": float(pg.width), "height": float(pg.height)})
            fields += _detect_page(pg, i)

    seen: dict = {}
    for f in fields:
        base = f"p{f['page']}_" + (slug(f["label"]) if f["type"] == "text" else "chk")
        seen[base] = seen.get(base, 0) + 1
        f["id"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
        f["origin"] = "detected"
    return {"version": 1, "source": {"pages": len(pages)}, "pages": pages, "fields": fields}
