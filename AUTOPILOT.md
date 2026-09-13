# AUTOPILOT.md — autobuild descriptor for FormFill

## Goal

For this run (2026-09-12 night into 2026-09-13 07:00 local): **get FormFill
to a point where `detect()`, given a genuinely scanned/image-only PDF and a
real OCR+CV backend, finds fillable fields on it and places each field's box
in the right spot** — concretely, `eval/scan_cv/samples/agm_proxy_form.pdf`,
a real scanned AGM proxy form (0 extractable characters, one full-page raster
image) that is the motivating case for this whole effort.

This is a four-part decomposition, already underway:

1. **The synthetic-page interface** — DONE, merged to `main`
   (`docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md`).
   `engine/detect/page_protocol.py` defines `DetectablePage`; `detect()` takes
   an optional `page_backend(pdfplumber_page, page_number) -> DetectablePage |
   None` and runs the SAME unmodified `rules.py` against whatever a backend
   returns for a scanned page. Do not touch `engine/detect/rules.py` or
   `engine/detect/__init__.py` — this contract is locked and reviewed.
2. **The shared CV algorithm** (`engine/scan_cv/`) — IN PROGRESS, spec at
   `docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md`, plan at
   `docs/superpowers/plans/2026-09-12-scan-cv-algorithm.md`. Read the plan
   first each pass — it has exact file paths, exact function signatures, and
   task-by-task TDD steps. As of this run: Tasks 1-3 (golden corpus,
   preprocessing, deskew) are done on a feature branch, not yet merged to
   `main` — merge that work in (or redo it against the plan if the branch is
   gone) before starting Task 4. Tasks 4 (line detection), 5 (checkbox
   detection), 6 (assembly into `detect_lines_and_boxes(bitmap, dpi)`) are
   the immediate next highest-value passes. Follow the plan's tasks in order;
   each has its own tests and acceptance criteria already written out.
3. **Wire it into a real backend** (not yet specced in detail — use
   judgement once part 2's plan is exhausted). Once
   `engine/scan_cv/pipeline.py#detect_lines_and_boxes` exists: build a real
   `page_backend` — render the pdfplumber page to a 300 DPI bitmap
   (`pypdfium2` is already a dependency and can rasterize a page; use it,
   don't add a new rendering dependency), run OCR on it (pytesseract +
   system Tesseract — check `tesseract --version` first; if the system
   binary genuinely isn't installed and can't be, that is a real blocker,
   not something to fake around — say so plainly in PROGRESS.md rather than
   stub OCR output), split OCR words into per-character `chars` entries per
   the interface spec's documented convention (even split of each word's
   bbox), run `detect_lines_and_boxes` for `rects`, assemble a
   `DetectablePage`-shaped object, and wire it into a real caller (`demo.py`
   is the natural one — it already imports `detect` directly and is
   explicitly a throwaway feel-test harness, not scored production code).
4. **Validate against the actual goal file.** Run the real, wired-up
   pipeline against `eval/scan_cv/samples/agm_proxy_form.pdf`. There is no
   hand-labelled ground truth for this file (it is not part of
   `eval/corpus/tuning`/`eval/holdout`, and must never be added there — see
   Protected paths), so a pass cannot claim "all fields correct" from a
   script alone. What a pass CAN and MUST verify mechanically before calling
   this done: `detect()` runs without crashing or timing out, returns a
   `notice` code of `ocr_assisted` (not `scanned`), returns a non-empty
   `fields` list, and every field's `rect` falls within the page's
   `[0, width] x [0, height]` bounds (a rect outside the page is a
   coordinate-mapping bug, not success). Write this exact check as a real
   script or test (e.g. `eval/scan_cv/test_agm_proxy_form.py`) once part 3
   exists, and keep it green. A human (the user) still does the final visual
   check — a rendered-boxes-over-the-page image, the same way `demo.py`
   already renders fields for any other PDF — when they're back; do not
   claim "recognizes all the fields correctly" as an autonomous verdict, only
   "the pipeline runs end-to-end and produces mechanically-sane output."

There is no "done" beyond that mechanical bar and the wall-clock deadline.
Rank by value each pass — finishing part 2's plan tasks in order is the
highest-value work until that plan is exhausted, then move to part 3.

## Starting point — already investigated, do not re-derive

- The interface (part 1) is merged. Read
  `docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md`
  once, so you don't reinvent the `DetectablePage` contract or the
  `page_backend` calling convention.
- The CV algorithm's design already ruled out one approach:
  `cv2.minAreaRect`-based skew detection was tried and rejected (34.2°
  detected on a true 3° rotation) — the plan uses a projection-profile
  search instead. Task 3 (deskew), once merged, further found and fixed a
  real bug in even that approach: naive full-frame scoring picks up
  rotation-introduced border artifacts on sparse pages and reports large
  spurious angles. The fixed version scores an inscribed border-free window
  plus a statistical support gate. Read `engine/scan_cv/deskew.py`'s
  docstrings once merged — they document a known remaining limitation
  (a page whose only horizontal content sits right at the top/bottom edges
  reads a worse angle than a page with interior content) rather than hiding
  it. Do not re-litigate settled algorithm choices from scratch; extend or
  fix them with evidence the way the deskew task did.
- `opencv-python` (BSD license) is already an approved new dependency for
  `engine/scan_cv/` only — never import it from `engine/detect/*`, which is
  copied verbatim into a Pyodide browser build that cannot install
  non-pure-Python wheels.
- `pytesseract` is NOT yet a dependency. If part 3 needs it, check the
  system actually has Tesseract installed (`tesseract --version`) before
  assuming this works in this environment — do not spend a pass building
  code that can never run locally without checking that first.

## Value ranking (what "highest-value" means here)

1. **The next unfinished task in `docs/superpowers/plans/2026-09-12-scan-cv-algorithm.md`**, in order, exactly as written (each task already has
   its file list, its exact test code or a verified starting point, and its
   own acceptance criteria) — until that plan is exhausted.
2. **Once the plan is exhausted: build the real `page_backend`** (part 3
   above), one real piece at a time (rendering, then OCR, then assembly,
   then wiring into `demo.py`) rather than one giant pass.
3. **The mechanical validation script/test against
   `eval/scan_cv/samples/agm_proxy_form.pdf`** (part 4), once there is a
   real backend to validate.
4. **A regression test for a real bug found along the way** (crash, wrong
   coordinate space, wrong field count on a fixture with known-correct
   geometry) — same standard as always: assert a hand-verified fact, never
   the code's own current output treated as ground truth.
5. Documentation of a decision that was measured but is not written down.

Do NOT spend a pass on the OLD goal from the 2026-09-06 run (chasing
`eval.blind` zero-field real PDFs) unless the plan above is fully exhausted,
part 3/4 are done, and there is real remaining time before the deadline —
that work is real and valuable but is not tonight's priority.

## Blind real-PDF testing workflow (old goal — lower priority tonight, see above)

Only relevant once parts 1-4 above are exhausted or blocked with time still
on the clock.

1. `python -m eval.blind` (or read `.autobuild/blind_report.txt`, may be
   stale) — pick a `STRUCTURED BUT ZERO FIELDS` or `CRASH` entry.
2. Open the PDF's real structure by hand — `pdfplumber.open(path).pages[i]`,
   look at `.chars`, `.rects`, `.extract_words()` around where a field
   should be. Work out WHY `engine/detect/rules.py` doesn't reach it. A
   coordinate- or file-specific patch is not a fix; find the general shape.
3. Fix it, run `scripts/verify.sh` (mandatory — the fix touches shared rule
   code and must not regress the 165 scored forms).
4. Probe the one file directly:
   `python -c "from eval.blind import probe_one; print(probe_one('PATH'))"`
   and eyeball the actual fields, not just the count.
5. Fetching more PDFs, if needed: reuse `eval/fetch.py` (public
   government/institution domains only, `--limit 20` batches). Never touch
   `eval/corpus/tuning` or `eval/holdout` with fetched material.

## Protected paths — NEVER modify

The measurement apparatus is the only reason any claim in this repo is
trustworthy. A pass that edits it is not improving the product, it is moving
the goalposts. This has been attempted before and caught.

- `scores/**` — the gate's baseline. Editing it makes the gate meaningless.
- `eval/gate.py`, `eval/score.py`, `eval/match.py`, `eval/guards.py`,
  `eval/label.py`, `eval/limits.py` — the scorer, the gate and the guards.
- `eval/corpus/tuning/**`, `eval/holdout/**` — the scored ground truth. Never
  add `agm_proxy_form.pdf` or anything else from tonight's work here — it
  has no hand-verified ground truth and must not pretend to.
- `eval/corpus/hard/**`, `eval/corpus/synth/**` — the adversarial corpus and
  its generator/truth.
- `eval/synth/test_hard.py` — contains `MAX_ALLOWED_F1`. **Do not raise it. Ever.**
- `tests/render/goldens/**` — regenerating a golden to make a render test pass
  hides exactly the defect the test exists to catch.
- `scripts/verify.sh` — a pass cannot rewrite its own gate.
- `AUTOPILOT.md` — this file.
- `fixtures/**` — the reference form (`safer.pdf`) only. Do NOT add
  `agm_proxy_form.pdf` or any other scan sample here — that convention
  predates tonight and stays; use `eval/scan_cv/samples/` instead (see
  below).
- `engine/detect/rules.py`, `engine/detect/__init__.py` — the interface
  (part 1) is locked and reviewed. A real bug in these files found along the
  way is worth flagging in `.autobuild/PROGRESS.md` under `## Needs human`,
  not fixing directly, unless it is a crash-safety fix with zero behavior
  change to the default (no-`page_backend`) path — the same bar Task 3 of
  the interface plan already held itself to.

**Explicitly NOT protected, and the working area for this run:**
`engine/scan_cv/**`, `eval/scan_cv/**` (including `eval/scan_cv/samples/`,
where tonight's target PDF lives), `demo/**` (already documented as a
throwaway feel-test harness, not scored code), `eval/corpus/real/**`,
`eval/corpus/real_v4/**`, `eval/blind.py`, `.autobuild/blind_report.txt`.

## Constraints

- **One logical change per pass.** Small and reviewable. Not the whole goal.
- **Never weaken a measurement to make a change pass.** If a change is good but
  the gate rejects it, the honest outcome is to revert and record why. A
  clean "do not merge" is a successful pass.
- **A change with no measurable benefit on tuning/holdout needs a stated
  reason.** Tonight's whole effort is exactly this case by design — scanned
  PDFs have no ground truth in `tuning`/`holdout`, so zero gate movement
  there is expected and correct as long as `scripts/verify.sh` still passes
  clean (its test-suite half will exercise `tests/scan_cv/` and
  `eval/scan_cv/` directly). Say so in `.autobuild/PROGRESS.md`.
- **Do not attempt the checkbox-truth corpus fix.** `docs/HANDOVER.md`
  limitation 4 records that `keep_reachable()` deletes 1,498 of 2,783 checkbox
  widgets from ground truth. It requires regenerating truth and re-baselining
  `scores/**`, which is protected. Leave it for a human-supervised session.
- **Licensing matters now.** Do not add a dependency under GPL/AGPL/LGPL/SSPL.
  `opencv-python` (BSD) is already approved for `engine/scan_cv/`. If
  `pytesseract` (Apache 2.0) is added for part 3, note the license in
  `.autobuild/PROGRESS.md` the same way.
- **The demo and the tests must not drift.** `tools/inject.mjs` is the single
  implementation of field injection, used by both. If you change injection,
  both paths must still work, and `demo/demo.py` must still copy `tools/`
  into the served tree.
- If a change needs a judgement only a human should make — product scope,
  pricing, branding, anything about how a filled-in form's data is handled —
  skip it and say why.

## Machine config (read by autobuild.sh — keep exact key = value format)

```autobuild
verify = ./scripts/verify.sh
gate = pr
notify = sh -c 'printf "\n[%s] %s\n%s\n" "$(date "+%Y-%m-%d %H:%M")" "{title}" "{body}" >> .autobuild/notify.log'
branch_prefix = autobuild
email_to = ryan.xu282@gmail.com
email_cmd =
```

<!--
verify   scripts/verify.sh runs the test suite AND the detection eval, gated
         against scores/HEAD_BASELINE.json. Takes about two and a half
         minutes. pytest alone cannot see a recall regression, which is the
         failure mode that matters most in this repo.

gate     pr. Reconfirmed 2026-09-12: main has real branch protection (classic
         API: required_pull_request_reviews set with 0 required approvals,
         required_status_checks strict on context "tests"). A direct push to
         main is rejected by GitHub itself. autobuild.sh's own startup check
         queries this and would force `pr` regardless of what this file
         says — this value just makes that explicit instead of relying on
         the override. Note: the newer rulesets API
         (repos/.../rules/branches/main) returns an EMPTY list for this repo
         — the protection is classic, not a ruleset. Check both endpoints,
         never just one, exactly as the autobuild skill itself warns.

notify   Appends to .autobuild/notify.log. Left as a local log rather than
         `gh issue create` because the user reads milestones directly from
         this file and PROGRESS.md in an interactive session, not by polling
         GitHub issues.

email_cmd  EMPTY, deliberately. There is no RESEND_API_KEY and no msmtp on this
         machine, so any email command here would fail silently every pass and
         create a false impression that milestones were being reported.
         Milestones land in .autobuild/milestones.log and PROGRESS.md instead.
-->
