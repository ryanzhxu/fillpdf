# FormFill — handover

State as of 2026-09-02. Everything below is measured, not estimated.

## What this is

A detector that finds where a person can write on a **flat** PDF — one with no
interactive form fields — so an app can put real input boxes there.

    ./.venv/bin/python demo/demo.py fixtures/safer.pdf

That runs the detector, renders the pages in a browser, and lets you type into
the boxes it found, drag them, add missing ones, and download a filled PDF. It
calls the same `engine.detect.detect()` the evaluation harness scores, so what
you see is what is measured.

## Where it stands

    tuning   f1 0.6556   precision 0.7905   recall 0.5601
    holdout  f1 0.6420   precision 0.7035   recall 0.5903
    label_plausibility 0.3511        106 tests passing

Corpus: 165 real government forms (419 fetched), 6,951 fillable fields, 9
producer families. 110 forms tuning, 55 holdout. Plus 25 synthetic hard forms.

Tuning and holdout agree within 0.014, which is the main evidence the detector is
not fitted to its tuning set.

**Do not compare these numbers to anything in the git history before commit
`19fe9fb`.** The corpus was corrected twice; older figures were measured against
fields no rule could ever find.

## Layout

    engine/detect/          the detector. rules.py is ~1,500 lines, 12 rules
    eval/                   the evaluation harness
      label.py              strips a fillable PDF into (flat pdf, answer key)
      score.py              matches detections to truth, writes scores/<sha>.json
      match.py              IoU matching, one-to-one, greedy
      gate.py               the acceptance gate
      guards.py             truth-free quality signals
      synth/                synthetic form generators (easy and hard)
      adversarial/          14 hostile PDFs
    demo/                   the browser demo
    docs/tuning/log.md      every iteration, merged and rejected, with reasons
    review/current/         SAFER pages rendered with current boxes

## The core idea, and why it works

A fillable PDF carries its own answer key. Strip the AcroForm and you have a flat
form (the detector's input) plus the widget rectangles (the truth). No human
labelling. `eval/label.py` does this.

Two things make it honest:

- **Never flatten appearance streams when stripping.** That would paint field
  borders onto the page and hand the detector a cue a real flat form never has.
- **Filter unreachable widgets per widget, not per form.** A widget with no
  vector rule, checkbox glyph, underscore or dot leader within 6pt is not a fair
  test case — nothing could find it. Filtering whole forms instead discarded 15
  of 21 usable ones.

## Running the harness

    # score the current detector
    ./.venv/bin/python -m eval.score --corpus eval/corpus/tuning \
        --holdout eval/holdout --out /tmp/out

    # gate a candidate against a baseline
    ./.venv/bin/python -m eval.gate --candidate /tmp/out/<file>.json \
        --baseline scores/HEAD_BASELINE.json

    # tests (skip adversarial; it is slow)
    ./.venv/bin/python -m pytest -q --ignore=eval/adversarial

A full scoring run takes about two minutes.

## What the gate checks

Overall f1, holdout f1, per-family f1, precision, `label_accuracy`, and the
label-free guards. It fails on a regression in any of them.

Two design decisions in it were learned the hard way and should not be undone:

- **Guards fail on a RISE plus NEW offenders, never on an absolute threshold.**
  An absolute `box_over_ink` threshold would have blocked a change worth 10
  points of precision, because removing 318 correctly-placed boxes shifts the
  average without anything getting worse.
- **An unavailable check warns loudly rather than passing silently.** A gate that
  passes because it had nothing to check is worse than no gate.

## Known limitations — read these before trusting a number

1. **On real forms, label quality is only partly measured.** `label_accuracy`
   needs truth labels, which only synthetic corpora have. `label_plausibility`
   (truth-free) covers the gap but flags 35% of labels, much of it by
   construction. A change can still raise recall while attaching poor names.
2. **Recall has a structural ceiling below 100%.** About 27% of missed fields are
   "reachable" only via a coincidental unrelated rule within the 6pt pad, not
   their own marker. Realistic ceiling with today's signal vocabulary is roughly
   85-90%.
3. **Two rules have never fired on a real form.** R11 (dot leaders) and R14 (comb
   fields), across all 165. Both constructs occur, and in each case the rule
   correctly declined the specific instance. Re-validate when the corpus grows;
   a third such rule was rejected on this basis.
4. **The reachability filter defines "reachable" by what current rules read.** A
   future rule reading a new signal will find its evidence pre-filtered. Add the
   signal to `MARK_CHARS` in `eval/label.py` whenever a rule learns one.
5. **Score files record `git_sha` at scoring time**, which is usually before the
   commit. Trust `detector_fingerprint` and `git_dirty` instead.

## Queued, with numbers already measured

- **Caption inside a cell's bottom band.** Airtight when tried: every candidate
  matched truth, zero false positives. Left out only because it pushed the
  hard-corpus tripwire past its ceiling. That tripwire means the CORPUS needs
  difficulty, not that the change is wrong. Take the change, strengthen the
  generator.
- **Mislabelling found by `label_plausibility`** in current output, e.g.
  `'Name*: Last Name*:'` (two headers merged), `'the applicant must be paid
  within the'` (a sentence fragment), and `'Landlord Phone # Date:'` on
  safer.pdf itself.
- **SAFER's "Option 1 / Option 2" consent boxes are undetected.** They are single
  25pt stroked rects; `grid_cells()` only treats a rect as a ruling line below
  3pt. R6 was written for these and never once found them.
- **R2 row-label column**, 239 of its false positives, precision 48.7% on
  real/Adobe. Two fixes measured and both rejected: a prose-wrapping-sibling test
  (61 FP but 21 TP lost naive, 21 FP for 0 TP tuned but zero holdout benefit) and
  a sibling-count test (worse than 1:1).
- **422 fields near partial ruling that never assembles into a cell.** Needs
  better cell reconstruction, not a new rule.

## Process rules that earned their place

- **One change per iteration**, gated, logged whether merged or rejected.
- **Never move the baseline while candidates are in flight.** Doing so made an
  agent measure a stale worktree against a fresh baseline and conclude, wrongly,
  that the baseline was broken.
- **Verify the mechanism, not just the metric.** A fix whose first version passed
  its score but swallowed a header row was caught by re-running the diagnostic,
  not by the number.
- **Watch `near_miss`.** If it falls while true positives fall, the change
  removed real fields that only needed better geometry.
- **Render the page and look at it.** Two false positives sat on section headings
  for fourteen gated iterations because no truth widget sits on a heading, so
  precision never moved. Looking found them in seconds.
- **A gate-passing change can still be wrong.** Three were rejected: one whose
  own survey disproved its premise, one that bought +0.0014 tuning with three
  tuned constants and zero holdout benefit, one whose opportunity evaporated on
  contact with the data.
