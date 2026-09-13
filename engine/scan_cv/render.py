"""Renders a PDF page to a bitmap for the scan-CV pipeline.

Part 3 (real page_backend) of docs/superpowers/specs/2026-09-12-scan-cv-
algorithm-design.md's four-part decomposition: this is the "render the page"
piece, ahead of OCR and DetectablePage assembly. Uses pypdfium2, already a
runtime dependency (requirements.txt), rather than adding a new rendering
library.
"""
import cv2
import numpy as np
import pypdfium2 as pdfium


def render_page_to_bitmap(pdf_path, page_number, dpi=300):
    """Renders page `page_number` (0-indexed, matching pdfplumber's
    `pdf.pages` indexing) of the PDF at `pdf_path` to a BGR bitmap
    (cv2.imread-shaped, uint8) at approximately `dpi`.

    pypdfium2 rounds the pixel size to the nearest integer per axis, so the
    actual pixels-per-point ratio can differ slightly between the two axes,
    and from dpi/72, by a sub-point amount. Callers that map detected pixel
    coordinates back to PDF points should be aware `dpi` is nominal, not
    exact per-axis.
    """
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[page_number]
        bitmap = page.render(scale=dpi / 72)
        rgb = np.asarray(bitmap.to_pil().convert("RGB"))
    finally:
        pdf.close()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
