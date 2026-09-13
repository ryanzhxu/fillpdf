# engine/scan_cv/lines.py
"""Detects write-on lines / table rules and emits them as DetectablePage
rects, matching rules.py's existing thin-ruling-line convention exactly
(height<3, width>=5 for horizontal) so grid_cells()/R5b/etc. read them
with zero changes to rules.py.
"""
import cv2
import numpy as np

ANGLE_TOL_DEG = 3.0          # near-horizontal tolerance
MIN_LEN_FRACTION = 0.1       # of page width
MAX_LINE_GAP = 5             # px; see this task's design guidance on bridging
MERGE_Y_TOL_PX = 4           # px; merge near-collinear fragments into one line
MERGE_X_GAP_PX = 15          # px; max horizontal gap to still merge two fragments
VERTICAL_OPEN_KSIZE = 15     # px; min vertical stroke length kept as a box-side candidate
SQUARE_ASPECT_LOW = 0.4      # a horizontal candidate whose length is within
SQUARE_ASPECT_HIGH = 2.5     # this ratio of a bracketing vertical's length is a box side
ENDPOINT_TOL_PX = 6          # px; how close a vertical must sit to an endpoint to bracket it


def detect_lines(deskewed, M, dpi):
    """deskewed: the binary, already-deskewed image (engine.scan_cv.deskew's
    output). M: the rotation matrix deskew() used, for mapping detections
    back to original pixel space. Callers get both from ONE preprocess()+
    deskew() call shared with detect_checkboxes -- see Task 6."""
    M_inv = cv2.invertAffineTransform(M)
    scale = dpi / 72.0

    h, w = deskewed.shape
    # A real scanned page's body text produces just as many near-horizontal
    # Canny edges as its write-on lines do (letter strokes, baselines), so
    # running Hough directly on the full image forces a threshold trade-off
    # that can't win: high enough to reject text drops real lines too (only
    # 2 of this algorithm's own eval/scan_cv/samples/agm_proxy_form.pdf's
    # ~11 write-on/border lines survived before this fix), low enough to
    # keep faint lines lets paragraph text through as spurious "lines".
    # A horizontal morphological opening -- erode then dilate with a wide
    # horizontal kernel -- keeps only runs already as long as the minimum
    # line length this module accepts and erases everything narrower
    # (individual glyphs), so Hough only ever sees line-shaped structures.
    ksize = max(15, int(MIN_LEN_FRACTION * w))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, 1))
    opened = cv2.morphologyEx(deskewed, cv2.MORPH_OPEN, kernel)
    edges = cv2.Canny(opened, 50, 150)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=30,
                            minLineLength=MIN_LEN_FRACTION * w,
                            maxLineGap=MAX_LINE_GAP)
    if raw is None:
        return []

    segments = []
    for l in raw:
        x0, y0, x1, y1 = (l if l.ndim == 1 else l[0])
        angle = abs(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
        if not (angle < ANGLE_TOL_DEG or angle > 180 - ANGLE_TOL_DEG):
            continue
        px0, px1 = sorted((x0, x1))
        py = (y0 + y1) / 2.0
        segments.append((px0, px1, py))

    merged = _merge_collinear(segments)

    # The horizontal opening above cleans up straight runs regardless of
    # source, so it recovers a small closed box's top/bottom edges just as
    # reliably as a free-standing write-on line's -- a checkbox's edges are
    # short enough to normally fall under MIN_LEN_FRACTION on a real page,
    # but nothing here should rely on that coincidence. Drop any candidate
    # that is actually one side of a small square (matched by a bracketing
    # vertical stroke of comparable length): that shape is a box, already
    # detect_checkboxes' job, not a line.
    verticals = _vertical_strokes(deskewed)
    merged = [(px0, px1, py) for px0, px1, py in merged
              if not _is_box_edge(px0, px1, py, verticals)]

    out = []
    for px0, px1, py in merged:
        p0 = M_inv @ np.array([px0, py, 1.0])
        p1 = M_inv @ np.array([px1, py, 1.0])
        qx0, qx1 = sorted((p0[0], p1[0]))
        qy = (p0[1] + p1[1]) / 2.0
        x0_pt, x1_pt, y_pt = qx0 / scale, qx1 / scale, qy / scale
        out.append({
            "x0": x0_pt, "x1": x1_pt, "top": y_pt - 1.0, "bottom": y_pt + 1.0,
            "width": x1_pt - x0_pt, "height": 2.0,
            "fill": False, "stroke": True,
        })
    return out


def _vertical_strokes(deskewed):
    """Finds vertical strokes at least VERTICAL_OPEN_KSIZE px long via a
    vertical morphological opening -- long enough to survive stray noise,
    short enough to still catch a checkbox's sides. Returns (x, y0, y1)
    per stroke, x at its horizontal center."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, VERTICAL_OPEN_KSIZE))
    vopened = cv2.morphologyEx(deskewed, cv2.MORPH_OPEN, kernel)
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(vopened)
    return [(x + bw / 2.0, y, y + bh) for x, y, bw, bh, _area in stats[1:]]


def _is_box_edge(px0, px1, py, verticals):
    """True if a vertical stroke of comparable length brackets this
    horizontal segment at BOTH of its endpoints -- i.e. it is one side of a
    small closed box rather than a free-standing line.

    Requiring both ends, not just one, matters on a real table: a write-on
    rule that starts at a column divider (a T-junction, not a box corner)
    has a comparable-length vertical at exactly one endpoint, and a single-
    endpoint check misreads that as a box edge and drops a real line. A
    closed box always has a vertical at both ends; a T-junction never does.
    """
    length = px1 - px0

    def _bracketed_at(x_target):
        for vx, vy0, vy1 in verticals:
            vlen = vy1 - vy0
            if not (SQUARE_ASPECT_LOW * length <= vlen <= SQUARE_ASPECT_HIGH * length):
                continue
            if abs(vx - x_target) <= ENDPOINT_TOL_PX \
                    and vy0 - ENDPOINT_TOL_PX <= py <= vy1 + ENDPOINT_TOL_PX:
                return True
        return False

    return _bracketed_at(px0) and _bracketed_at(px1)


def _merge_collinear(segments):
    """Merges near-horizontal Hough fragments that lie on the same row
    (within MERGE_Y_TOL_PX) and are close enough horizontally
    (within MERGE_X_GAP_PX) into a single logical line, mirroring
    rules.py's _merge_ruling_lines."""
    segments = sorted(segments, key=lambda s: (round(s[2]), s[0]))
    merged = []
    for px0, px1, py in segments:
        placed = False
        for i, (mx0, mx1, my, count) in enumerate(merged):
            avg_y = my / count
            if abs(py - avg_y) <= MERGE_Y_TOL_PX and px0 <= mx1 + MERGE_X_GAP_PX:
                merged[i] = (min(mx0, px0), max(mx1, px1), my + py, count + 1)
                placed = True
                break
        if not placed:
            merged.append((px0, px1, py, 1))
    return [(mx0, mx1, my / count) for mx0, mx1, my, count in merged]
