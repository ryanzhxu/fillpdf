# Current detector on SAFER — 225 fields

Four pages rendered: p2 (applicant details), p3 (residency + household),
p4 (contact + rent), p5 (income).

Blue = text field. Green = checkbox. Every box now carries a label; hover it in
the live demo to read it.

    by rule: {'R2': 38, 'R5b': 14, 'R3': 71, 'R1': 81, 'R5': 9, 'R4': 12}
    checkboxes: 81, all labelled
    signature lines: deliberately none

**What to look for:** a box somewhere you would NOT write, or a place you WOULD
write with no box. Either is worth telling me. Rough is fine — "page 4, the
phone row" is enough.

Live version, where you can type and drag:

    ./.venv/bin/python demo/demo.py fixtures/safer.pdf

Scores now: tuning f1 0.7244, holdout f1 0.6551 (real government forms, never
tuned against).
