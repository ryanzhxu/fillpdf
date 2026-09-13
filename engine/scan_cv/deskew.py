"""Detects and corrects page skew on a preprocessed (binary) scan image.

See docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md's
"Pipeline architecture" section for why this step exists and what must
happen to detected coordinates afterward (the inverse-transform chain).

Method: a projection-profile angle search. For each candidate angle the
image is rotated and its horizontal row sums are taken; the row-sum
profile of a correctly straightened page has the sharpest peaks, because
every horizontal feature then falls entirely inside one row band. Profile
peakedness measures that, so the correction angle is the peakedness-
maximizing angle.

A minAreaRect-based estimate was tried first during design and rejected:
it read 34.2 degrees on a true 3-degree skew, because minAreaRect
describes the bounding box of all foreground ink rather than the
dominant text/rule direction.

The search runs coarse-to-fine (whole degrees, then a fine sweep around
the coarse winner) rather than as one fine pass. That costs fewer
rotations than a single fine sweep and lands nearer the true angle. The
residual matters downstream: leftover skew makes a corrected ruling line
climb across rows in a staircase, which a later Hough pass can misread.

Two guards keep the search from reporting an angle a page does not
actually support -- both were added after measured counterexamples, see
`_inscribed_window` and `_is_supported`.
"""
import math
import statistics

import cv2

SEARCH_DEG = 10.0
COARSE_STEP_DEG = 1.0
FINE_STEP_DEG = 0.05

# Guard thresholds for `_is_supported`, in the scale-free units of
# `_profile_score`. Calibrated against synthetic pages spanning real skews
# (0.7 to 7.3 degrees, 1 to 4 rules, text-like blocks) and structureless
# pages (scattered dots, vertical rules only, all-foreground, blank): the
# weakest real page clears the ratio gate by 1.21x and the gain gate by
# 2.38x, and the structureless page that comes closest still misses one
# gate or the other by 1.28x. See tests/scan_cv/test_deskew.py.
MIN_PEAK_RATIO = 2.0
MIN_PEAK_GAIN = 1.0


def _inscribed_window(shape, search_deg):
    """Returns the (width, height) of the largest centered, axis-aligned
    window that no rotation in [-search_deg, search_deg] can fill with
    border pixels.

    Scoring the whole frame instead is wrong: `warpAffine` cuts wedges of
    `borderValue` into the corners of a same-size rotation, and those
    wedges are pixels the rotation invented rather than page content.
    Their area grows with |angle|, so on a page with little real
    horizontal ink the wedge geometry -- not the page -- becomes the
    dominant term in the profile, and the search reads a large angle off
    its own border artifact. Measured before this window existed: an
    all-foreground image scored 991,066,982 at -10 degrees against 0 at 0
    degrees, and pinned the answer to the search bound.

    The window keeps the image's aspect ratio, so it is `scale * w` by
    `scale * h`. A centered window fits inside the frame rotated by
    `theta` when its own corners, rotated back by `theta`, stay inside the
    frame: `scale * (w cos + h sin) <= w` and `scale * (w sin + h cos) <=
    h`. Each bound is a sinusoid of amplitude hypot(w, h), so its largest
    value over [0, search_deg] is at the interior peak when that peak is
    inside the range and at `search_deg` otherwise. Past both peaks the
    bounds saturate at hypot(w, h), which yields the window inscribed in
    the image's inscribed circle, so a `search_deg` far wider than the
    default degrades gracefully instead of needing a clamp. Verified
    border-free through a 90-degree search on page-sized images; an image
    only a pixel or two across cannot hold a centered window at all, but
    it also has no skew to read, and `_is_supported` answers 0.0 for it.

    The cost is the page's outer margin: at the default 10-degree search a
    letter page keeps its central 83% in each axis. Skew is a property of
    the whole page, so estimating it from the middle is sound, but a page
    whose only horizontal structure sits in the discarded margin -- rules
    along the very top and bottom edges and nothing between them -- reads
    a worse angle than it would from the full frame. Measured on such a
    page: -4.5 degrees against a true -3.0. Adding one rule anywhere in
    the middle restores an exact reading.
    """
    h, w = shape
    theta = math.radians(abs(search_deg))
    diag = math.hypot(w, h)
    reach_x = (diag if math.atan2(h, w) <= theta
               else w * math.cos(theta) + h * math.sin(theta))
    reach_y = (diag if math.atan2(w, h) <= theta
               else w * math.sin(theta) + h * math.cos(theta))
    # Spend one pixel per axis on slack: the window is placed at an integer
    # offset, so its center can sit half a pixel off the rotation center.
    scale = min((w - 1) / reach_x, (h - 1) / reach_y)
    return max(1, int(w * scale)), max(1, int(h * scale))


def _profile_score(binary, angle, window):
    """Peakedness of the horizontal row-sum profile at `angle`, measured
    over `window` only (higher is better aligned).

    The value is the profile's squared coefficient of variation,
    `var / mean**2`, not the raw variance. Raw variance scales with the
    square of how much ink the page carries, so no fixed threshold can
    read it; this form is scale-free, which is what lets `_is_supported`
    compare one page's peak against a constant.

    Rotation uses INTER_NEAREST: linear interpolation on a binary image
    invents gray fringe values that blunt the profile.
    """
    win_w, win_h = window
    h, w = binary.shape
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    # Warp straight into the window instead of warping the full frame and
    # slicing: same pixels, fewer of them.
    M[0, 2] -= (w - win_w) // 2
    M[1, 2] -= (h - win_h) // 2
    rotated = cv2.warpAffine(binary, M, (win_w, win_h), borderValue=0,
                             flags=cv2.INTER_NEAREST)
    profile = rotated.sum(axis=1)
    mean = profile.mean()
    if mean <= 0:
        return 0.0
    return float(profile.var() / (mean * mean))


def _sweep(binary, window, lo, hi, step):
    """Scans [lo, hi] on an integer-indexed grid and returns
    (best_angle, every_score). The grid is indexed rather than accumulated
    so the sampled angles are exact and identical in any reimplementation.
    Exact ties resolve to the smaller correction."""
    scores = []
    best_angle, best_score = lo, -1.0
    for i in range(int(round((hi - lo) / step)) + 1):
        angle = lo + i * step
        score = _profile_score(binary, angle, window)
        scores.append(score)
        if score > best_score or (score == best_score
                                  and abs(angle) < abs(best_angle)):
            best_score, best_angle = score, angle
    return best_angle, scores


def _is_supported(scores):
    """True when the coarse sweep found a real peak rather than noise.

    A page with a dominant horizontal direction concentrates its ink into
    a few rows at one angle only, so its winning score towers over the
    rest of the sweep. A page without one produces a curve that is flat,
    or that drifts with |angle| under residual rasterization effects; on
    such a curve the winning angle is whichever angle noise or drift
    happened to favor, which is exactly the reading this guard exists to
    suppress.

    Both a ratio and a difference must clear their threshold, because
    each alone admits a measured counterexample. Ratio alone accepts a
    page of vertical rules, whose curve is near zero everywhere yet whose
    peak is still several times its own median. Difference alone accepts
    a page of scattered dots, whose profile is lumpy enough that
    reshuffling the lumps moves the score by a wide absolute margin. Both
    thresholds compare against the sweep's median, which on a real page
    sits well off the peak.

    Neither test divides, so an all-zero curve (a blank or a uniformly
    inked page) fails the difference test rather than raising.
    """
    peak = max(scores)
    baseline = statistics.median(scores)
    return (peak >= MIN_PEAK_RATIO * baseline
            and peak - baseline >= MIN_PEAK_GAIN)


def detect_skew_angle(binary, search_deg=SEARCH_DEG,
                      coarse_step_deg=COARSE_STEP_DEG,
                      fine_step_deg=FINE_STEP_DEG):
    """Returns the rotation angle (degrees) that straightens `binary`.

    `binary` is a single-channel image as `preprocess()` returns
    (foreground 255, background 0). The result is the angle to rotate by,
    so a page skewed by +3 degrees yields about -3.0.

    Returns exactly 0.0 for a page with no dominant horizontal direction
    -- a blank or uniformly inked page, a page of scattered marks, a page
    whose only long strokes are vertical. Such a page has nothing to
    straighten to, and `_is_supported` rejects the sweep's winner rather
    than passing back an angle the image does not support. That is the
    safe failure, and it is enforced, not merely hoped for: the guard is
    what makes it true. A page that does carry horizontal structure still
    resolves skews well under a degree.
    """
    window = _inscribed_window(binary.shape, search_deg)
    coarse, scores = _sweep(binary, window, -search_deg, search_deg,
                            coarse_step_deg)
    if not _is_supported(scores):
        return 0.0
    lo = max(-search_deg, coarse - coarse_step_deg)
    hi = min(search_deg, coarse + coarse_step_deg)
    return _sweep(binary, window, lo, hi, fine_step_deg)[0]


def deskew(binary):
    """Returns (rotated_image, M) -- M is the 2x3 matrix used, so callers
    can later invert it via cv2.invertAffineTransform(M) to map detected
    coordinates back to the original (pre-deskew) pixel space."""
    h, w = binary.shape
    center = (w / 2.0, h / 2.0)
    angle = detect_skew_angle(binary)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(binary, M, (w, h), borderValue=0,
                             flags=cv2.INTER_NEAREST)
    return rotated, M
