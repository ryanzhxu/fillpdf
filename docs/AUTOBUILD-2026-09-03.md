# Autobuild session — 2026-09-03

Two `autobuild` invocations, back to back, on `/Users/ryan.xu/Developer/formfill`.
Both stalled cleanly (12 consecutive passes with nothing to land) rather than
running to the intended 6pm deadline — see **What went wrong** at the end.
Everything below is verified against the live repo, not copied from the
loop's own log.

## Where things stand right now

Commit `38945ba`, clean tree, independently re-run:

    170 tests passing
    tuning   f1 0.644376   precision 0.782767   recall 0.547567
    holdout  f1 0.642074   precision 0.704287   recall 0.589960
    GATE PASSED against scores/HEAD_BASELINE.json

That is up from the start of the day (`56974b4`: tuning f1 0.644667 / P
0.777027, holdout f1 0.642758 / P 0.703445) — precision rose on both corpora
with recall essentially flat, the good-shaped result this project has flagged
before as unusual.

`python -m eval.blind` (built today, `a0051c9`) now reports, on the 145 real
PDFs that were never fillable and so carry no ground truth:

    145 PDFs probed | 0 crashed | 12 structured docs with ZERO fields found
    | 26 zero-field docs that look like prose, not forms (correctly empty)

Down from 23 structured-zero-field docs this morning — today's fixes cleared
roughly half of that list, and the SymbolMT-glyph gap noted below put one file
back into it.

## What landed today, in order

Two separate `autobuild` runs. Attribution note: the first run's commits were
authored under a prior model-attribution setting (`Claude Opus 5`); everything
from `19b75c0` onward is under the current one (`Claude Sonnet 5`).

### Run 1 (00:25–02:32) — general improvement, 8 landed

1. **Scanned/image-only PDF guard** (`608c127`) — `detect()` attaches an honest
   `notice` instead of silently returning nothing on a scan.
2. **Notice surfaced in the demo UI** (`4b96d1d`).
3. **Demo build() wrapped in try/catch** (`33136ba`) — a failed inject no longer
   leaves a dead Download button with no explanation.
4. **Keyboard navigation follows reading order** (`0598669`) — Tab/"Next field"
   used to jump around the page in rule-emission order.
5. **Duplicate field-name data loss fixed** (`a893fcc`) — renaming a box to an
   existing name silently dropped the second field's value on download.
6. **Non-WinAnsi text crash fixed** (`87bb5de`) — one CJK/emoji/Cyrillic
   character in any field threw `WinAnsi cannot encode` out of `doc.save()`
   and the user got no PDF at all, every field lost, not just the one with
   the character.
7. **fields.json load failure handled** (`d5a3117`) — a missing/404 fields.json
   used to leave "rendering…" spinning forever.
8. **Multiline fields render as `<textarea>`** (`1d9a91a`) — were single-line
   inputs that silently scrolled off typed text.
9. **Radio groups for Yes/No pairs** (`d10f080`) — the flagship correctness
   bug named in this run's goal: a person could tick both "Yes" and "No" on
   the same question. Fixed conservatively (exactly one Yes + one No under an
   identical `?`/`:`-ending prompt); a naive broader rule was tried and
   rejected after it wrongly grouped a multi-select "cc:" list.

### Between runs — blind-testing infrastructure

- **`eval/blind.py`** (`a0051c9`) — runs the detector plus the existing
  truth-free guards across the 145 real, never-fillable PDFs nothing had ever
  tested against. First run flagged 64 "zero fields"; three checked by hand
  turned out to be correctly-empty prose (an instructions sheet, a court
  notice), so a structural filter (reusing `fetch.py`'s own signal counts) was
  added to separate real gaps from correctly-empty documents.
- **`AUTOPILOT.md` rewritten** (`19b75c0`) around that finding — the 23-item
  ranked list became the loop's stated starting backlog, and protected paths
  were corrected (`eval/corpus/real/**` is raw unscored material, not the
  scored ground truth, and was wrongly blanket-protected before).

### Run 2 (07:46–09:49) — chasing the blind-test backlog, 4 landed

1. **Curve-drawn checkbox/radio detection** (`da5982e`) — R18 previously only
   read `page.rects`. A whole producer template draws its Yes/No, Mr/Mrs/Ms,
   and Male/Female choices as rounded-rect Bezier curves (`m l c l c l c l c
   h`), invisible to every existing rule. Found on 13 of 16 curve-heavy blind
   candidates — a generalizable gap, not a one-file quirk. A same-size-sibling
   guard was needed to avoid claiming a lone decorative icon.
2. **`no_fields` notice** (entry 14) — when detection genuinely finds nothing
   on a non-scanned document, `detect()` now says so instead of returning an
   empty result the demo shows as a blank page.
3. **R3 multi-line-header false positive fixed** (entry 15) — a form whose
   checkbox rows have no rule between them let `grid_cells()` merge 3–4
   unrelated lines into one cell; R3 read the garbled result as a plausible
   column header and inherited it onto an unrelated row. Fixed with a
   line-spacing-ratio test. A naive first attempt (reject any multi-line
   header) broke a **real** 3-line wrapped header on `fixtures/safer.pdf`
   ("Relationship / to / Applicant") — caught by the existing fixture-count
   test, not assumed away.
4. **R5b decorative-underline gap widened** (entry 16) — a producer's heading
   underlines sit 2.93–3.07pt below the heading text; the existing tolerance
   was exactly 3pt, so roughly half the headings on affected forms leaked
   through as fake write-on lines. Widened with margin. **This one moved the
   scored gate in the improving direction**, not just parity — the same shape
   exists at lower frequency in the tuning corpus itself.

### Investigated and correctly rejected (real technical content, worth reading before retrying)

- **DSHS stroke-drawn checkboxes** (unfilled 8–8.7pt squares, one template
  translated into Khmer/Vietnamese/Russian/Spanish/Somali — the file that
  started this whole blind-testing effort). A stroke-only detection path was
  built and gate-tested: **failed** (f1 0.6447→0.6278, precision 0.7792→0.7264).
  556 same-shape candidates exist in the *scored* corpus alone, most with
  plausible option-like labels (Yes/No, Landlord/Tenant), geometrically
  identical to an ordinary ruled table cell — this is R6's exact 2024 failure
  mode. **Traced to the protected `keep_reachable()` checkbox-truth gap**
  (limitation 4, `docs/HANDOVER.md`) rather than assumed: cannot be
  distinguished from a real precision loss without touching protected `eval/`
  code.
- **Unicode ballot-box glyph `☐` (U+2610)**, found undetected in 17 scored
  corpus files (12 tuning, 5 holdout) plus the Somali-translation form that
  surfaced it. Added to `CHECK_GLYPHS`, gate-tested twice (with `□` too, and
  alone): **failed both times** (f1 down to 0.6373 / 0.6350). Traced by hand:
  on one tuning file, 2 of 8 identical-looking glyphs have a ground-truth
  widget and the other 6 do not — **the same protected truth-gap, confirmed on
  a second, unrelated glyph shape.** This is now a *pattern*, not one file's
  bad luck.
- **`page.lines` support for R5b** (a producer draws real PDF line primitives
  for its write-on lines, which `pdfplumber` exposes completely separately
  from `page.rects` — every rule reading `_qualified_write_on_lines` is 100%
  blind to this regardless of layout quality). Confirmed on 83 of 165 scored
  corpus files (10,355 line objects). Fix implemented, worked on the three
  motivating files (0→1, 0→7, 0→1 fields) — **but gate-failed**: R5b's own
  detection count jumped +546 corpus-wide, only +72 matched (87% false-positive
  rate on the new detections). Cause found by hand: short decorative line
  segments nested *inside* an already-bordered table row, mislabelled from a
  stale distant caption. Scale of the flood (546 new claims, 13% real)
  indicates more than one false-positive shape — not safely closed by one
  narrow guard in a single pass.

## Open, generalizable gaps — ranked

1. **The checkbox-truth defect in `eval/label.py`/`keep_reachable()` is now the
   clear top priority.** Three independent pieces of evidence today (the
   original standalone-stroked-box finding two sessions ago, DSHS stroke
   squares, the Unicode ballot-box glyph) all hit the exact same wall: real
   detections are indistinguishable from false positives because 1,498 of
   2,783 checkbox widgets are missing from ground truth. This needs a
   human-supervised session that can regenerate truth and re-baseline
   `scores/**` — deliberately left out of scope for the autonomous loop.
   Once fixed, re-try both reverted attempts above; they may just work.
2. **`page.lines` support for R5b, properly guarded.** The gap is real and
   affects 83/165 scored forms — worth doing once a guard against the
   nested-decorative-segment shape is found (e.g., skip an hrule wholly
   contained, with much smaller x-span, inside a bordered `page.rects` row).
   Do not just retry the same patch; the false-positive rate suggests more
   than one shape needs excluding.
3. **`SymbolMT` PUA checkbox glyph** on `9e5fa53418722365.pdf` (surfaced when
   the R5b fix above resolved that file's other bogus fields) — likely another
   `CHECK_GLYPHS` candidate, likely to hit the same truth-gap wall as `☐` did.
   Worth trying once item 1 is done, to get a fourth data point either way.
4. **Curve-to-rect merge for TEXT-entry boxes**, not just checkboxes — the same
   producer that draws curve checkboxes also draws its text boxes as rounded
   curves (e.g. a 382×16.7 "First and middle names" box). Real value, but a
   materially bigger change than the checkbox path (no existing rule analog,
   more false-positive surface) — was deliberately left as a stated follow-up
   rather than folded into the curve-checkbox pass.
5. **R3 "header survives past its table's end"** on `fixtures/safer.pdf` page
   2 — a `'Gender'` box spanning 264.9pt across the "2. Spouse or Partner
   Information" heading. Reported by the product owner, still open. Two
   guards were tried in an earlier session and both cost real recall (107 and
   then a second-order false positive); needs a different signal (whether the
   candidate row's own bounds contain printed text belonging to something
   else), not a tighter version of what's been tried.
6. **The remaining 12 structured-zero-field blind-pool documents** — re-check
   after item 1 lands; several are very likely more instances of the same
   checkbox-truth-blocked shapes rather than new leads.
7. **`sec_whitespace_field`** — still the last unsolved hard-corpus construct
   (isolated recall 9%), unrelated to today's work.

## What went wrong — read before relaunching unattended

Both runs stalled well short of the stated deadline, and I did not check on
either while they ran — that gap is on me, not the tooling; a scheduled check-in
during a long unattended run would have caught it hours earlier.

- **Run 1** stalled at 02:32 (20 passes, 8 landed) — a clean, expected stall
  once the obvious wins were exhausted.
- **Run 2** stalled at 09:49 (20 passes, 4 landed), then sat **idle for ~8.7
  hours** with nobody watching. Separately, this run's pass durations show a
  real anomaly worth investigating before trusting a future unattended run:
  passes 1–9 each took several minutes (real investigation, matching the
  detailed ledger entries above); passes 10 through 20 — all twelve of the
  stall-triggering passes — each completed in **2–3 seconds**. That is far too
  fast for a genuine `claude -p` investigation pass. The stderr from each of
  those calls is deleted immediately by the driver (`rm -f "$errf"` in
  `run_agent()`), so the actual cause cannot be reconstructed after the fact.
  The leading hypothesis, not confirmed: a usage/rate-limit response whose
  wording didn't match the driver's detection regex, so it was treated as a
  normal (empty) pass twelve times instead of triggering the built-in
  wait-for-reset behavior. If relaunching, watch the first few pass durations
  in `.autobuild/run*.log` — a sudden drop to a few seconds per pass is the
  tell, and is worth investigating rather than assuming the backlog is
  genuinely exhausted.
- Two `autobuild.sh` processes were found running against a **different**
  repo (`/Users/ryan.xu/Developer/canadian-benefits`) during today's session —
  not started by this session, almost certainly another parallel Claude Code
  session. Not touched, just worth knowing it exists if you see it again.

## To resume

```bash
cd /Users/ryan.xu/Developer/formfill
./scripts/verify.sh                        # confirm still green before anything else
python -m eval.blind                        # refresh the ranked blind-pool list
~/.claude/skills/autobuild/bin/autobuild.sh \
  --repo /Users/ryan.xu/Developer/formfill \
  --until "YYYY-MM-DD HH:MM" \
  --mode yolo --stall 12
```

Check pass durations in the log after the first few passes; if they cliff-drop
to a few seconds each, stop the loop (`pkill -f autobuild.sh`) and investigate
rather than letting it burn through the stall counter silently.
