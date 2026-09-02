"""Match detected boxes against truth widgets.

Rules (see eval/contracts/README.md and the task brief):
  1. Same page.
  2. Same type family. "text" and "multiline" are one family ("text-like");
     "checkbox" is its own family. Truth type "choice" counts as "text-like".
  3. IoU >= 0.5.

Matching is one-to-one, resolved greedily by descending IoU: the
highest-IoU candidate pair is taken first, then the next highest among
what remains, and so on. A truth widget or a detection can appear in at
most one match.
"""

TEXT_LIKE = {"text", "multiline", "choice"}


def _type_family(t: str) -> str:
    """Collapse a type string to its matching family: 'text' or 'checkbox'."""
    return "text" if t in TEXT_LIKE else t


def _normalize_rect(rect):
    """Return (x0, y0, x1, y1) with x0<=x1 and y0<=y1, handling swapped corners."""
    x0, y0, x1, y1 = rect
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def iou(a: list, b: list) -> float:
    """Intersection-over-union of two [x0,y0,x1,y1] rects.

    Corners may be given in any order; they are normalised to min/max
    before computing areas. Returns 0.0 for a zero-area union.
    """
    ax0, ay0, ax1, ay1 = _normalize_rect(a)
    bx0, by0, bx1, by1 = _normalize_rect(b)

    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter_w = max(0.0, ix1 - ix0)
    inter_h = max(0.0, iy1 - iy0)
    inter = inter_w * inter_h

    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    union = area_a + area_b - inter

    if union <= 0:
        return 0.0
    return inter / union


def match(detections: list, truth: list) -> dict:
    """Greedy one-to-one matching of detections to truth widgets.

    Returns:
        {
            "matches": [(det_idx, truth_idx, iou), ...],
            "unmatched_det": [det_idx, ...],
            "unmatched_truth": [truth_idx, ...],
            "near_miss": [truth_idx, ...],
        }

    near_miss lists truth widgets, among those left unmatched, that have
    some same-page/same-type-family detection at 0.15 <= IoU < 0.5 -- the
    rule fired near the right spot but not well enough to count.
    """
    det_rects = [_normalize_rect(d["rect"]) for d in detections]
    truth_rects = [_normalize_rect(t["rect"]) for t in truth]

    candidates = []
    for di, d in enumerate(detections):
        for ti, t in enumerate(truth):
            if d["page"] != t["page"]:
                continue
            if _type_family(d["type"]) != _type_family(t["type"]):
                continue
            iou_val = iou(det_rects[di], truth_rects[ti])
            if iou_val >= 0.5:
                candidates.append((iou_val, di, ti))

    # Descending IoU; stable sort keeps generation order (det-major) as the
    # tie-break so results are deterministic.
    candidates.sort(key=lambda c: c[0], reverse=True)

    matched_det, matched_truth = set(), set()
    matches = []
    for iou_val, di, ti in candidates:
        if di in matched_det or ti in matched_truth:
            continue
        matched_det.add(di)
        matched_truth.add(ti)
        matches.append((di, ti, iou_val))

    unmatched_det = [i for i in range(len(detections)) if i not in matched_det]
    unmatched_truth = [i for i in range(len(truth)) if i not in matched_truth]

    near_miss = []
    for ti in unmatched_truth:
        t = truth[ti]
        for di, d in enumerate(detections):
            if d["page"] != t["page"]:
                continue
            if _type_family(d["type"]) != _type_family(t["type"]):
                continue
            iv = iou(det_rects[di], truth_rects[ti])
            if 0.15 <= iv < 0.5:
                near_miss.append(ti)
                break

    return {
        "matches": matches,
        "unmatched_det": unmatched_det,
        "unmatched_truth": unmatched_truth,
        "near_miss": near_miss,
    }
