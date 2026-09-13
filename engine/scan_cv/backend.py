"""Assembles render.py + ocr.py + pipeline.py into a real `page_backend`,
part 3 (real page_backend) of docs/superpowers/specs/2026-09-12-scan-
detection-interface-design.md -- the assembly piece, following rendering
and OCR. This is what wires the shared scan-CV algorithm into `detect()`'s
`page_backend(pdfplumber_page, page_number) -> DetectablePage | None`
calling convention (engine/detect/__init__.py).
"""
from engine.scan_cv.ocr import ocr_page
from engine.scan_cv.pipeline import detect_lines_and_boxes
from engine.scan_cv.render import render_page_to_bitmap

DPI = 300


class _ScanCVPage:
    """A DetectablePage (engine/detect/page_protocol.py) assembled from real
    OCR + CV output for one scanned page. `curves` stays empty -- v1 backend,
    see page_protocol.py's note that curves may legitimately be []."""

    def __init__(self, width, height, chars, rects, words):
        self.width = width
        self.height = height
        self.chars = chars
        self.rects = rects
        self.curves = []
        self._words = words

    def extract_words(self):
        return self._words


def make_cv_ocr_backend(pdf_path, dpi=DPI):
    """Returns a `page_backend` closure over `pdf_path`, suitable for
    `engine.detect.detect(pdf_path, page_backend=...)`.

    `page_number` in the returned closure is 1-indexed, matching detect()'s
    own calling convention (`page_backend(pg, i)` inside its
    `enumerate(pdf.pages, 1)` loop); render_page_to_bitmap takes a 0-indexed
    page_number matching pdfplumber's `pdf.pages` indexing directly, hence
    the `- 1` below.
    """
    def page_backend(pdfplumber_page, page_number):
        bitmap = render_page_to_bitmap(pdf_path, page_number - 1, dpi=dpi)
        rects = detect_lines_and_boxes(bitmap, dpi)
        words, chars = ocr_page(bitmap, dpi)
        if not words and not rects:
            # Nothing recovered at all (e.g. a blank/near-blank scan) --
            # decline so detect() keeps the honest "scanned" notice instead
            # of claiming ocr_assisted over an empty page. See the interface
            # spec's notice-precedence section.
            return None
        return _ScanCVPage(
            width=float(pdfplumber_page.width),
            height=float(pdfplumber_page.height),
            chars=chars, rects=rects, words=words,
        )
    return page_backend
