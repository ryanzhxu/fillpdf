# Build vs adopt — what exists, and what is worth taking

Date: 2026-09-02. Research only. **Nothing here has been merged.**

## Why this document exists

The project opened with a research question: are there public tools or agent
skills that already fill flat PDFs by injecting real form fields, and should we
adopt one rather than build?

We built our own. **The reasons were never written down.** There is no
"alternatives considered" section in
`docs/superpowers/specs/2026-09-01-formfill-design.md`, and no record of what
was surveyed. A decision that shaped the entire project has no justification in
the repository. This document repairs that, and re-runs the survey against what
exists today.

Treat the "why we built" section as reconstruction, not testimony.

## Where we actually stand

Measured, commit `89c0c9d`:

    tuning   f1 0.6447   precision 0.7792   recall 0.5497
    holdout  f1 0.6424   precision 0.7034   recall 0.5911
    165 real government forms, 6,951 fillable fields, 9 producer families
    SAFER form: 223 fields across 8 pages in 0.43 seconds

## What is out there

### 1. acroforge — the direct overlap

Apache-2.0, Python 3.11+, on PyPI. Turns a flat PDF into a fillable AcroForm.
Built on the same base we are: `pdfplumber`, `pypdf`, `reportlab`.

Its three detection heuristics map almost exactly onto ours:

| acroforge | ours |
|---|---|
| underline forms — write-on rules to text fields | R5, R5b |
| table/grid cells, label-aware, skips section headers | R2, R3, R4, R10, R12, R16, R17 |
| checkboxes: vector squares and font glyphs | R1, R18 |

**Its detection is not measured.** The author states plainly: *"We make no
promise about detection precision or recall on any form."* Every detected field
carries `confidence < 1.0` and the output is a draft manifest for human review.
The repo is early: 8 stars, 62 commits.

So on the thing this project has spent its whole effort on — detection quality
on real forms — we are ahead, and we can prove it. They cannot.

**But it is ahead of us in five concrete places:**

1. **Cross-viewer golden-image render tests.** Every field type is rendered in
   both pdfium (Chrome) and pdf.js (Firefox) in CI, and a change failing either
   blocks. We have **zero** render tests — verified, nothing in `tests/` or
   `eval/` touches pdfium or pdf.js. We inject AcroForm widgets and have never
   machine-checked that they render correctly anywhere. This is the single
   most valuable thing to take.
2. **Field type coverage.** It emits TEXT (with `maxlen`), COMB (with cell
   count), CHECKBOX (with `export_value`), RADIO (one spec per button, shared
   name), SIGNATURE. We emit text, multiline, checkbox — no radio, no
   signature, and R14's comb type has never fired on a real form. Radio groups
   in particular are wrong today: mutually exclusive options become independent
   checkboxes, so a user can tick both "Consent Granted" and "Consent Not
   Granted" on the SAFER form right now.
3. **A typed refusal for scanned PDFs.** `ScannedPDFError` when there is no
   text layer. We have no such guard — verified. A public app will be handed
   scans on day one and will currently return an empty result with no
   explanation.
4. **Clean API separation.** `build()` / `fill()` / `flatten()` are
   deterministic bytes-to-bytes functions; `detect()` / `make_fillable()` are
   explicitly labelled best-effort. Ours does not draw that line, which matters
   because the deterministic half can be guaranteed and the detection half
   never can.
5. **License-enforcement CI** (`pip-licenses --fail-on='GPL;AGPL;LGPL;SSPL'`).
   Cheap, and relevant if this is ever a public service.

### 2. Docling / TableFormer — the capability we lack

MIT, from IBM. `TableFormer` is a vision-transformer for table structure
recovery that explicitly handles **partial or missing borderlines**, empty
cells, spans, and inconsistent alignment.

That is our largest single gap. `docs/HANDOVER.md` records 422 fields sitting
near partial ruling that never assembles into a cell, and says the fix is
better cell reconstruction rather than another rule. TableFormer is precisely
that, already trained.

**The cost is the problem.** Published figures: 2–6 seconds per table on a
standard CPU, and 6–15 seconds per page on real documents. We currently do a
whole 8-page form in **0.43 seconds**. Adopting it is a 100x-plus latency
change and pulls in torch-class dependencies for a public upload service.

Verdict: real capability, wrong shape for the main path. Plausible only as an
opt-in second pass on pages where our own cell reconstruction fails, never as
the default.

### 3. The official Anthropic PDF skill

A toolkit skill wrapping `pypdf`, `pdfplumber`, `reportlab`, with a separate
`forms.md` loaded only when filling a form. It fills **fields that already
exist**. It does not detect fields in a flat PDF, which is the entire hard
problem here. Not a substitute; nothing to absorb that we do not already have.

## Why building was still right

Reconstructed from what the alternatives actually are:

- The hard problem is detection on flat forms, and **no surveyed project
  measures it**. acroforge disclaims accuracy outright. Adopting it would have
  meant inheriting an unmeasured heuristic set and no way to tell whether a
  change helped.
- Our real asset is not the rules — it is the harness. Stripping fillable PDFs
  to produce (flat form, answer key) pairs gave 165 measured forms, a gate, a
  holdout, truth-free guards, and an adversarial corpus. That is what makes
  every change checkable, and none of it exists elsewhere.
- The rules are replaceable. The measurement is not.

The honest counter-argument: had we started from acroforge's engine and put our
harness around it, we would have gained the render tests and the field-type
coverage for free and spent our effort only on detection. That would probably
have been the better opening move. It is not a reason to switch now — the
detection work does not transfer back — but it is why the gaps below are worth
closing.

## Recommendation — take four things, in this order

Ranked by value against risk. **None of this is merged; each is a separate
piece of work with its own measurement.**

1. **Cross-viewer render tests.** Highest value, lowest risk, no detector
   change. We currently ship injected widgets on nothing but visual inspection
   of Preview. Build golden-image tests in pdfium and pdf.js for every field
   type we emit. This can be done today and is independent of everything else.
2. **Radio groups.** A correctness bug, not a gap: the SAFER consent boxes are
   mutually exclusive in law and tickable-both in our output. Needs a field
   type, a grouping rule, and `pdf-lib` support checked.
3. **A scanned-PDF guard.** One check — no text layer, or a text layer far too
   sparse for the page count — and a clear message. Required before any public
   upload path.
4. **TableFormer as an opt-in fallback**, evaluated but not wired into the main
   path, and only after the checkbox-truth defect in `eval/label.py` is fixed —
   otherwise we cannot measure whether it helps.

Explicitly **not** recommended: replacing our detector with acroforge's, or
adopting the Anthropic PDF skill. The first trades measured rules for unmeasured
ones; the second does not address detection at all.

## Sources

- acroforge: https://github.com/san64777/acroforge · https://pypi.org/project/acroforge/
- Docling technical report: https://arxiv.org/abs/2408.09869
- docling-ibm-models: https://github.com/docling-project/docling-ibm-models
- Anthropic Agent Skills: https://github.com/anthropics/skills
