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

Two details keep the search from reporting an angle a page does not
actually support, both added after measured counterexamples: the border
fill in `_profile_score` and the check in `_is_supported`.
"""
import statistics

import cv2

SEARCH_DEG = 10.0
COARSE_STEP_DEG = 1.0
FINE_STEP_DEG = 0.05

# Guard threshold for `_is_supported`. Calibrated against synthetic pages
# spanning real skews (0.1 to 7.3 degrees; 1 to 4 rules; text-like blocks
# from sparse to a 0.6 ink-to-pitch ratio; rules confined to the page
# edges) and 81 structureless pages (scattered dots at 5 to 200 marks,
# vertical rules, salt-and-pepper, uniform ink, blank). The weakest real
# page scores 2.42x its sweep's median and the strongest structureless
# page that would otherwise answer a nonzero angle scores 1.74x, so the
# threshold sits between them. See tests/scan_cv/test_deskew.py.
MIN_PEAK_RATIO = 2.0


def _profile_score(binary, angle, border):
    """Peakedness of the horizontal row-sum profile at `angle` (higher is
    better aligned).

    `border` is what a same-size rotation puts in the corners it cannot
    fill from the source, and it must be the image's own mean level. A
    hard 0 there is a lie: it claims the corners are blank when they are
    simply unknown, and the wedge it invents grows with |angle|, so on a
    page with little real horizontal ink the wedge geometry rather than
    the page becomes the dominant term. Measured with borderValue=0: an
    all-foreground image scored 991,066,982 at -10 degrees against 0 at 0
    degrees, and pinned the answer to the search bound. Filling with the
    mean makes the wedge contribute the page's own average ink, so it
    neither adds nor removes contrast and the score stays comparable
    across angles.

    The value is the profile's squared coefficient of variation,
    `var / mean**2`, not the raw variance. Raw variance scales with the
    square of how much ink the page carries, so no fixed threshold can
    read it; this form is scale-free, which is what lets `_is_supported`
    compare one page's peak against a constant.

    Rotation uses INTER_NEAREST: linear interpolation on a binary image
    invents gray fringe values that blunt the profile.
    """
    h, w = binary.shape
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(binary, M, (w, h), borderValue=border,
                             flags=cv2.INTER_NEAREST)
    profile = rotated.sum(axis=1)
    mean = profile.mean()
    if mean <= 0:
        return 0.0
    return float(profile.var() / (mean * mean))


def _sweep(binary, border, lo, hi, step):
    """Scans [lo, hi] on an integer-indexed grid and returns
    (best_angle, every_score). The grid is indexed rather than accumulated
    so the sampled angles are exact and identical in any reimplementation.
    Exact ties resolve to the smaller correction."""
    scores = []
    best_angle, best_score = lo, -1.0
    for i in range(int(round((hi - lo) / step)) + 1):
        angle = lo + i * step
        score = _profile_score(binary, angle, border)
        scores.append(score)
        if score > best_score or (score == best_score
                                  and abs(angle) < abs(best_angle)):
            best_score, best_angle = score, angle
    return best_angle, scores


def _is_supported(scores):
    """True when the coarse sweep found a real peak rather than noise.

    A page with a dominant horizontal direction concentrates its ink into
    a few rows at one angle only, so its winning score stands well above
    the rest of the sweep. A page without one produces a nearly flat
    curve whose winner is whichever angle noise happened to favor, which
    is exactly the reading this check exists to suppress.

    The comparison is against the sweep's median, which on a real page
    sits well off the peak, and it is a ratio rather than a fixed
    quantity. An earlier version of this guard also required the peak to
    beat the median by an absolute margin. That was wrong: the score's
    ceiling depends on what fraction of rows carry ink, so an ordinary
    single-spaced text page tops out near 1.2 and a fixed margin of 1.0
    silently rejected real skew on it. Nothing here may assume a page is
    mostly white.

    The test is strict, so an all-zero curve -- a blank or a uniformly
    inked page, where every angle scores 0.0 -- is rejected rather than
    accepted on a degenerate 0 >= 0.

    Two structureless shapes out of 81 tested still get through, both
    pages that carry no horizontal ink at all: a page ruled only with
    45-degree diagonals answers +4.40, and five vertical rules on a page
    tilted 3 degrees answer -1.60. An absolute floor on the peak would
    catch both, and it is deliberately not here -- that is the construct
    that silently rejected real skew on dense text, and a page of pure
    diagonals is not a form scan. Prefer a wrong angle on a page with no
    horizontal structure over a silent 0.0 on a page that has some.
    """
    return max(scores) > MIN_PEAK_RATIO * statistics.median(scores)


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
    safe failure, and it is enforced, not merely hoped for.

    Pages that do carry horizontal structure keep their reading whether
    the structure is thin or dense: a single rule, rules confined to the
    page edges, and single-spaced text all resolve, down to skews of
    about 0.1 degrees.
    """
    border = float(binary.mean())
    coarse, scores = _sweep(binary, border, -search_deg, search_deg,
                            coarse_step_deg)
    if not _is_supported(scores):
        return 0.0
    lo = max(-search_deg, coarse - coarse_step_deg)
    hi = min(search_deg, coarse + coarse_step_deg)
    return _sweep(binary, border, lo, hi, fine_step_deg)[0]


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
