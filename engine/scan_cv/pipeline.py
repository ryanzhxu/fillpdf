"""Public entry point for the shared scan-CV algorithm. See
docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md.
"""
from engine.scan_cv.preprocess import preprocess
from engine.scan_cv.deskew import deskew
from engine.scan_cv.lines import detect_lines, detect_verticals
from engine.scan_cv.checkboxes import detect_checkboxes


def detect_lines_and_boxes(bitmap, dpi):
    """bitmap: a BGR image (cv2.imread-shaped) rendered at `dpi`. Returns a
    list of DetectablePage.rects-shaped dicts in PDF points, combining
    line, box-side, and checkbox candidates. Pure with respect to its
    inputs.

    preprocess()+deskew() run exactly once here and the result is shared
    with all three detectors -- each independently re-running the deskew
    search (a ~100-iteration rotation loop) would multiply CV cost on every
    page for no benefit, since all three need the identical deskewed image
    and rotation matrix."""
    binary = preprocess(bitmap)
    deskewed, M = deskew(binary)
    boxes = detect_checkboxes(deskewed, M, dpi)
    verticals = _drop_checkbox_edges(detect_verticals(deskewed, M, dpi), boxes)
    return detect_lines(deskewed, M, dpi) + verticals + boxes


CHECKBOX_EDGE_TOL_PT = 3   # points; see _drop_checkbox_edges


def _drop_checkbox_edges(verticals, boxes, tol=CHECKBOX_EDGE_TOL_PT):
    """A checkbox's own left/right side is a real vertical stroke, so
    detect_verticals() reports it same as any other box side -- but R1
    (engine/detect/rules.py) already claims the checkbox itself from its
    glyph/fill rect, and grid_cells()/R5b only need ONE rect describing
    that edge, not two. Confirmed on eval/scan_cv/golden/fixture_rotated:
    without this, its single checkbox's two sides came back as extra
    `stroke` rects alongside the `fill` rect the checkbox test already
    counts, double-reporting the same geometry."""
    return [v for v in verticals
            if not any((abs(v["x0"] - b["x0"]) < tol or abs(v["x0"] - b["x1"]) < tol)
                       and v["top"] >= b["top"] - tol and v["bottom"] <= b["bottom"] + tol
                       for b in boxes)]
