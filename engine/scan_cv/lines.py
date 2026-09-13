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


def detect_lines(deskewed, M, dpi):
    """deskewed: the binary, already-deskewed image (engine.scan_cv.deskew's
    output). M: the rotation matrix deskew() used, for mapping detections
    back to original pixel space. Callers get both from ONE preprocess()+
    deskew() call shared with detect_checkboxes -- see Task 6."""
    M_inv = cv2.invertAffineTransform(M)
    scale = dpi / 72.0

    h, w = deskewed.shape
    edges = cv2.Canny(deskewed, 50, 150)
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
