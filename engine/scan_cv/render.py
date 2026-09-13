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

    Known, measured, bounded limitation: pypdfium2 rasterizes the page's
    CropBox, while pdfplumber's `page.width`/`page.height` (what backend.py
    reports as the DetectablePage's size) come from the page's MediaBox
    (pdfplumber.Page.bbox defaults to self.mediabox). When a PDF's CropBox
    differs from its MediaBox -- confirmed present on 11 pages across 5
    files in eval/corpus/real (e.g. page 8 of 2100810741937e1e.pdf: MediaBox
    615x794.4pt vs CropBox 612x792pt, origin offset ~1.44pt/1.2pt) -- OCR/CV
    coordinates, computed in this render's own pixel space and scaled by
    dpi/72, land a couple points off from where a native pdfplumber char at
    the same visual position would report. Measured directly against real
    text on an actual CropBox-offset page: the discrepancy is ~1-2.4pt,
    smaller than OCR's own bounding-box imprecision and well within
    rules.py's multi-point matching tolerances, and the render always covers
    less area than the MediaBox (never more), so this cannot push a rect
    outside `[0, width] x [0, height]`. None of the corpus's CropBox-mismatched
    pages are scanned (0-char) today, so no real field has been affected by
    this yet -- noted here rather than fixed speculatively.
    """
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[page_number]
        bitmap = page.render(scale=dpi / 72)
        rgb = np.asarray(bitmap.to_pil().convert("RGB"))
    finally:
        pdf.close()
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
