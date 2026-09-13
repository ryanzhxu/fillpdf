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

# pytesseract's own default (timeout=0) waits on the tesseract subprocess
# forever -- see pytesseract.pytesseract.timeout_manager, `not seconds` skips
# the timeout branch entirely. AUTOPILOT.md's part-4 bar requires detect() to
# run "without crashing or timing out" on a real scan, which a hung
# subprocess would silently break for any future page, not just tonight's
# one golden sample. 60s is generous for a single page at 300 DPI (the real
# target scan finishes in ~1-2s).
TIMEOUT_SECONDS = 60


def ocr_page(bitmap, dpi, timeout=TIMEOUT_SECONDS):
    """bitmap: a BGR image (cv2.imread-shaped) rendered at `dpi`, as
    render_page_to_bitmap() returns. Returns (words, chars):

    - words: one DetectablePage `extract_words()`-shaped dict per
      recognized word: {"x0", "x1", "top", "bottom", "text"}.
    - chars: DetectablePage.chars-shaped dicts, one per character, each
      word's box divided evenly by character count -- the interface
      spec's documented convention for word-level OCR granularity.

    All coordinates are in PDF points, matching the page `bitmap` was
    rendered from at `dpi`.

    If the tesseract subprocess does not finish within `timeout` seconds,
    pytesseract kills it and raises RuntimeError -- caught here and treated
    the same as "no words recognized", so a stuck OCR call degrades the same
    way a blank page already does (backend.py's CV-only/decline fallback)
    instead of hanging detect() forever.

    Passes `--dpi` explicitly: the PIL image built from `bitmap` carries no
    DPI metadata (render_page_to_bitmap builds it from a raw numpy array),
    and without it tesseract falls back to an internal guess rather than
    the bitmap's real resolution, which measurably degrades recognition on
    dense real-world layouts. Measured directly against
    eval/corpus/real/d5a49cf46d75829e.pdf's page 5 (a dense two-column code
    table): omitting --dpi recognized only 147 words and silently dropped
    whole table rows (e.g. "002 AMBULANCE", "003 ANTIQUE VEHICLE" missing
    entirely); passing the correct --dpi recognized 341 words at
    effectively the same average confidence (79.7 vs 80.3), recovering the
    missing rows. Confirmed this is not a one-file fluke: also checked
    against eval/scan_cv/samples/agm_proxy_form.pdf and
    eval/corpus/real/5180e9e5652573e2.pdf, where word counts and confidence
    were flat to slightly better -- no file got worse.
    """
    scale = dpi / 72.0
    image = Image.fromarray(bitmap[:, :, ::-1])  # BGR -> RGB
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT,
                                         timeout=timeout, config=f"--dpi {round(dpi)}")
    except RuntimeError:
        return [], []

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
