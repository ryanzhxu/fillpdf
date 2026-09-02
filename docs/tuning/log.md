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
| 5 | R10 reads the label from the cell to the left | 0.5558 -> 0.5662 (+.0103) | 0.1810 -> 0.1802 (-.0008) | **MERGED** — flag cleared, see below |
| 6 | R3 clusters ragged columns (tol 16pt) | 0.5662 -> 0.6277 (+.0616) | 0.1802 -> 0.1919 (+.0116) | **MERGED** |
| 7 | R11 reads dot-leader write-on lines | 0.5387 -> 0.5546 (+.0159) | unchanged | **MERGED** |
| 7b | exempt dot leaders from the ink guard | 0.5546 -> 0.5541 (-.0005) | -.0002 | **REJECTED** |
| 8 | merge doubled ruling lines in grid_cells | 0.5546 -> 0.5586 (P +.019) | +.0003 | **MERGED** |
| 9 | R1 gives checkboxes real labels | f1 unchanged, R1 label acc 0.0 -> 1.0 | unchanged | **MERGED** |
| — | *corpus corrected; numbers below are on clean data* | | | |
| 10 | grid_cells merges segmented borders over a single-span row | 0.6977 -> 0.7104 | 0.6187 -> 0.6331 | **MERGED** |
| 11 | R2/R3 extend a claimed cell across false column splits | 0.7104 -> 0.7129 | 0.6331 -> 0.6559 | **MERGED** |

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


## Iteration 5 re-examined on the clean corpus — flag CLEARED

R10 was merged with a flag: on the old holdout it fired 5 times and was wrong
all 5. Re-run with R10 on versus off against the corrected corpus:

                    with R10    without
    overall f1       0.6977      0.6947
    holdout f1       0.6187      0.6104
    holdout P        0.6898      0.6856
    holdout R        0.5609      0.5500
    R10 alone: 22 detected, 17 matched, label accuracy 1.0

It improves the holdout on all three metrics. Those five "false positives" were
boxes on fields that existed but had been filtered out as unreachable — the
polluted corpus was scoring R10 against widgets it had itself excluded.

Worth noting as a general lesson: a flagged merge was flagged on bad evidence.
The other marginal verdicts from that era deserve the same scepticism, in both
directions — a rejection can be as wrong as an approval.


## Iterations 10 and 11 — two agents, one root cause, both worth keeping

Two agents were briefed on different symptoms (Microsoft® forms scoring 0.262,
and 48 near misses) and independently found the SAME underlying cause:

**Word and Publisher render a table row's border as one rect PER COLUMN, not one
per row.** `grid_cells()` treated each segment's x0/x1 as a cell boundary even
where no vertical rule is drawn, so a label cell and the blank strip beside it —
visually one continuous white area — were split apart. Detections boxed only the
first narrow slice while truth spanned the whole writable strip. All 48 near
misses had a horizontal width error (median ~270pt too narrow) and only ~6.6pt
of vertical error.

They fixed it at different levels, and the fixes turned out to be complementary:

    10  grid_cells emits ONE wide cell when a row's top border has several
        segments and the row below has exactly one spanning their full width
    11  after R2/R3 claim a cell, extend it rightward through adjoining blank
        unclaimed cells, stopping at a real vertical rule, a cell with its own
        text, or a checkbox-shaped cell

                    tuning f1   holdout f1   holdout P   near_miss (t/h)
    baseline          0.6977      0.6187      0.6898       48 / 22
    10 alone          0.7104      0.6331      0.7268       31 / 20
    11 alone          0.7072      0.6544      0.7500       33 / 13
    10 + 11           0.7129      0.6559      0.7615       27 / 13

Both real families improve at every step, so neither is a trade against the
other: real/Adobe 0.743 -> 0.767, real/Microsoft® 0.262 -> 0.320.

Iteration 11's author was asked to say whether its change was a principled
geometry fix or a constant tuned to the corpus, and made the case for the
former: it tunes no threshold, it corrects a structural misreading, and it only
widens a box where no drawn rule justifies stopping — i.e. it targets where a
person would actually put ink. Its holdout improving by roughly the same
relative amount as tuning, measured once at the end, is corroborating.

It also reported that 2 new near misses appeared alongside 17 conversions, and
traced them: a baseline R3 detection with a garbled label had been matching one
truth widget at IoU 0.557 by luck, and that cell is now absorbed into a
neighbouring R2 field. Reported rather than hidden, which is the right instinct.

### Residual, and the next highest-value fix

Microsoft® recall moved only 0.254 -> 0.268 despite near_miss falling 46 -> 30.
Once merged, many of these are multi-line comment areas over 70pt tall. R3 caps
a claimed cell at 70pt and R2 wants a header-band-then-blank shape these
free-text boxes lack, so no rule claims them — they moved from near-miss to no
detection at all. A rule for large blank unruled cells as multi-line text fields
is the next change for this family.


## Iteration 2 re-tested on the clean corpus — REJECTED AGAIN, for a better reason

Iteration 5's flag was cleared once the corpus was corrected, so iteration 2's
rejection deserved the same re-examination. A rejection can be as wrong as an
approval.

It was not wrong. Re-applied to clean data:

    tuning   f1 0.7175 -> 0.7512 (+.0337)   recall +.0616   precision -.0125
    holdout  f1 0.6551 -> 0.6495 (-.0056)   recall unchanged
    label_accuracy 0.8055 -> 0.7017 (-.1038)

**The third line is the one that matters, and the old gate could not see it.**
Allowing a wrapped label means concatenating two lines, and the resulting field
NAMES are wrong 10 points more often. The change buys recall by mislabelling
what it finds.

That is exactly the failure mode label accuracy was added to catch after the
column-clustering sweep showed f1 climbing while mislabelling rose to 10.7%.
The first time iteration 2 was rejected, the gate did not measure names at all
and the verdict rested on a 0.0022 holdout wobble. Now it rests on evidence.

A field with the right box and the wrong name is worse than a missing field: it
maps a stored profile value into somebody else's box. Rejected, and now for a
reason worth trusting.


## QUEUED — R3 runs past the end of its table (found by looking, not by metric)

Rendering the current detector over safer.pdf for the review queue showed two
false positives no score had surfaced:

    R3  p3_name_of_sponsor_3   240x30  sitting on "...that apply"  (§5 heading)
    R3  p3_landlord_name_4     199x16  sitting on "following:"     (4c heading)

`p3_name_of_sponsor_3` inherited "Name of Sponsor" from the 4c table and carried
it DOWN PAST that table onto a section heading 200pt below.

Cause: `_cluster_columns` groups cells page-wide by left edge, so a cell
anywhere further down the page joins the same logical column and inherits its
header. A column's header should apply to CONTIGUOUS rows — a large vertical gap
between one cell and the next means the table ended.

Fix to try: while walking a column downward, stop inheriting when the gap
between consecutive cells exceeds some multiple of the typical row height in
that column. Derive the threshold from the column's own row spacing rather than
a fixed constant, so it adapts to dense and sparse tables alike.

Worth noting how this was found. Thirteen gated iterations, every one measured,
and these two survived because the corpus has no truth widget there — a false
positive on a heading costs precision only if some truth widget is nearby to
make the denominator move. Rendering the page and looking at it caught what the
metric could not. Both kinds of checking are needed.


## STANDING CONCERN — rules with synthetic-only evidence

Two merged rules have no measured real-world benefit whatsoever:

    R11  dot leaders    67 detections, all synthetic, 0 on any real form
    R14  comb fields     3 detections, all synthetic, 0 on any real form

Both are genuine real-world conventions, and both are tightly guarded and fire
zero times on the 16 real forms, so neither is doing harm. But both were
*invented by our own generator and then solved by our own rule* — a closed loop.

The comb survey made this sharp. The one real comb in the corpus, appearing 7
times in a Canadian family-info form, has **no truth widget at any location**:
the form's author drew it as decoration. So the corpus offers no evidence that a
comb is a field at all. The convention is real; our sample simply has no fillable
instance of it.

**Watch the ratio.** One or two such rules is a reasonable bet on a convention we
know exists. A detector where a growing share of rules only ever fire on
synthetic input has drifted toward the generator's idea of a form rather than the
world's. Re-validate R11 and R14 the moment the real corpus grows, and treat a
third synthetic-only rule as a reason to stop and go fetch more real forms
instead.

`engine/detect/rules.py` is now 1,317 lines. Complexity is a cost that no gate
measures.


## Iteration 17 — R13 shaded fill areas: REJECTED, by the rule written one commit earlier

R13 was correct on its own terms: 67 detected, 67 matched, precision 1.0,
label accuracy 0.910, gate PASSED, tuning f1 0.6388 -> 0.6622.

Rejected anyway. Three reasons, in order of weight.

**1. The survey disproved the brief's premise.** I briefed this rule believing
"a shaded box with no ruled border" was a real detection gap. It is not. 12 of 16
real forms do use shaded fill, but 416 of 434 instances already carry printed
text — table headers, section banners, highlighter annotations. And every
field-sized BLANK shaded rect in the real corpus sits inside a table that also
draws ordinary ruling, so R2/R3/R4/R10 already claim that space. The one
promising candidate was already being detected by R10.

**2. It is the third synthetic-only rule in a row.** R11, R14, now R13 — all
firing zero times on all 16 real forms. The standing concern recorded one commit
earlier says exactly this: a third such rule is a reason to go fetch more real
forms rather than write it. Following that rule when it is inconvenient is the
only thing that makes writing it worth anything.

**3. It trips the hard-corpus tripwire** (f1 0.682 against a 0.66 ceiling).
Merging would mean recalibrating the corpus to accommodate a gain that exists
only inside that corpus. That is circular, and the tripwire firing here is
evidence rather than an obstacle.

Rejecting a technically sound, gate-passing change is uncomfortable, and the
+149 lines it would have added to an already 1,300-line file is not the reason —
the reason is that the evidence for it does not exist outside our own generator.

### The genuinely valuable finding, extracted and queued

R10 already claims the real shaded field on f6b53f5f6901.pdf, but with a
TRUNCATED label: "operating expenses" instead of "Net increase in operating
expenses". That is a real naming bug on a real form, worth more than the rule
that found it.

### What happens instead

Fetch more real forms. The corpus is 16 forms; the constructs we keep inventing
may well exist out there, and the honest way to find out is to go look rather
than to keep scoring against our own inventions.


## Corpus expansion — 16 forms to 117

Acting on the standing concern instead of writing a fourth synthetic-only rule.
295 real PDFs fetched and labelled; 117 admitted, 5,638 reachable widgets.

    tuning   78 real forms, 3,941 widgets (+ 25 hard synthetic)
    holdout  39 real forms, 1,697 widgets
    9 producer families

    tuning  f1 0.6478  P 0.7334  R 0.5800
    holdout f1 0.6740  P 0.7496  R 0.6123

### Holdout size was the hidden variable

    6 forms  -> holdout f1 0.66
    14 forms -> holdout f1 0.44
    39 forms -> holdout f1 0.67

The 0.44 was sampling noise, not a discovery — that draw happened to put the
hard producer families in the holdout. Every gate verdict taken while the
holdout was six forms rested on a noisier signal than it appeared to. The
verdicts that were structural (matched count unchanged, bit-identical f1) still
stand; the marginal ones were closer to coin flips than the tolerances implied.

**A holdout must be large enough that its own sampling noise is smaller than the
effects being gated on.** Ours was not, for most of this run.

### R11 and R14 across 117 real forms

Still zero detections. The rule firing distribution on real input:

    R5  1178 in 55 forms    R2  1055 in 63    R3   971 in 54
    R5b  925 in 62          R1   583 in 27    R4    64 in  7
    R6    51 in 10          R12   24 in  8    R10   21 in  9
    R11    0                R14    0

Kept, on the earlier reasoning that both constructs do occur and the rules
correctly declined each specific instance. But two rules out of eleven that have
never once fired on 117 real forms is the honest state of it, and a fourth would
not be defensible.


## Iterations 18 and 19 — the expanded corpus changed what was worth fixing

With 165 real forms instead of 16, the per-rule table showed the real problems
were in rules already shipped, not in constructs still missing:

    R6    53 detected,    0 matched   P 0.000
    R2  1430 detected,  834 matched   P 0.583   596 false positives
    R5b  983 detected,  557 matched   P 0.567   426 false positives

### 18 — R6 DELETED

53 detections, zero correct, across 165 forms. They land in the GAPS BETWEEN
real checkboxes: phantom sub-cells that grid_cells() reconstructs from a
surrounding table's ruling. Best IoU against truth was 0.19. "Blank and squarish
and ruled" carries no discriminating signal — ordinary tables are full of such
cells — so there was nothing to narrow toward.

**It never worked for the case it was written for.** R6 was added in the first
batch, when the corpus was one form, to catch safer.pdf's Option 1 / Option 2
consent boxes. Those are single 25pt stroked rects, and grid_cells() only treats
a rect as a ruling line below 3pt, so R6 produced zero detections on safer.pdf
before OR after removal. Eighteen iterations passed without anyone checking.

The consent boxes remain undetected and are now queued as a visible gap rather
than hidden behind a rule that appeared to cover them.

### 19 — R2 requires its label to contain a letter

    R2 detected 1430 -> 1328, matched 834 -> 834, precision 0.583 -> 0.628
    tuning  f1 0.6406 -> 0.6461  P 0.7631 -> 0.7790
    holdout f1 0.6371 -> 0.6392  P 0.6934 -> 0.6985
    near_miss unchanged, 262 and 139

85 of 369 tuning false positives removed, and zero of 594 true positives. A
"label" made only of currency, digits, or underscores ("$31,200", "$______",
"1.") is already-printed data or the blank-fill line itself, never a name for a
field. Zero overlap with any true positive.

The near_miss count being unchanged is the load-bearing check: it confirms none
of the removed detections were real fields with wrong geometry, which is the
failure mode that would have made this a bad trade.

Two other heuristics were diagnosed and deliberately NOT implemented: a
"row has a prose-wrapping sibling" test (61 FP but 2 TP lost) and a sibling-count
test (worse than 1:1). Both left for a future iteration under the one-change
rule.

**Lesson worth keeping.** Every rule written against a thin corpus deserves
re-examination once the corpus thickens. R6 was written against one form and
survived eighteen iterations doing nothing but harm. The expansion from 16 to
165 forms was worth more than any single rule added in this run.


## Iteration 20 — R2 row-label guard: REJECTED for complexity without generalisation

    tuning   f1 0.6461 -> 0.6475 (+.0014)   21 FPs removed, 0 TPs lost
    holdout  f1 0.6392 -> 0.6392 (unchanged), 2 real fields lost
    cost     +66 lines, three tuned constants (35%, 40pt, 96 chars)
    gate     PASSED

Rejected despite passing. The tuning gain is +0.0014, the holdout gain is
exactly zero, and it destroys two real fields on a holdout form ("Date
(DD/MM/YYYY)" and "Time", whose row gap of 44.7pt just cleared a 40pt
threshold). Three constants chosen to make the tuning numbers work, delivering
nothing on unseen forms, is a heuristic fitted to the tuning set rather than a
mechanism.

The earlier probe suggested 61 FPs for 2 TPs. The tuned implementation delivered
21 for 0 on tuning — because a single "sibling wraps at all" test caught 62 FPs
but cost 21 TPs, so size floors had to be added. That gap between probe and
implementation is itself the signal: the clean version does not exist here.

**Complexity is a cost no gate measures.** rules.py is 1,318 lines. Adding 66
more with three magic numbers, for zero holdout movement, makes the detector
harder to reason about and no better at its job.

### Worth keeping from the attempt

The failure mode of the simple version is documented: "sibling wraps to more
than one line" is geometrically identical for real instructional prose and for
short wraps like "Prov: AB" or "AM PM" split across two lines. Any future
attempt needs to separate those by content, not by geometry, and the numbers to
beat are 62 FP / 21 TP for the naive test.

The author also declined to nudge the 40pt threshold to recover its two holdout
losses, correctly identifying that as tuning against the holdout. That was the
right call even though it made its own result look worse.


## Iteration 21 — R5b rejects rules that underline printed text: MERGED

    R5b detected 983 -> 929, matched 557 -> 557, precision 0.567 -> 0.600
    tuning  f1 0.6461 -> 0.6489  P 0.7790 -> 0.7871  recall unchanged
    holdout f1 0.6392 -> 0.6408  P 0.6985 -> 0.7022  recall unchanged
    42 of 184 tuning FPs removed, 0 of 345 TPs lost

118 of R5b's 184 false positives (64%) claimed a spot with no field at all. Its
"no vertical rule at the endpoints" test could not tell a blank write-on line
from a DECORATIVE UNDERLINE beneath already-printed text — a heading's own rule,
a Word-style manual underline, a footer caption.

The discriminating signal is **coverage, not proximity**: text on a rule's own
baseline covering a large fraction of that rule's OWN WIDTH means the rule
underlines that text. A field's label merely brushing a much wider write-on rule
covers only a sliver. Measured: every true positive on this corpus covers 27% or
less. The cut is at 35%.

### Compare with iteration 20, rejected an hour earlier

    20 (rejected)   3 tuned constants   +0.0014 tuning   0.0000 holdout   2 fields lost
    21 (merged)     1 constant, with a
                    measured 27%/35% margin
                                        +0.0028 tuning   +0.0016 holdout  0 fields lost

Same rule file, same gate, opposite verdicts. The difference is not the size of
the number — both are small — it is whether the signal is a mechanism with a
measured margin or a set of thresholds fitted until the tuning corpus moved.

The author also traced the single near_miss delta (262 to 261) back to its truth
widget to confirm it was a decorative underline coincidentally overlapping a
separate nearby field, not a real detection lost.

### Left alone, with numbers, for whoever continues

- R5b never applies `_merge_ruling_lines`, so one visual line drawn as 2-3
  stacked rects yields duplicates. ~5 FP, 0 TP, in 1-2 forms.
- R5b has no OFFICE_USE guard although R2, R3, R12 and R14 all do. ~4-6 FP.
- `2312b040cb7e.pdf` draws one printed line as two abutting segments while truth
  expects one wide field — genuine wrong geometry, not a wrong claim.
- All-caps heading, word count, under-vs-left label source, and header-margin
  position were each tested as discriminators and none separated cleanly.


## Iteration 24 — R15 small hand-ruled checkboxes: REJECTED, opportunity was not real

The recall diagnosis identified this as the largest single group of missed
fields: 347 real-corpus checkbox widgets, median 13x13pt, worth ~6 points of
recall. It did not survive contact with the data.

    naive (small, near-square, single rect, nearby label)   precision 5.8%
    + stroke and content-tag filters                        precision ~35%, still f1-negative
    + isolation (no same-sized square shares its row/column) +2 tuning matches, 1 holdout FP

R15 alone: 3 detected, 2 matched. Effectively a no-op.

**Why the opportunity was illusory.** Most small squarish ink on real forms has
no backing truth widget at all: decorative Yes/No print squares, and printed
checklists whose real fillable field is one large free-text box elsewhere on the
page. Those are geometrically — and even content-tag-wise — indistinguishable
from a genuine hand-ruled checkbox.

So the 347 figure counted widgets that LOOK recoverable by shape, but shape is
not the signal. This tightens the recall-ceiling estimate: a further chunk of the
2,325 misses is unreachable without a signal we do not have.

The author correctly noted R15 was a different failure mode from R6 (R15 reads
rects where both dimensions are >= 3pt, disjoint from grid_cells' hairline
population) — so the deletion of R6 was not the reason this failed. It failed on
its own merits.

It also disclosed that it checked holdout more often than the "once at the end"
rule intended, rather than hiding it.

## Iteration 24b — scores files could name the wrong commit

The same agent reported HEAD_BASELINE.json as stale: git_sha 3c5a901, but a
clean run of 3c5a901 does not reproduce its numbers.

It was right about the symptom and the cause is mine. **Scoring happens BEFORE
the change is committed**, so the recorded sha names the PREVIOUS commit while
the numbers come from uncommitted code. Anyone reading scores/*.json would
reasonably conclude the file was stale and distrust it — one did, and spent
effort on it.

Scores now carry `git_dirty` and a `detector_fingerprint` (sha256 of
engine/detect/*.py as it was on disk). The fingerprint identifies the code that
actually produced the numbers; git_dirty says whether the sha can be trusted
alone.


## Iteration 25 — R3 inherits a header from free-floating page text: MERGED

    10 of 185 flagged widgets recovered, 0 false positives
    tuning  f1 0.6528 -> 0.6541  P 0.7894 -> 0.7899  R 0.5565 -> 0.5582
    holdout completely unchanged; label_accuracy bit-identical
    near_miss unchanged at 261

R3 could only read a column header off a `grid_cells()` cell, so a heading that
prints as free page text with no ruling around it was invisible — though a person
reading the page sees it plainly. Four guards together: tight horizontal
alignment to the column's own first row (6pt), vertical proximity (24pt),
heading shape, and corroboration by an adjacent column at the same line-top.

### The finding that matters more than the change

Two earlier versions recovered MORE fields and were discarded. The corroboration-
only version added 17 matches, but on `0b7c460527c6.pdf` those matches carried
labels like "ADDRESS OF" and "BEING RENTED TO TENANT(s)" — fragments of one
run-on sentence straddling a column boundary, not field names.

**The scorer would have counted every one of them as a recall gain**, because
box-IoU matching is all it does on real forms. Real stripped forms carry no truth
labels, so `label_accuracy` reports UNAVAILABLE for them and cannot see this.

So there is a live blind spot: **on real forms, label quality is unmeasured**. A
change can raise recall while silently attaching nonsense names, and only manual
inspection catches it. This is the same class of failure as the original
label-blindness fixed in harness round 2, but scoped to exactly the corpus where
truth labels do not exist — which is now 165 of the forms.

The fix that caught it is worth naming: reject a candidate when a floating word
on the shared line overlaps the physical GAP between two cells. A real header row
has words inside each column; a run-on sentence has a word bridging them.

### Yield note

10 of 185 is a low recovery rate, deliberately. The remaining 175 need signals
this rule refuses to guess at. Zero holdout effect because no holdout form
triggers it at all.


## Harness round 3 — label quality is now measurable on real forms

`label_accuracy` compares a label to a truth label, and only synthetic corpora
have those. All 165 real forms are stripped AcroForms with geometry but no label
text, so it reported UNAVAILABLE for the bulk of the evidence. A change could
raise recall while attaching nonsense names and nothing would object — which
nearly happened in iteration 25.

`label_plausibility` is truth-free, in the spirit of box_over_ink. Three signals
survived measurement:

- **provenance** — do the label's words occur, in that order, on the page?
- **contiguity** — is there a gap between matched words far wider than the page's
  own normal word spacing? That means two headers merged across a gutter.
- **fragment shape** — does a normally-spaced word sit immediately outside the
  label's span, excluded from it? That means a slice taken mid-run.

A fourth, "far from its matched text", was dropped: it fired constantly on one
header legitimately governing a tall stack of boxes.

Two false-positive modes were found and fixed against real output, not toy
cases: checkbox glyphs and underscore blanks sit beside almost every real label
by construction, and R11's dot leaders tokenise each dot as a word, which drove
R11 to a false 100% until both got the same exclusion box_over_ink already uses
for ink.

### It found real bugs in today's output

    1f697fad5785 p1  R2   'street address city, town or village country postal'
    1f697fad5785 p10 R2   'the applicant must be paid within the'
    1f95bd47c4b0 p1  R5b  'Name*: Last Name*:'   'Address*: City / Town*'
    2312b040cb7e     R5b  'First name: Middle'   'Middle name: Phone'  (x3)
    035b8cec3f20 p6  R5b  'is collected under section 26 (a) and (c) of the Freedom of '
    fixtures/safer.pdf    'Landlord Phone # Date:'   <- on our own reference form
    holdout 1fd892d78268  'Email' / 'Address:'  lifted mid-sentence from body text

Overall 35.2% of labels carry at least one flag. Per rule: R2 22.5%, R3 30.4%,
R5 54.5%, R5b 43.5%, and R4/R10/R11/R12 at or under 8%. R5 and R5b sit high by
construction — they read short context words beside fill-in blanks — so the
per-rule split matters more than the single number.

Gated on the box_over_ink pattern: fail only on a rise over 0.01 WITH new
offenders, warn on a rise without them, no-op when either run lacks the field.

**Cost:** a full scoring run now takes about two minutes, up from well under
one. Worth it, but it is the reason to keep the corpus from growing without
bound.


## Iteration 27 — R17, caption inside a cell's bottom band: MERGED

    R17: 41 detected, 41 matched, precision 1.0
    tuning  f1 0.6330 -> 0.6390  P 0.7746 -> 0.7770  R 0.5352 -> 0.5427
    label_accuracy 0.7009 -> 0.7255 (all 41 new label pairs agreed)
    holdout byte-identical

R2 turned upside down: the same one-line-of-text requirement, but the blank
space is ABOVE the caption up to the cell's own top border, reached only after
R2 has passed the cell by.

### My brief was wrong and the agent corrected it

I told it the shape was "airtight — every candidate matched truth, zero false
positives", quoting the original author. That measurement was synthetic-only. On
the full tuning+holdout corpus the naive version had **23 false positives out of
70 detections**. Three guards, all found by measuring real forms, cleared them:
row-mate rejection (a cell sharing its row band with another is a header or
row-label cell — 20 of 23), a width cap for page-wide notice bars, and a 4pt
bottom-gap floor (true matches cluster at 0.9pt; the residual false positive sat
11.8pt clear).

Passing a previous agent's claim forward as established fact was my error. Claims
from one measurement context do not transfer to another, and the brief should
have said "reported airtight on synthetic; verify on the full corpus".

### The tripwire fired again, and the ceiling moved — deliberately

R17 solves `sec_label_below`, which the hard corpus used as a difficulty source,
so the corpus score went 0.537 -> 0.569 past its 0.55 ceiling. Ceiling raised to
0.58.

**Raising it is bookkeeping, not a fix.** The corpus has permanently lost that
construct as difficulty. The loop is working as intended — corpus adds
difficulty, detector solves it, corpus needs more — but the ledger has to stay
honest about which constructs are spent. `sec_label_below` is now spent, joining
`dotted_line`, `ragged_table` and `merged_header_table`.

## Iteration 28 — gutter cut in label building (R2, R5, R5b, R10) — MERGED

`feaca36`

The first change specified by a metric rather than merely scored by one.
`label_plausibility` (added iteration 26) exists to judge label quality on real
forms, which carry no truth labels. Its `internal_gap` signal found that four
rules concatenated separate column headers into one label whenever their scan
crossed a gutter.

Offenders it named, all one mechanism:

    'Name*: Last Name*:'                                  (two column headers)
    'Address*: City / Town*'
    'First name: Middle' / 'Middle name: Phone'
    'street address city, town or village country postal'
    'Requested change: Amendment Duplicate Reason:'
    'Landlord Phone # Date:'                              (safer.pdf, page 7)

The last had been there since the first demo of this project.

Fix: `_page_gap_threshold()` takes the page's own median inter-word gap (not a
fixed point value — font size varies per form) and `_cut_at_gutter()` trims at
the first gap far wider than that, keeping the run adjacent to the field.

    label_plausibility 0.3720 -> 0.3115    internal_gap 364 -> 68
    tuning  f1 0.639017 -> 0.639162   P 0.776969 -> 0.777027
    holdout f1 0.641961 -> 0.641833 (noise)
    true positives lost: zero   near_miss unchanged 273 / 139
    label_accuracy 0.725451 unchanged (synthetic only; blind to this)
    GATE PASSED

f1 being flat is the expected shape: this changes what a field is *called*, not
whether it is found. Reporting it as a win on f1 would have been the wrong
claim — `label_plausibility` is the measurement that moved, by 16%.

Two follow-on defects surfaced during verification and were fixed:

1. Cutting the label defeated existing safety filters (R2's wide-colon-header
   guard, R5b's `OFFICE_USE` guard), because those read the already-shortened
   label. Precision dropped below baseline and the gate failed. Now the guards
   check the full pre-cut text while the cut text is emitted.
2. A lone symbol token could end up as the entire label
   (`'Amount of rent increase: $'` -> `'$'`). The cut now keeps pulling in words
   until the kept run contains a letter.

Rejected during the same iteration: applying the cut to R3 as well. R3's label
feeds a column-pitch/header-inheritance state machine, and shortening a label
there re-triggers header updates — ~10 new false positives for 2 true positives.
R3 was not among the named offenders; left alone.

**Test note.** `test_finds_the_real_cross_column_header_merge_bug` failed on
merge. Its own docstring said it pinned a live-detector defect and left the
detector out of scope — so it was a test *of the defect*, and fixing the defect
had to break it. Rewritten to feed the guard a handmade merged label (testing
the guard, which is what the file is for), plus a new regression test that the
detector returns `'Landlord Phone #'` and `'Date:'` as two fields. 107 tests.
