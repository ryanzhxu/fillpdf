"""Runs OCR on a rendered page bitmap and emits DetectablePage-shaped
`words`/`chars`, part 3 (real page_backend) of docs/superpowers/specs/
2026-09-12-scan-detection-interface-design.md -- the OCR piece, following
render.py's rendering piece.

Runs on the ORIGINAL (not deskewed) bitmap deliberately: engine/scan_cv/
lines.py and checkboxes.py detect on the deskewed image but inverse-
transform their results back into the original bitmap's pixel space before
scaling to PDF points (see lines.py's `M_inv` step), so the original bitmap
is already the shared coordinate space both this module and pipeline.py's
rects land in once scaled by the same dpi. Tesseract also tolerates small
page skew internally, so there is no accuracy reason to deskew first.
"""
import pytesseract
from PIL import Image

MIN_CONFIDENCE = 0  # Tesseract reports -1 for non-word rows (blocks/lines).


def ocr_page(bitmap, dpi):
    """bitmap: a BGR image (cv2.imread-shaped) rendered at `dpi`, as
    render_page_to_bitmap() returns. Returns (words, chars):

    - words: one DetectablePage `extract_words()`-shaped dict per
      recognized word: {"x0", "x1", "top", "bottom", "text"}.
    - chars: DetectablePage.chars-shaped dicts, one per character, each
      word's box divided evenly by character count -- the interface
      spec's documented convention for word-level OCR granularity.

    All coordinates are in PDF points, matching the page `bitmap` was
    rendered from at `dpi`.
    """
    scale = dpi / 72.0
    image = Image.fromarray(bitmap[:, :, ::-1])  # BGR -> RGB
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    words = []
    chars = []
    for text, left, top, width, height, conf in zip(
        data["text"], data["left"], data["top"], data["width"], data["height"],
        data["conf"],
    ):
        text = text.strip()
        if not text or float(conf) <= MIN_CONFIDENCE:
            continue
        x0, top_pt = left / scale, top / scale
        x1, bottom_pt = (left + width) / scale, (top + height) / scale
        words.append({"x0": x0, "x1": x1, "top": top_pt, "bottom": bottom_pt,
                      "text": text})

        char_w = (x1 - x0) / len(text)
        for i, ch in enumerate(text):
            chars.append({
                "x0": x0 + i * char_w, "x1": x0 + (i + 1) * char_w,
                "top": top_pt, "bottom": bottom_pt, "text": ch,
            })
    return words, chars
