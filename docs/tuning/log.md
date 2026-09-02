# Tuning log

One rule change per iteration. Gated on: overall f1, holdout f1, per-family f1,
precision, and the label-free guards. The holdout is never tuned against.

| # | Change | tuning f1 | holdout f1 | Verdict |
|---|---|---|---|---|
| 1 | R9 rejects boxes on running text | 0.6274 -> 0.6360 | 0.2803 (+.0303) | **MERGED** |
| 2 | R2 accepts multi-line labels | 0.5133 -> 0.5622 (+.0488) | 0.1810 -> 0.1788 (-.0022) | **REJECTED** |
| 3 | R1 uses the glyph's real bbox | 0.5133 -> 0.5304 (+.0170) | 0.1810 (unchanged) | **MERGED** |
| 3b | harness: per-rule metrics could exceed 1.0 | n/a | n/a | **MERGED** |
| 4 | R3 refuses office-use columns | 0.5304 -> 0.5558 (P +.105) | unchanged | **MERGED** |
| 5 | R10 reads the label from the cell to the left | 0.5558 -> 0.5662 (+.0103) | 0.1810 -> 0.1802 (-.0008) | **MERGED, flagged** |
| 6 | R3 clusters ragged columns (tol 16pt) | 0.5662 -> 0.6277 (+.0616) | 0.1802 -> 0.1919 (+.0116) | **MERGED** |
| 7 | R11 reads dot-leader write-on lines | 0.5387 -> 0.5546 (+.0159) | unchanged | **MERGED** |
| 7b | exempt dot leaders from the ink guard | 0.5546 -> 0.5541 (-.0005) | -.0002 | **REJECTED** |
| 8 | merge doubled ruling lines in grid_cells | 0.5546 -> 0.5586 (P +.019) | +.0003 | **MERGED** |
| 9 | R1 gives checkboxes real labels | f1 unchanged, R1 label acc 0.0 -> 1.0 | unchanged | **MERGED** |

## Iteration 1 — MERGED

A naive "reject any box over 35% ink coverage" raised precision but cut recall
enough to lower f1, and the gate refused it. Investigating instead of relaxing
the gate showed it killed 194 wrong boxes but also 125 correct ones, whose truth
widgets sat on printed labels themselves. Acrobat's auto-detection places widgets
over labels, so part of the apparent damage was the answer key, not the rule.

The working distinction: a box on a heading covers part of a line that continues
past the box edge; a box legitimately holding its own short label covers text
that ends inside it. Only the first is rejected.

Removed both worst false positives on safer.pdf, including the "4c." heading box
Ryan found by hand. Holdout improved more than tuning, which is the signal that
it generalises.

## Iteration 2 — REJECTED, and worth keeping the record

R2 required a label to fit one ~6pt band, giving 0% recall on any cell with a
wrapped label. Allowing a contiguous multi-line label block, capped at 2 lines
and 60 characters so prose cannot masquerade as a label, gained a lot on tuning:

    tuning   f1 +0.0488   recall +0.0635   precision +0.0008

But on the holdout it added only false positives:

    holdout  f1 -0.0022   precision -0.0145   recall +0.0000 (exactly unchanged)

Tested against two independent holdouts -- 3 forms / 273 widgets, then 7 forms /
830 widgets -- with the same result both times. Holdout recall was exactly
unchanged in both, meaning the multi-line-label pattern is present in the tuning
forms and absent from the holdout ones.

This may still be a good change in the wild. The corpus cannot demonstrate it,
so it does not merge. Revisit when the corpus covers more issuers.

**Process note:** the temptation here was real. The change looked like a clear
win (+0.049 f1) and the block was -0.0022 on a small holdout. Relaxing the gate
at the moment it first blocks a change of mine would have made every later
result untrustworthy. The correct response to "the holdout is too small to
adjudicate" was to grow the holdout, which is what happened -- 9 forms became
21, and the verdict did not change.


## Iteration 3 — MERGED

R1 emitted a fixed 10x10 rect anchored at the glyph's bottom-left rather than the
glyph's real bounding box. It only ever looked correct because every checkbox
glyph on safer.pdf is exactly 10pt. R1 precision went 0.6093 -> 1.0000.

Found by the hard-mode corpus, which varies glyph size from 8pt to 14pt. Neither
the easy corpus nor safer.pdf could have surfaced it, since both are uniformly
10pt. That is the hard corpus earning its keep.

## Iteration 3b — harness fix

Iteration 3 printed "R1 only f1 1.5330, recall 3.2826". A fraction above 1.0 is
a bug, not a result.

per_rule counts matches by the detection's rule but counts truth from the truth
widget's `expects_rule`, which only synthetic corpora carry. On real stripped
forms there is no rule attribution on the truth side, so matched exceeds truth.

Clamping to [0,1] alone would have reported recall 1.0 — a lie dressed as a fix.
Buckets now carry `attribution_ok`, false whenever matched exceeds truth, so an
unavailable recall reads as unavailable rather than as a perfect score.

**Read per_rule as diagnostic only.** Gating uses overall, holdout, per_family,
precision and the guards, none of which are affected.


## Iteration 4 — MERGED

R3 claimed every blank cell under a column header. An "office use only" column
is bordered, headed, and must not be filled by the applicant.

    tuning   precision 0.7280 -> 0.8329 (+.1049), recall 0.4171 unchanged
    matched  1839 -> 1839

The matched count being exactly equal is a structural guarantee, not luck: the
change only turns a header into "no header", never the reverse, so R3's true
positives can shrink or stay equal but never grow. Zero real fields discarded.

Evidence: of 410 R3 false positives, 318 carried the label "Office Use", which
had zero true positives anywhere in the corpus. Width and height were tried as
discriminators first and rejected — their TP and FP distributions overlap almost
entirely.

**The gate design earned its keep here.** box_over_ink rose +0.011 while the
offender list stayed byte-identical. That is the arithmetic of removing 318
correctly-placed boxes from the denominator — exactly the false alarm the guards
author warned about. A hard absolute threshold would have blocked a change worth
10 points of precision. Gating on a rise PLUS new offenders let it through.

Caveats kept in view: the holdout has no office-use columns, so "not worse" is
satisfied trivially rather than demonstrated. And 92 non-office R3 false
positives remain, concentrated in four forms with repeated line-item rows.


## Iteration 5 — MERGED, but flagged

Forms often put the label in one cell and the writing space in the cell to its
right. R2 wants the label inside the cell; R3 wants a column header above.
Neither could see this layout, so its recall was 0.00.

    tuning   f1 +0.0103   precision +0.0032   recall +0.0109
    holdout  f1 -0.0008 (inside tolerance)    R10 alone: 48 of 56 correct

**Why it is flagged.** On the holdout R10 fired 5 times and was wrong all 5:
section headings reused across a data table's column widths, and option words
like "By Email" whose right-hand cell is meant for a checkbox. Telling those
from real labels needs semantic judgement about what a heading is, not another
structural filter.

The gate passed, so I followed it rather than overriding on a hunch — but the
five failures are rendered into `review/iteration_05_R10/` for human review.
This is precisely the class of error a person catches and a metric does not.

Filters tried and rejected by the author, worth remembering: a minimum label
length of 2 killed real fields (bare digit row labels in numbered tables), and
rejecting labels ending in "?" discarded a genuine one.

## Process error — do not repeat

I refreshed `HEAD_BASELINE.json` after iteration 4 merged, while candidate agents
were still in flight against the older baseline. That agent measured a
pre-iteration-4 worktree against a post-iteration-4 baseline and reasonably
concluded the baseline was stale. It was not — its worktree was behind.

**Rule: never move the baseline while candidates are in flight.** Either freeze
it for the batch, or re-measure every candidate on the new HEAD before judging
it, which is what happened here.


## Iteration 6 — MERGED, best change of the run

R3 grouped cells into columns by exact match on the left x coordinate. Real
tables are ragged, so one logical column scattered into single-cell groups with
no header above them. Clustering at 16pt tolerance:

    tuning   f1 0.5662 -> 0.6277 (+.0616)   precision +.0180   recall +.0683
    holdout  f1 0.1802 -> 0.1919 (+.0116)   precision +.0104   recall +.0096

The first change to improve the holdout on all three metrics.

### The finding that matters more than the change

**The gate is structurally blind to mislabelling.** `eval/match.py` compares
page, type family and rect IoU. It never compares label text. A detection that
merges two adjacent columns under the WRONG header scores as a perfect match.

Sweeping the tolerance, f1 climbed smoothly all the way to tol=60 with no
precision penalty, while an independent label-accuracy check showed mislabelling
rising to 10.7% with a cliff between 40 and 50. The author chose tol=16 on that
evidence (1.34% mislabel) rather than the value the gate rewarded.

Field names are not cosmetic. A wrongly named field maps a stored profile value
into the wrong box on somebody's application. **Label accuracy must become a
tracked metric and a gate condition.** Queued.

Also rejected and worth keeping: clustering on both x0 and x1 was strictly worse
on precision AND recall — a ragged column's right edge does not drift in
lock-step with its left.

### The hard corpus is exhausted

The detector now scores f1 0.851 on the hard corpus, up from 0.644, against a
0.85 ceiling. That is the detector outgrowing the corpus, not a regression.

The assertion is re-scoped as an explicit **generator-quality tripwire**: it
fires when the generator needs new difficulty, never as a reason to make the
detector worse. Raising its threshold is only legitimate alongside work to
strengthen the generator, which is queued.

For reference the easy corpus sits at f1 0.949 and has not moved, so the hard
corpus is still doing its job — just with less headroom than it had.


## Harness round 2 — label accuracy, and a harder corpus

Two measurement changes, no detector change. Detector f1 was 0.6277 before and
after on the old corpus.

### Label accuracy is now measured and gated

The scorer never compared label text, so a box with the right rectangle and the
wrong NAME scored as a perfect match. Now tracked per bucket and gated at a 0.02
drop, but only when measurable in both runs — real stripped forms carry no truth
labels, so it reports UNAVAILABLE rather than a fabricated 1.0.

Normalisation is deliberately narrow: trailing colon, trailing date-placeholder
parenthetical, whitespace, case. Other parentheticals are NOT collapsed, because
truth uses "(Yes)"/"(No)" for checkbox questions and detections carry
"(optional)" — collapsing them would hide real distinctions.

**Checkbox pairs are excluded from the gated figure.** The detector gives
checkboxes no label at all, so R1 sits at a permanent 0%. Including them would
let a change that merely shifts the checkbox/text mix move the gate without
touching naming accuracy — the same false alarm as an absolute box_over_ink
threshold. Excluded from the form-level number (0.8936 -> 0.9857), still counted
per rule so R1=0.0 stays visible.

**That 0% is a product bug, queued.** A checkbox with no label means a person
cannot tell what they are ticking and profile mapping cannot reach it. On
safer.pdf that is 81 of 242 fields.

### The hard corpus was rebuilt

The detector had outgrown it — 0.644 at creation, 0.851 after four merged
changes. New difficulty: merged group headers with no internal divider,
dot-leader write-on lines, bilingual captions, and continuation tables whose
header sits on the previous page.

**Variety was chosen over difficulty.** The delivered version reached f1 0.682,
but only by weighting five mechanisms so heavily that each appeared in 22-24 of
25 forms — a stress test of five tricks, which would tune the detector to those
tricks rather than to robustness. Dialing the weights back gives f1 0.733 with
only one feature reaching 90% of forms. The gradient from 0.851 is what matters,
not the last 0.05.

One feature flagged as borderline by its author and kept after review:
continuation tables exploit that detection considers one page at a time. That is
a real detector limitation rather than a page-content trick, and the widget
itself is an ordinary bordered cell, so it is a fair test.

New baseline on the harder corpus: tuning f1 0.5387, label_accuracy 0.7984
(down from 0.9857 — the new difficulty exposes naming failures the old corpus
could not reach). Holdout unchanged at 0.1919, being real forms.


## Iteration 7 — MERGED

Nothing read the dot-leader write-on line ("Name . . . . . ." / "Amount ......"),
a common real convention. Recall on it was 0.000. R11 adds it: 67 detected, 67
matched, label accuracy 0.955.

Both traps were handled explicitly. A prose ellipsis is excluded by a minimum
dot COUNT of 6, not width alone — a large enough font clears R5's 25pt with
three dots. A dotted table divider is excluded by being flush against a cell
rule, and by carrying no label on its baseline.

**Honest limitation:** all 67 detections fired inside synthetic fixtures. R11
never fires on a real form in this corpus, and the dot-leader mechanism was
itself added to that corpus one batch earlier — close to a closed loop. Dot
leaders are a genuine convention so the rule is worth having, but its evidence
is weaker than its numbers suggest.

## Iteration 7b — REJECTED, and instructive

R11's boxes sit ON the dots by design, and the ink guard counted those dots as
ink, pushing box_over_ink up 0.026. Underscores are already exempt for exactly
that reason, so exempting dot leaders looked obviously correct.

It was not. Precision fell 0.8410 -> 0.8386, f1 fell, and box_over_ink went UP
rather than down. The reason: R9 uses the same exemption set to decide what to
reject, so exempting dots stopped R9 killing boxes it was correctly killing —
including on the real fee-schedule page R11 had proposed and R9 had caught.

A plausible symmetry argument lost to the measurement. Reverted.


## Iteration 8 — MERGED, and it was my bug

I guessed R3 was over-generating down long columns and named the 16pt clustering
as the likely culprit. Both wrong, and the investigation said so.

`grid_cells()` was the problem. Many PDFs draw one visual table border as TWO
stacked rects — a ~1.4pt stroke and a ~0.1pt hairline over the same x-range —
and both pass the `height < 3` ruling-line filter. Every cell below was built
twice, near-identical but not equal, so the `set()` dedup never collapsed them.
In greedy matching one twin matched truth and its sibling was a pure false
positive: 36 of one form's 46.

    detected 2032 -> 1988    matched 1709 -> 1709    precision 0.8410 -> 0.8597

Every removed box a false positive; zero real fields lost, tuning and holdout.

The first attempt merged on vertical gap alone and swallowed a header row's
full-width border together with the per-column underlines beneath it, costing 21
real matches. Caught by re-running the diagnostic *before* trusting the score.
Adding an x-span condition fixed it. Verify the mechanism, not just the metric.

## Iteration 9 — MERGED, a product bug not a metric

Checkboxes had no label at all: R1 emitted `""` and they were named `p3_chk`,
`p3_chk_2`. In the app that means a person sees a tickable box with no idea what
it means, and profile mapping — which works by name — can never reach it. On
safer.pdf, 81 of 242 fields.

    R1 label_accuracy 0.0 -> 1.0 (83/83)
    f1, precision, recall all bit-identical -- a pure labelling change
    safer.pdf: 81 of 81 checkboxes now labelled, 0 blank

Reads correctly by hand, including wrapped options and the
`"question (option)"` form for shared-question rows:

    '4a. Have you lived in B.C. for the past twelve months? (Yes)'
    'Living with a spouse or common-law partner'
    'First Nations' / 'Métis' / 'Inuit' / 'Other'

**One label is confidently wrong and was left alone deliberately.** Checkbox 36
reads `'...do you? (Rent Trailer Rent)'` — the page has "[ ] Own [ ] Rent
Trailer Rent $____", and "Trailer Rent" belongs to the next field but follows at
ordinary word spacing, indistinguishable by gap from a real multi-word option
like "Life Lease". Special-casing it risked regressing the multi-word cases that
work. Flagged rather than patched.


## CORPUS CORRECTION — every number before this point was measured wrong

Investigating why real-form recall was stuck at 0.13 after nine iterations, the
per-family breakdown showed four families scoring **exactly zero**:

    real/Acrobat    f1 0.000   truth 200   matched 0
    real/Designer   f1 0.000   truth 338   matched 0
    real/Microsoft: f1 0.007   truth 277   matched 1
    real/unknown    f1 0.000   truth 645   matched 0

Those forms have **no table structure at all** — h_rect 0, v_rect 0 — while
carrying 26,000 to 50,000 characters. No rule can see them: R2, R3 and R5b all
read vector rules.

They were admitted because my own admission test counted **any nearby
character** as supporting structure. I identified that exact weakness when
writing the harness spec, then implemented it anyway.

Of 3,202 truth widgets, only about a third were ever reachable. The rest were
impossible test cases dragging every metric down.

### The fix, and why it is at the widget level

First attempt rejected whole FORMS lacking structure, which threw away 15 of 21
usable forms. Filtering at the WIDGET level keeps the reachable fields of a
partly-structured form and drops only the unreachable ones.

"Reachable" now means a thin vector rule, a checkbox glyph, an underscore, or a
dot leader within 6pt — signals a rule actually reads.

### The corrected picture

                      polluted      reachable only
    tuning f1          0.5586          0.6977
    holdout f1         0.1922          0.6187
    holdout recall     0.1337          0.5609

**The detector did not improve. The measurement was wrong.** Numbers before and
after this point are not comparable.

### What this means for the nine iterations already merged

Their gate verdicts were computed against a partly meaningless denominator, so
they were noisier than they looked. The changes whose evidence was structural
rather than statistical still stand on their own: iteration 4 (matched exactly
unchanged), 8 (44 detections removed, matched unchanged) and 9 (bit-identical
f1) are safe by construction. The marginal ones — iteration 5 in particular,
already flagged — deserve re-examination against the clean corpus.

### Known limitation of the new test

It defines "reachable" as "carries a signal some CURRENT rule reads". A future
rule reading a new signal would find its evidence already filtered out. Dot
leaders were added when R11 landed; whenever a rule learns a new signal, add it
to MARK_CHARS in eval/label.py.
