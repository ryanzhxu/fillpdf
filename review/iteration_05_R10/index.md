# Iteration 5 — R10, left-of-cell labels (MERGED, flagged)

Only the boxes R10 added are drawn. On the holdout it fired 5 times and was
wrong all 5 times, so these images are the failures, not the successes.

**What to check:** are any of these actually places you would write? If none of
them are, R10 is riskier than its tuning numbers suggest and I should tighten it.

Known failure shape: a section heading or column header reused across a data
table's column widths, and option words like "By Email" whose right-hand cell is
meant for a checkbox rather than text.

Scores: tuning f1 0.5558 -> 0.5662 (P +.0032, R +.0109); holdout f1 -0.0008.
On tuning R10 was right 48 times out of 56. On holdout, 0 out of 5.
