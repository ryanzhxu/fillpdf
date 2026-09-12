# Scan detection: shared CV algorithm contract (sub-project 2a of the revised decomposition)

Date: 2026-09-12
Status: approved, ready for implementation plan

## Background

`docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md` (sub-project
1, merged) defines `DetectablePage`, a structural contract that lets
`engine/detect/rules.py`'s existing, unmodified rules run against a synthetic
page assembled from OCR + image-detection output. That spec's original
four-part decomposition listed sub-project 2 as "local/CLI backend" and
sub-project 3 as "browser backend," each independently implementing OCR and
CV.

While designing those two, two decisions from the user changed the shape of
the work enough to warrant a new sub-project between them:

1. **The local and browser backends must produce matching detection
   quality** (same conceptual algorithm, not independently-tuned
   reimplementations) — otherwise a user gets different results for the same
   file depending on whether they ran it locally or on the public site.
2. **v1 targets a broader CV pipeline** (deskew, denoising, adaptive
   thresholding, multi-scale line detection) rather than a narrow, minimally
   tuned heuristic.

Both decisions mean the local (Python/opencv-python) and browser
(JS/OpenCV.js) implementations need to follow the *same* nontrivial
algorithm, or "matching quality" is just an unverified hope. This document
is that shared algorithm contract. It supersedes the old sub-project 2's
scope by splitting it:

- **2a (this document):** the shared algorithm — pipeline architecture,
  coordinate-mapping contract, detection-to-`DetectablePage` mapping
  conventions, and a golden test corpus any correct implementation must
  match. No production backend code.
- **2b:** local/CLI implementation of 2a (opencv-python + pytesseract),
  wired into `demo.py`/CLI/eval.
- **3:** browser implementation of 2a (OpenCV.js + tesseract.js), wired into
  the Pyodide bridge.
- **4:** the real-scan tuning/eval corpus (unchanged from sub-project 1's
  spec) — precision/recall against genuine scanned forms, distinct from
  this document's golden corpus (see "Testing" below).

## Scope

**In scope:** the CV half only — turning a rendered page bitmap into
line/box candidates in `DetectablePage.rects` shape. Pipeline architecture,
the coordinate-space contract (verified), line- and checkbox-detection
conventions (verified against `rules.py`'s actual rect-consuming rules), and
a golden-corpus testing strategy.

**Out of scope, deferred to 2b:** the exact skew-detection algorithm and its
tuned parameters. A first attempt at skew detection (`minAreaRect` on
thresholded foreground pixels) was prototyped against a synthetic 3°-rotated
test image during this design and detected 34.2° — badly wrong. Unlike
`rules.py`'s existing thresholds (which this project could read and verify
because they already existed, tuned against real data), a CV skew-detection
algorithm is new work that needs real iteration against real data to get
right. This document fixes the *contract* around it (inputs, outputs,
where the inverse transform must be applied, the tolerance the golden
corpus checks against) and leaves the specific algorithm and its
parameters as 2b's empirical work — the same way `rules.py`'s own
thresholds were originally arrived at by tuning against a real corpus, not
decided in advance.

**Out of scope, deferred to 2b/3 individually:** OCR integration
(pytesseract vs. tesseract.js) and the word-to-`chars` splitting mechanics —
already fully specified in sub-project 1's spec, and thin enough per-backend
that it does not need a shared algorithm document.

**Out of scope, deferred to 3:** any Pyodide-bridge or JS-specific wiring.

**Out of scope, deferred to 4:** precision/recall tuning against real scanned
forms.

## Pipeline architecture

Both bindings implement the same stages:

1. **Render** the PDF page to a bitmap at a fixed **300 DPI** (Tesseract's
   documented accuracy sweet spot). Scale factor PDF points → pixels:
   `SCALE = 300 / 72 ≈ 4.1667`. This factor is the single source of truth
   for the final pixel → point conversion; a page's pixel dimensions are
   always `round(width_pt * SCALE)` × `round(height_pt * SCALE)`, so a
   binding never needs to pass page width/height separately from the
   bitmap itself — they are derivable from the bitmap's pixel dimensions
   and the fixed DPI.
2. **Grayscale + light denoise** (small-kernel median blur) to reduce scan
   artifacts without eroding line edges.
3. **Deskew:** detect the dominant skew angle (algorithm and parameters are
   2b's empirical work — see Scope above), then rotate the bitmap around
   its own center by `-angle` using a **same-size** affine warp
   (`getRotationMatrix2D` + `warpAffine` / `cv.warpAffine` — identical API
   shape in `cv2` and OpenCV.js). Same-size (rather than expanding the
   canvas to avoid cropping) keeps the inverse transform a plain 2D affine
   inversion with no separate offset bookkeeping; real-world scan skew is
   small enough (a few degrees) that corner cropping is not a practical
   concern. The rotation matrix `M` must be retained for step 6.
4. **Adaptive threshold** (Gaussian, not global Otsu) to binarize despite
   uneven scan lighting/shadow.
5. **Detect** lines and checkbox-like boxes on this binarized, deskewed
   image (see the two sections below).
6. **Coordinate mapping**, applied to every detected point before it is
   returned: (a) apply `invertAffineTransform(M)` to undo the deskew
   rotation, recovering the point's location in the original (pre-deskew,
   post-render) pixel space; (b) divide by `SCALE` to convert pixels to PDF
   points; (c) the result is already in pdfplumber's top-down,
   origin-at-page-top convention, since the render in step 1 preserves that
   orientation. This chain — verified as standard affine-transform algebra,
   not something requiring empirical proof — is what keeps this pipeline
   honoring sub-project 1's contract that a `DetectablePage`'s coordinates
   must be PDF points matching the real page.

## Line detection → synthetic `rects`

Write-on lines and table rules are found via Canny edge detection +
probabilistic Hough transform (`HoughLinesP` / `cv.HoughLinesP` — same API
shape in both bindings):

- Classify each detected segment as horizontal or vertical by its angle
  (within a small tolerance of 0°/90°); discard anything more diagonal —
  real write-on lines and table rules are never drawn at an odd angle.
- Merge nearby, collinear segments into one logical line, the same way
  `rules.py`'s own `_merge_ruling_lines` merges fragmentary ruling-line
  rects, so two Hough fragments of one physical line do not become two
  spurious rects.
- Minimum line length is a **fraction of page width/height**, not a fixed
  pixel count — a 300 DPI Letter page and a 300 DPI Legal page differ in
  pixel dimensions but not in what counts as "a real line" relative to the
  page.
- Emit each merged line as a `rects` entry shaped exactly like a real thin
  ruling line already is in `rules.py`'s own convention: vertical
  (`width < 3, height >= 5`) or horizontal (`height < 3, width >= 5`), in
  PDF points after the full coordinate-mapping chain above. This is
  deliberate: `grid_cells()`, R5b, and every other rect-based rule that
  already reads thin ruling lines works completely unmodified, with zero
  new logic in `rules.py`.

## Checkbox detection → synthetic `rects`, and a real gap in the existing rules

Checkbox candidates come from `findContours` + `approxPolyDP` on the
binarized image, filtered to roughly axis-aligned quadrilaterals whose side
length falls in a pixel band derived from R18's existing PDF-point band
(`R18_CHK_MIN`–`R18_CHK_MAX` = 18–32pt in `rules.py`, scaled by `SCALE` ≈
75–133px), with the same squareness tolerance R18 already uses (converted
to pixels the same way).

**Finding, confirmed by reading `rules.py`:** R18 only fires on rects with
`fill=True, stroke=False` (`raw_shaded = [r for r in page.rects if
r["fill"] and not r["stroke"] and _r18_is_chk_band(r)]`) — a solid filled
square. It was scoped that way for one specific real producer's solid-fill
consent boxes. The overwhelmingly common real-world checkbox on a scanned
form is a **hollow outlined square** (`stroke=True, fill=False`), which R18
does not handle, and no other existing rule does either (R1 needs a font
glyph; R16/R17 need `grid_cells()`-derived blank cells with specific
caption shapes). A CV-detected hollow checkbox, emitted faithfully as
`fill=False, stroke=True`, would silently match nothing.

**A correct fix for this already exists, parked.** `blocked/stroked-square-checkbox`
broadens R18 to accept a stroked band (7-20pt, square within 2pt) and is
verified correct by hand-inspection against real forms (see its commit
message). It is not merged because the *scored corpus's ground truth* is
itself broken for this exact shape — `eval/label.py`'s `keep_reachable()`
deletes any rect-based checkbox widget under 3pt-thin from truth, so these
widgets were never scoreable to begin with, and `scripts/verify.sh` sees a
false regression against a broken baseline. Fixing that requires
regenerating protected `scores/**` truth in a human-supervised session
(per `AUTOPILOT.md`), which is out of scope here and orthogonal to
OCR/CV — it would fix real vector-PDF hollow checkboxes too, not just
scanned ones. This is worth tracking as a separate follow-up outside this
scan-detection effort.

**Decision for this sub-project:** a CV-detected checkbox-shaped candidate
is always emitted as `fill=True, stroke=False`, regardless of whether the
original ink was hollow or solid. These are synthetic, backend-fabricated
rects, not literal records of a PDF paint operation — `fill`/`stroke` here
are simply the signal channel R18 already reads for "this is a checkbox,"
and a hollow checkbox communicates the identical thing to a human as a
solid one. This requires no change to `rules.py` (which sub-project 1
established as off-limits for exactly this kind of reason: it is the
real-corpus-tuned code every score depends on), and does not block on the
ground-truth fix above landing. A future maintainer reading a checkbox
candidate's implementation must find this convention documented inline,
not discover it by surprise.

## Golden corpus: how "matching quality" is actually verified

A CV pipeline is inherently approximate, so "the Python and JS
implementations produce matching results" needs an operational definition,
not just shared spec text:

- This sub-project's own deliverable includes a small **golden corpus**: a
  handful of synthetic bitmap images generated deterministically (via
  Pillow) — a straight line, a checkbox square, and at least one case with
  a known rotation applied to exercise deskew — checked into the repo,
  each with an expected-output JSON (line/box coordinates in PDF points,
  per the coordinate-mapping chain above).
- A real Python reference implementation (opencv-python) produces these
  expected outputs within a defined numeric tolerance (to be fixed during
  2b's implementation once real algorithm parameters exist — this document
  fixes the mechanism, not the tolerance number, for the same reason the
  skew algorithm itself is deferred).
- Sub-projects 2b and 3 both run their implementation against this same
  golden corpus and must match within that tolerance. This is what
  operationalizes "matching quality" between the two languages, rather than
  leaving it an unverified intention.
- The larger real-scanned-form corpus (precision/recall tuning against
  genuine scans, sub-project 4) is unchanged and serves a different
  purpose: this golden corpus is narrower and purely about algorithmic
  parity between the two bindings, not about real-world detection quality.

## Interface boundary for 2b and 3

Both bindings implement one function with the same contract:

```
detect_lines_and_boxes(bitmap, dpi: int) -> list[dict]
```

- `bitmap`: the rendered page image (a `numpy` array in Python; an
  `ImageData`/canvas-backed equivalent in JS) at the given `dpi`. Page
  width/height in points are derivable from the bitmap's pixel dimensions
  and `dpi` — never passed as a separate, independently-suppliable
  parameter, which would open a class of bugs where the two disagree.
- Returns a list of `rects`-shaped dicts (`x0, x1, top, bottom, width,
  height, fill, stroke`), in PDF points, top-down origin, ready to feed
  directly into a `DetectablePage.rects` list — the caller (2b's or 3's own
  `page_backend` implementation) does not need to know anything about the
  internal rotation-matrix bookkeeping; that is fully resolved inside this
  function per the coordinate-mapping chain above.
- This function is pure with respect to its inputs — no file I/O, no
  network, deterministic given the same bitmap and dpi (aside from
  ordinary floating-point rounding).

## Out of scope for this sub-project

- The exact skew-detection algorithm and its tuned parameters (2b's
  empirical work).
- OCR integration and the word-to-`chars` splitting mechanics (already
  specified in sub-project 1; thin per-backend work).
- Any production backend code, `demo.py`/CLI wiring, or Pyodide-bridge
  wiring.
- Real-scanned-form precision/recall tuning (sub-project 4).
