# AUTOPILOT.md — autobuild descriptor for FormFill

## Goal

For this run: **find and fix real detection bugs by testing on genuine,
never-fillable PDFs the tuned corpus does not cover, then improve the overall
filling experience.** This may become a commercial product, so correctness and
honest error handling matter as much as raw detection quality.

`eval/corpus/tuning` and `eval/holdout` only contain PDFs that WERE fillable
and got stripped for ground truth. Most of what has actually been fetched
(`eval/corpus/real/`) never had an AcroForm at all — 145 of 419 files, verdict
`flat-wordlike` or `flat-sparse` — and until this run, nothing had ever run the
detector against that pool. There is no answer key for these, so they cannot
move `tuning`/`holdout` f1. Their value is different: they are real producers
and real layouts a hand-picked 165-form corpus may simply not contain, and a
crash or a silent zero-field result on one of them is a bug regardless of what
any score says.

There is no "done". Rank by value each pass and make one change.

## Tonight's run (2026-09-05, until ~00:00)

Same goal, renewed by the user with one explicit priority: the existing
145-file blind pool from the 2026-09-03 run is largely mined out (it stalled
twice at 12 consecutive no-change passes — see `.autobuild/notify.log` and
`.autobuild/PROGRESS.md` entry 14, "every remaining zero-field lead there is
already dispositioned"). So for THIS run, treat value-ranking item #3
(fetch more candidates) as a standing priority, not a last resort — spend
early passes growing the pool with `eval/fetch.py` before assuming the
existing 145 have nothing left, then chase whatever `eval.blind` surfaces
against the grown pool per the normal workflow below. Regenerate
`.autobuild/blind_report.txt` after any fetch batch; it is stale the moment
new files land.

## Starting point — already investigated, do not re-derive

`python -m eval.blind` (new this run, `a0051c9`) runs the live detector plus
the truth-free guards across that 145-file pool and ranks the worst offenders.
A saved run sits at `.autobuild/blind_report.txt` — read it before generating
a fresh one; regenerate only if it looks stale (new fetches happened) or you
need `--dir eval/corpus/real_v4` (an older 53M fetch snapshot, also unscored,
also fair game).

**Already found, confirmed by hand, not yet fixed:**

- `eval/corpus/real/0a399532c0a54567.pdf` — a non-English (Khmer-script) social
  services form, 145 rects on page 1, **zero fields detected across both
  pages**. Likely a font-encoding or non-Latin-text assumption somewhere in
  label/word extraction. High value if the cause generalizes.
- `eval/corpus/real/1bdaa5e8fd5eaace.pdf` — a 4-page ACC-style application
  ("Tell us about yourself", numbered questions, "Flat/House number Street
  name", "Total cost of meals $ $"), 20–28 rects per page, **zero fields on
  every page**. A real ruled form our rules should reach and don't.
- 21 more structured, zero-field documents in the saved report, unexamined.

**Do NOT treat "zero fields" alone as a bug.** 41 of the 145 are correctly
empty — instruction sheets, cover letters, notices that happened to be linked
next to a real form when fetched. `eval.blind`'s `structured` flag already
filters most of these out using fetch.py's own signal counts
(checkbox glyphs, underscore runs, ruled-rect counts); trust it over a bare
zero. If a flagged "prose" document turns out to actually be a form, that is
itself worth a one-line note — the filter has a false-negative rate nobody has
measured yet.

## Value ranking (what "highest-value" means here)

1. **Chase a `eval.blind` finding to a real cause and fix it**, verified with
   `scripts/verify.sh` and re-confirmed by re-running `eval.blind` on that file
   (fields must go from 0 to something real, not from 0 to garbage — look at
   what got detected, not just the count).
2. **A correctness bug a real user would hit.** Grouping/injection/rendering
   bugs found this way (duplicate names losing data, non-Latin text crashing
   the save, a missing radio group) are the pattern to keep applying —
   several already fixed this run, see `.autobuild/PROGRESS.md`.
3. **Fetch a few more candidates and extend the blind pool**, per the workflow
   below, if the current 145 are exhausted of obvious findings. This alone is a
   landable, low-risk contribution even with no code fix attached — it grows
   the thing this whole run is testing against.
4. **The filling experience.** Keyboard flow, focus, large forms, honest
   failure messages for anything that currently does nothing silently.
5. Documentation of a decision that was measured but is not written down.

## Blind real-PDF testing workflow

1. `python -m eval.blind` (or read `.autobuild/blind_report.txt`) — pick a
   `STRUCTURED BUT ZERO FIELDS` or `CRASH` entry, or a high `label_plausibility`
   / `box_over_ink` one if the zero-field list is exhausted.
2. Open the PDF's real structure by hand — the same technique used throughout
   this project: `pdfplumber.open(path).pages[i]`, look at `.chars`, `.rects`,
   `.extract_words()` around where a field should be. Work out WHY the
   existing rules in `engine/detect/rules.py` don't reach it. A coordinate- or
   file-specific patch is not a fix; find the general shape (a font encoding,
   a rect-width assumption, a label-search radius) the way every prior rule
   in this codebase was built.
3. Fix it, then run `scripts/verify.sh` (tests + the full tuning/holdout gate
   — mandatory, even though the file you're chasing has no ground truth of its
   own: the fix touches shared rule code and must not regress the 165 scored
   forms).
4. Re-run `python -m eval.blind --dir eval/corpus/real --limit 1 --worst-only 1`
   is not enough to confirm a single-file fix — probe that one file directly:
   `python -c "from eval.blind import probe_one; print(probe_one('PATH'))"`
   and eyeball the actual fields, not just the count.
5. **Fetching more PDFs, if the current pool runs out of leads**, reuse
   `eval/fetch.py` — do not write a new fetcher. It is already polite:
   robots.txt honoured, one request per host every ≥2s, a fixed identifying
   User-Agent, caps at 60 files/run and 20MB/30 pages per file, and it backs
   off a whole host on 403/429. Keep it that way:
   - Public government or public-institution domains ONLY. No login, no
     paywall, no CAPTCHA bypass of any kind.
   - `python -m eval.fetch --out eval/corpus/real --limit 20` — small batches,
     so a bad run is cheap to notice and this doesn't dominate a pass's time
     budget.
   - Never touch `eval/corpus/tuning` or `eval/holdout` with fetched material.
     A fetched PDF only gets ground truth through human-verified labelling —
     out of scope for this loop, by design (see Protected paths).

## Protected paths — NEVER modify

The measurement apparatus is the only reason any claim in this repo is
trustworthy. A pass that edits it is not improving the product, it is moving
the goalposts. This has been attempted before and caught.

- `scores/**` — the gate's baseline. Editing it makes the gate meaningless.
- `eval/gate.py`, `eval/score.py`, `eval/match.py`, `eval/guards.py`,
  `eval/label.py`, `eval/limits.py` — the scorer, the gate and the guards.
- `eval/corpus/tuning/**`, `eval/holdout/**` — the scored ground truth.
- `eval/corpus/hard/**`, `eval/corpus/synth/**` — the adversarial corpus and
  its generator/truth.
- `eval/synth/test_hard.py` — contains `MAX_ALLOWED_F1`. It has been raised
  twice historically to absorb solved constructs and both times that was
  recorded as bookkeeping, not a fix. **Do not raise it. Ever.**
- `tests/render/goldens/**` — regenerating a golden to make a render test pass
  hides exactly the defect the test exists to catch.
- `scripts/verify.sh` — a pass cannot rewrite its own gate.
- `AUTOPILOT.md` — this file.
- `fixtures/**` — the reference form.

**Explicitly NOT protected, and the working area for this run:**
`eval/corpus/real/**`, `eval/corpus/real_v4/**`, `eval/blind.py`,
`.autobuild/blind_report.txt`. Nothing in these is read by the scorer or the
gate — grow, probe, and rewrite them freely.

## Constraints

- **One logical change per pass.** Small and reviewable. Not the whole goal.
- **Never weaken a measurement to make a change pass.** If a change is good but
  the gate rejects it, the honest outcome is to revert and record why. Several
  candidates have been rejected that way in this repo and every rejection was
  correct. A clean "do not merge" is a successful pass.
- **A change with no measurable benefit on tuning/holdout needs a stated
  reason.** For a blind-corpus fix that reason is built in — no ground truth
  exists there, so zero gate movement is expected and correct as long as
  `scripts/verify.sh` still passes clean. Say so in `.autobuild/PROGRESS.md`
  rather than stretching to claim a corpus win that isn't there.
- **Do not attempt the checkbox-truth corpus fix.** `docs/HANDOVER.md`
  limitation 4 records that `keep_reachable()` deletes 1,498 of 2,783 checkbox
  widgets from ground truth. It requires regenerating truth and re-baselining
  `scores/**`, which is protected. Leave it for a human-supervised session.
- **Licensing matters now.** Do not add a dependency under GPL/AGPL/LGPL/SSPL.
  Prefer no new dependency at all; if one is genuinely required, note the
  licence in `.autobuild/PROGRESS.md`.
- **The demo and the tests must not drift.** `tools/inject.mjs` is the single
  implementation of field injection, used by both. This repo has already lost
  a night to the demo importing a dead module while every test passed. If you
  change injection, both paths must still work, and `demo/demo.py` must still
  copy `tools/` into the served tree.
- If a change needs a judgement only a human should make — product scope,
  pricing, branding, anything about how a filled-in form's data is handled —
  skip it and say why.

## Machine config (read by autobuild.sh — keep exact key = value format)

```autobuild
verify = ./scripts/verify.sh
gate = pr
notify = sh -c 'printf "\n[%s] %s\n%s\n" "$(date "+%Y-%m-%d %H:%M")" "{title}" "{body}" >> .autobuild/notify.log'
branch_prefix = autobuild
email_to = ryan.xu282@gmail.com
email_cmd =
```

<!--
verify   scripts/verify.sh runs the test suite AND the detection eval, gated
         against scores/HEAD_BASELINE.json. Takes about two and a half
         minutes. pytest alone cannot see a recall regression, which is the
         failure mode that matters most in this repo.

gate     pr. As of 2026-09-05 this repo has a real GitHub remote
         (ryanzhxu/fillpdf) and CI (.github/workflows/ci.yml), and `main` has
         branch protection requiring a passing `tests` status check plus a PR
         (required_pull_request_reviews is set, required_approving_review_count
         0). A direct push to main is rejected by GitHub itself, so `direct`
         would fail outright now. autobuild.sh's own startup check queries
         this and would force `pr` regardless of what this file says — this
         value just makes that explicit instead of relying on the override.

notify   Appends to .autobuild/notify.log. Left as a local log rather than
         `gh issue create` (now possible, since a remote exists) because the
         user reads milestones directly from this file and PROGRESS.md in an
         interactive session, not by polling GitHub issues.

email_cmd  EMPTY, deliberately. There is no RESEND_API_KEY and no msmtp on this
         machine, so any email command here would fail silently every pass and
         create a false impression that milestones were being reported.
         Milestones land in .autobuild/milestones.log and PROGRESS.md instead.
-->
