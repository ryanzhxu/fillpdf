# Detection Evaluation Harness — Design

Date: 2026-09-01
Status: approved for planning
Relationship: **prerequisite for T1** in `2026-09-01-formfill-design.md`

## 1. Why this exists, and why it comes first

The FormFill spec sets track T1 a target of "at least 90 percent of true fields
on `fixtures/safer.pdf` with at most 3 false positives."

That target is currently unverifiable. The demo emits 246 fields. The true count
is "roughly 200", which is an estimate made by looking at the pages. Nobody has
counted. So the stated target cannot be passed or failed.

This matters more than it sounds. Any tuning loop pointed at an unvalidated
number will optimize that number. Deleting rule R5b raises precision. Emitting a
box on every table cell raises recall. Both would register as progress. The
detector would get worse while the metric improved, and nobody would see it.

Therefore: **build the scoreboard, then tune.** This document specifies the
scoreboard.

## 2. The unlock — fillable PDFs are free labeled data

A PDF that already contains an AcroForm carries its own answer key.

```
fillable.pdf ──strip AcroForm──> flat.pdf      (the detector's input)
             └─widget rects────> truth.json    (the answer key)
```

The stripped widget rectangles are exactly what the detector is supposed to
find. Run detection on the flattened copy, compare against the key, and you have
precision and recall with **no human labeling at all**.

Measured on files already present locally:

| File | Pages | Fields |
|---|---|---|
| `t2201-fill-23e.pdf` (CRA Disability Tax Credit) | 16 | 585 |
| `SAFER-Application-Form.pdf` | 8 | 0 — flat |

One CRA form yields 585 labels. A few hundred forms yield tens of thousands.

### 2.1 Extract widgets, not fields

A form field is not a rectangle. One field may own several widget annotations,
possibly on different pages. In `t2201-fill-23e.pdf`, 289 of 585 entries report
`/FT: None`, because they are parent nodes in the field tree rather than
leaves.

The answer key is built by walking every page's `/Annots`, keeping entries whose
`/Subtype` is `/Widget`, and recording:

```json
{
  "page": 3,
  "rect": [92.0, 63.0, 290.0, 79.0],
  "type": "text" | "checkbox" | "choice",
  "field_name": "applicant_surname",
  "flags": { "readonly": false, "hidden": false }
}
```

Widgets that are hidden, read-only, or zero-area are excluded. They are not
things a person fills in, so counting them would punish the detector for being
right.

### 2.2 Strip cleanly

Stripping must remove the interactive layer and nothing else:

- Delete `/AcroForm` from the catalog.
- Delete every `/Widget` annotation from every page.
- Keep all page content streams untouched.
- Do **not** flatten appearance streams onto the page. Flattening would paint
  the field's border onto the page and hand the detector a visual cue that a
  genuinely flat form would never contain. That would inflate every score.

### 2.3 Reject unfair forms

Some fillable PDFs draw nothing beneath the widget. The box exists only as an
annotation. Strip it and the page is blank there — no rule, no label, no glyph.
No detector could find that field, and no human could fill the printed form
either.

Scoring against such a form punishes the detector for a defect in the source.
So each stripped form passes an admission test: for at least 60 percent of its
widgets there must be page structure within 6 points of the widget rect — a
vector rule, a text label, or a glyph. Forms below that threshold are recorded
with the reason and excluded from scoring.

The count of excluded forms is itself reported. A sharp rise means the admission
test is wrong, not that the corpus is bad.

## 3. The holdout — and why the auto-labeled corpus is not enough

Forms that ship fillable were usually authored in Acrobat. Forms that ship flat
were usually exported from Word. Their internal structure differs: Word draws
table borders as thin filled rects, which is the signal rules R2, R3 and R5b all
depend on. An Acrobat-authored corpus under-represents exactly the case
FormFill exists to serve.

So the corpus is split, and the split is enforced:

| Set | Source | Labels | Used for |
|---|---|---|---|
| **Tuning** | fillable forms, stripped | automatic | proposing and testing rule changes |
| **Holdout** | genuinely flat forms | hand-labeled, in the demo UI | the acceptance gate only |

**The holdout is never tuned against.** No rule may be written by looking at
holdout failures. It exists to answer one question: did a change that helped the
auto-labeled corpus also help real flat forms?

`fixtures/safer.pdf` is holdout item one. Its labels are produced by opening the
demo, correcting every box by hand, and exporting the result. That is a few
hours of work, once, and it is the most valuable data in the project. Target: 15
to 25 hand-labeled flat forms.

## 4. Scoring

### 4.1 Matching

A detection matches a truth widget when all three hold:

1. Same page.
2. Same type. `text` matches `text`; `checkbox` matches `checkbox`. A text box
   over a checkbox is a miss **and** a false positive, not a partial credit.
3. `IoU >= 0.5`.

Matching is one-to-one, resolved greedily by descending IoU, so two detections
covering one truth box score one match and one false positive.

### 4.2 Metrics

| Metric | Meaning |
|---|---|
| **Recall** | matched truth / all truth — did we find the fields? |
| **Precision** | matched detections / all detections — did we emit junk? |
| **F1** | the headline number |
| **Placement** | mean IoU across matches — are the boxes well placed? |
| **Near-miss rate** | truth boxes with a detection at `IoU >= 0.15` but `< 0.5` |

The near-miss rate is the most useful diagnostic. It separates "the rule did not
fire" from "the rule fired in the wrong place", and those need opposite fixes.

Every metric is reported three ways: overall, per rule (R1 … R8), and per form
family (issuer plus authoring tool, read from the PDF producer string). A rule
that helps CRA forms and wrecks IRS forms is invisible in an overall number.

### 4.3 Determinism

Scores must be comparable across months.

- The corpus is pinned by content hash. A form whose bytes change is a new form.
- Nothing in the detector may depend on dict ordering, wall time, or randomness.
- Every score run writes `scores/<git-sha>.json` and is committed. Regressions
  then show up in `git log`, not in someone's memory.

## 5. The acceptance gate

A rule change is accepted only when **all** of these hold:

1. Overall F1 on the tuning corpus does not decrease.
2. F1 on the holdout does not decrease.
3. No form family drops more than 2 points of F1.
4. No new crash, timeout, or memory kill.

Rule 2 is the one that matters. It is what stops the loop from overfitting to
Acrobat-authored forms.

A change that raises recall while dropping precision below its previous value is
not an improvement. Precision failures put boxes on top of a person's form,
which is worse than a missing box they can add themselves.

## 6. Corpus acquisition

Sources: public forms from tax, immigration, housing, health, and benefits
agencies. Canadian and US first, since those are the target users.

Rules for the fetcher, all cheap and all non-negotiable:

- Respect `robots.txt`.
- One request at a time per host, with a delay between requests.
- A real `User-Agent` naming the project.
- Cache by URL and content hash. Never re-fetch a form already held.
- Record the source URL, fetch date, and issuer for every file.

Government forms are public documents and freely downloadable. The corpus is
kept out of git — it is large, and it is reproducible from the manifest.

## 7. Robustness

The harness must survive hostile and broken input, because the product must.

- Every form is scored in a **subprocess** with a 30 second CPU cap and a 512 MB
  memory cap. One bad PDF cannot end a run of 400.
- A crash, timeout, or kill is recorded as a result with its reason. It is data
  about robustness, not an interruption.
- The adversarial corpus from the FormFill spec — decompression bomb, 5,000
  pages, 100,000 rects, encrypted, truncated, HTML renamed to `.pdf` — is scored
  on every run. Its pass rate is a reported metric.
- A run is resumable. Scoring 400 forms should never restart from zero.

## 8. The tuning loop

Only once sections 2 through 7 exist and `score.py` prints a real number.

```
1. Fetch      pull N new forms, respecting section 6
2. Label      strip and admit, per section 2
3. Score      per section 4, in subprocesses, per section 7
4. Diagnose   cluster failures by rule and form family; find the missed signal
5. Propose    change one rule, or add one. One change at a time.
6. Gate       re-score everything. Accept only under section 5.
7. Commit     with the score delta in the message
```

Steps 1 to 4, 6 and 7 are automation. Step 5 is judgment and is where a model is
actually needed.

**One change per iteration.** Two rule changes scored together cannot be
attributed, and an accepted pair may contain one improvement and one regression
that happen to cancel.

**Bounds.** The loop is not open-ended. It stops when a fixed iteration count is
reached, or when three consecutive proposals fail the gate. Both are cheaper
than discovering next month that it ran all along.

**Cost.** Each iteration spends model time. A nightly bounded run is the
sensible default. A continuously running loop is not, and would be easy to
forget about.

## 9. Honest risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Corpus bias toward Acrobat-authored forms | The product targets Word-exported flat forms | The holdout, and the section 5 gate |
| Auto-labels are imperfect truth | Some widgets sit where no human would write | The admission test in 2.3, plus manual review of a sample |
| Overfitting to the corpus | Scores climb, real forms do not improve | Holdout, per-family reporting, one change per iteration |
| The holdout is small | 20 forms is a weak signal | Grow it steadily. Treat a holdout gain under 1 point as noise |
| Metric hides the user's experience | F1 says nothing about a box landing on top of a heading | Report precision separately and weight it in judgment |

The last one deserves saying plainly. F1 is a proxy. A form with 95 percent
recall and boxes scattered over the headings is worse to use than one with 80
percent recall and clean placement. The number informs the decision. It does not
make it.

## 10. Deliverables

| Piece | What it does |
|---|---|
| `eval/fetch.py` | Acquire the corpus, politely, with a manifest |
| `eval/label.py` | Strip fillable forms, extract widget truth, admission test |
| `eval/score.py` | Match, compute metrics, write `scores/<sha>.json` |
| `eval/report.py` | Overall, per rule, per family; diff against a previous run |
| `eval/holdout/` | Hand-labeled flat forms, starting with SAFER |
| `eval/adversarial/` | The hostile corpus from the FormFill spec |

`score.py` printing a trustworthy number is the milestone. Everything after it
is comparatively easy. Nothing before it can be judged at all.
