"""Public entry point for the shared scan-CV algorithm. See
docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md.
"""
from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import deskew
from engine.scan_cv.lines import detect_lines
from engine.scan_cv.checkboxes import detect_checkboxes


def detect_lines_and_boxes(bitmap, dpi):
    """bitmap: a BGR image (cv2.imread-shaped) rendered at `dpi`. Returns a
    list of DetectablePage.rects-shaped dicts in PDF points, combining
    line and checkbox candidates. Pure with respect to its inputs.

    preprocess()+deskew() run exactly once here and the result is shared
    with both detectors -- each independently re-running the deskew search
    (a ~100-iteration rotation loop) would double CV cost on every page for
    no benefit, since both detectors need the identical deskewed image and
    rotation matrix."""
    binary = preprocess(bitmap)
    deskewed, M = deskew(binary)
    return detect_lines(deskewed, M, dpi) + detect_checkboxes(deskewed, M, dpi)
