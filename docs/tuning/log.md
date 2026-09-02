# Tuning log

One rule change per iteration. Gated on: overall f1, holdout f1, per-family f1,
precision, and the label-free guards. The holdout is never tuned against.

| # | Change | tuning f1 | holdout f1 | Verdict |
|---|---|---|---|---|
| 1 | R9 rejects boxes on running text | 0.6274 -> 0.6360 | 0.2803 (+.0303) | **MERGED** |
| 2 | R2 accepts multi-line labels | 0.5133 -> 0.5622 (+.0488) | 0.1810 -> 0.1788 (-.0022) | **REJECTED** |

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
