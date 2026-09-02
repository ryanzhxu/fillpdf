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
