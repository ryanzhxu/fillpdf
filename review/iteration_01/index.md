# Iteration 1 — R9 rejects boxes on running text

Look at the *_compare.png files: BEFORE on the left, AFTER on the right.

**What to check:** every box that disappeared should be one you agree was wrong.
If any box vanished that you think SHOULD be fillable, tell me the page and
roughly where — that is a false rejection and I will tighten R9.

Boxes removed on safer.pdf: 4
  - p3_4c_if_you_or_your_spouse_were
  - p3_current_address_3
  - p3_dd_mm_yyyy_from_date_4
  - p6_scan_and_upload

Pages shown: p3, p6

Scores (real government forms, holdout never tuned against):
  tuning  f1 0.6274 -> 0.6360   precision 0.6632 -> 0.6897
  holdout f1 0.2500 -> 0.2803   precision 0.2548 -> 0.3268
