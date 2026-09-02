# Tuning log

One rule change per iteration. Gated on: overall f1, holdout f1, per-family f1,
precision, and the label-free guards. The holdout is never tuned against.

| # | Change | tuning f1 | holdout f1 | Verdict |
|---|---|---|---|---|
| 1 | R9 rejects boxes on running text | 0.6274 -> 0.6360 | 0.2803 (+.0303) | **MERGED** |
| 2 | R2 accepts multi-line labels | 0.5133 -> 0.5622 (+.0488) | 0.1810 -> 0.1788 (-.0022) | **REJECTED** |
| 3 | R1 uses the glyph's real bbox | 0.5133 -> 0.5304 (+.0170) | 0.1810 (unchanged) | **MERGED** |
| 3b | harness: per-rule metrics could exceed 1.0 | n/a | n/a | **MERGED** |

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
