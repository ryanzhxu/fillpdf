# AUTOPILOT.md — autobuild descriptor for FormFill

## Goal

Improve how well FormFill finds and captures input fields on flat PDFs, and
improve the experience of actually filling one. This may become a commercial
product, so treat correctness, honest error handling, and permissive licensing
as part of the goal rather than polish to add later.

There is no "done". Rank by value each pass and make one change.

## Value ranking (what "highest-value" means here)

1. **A correctness bug a real user would hit.** Highest value. Known, live,
   verified example: mutually exclusive options are emitted as independent
   checkboxes, so a person can tick BOTH "Option 1: Consent Granted" and
   "Option 2: Consent Not Granted" on `fixtures/safer.pdf` page 2. That is
   wrong in law, not just in software. Radio groups need a field type, a
   grouping rule, and `pdf-lib` support checked in `tools/inject.mjs`.
2. **A guard against a bad input a public app will certainly receive.** No
   scanned-PDF check exists: hand the detector an image-only PDF today and it
   returns an empty result with no explanation. A commercial product cannot do
   that. One check — no text layer, or a text layer implausibly sparse for the
   page count — plus a clear message the UI can show.
3. **Detection precision or recall, measured.** A rule change that raises
   tuning AND holdout without regressing either, or a false positive removed at
   no recall cost. `docs/HANDOVER.md` carries a ranked backlog with numbers
   already attached — read it before inventing a new idea.
4. **The filling experience.** Keyboard flow, tab order, focus behaviour, what
   happens on a form with 200+ fields, whether the download actually round
   trips. Small and real beats broad and speculative.
5. **Honest failure.** Anywhere the app silently does nothing, make it say what
   went wrong and what to do. This ranks above cosmetics for a paid product.
6. Documentation of a decision that was measured but is not written down.

## Protected paths — NEVER modify

The measurement apparatus is the only reason any claim in this repo is
trustworthy. A pass that edits it is not improving the product, it is moving
the goalposts. This has been attempted before and caught.

- `scores/**` — the gate's baseline. Editing it makes the gate meaningless.
- `eval/gate.py`, `eval/score.py`, `eval/match.py`, `eval/guards.py`,
  `eval/label.py`, `eval/limits.py` — the scorer, the gate and the guards.
- `eval/synth/test_hard.py` — contains `MAX_ALLOWED_F1`. It has been raised
  twice historically to absorb solved constructs and both times that was
  recorded as bookkeeping, not a fix. **Do not raise it. Ever.**
- `eval/corpus/**`, `eval/holdout/**` — the 165-form corpus and its truth.
- `tests/render/goldens/**` — regenerating a golden to make a render test pass
  hides exactly the defect the test exists to catch.
- `scripts/verify.sh` — a pass cannot rewrite its own gate.
- `AUTOPILOT.md` — this file.
- `fixtures/**` — the reference form.

## Constraints

- **One logical change per pass.** Small and reviewable. Not the whole goal.
- **Never weaken a measurement to make a change pass.** If a change is good but
  the gate rejects it, the honest outcome is to revert and record why. Six
  candidates have been rejected that way in this repo and every rejection was
  correct. A clean "do not merge" is a successful pass.
- **A change with no measurable benefit needs a stated reason to exist.** Zero
  effect on tuning and holdout means either it is a genuine correctness fix
  whose case the corpus cannot represent, or it is not worth landing. Say
  which, in `.autobuild/PROGRESS.md`.
- **Do not attempt the checkbox-truth corpus fix.** `docs/HANDOVER.md`
  limitation 4 records that `keep_reachable()` deletes 1,498 of 2,783 checkbox
  widgets from ground truth. It is the most valuable item in the backlog and it
  requires regenerating truth and re-baselining — which touches protected paths
  and needs a human. Leave it.
- **Licensing matters now.** Do not add a dependency under GPL/AGPL/LGPL/SSPL.
  Prefer no new dependency at all; if one is genuinely required, note the
  licence in `.autobuild/PROGRESS.md`.
- **The demo and the tests must not drift.** `tools/inject.mjs` is the single
  implementation of field injection, used by both. This repo has already lost a
  night to the demo importing a dead module while every test passed. If you
  change injection, both paths must still work, and `demo/demo.py` must still
  copy `tools/` into the served tree.
- If a change needs a judgement only a human should make — product scope,
  pricing, branding, anything about how the form's data is handled — skip it
  and say why.

## Machine config (read by autobuild.sh — keep exact key = value format)

```autobuild
verify = ./scripts/verify.sh
gate = direct
notify = sh -c 'printf "\n[%s] %s\n%s\n" "$(date "+%Y-%m-%d %H:%M")" "{title}" "{body}" >> .autobuild/notify.log'
branch_prefix = autobuild
email_to = ryan.xu282@gmail.com
email_cmd =
```

<!--
verify   scripts/verify.sh runs the 121-test suite AND the detection eval,
         gated against scores/HEAD_BASELINE.json. Takes about two and a half
         minutes. pytest alone cannot see a recall regression, which is the
         failure mode that matters most in this repo.

gate     direct. This repo has NO git remote and NO CI, so `pr` is impossible.
         land_direct commits locally FIRST and then pushes, so with no remote
         the commit still lands and only the push step logs a failure. That is
         the intended behaviour here: work accumulates in local history for
         review.

notify   Appends to .autobuild/notify.log. `gh issue create` cannot work with
         no remote.

email_cmd  EMPTY, deliberately. There is no RESEND_API_KEY and no msmtp on this
         machine, so any email command here would fail silently every pass and
         create a false impression that milestones were being reported.
         Milestones land in .autobuild/milestones.log and PROGRESS.md instead.
-->
