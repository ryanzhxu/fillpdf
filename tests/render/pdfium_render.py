"""Renders a PDF page (with form widgets) to a PIL image via pypdfium2.

pypdfium2 is the must-have engine: it's already a runtime dependency
(requirements.txt), it renders in-process (no subprocess overhead), and it
is what most of this repo's users will actually see a filled PDF through
(pdfium is Chrome's PDF engine, and Preview.app / most viewers use
comparable native rasterizers).
"""
from __future__ import annotations

import pypdfium2 as pdfium
from PIL import Image


def render_page(pdf_bytes: bytes, page_index: int = 0, scale: float = 3.0) -> tuple[Image.Image, float, float]:
    """Render one page with form widgets drawn. Returns (image, page_width_pt, page_height_pt)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    doc.init_forms()  # must happen before rendering for widget appearances to show
    page = doc[page_index]
    width_pt, height_pt = page.get_size()
    bitmap = page.render(scale=scale, may_draw_forms=True)
    return bitmap.to_pil().convert("RGB"), width_pt, height_pt
