"""Detects and corrects page skew on a preprocessed (binary) scan image.

See docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md's
"Pipeline architecture" section for why this step exists and what must
happen to detected coordinates afterward (the inverse-transform chain).

Method: a projection-profile angle search. For each candidate angle the
image is rotated and its horizontal row sums are taken; the row-sum
profile of a correctly straightened page has the sharpest peaks, because
every horizontal feature then falls entirely inside one row band. Profile
variance measures that peakedness, so the correction angle is the
variance-maximizing angle.

A minAreaRect-based estimate was tried first during design and rejected:
it read 34.2 degrees on a true 3-degree skew, because minAreaRect
describes the bounding box of all foreground ink rather than the
dominant text/rule direction.

The search runs coarse-to-fine (whole degrees, then a fine sweep around
the coarse winner) rather than as one fine pass. That costs fewer
rotations than a single fine sweep and lands nearer the true angle. The
residual matters downstream: leftover skew makes a corrected ruling line
climb across rows in a staircase, which a later Hough pass can misread.
"""
import cv2

SEARCH_DEG = 10.0
COARSE_STEP_DEG = 1.0
FINE_STEP_DEG = 0.05


def _profile_score(binary, angle):
    """Peakedness of the horizontal row-sum profile at `angle` (higher is
    better aligned). Rotation uses INTER_NEAREST: linear interpolation on
    a binary image invents gray fringe values that blunt the profile."""
    h, w = binary.shape
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(binary, M, (w, h), borderValue=0,
                             flags=cv2.INTER_NEAREST)
    return float(rotated.sum(axis=1).var())


def _best_angle(binary, lo, hi, step):
    """Scans [lo, hi] on an integer-indexed grid and returns the
    highest-scoring angle. The grid is indexed rather than accumulated so
    the sampled angles are exact and identical in any reimplementation.
    Exact ties resolve to the smaller correction."""
    best_angle, best_score = lo, -1.0
    for i in range(int(round((hi - lo) / step)) + 1):
        angle = lo + i * step
        score = _profile_score(binary, angle)
        if score > best_score or (score == best_score
                                  and abs(angle) < abs(best_angle)):
            best_score, best_angle = score, angle
    return best_angle


def detect_skew_angle(binary, search_deg=SEARCH_DEG,
                      coarse_step_deg=COARSE_STEP_DEG,
                      fine_step_deg=FINE_STEP_DEG):
    """Returns the rotation angle (degrees) that straightens `binary`.

    `binary` is a single-channel image as `preprocess()` returns
    (foreground 255, background 0). The result is the angle to rotate by,
    so a page skewed by +3 degrees yields about -3.0.

    Pages with almost no horizontal ink (a lone checkbox, say) have a
    nearly flat score curve, so the returned angle is near zero but is not
    meaningfully supported by the image. That is the safe failure: such a
    page has no dominant direction to straighten to.
    """
    coarse = _best_angle(binary, -search_deg, search_deg, coarse_step_deg)
    lo = max(-search_deg, coarse - coarse_step_deg)
    hi = min(search_deg, coarse + coarse_step_deg)
    return _best_angle(binary, lo, hi, fine_step_deg)


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
