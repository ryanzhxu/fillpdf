# engine/scan_cv/checkboxes.py
"""Detects checkbox-shaped squares and emits them as DetectablePage rects.

Always fill=True, stroke=False regardless of the source square's real
appearance -- see this plan's Global Constraints and
docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md's "Checkbox
detection" section for why.
"""
import cv2
import numpy as np

CHK_MIN_PT, CHK_MAX_PT = 18.0, 32.0
SQUARE_TOL_PT = 8.0

# A write-on/table rule that happens to cross a checkbox merges with it into
# one blob under plain findContours, splitting the box's outline (real bug
# observed on the rotated golden fixture, whose crossing line intersects the
# checkbox). Erase any run at least this long -- longer than the checkbox
# band above, shorter than a real ruling line -- before contour detection,
# then re-close the small gap the erasure leaves in the box's own edges.
_LINE_ERASE_FACTOR = 1.5
_CLOSE_KERNEL_PX = 5


def _erase_long_lines(binary, kernel_len_px):
    kernel_len_px = max(kernel_len_px, 1)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len_px, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_len_px))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    erased = cv2.subtract(binary, cv2.bitwise_or(h_lines, v_lines))
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (_CLOSE_KERNEL_PX, _CLOSE_KERNEL_PX))
    return cv2.morphologyEx(erased, cv2.MORPH_CLOSE, close_kernel)


def detect_checkboxes(deskewed, M, dpi):
    """deskewed: the binary, already-deskewed image (engine.scan_cv.deskew's
    output). M: the rotation matrix deskew() used. Callers get both from
    ONE preprocess()+deskew() call shared with detect_lines -- see Task 6."""
    M_inv = cv2.invertAffineTransform(M)
    scale = dpi / 72.0
    lo_px, hi_px = CHK_MIN_PT * scale, CHK_MAX_PT * scale
    tol_px = SQUARE_TOL_PT * scale

    cleaned = _erase_long_lines(deskewed, int(hi_px * _LINE_ERASE_FACTOR))
    # RETR_EXTERNAL: a checkbox's own interior hole is not a candidate --
    # only its outer boundary is.
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        if not (lo_px <= w <= hi_px and lo_px <= h <= hi_px and abs(w - h) <= tol_px):
            continue
        # Map all four corners, not just two opposite ones: M_inv is a
        # rotation, so it does not preserve which corner is top-left/
        # bottom-right, and a rotation center far from this box (as on the
        # golden rotated fixture) makes that error tens of pixels, not a
        # rounding-scale nuisance.
        corners = np.array([[x, y, 1.0], [x + w, y, 1.0],
                             [x, y + h, 1.0], [x + w, y + h, 1.0]])
        mapped = corners @ M_inv.T
        xs, ys = mapped[:, 0] / scale, mapped[:, 1] / scale
        x0_pt, x1_pt = xs.min(), xs.max()
        y0_pt, y1_pt = ys.min(), ys.max()
        out.append({
            "x0": x0_pt, "x1": x1_pt, "top": y0_pt, "bottom": y1_pt,
            "width": x1_pt - x0_pt, "height": y1_pt - y0_pt,
            "fill": True, "stroke": False,
        })
    return out
