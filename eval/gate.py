"""Acceptance gate: decide whether a candidate detector change may be kept.

Library:
    gate(candidate_scores, baseline_scores) -> dict
        {"passed": bool, "reasons": [...], "warnings": [...]}
    Both arguments are scores dicts per eval/contracts/scores.schema.json
    (e.g. json.loads of a scores/<sha>.json file).

CLI:
    python -m eval.gate --candidate scores/A.json --baseline scores/B.json
    exit 0 if passed, 1 if not.

A change is accepted only when ALL of these hold:
  1. Overall f1 on the tuning corpus does not decrease (tolerance -0.002).
  2. Holdout f1 does not decrease (same tolerance). This is the check that
     stops the loop overfitting to the synthetic generator. If either run's
     holdout has zero truth widgets, the check cannot run at all -- that is
     reported as a loud warning, never as a silent pass.
  3. No form family drops more than 0.02 f1.
  4. No new crash, timeout, or memory failure that was not in the baseline.
  5. Precision does not drop below the baseline precision.
  6. Overall label_accuracy does not drop more than 0.02 below baseline --
     but only when it was actually measurable (label_pairs > 0) in BOTH
     runs. Real stripped forms carry no truth labels at all, so a run
     scored only against those cannot be label-guarded; that is reported as
     a loud warning, never as a silent pass. This check exists because rect
     IoU alone cannot see a detection that lands on the right box with the
     wrong field name -- see eval/match.py's normalize_label.

Guards are handled separately, and deliberately not folded into a single
absolute threshold on box_over_ink -- see _check_guards.
"""
import argparse
import json
import sys
from pathlib import Path

F1_TOLERANCE = 0.002
FAMILY_DROP_LIMIT = 0.02
BOX_OVER_INK_RISE_LIMIT = 0.01
GLYPH_COVERAGE_FLOOR = 0.98
LABEL_ACCURACY_DROP_LIMIT = 0.02
CRASH_REASONS = {"crash", "timeout", "memory"}


def _f1(metrics):
    return metrics.get("f1", 0.0) if metrics else 0.0


def _check_overall_f1(candidate, baseline, reasons):
    cand_f1 = _f1(candidate.get("overall"))
    base_f1 = _f1(baseline.get("overall"))
    delta = cand_f1 - base_f1
    if delta < -F1_TOLERANCE:
        reasons.append(
            f"overall f1 dropped {delta:+.4f} "
            f"(baseline {base_f1:.4f} -> candidate {cand_f1:.4f})"
        )


def _check_holdout_f1(candidate, baseline, reasons, warnings):
    cand_holdout = candidate.get("holdout") or {}
    base_holdout = baseline.get("holdout") or {}
    cand_truth = cand_holdout.get("truth", 0)
    base_truth = base_holdout.get("truth", 0)
    if cand_truth == 0 or base_truth == 0:
        warnings.append(
            "HOLDOUT CHECK DID NOT RUN: holdout has zero truth widgets "
            f"(candidate truth={cand_truth}, baseline truth={base_truth}). "
            "This gate result is NOT holdout-guarded -- do not mistake it "
            "for one that is."
        )
        return
    cand_f1 = _f1(cand_holdout)
    base_f1 = _f1(base_holdout)
    delta = cand_f1 - base_f1
    if delta < -F1_TOLERANCE:
        reasons.append(
            f"holdout f1 dropped {delta:+.4f} "
            f"(baseline {base_f1:.4f} -> candidate {cand_f1:.4f})"
        )


def _check_label_accuracy(candidate, baseline, reasons, warnings):
    cand_overall = candidate.get("overall") or {}
    base_overall = baseline.get("overall") or {}
    cand_pairs = cand_overall.get("label_pairs", 0)
    base_pairs = base_overall.get("label_pairs", 0)
    cand_acc = cand_overall.get("label_accuracy")
    base_acc = base_overall.get("label_accuracy")
    measurable = (
        cand_pairs and base_pairs
        and isinstance(cand_acc, (int, float)) and isinstance(base_acc, (int, float))
    )
    if not measurable:
        warnings.append(
            "LABEL ACCURACY CHECK DID NOT RUN: label_accuracy is unavailable "
            f"(candidate label_pairs={cand_pairs}, baseline label_pairs={base_pairs}). "
            "This gate result is NOT label-guarded -- do not mistake it for one "
            "that is."
        )
        return
    delta = cand_acc - base_acc
    if delta < -LABEL_ACCURACY_DROP_LIMIT:
        reasons.append(
            f"label_accuracy dropped {delta:+.4f} "
            f"(baseline {base_acc:.4f} -> candidate {cand_acc:.4f})"
        )


def _check_family_drops(candidate, baseline, reasons):
    cand_families = candidate.get("per_family") or {}
    base_families = baseline.get("per_family") or {}
    for family, base_metrics in sorted(base_families.items()):
        if base_metrics.get("truth", 0) == 0:
            continue
        base_f1 = _f1(base_metrics)
        cand_f1 = _f1(cand_families.get(family))
        delta = base_f1 - cand_f1
        if delta > FAMILY_DROP_LIMIT:
            reasons.append(
                f"family '{family}' f1 dropped {delta:.4f} "
                f"(baseline {base_f1:.4f} -> candidate {cand_f1:.4f}), "
                f"limit {FAMILY_DROP_LIMIT}"
            )


def _check_new_crashes(candidate, baseline, reasons):
    base_bad_forms = {
        f["form"] for f in (baseline.get("failures") or [])
        if f.get("reason") in CRASH_REASONS
    }
    new = [
        f for f in (candidate.get("failures") or [])
        if f.get("reason") in CRASH_REASONS and f.get("form") not in base_bad_forms
    ]
    for f in new:
        reasons.append(
            f"new {f['reason']} failure on '{f['form']}': {f.get('detail', '')}"
        )


def _check_precision(candidate, baseline, reasons):
    cand_p = candidate.get("overall", {}).get("precision", 0.0)
    base_p = baseline.get("overall", {}).get("precision", 0.0)
    if cand_p < base_p - 1e-9:
        reasons.append(
            f"precision dropped from {base_p:.4f} to {cand_p:.4f} "
            "(recall gains must not be bought with precision)"
        )


def _offender_ids(guards):
    return {o["id"] for o in (guards or {}).get("box_over_ink_offenders", [])}


def _check_guards(candidate, baseline, reasons, warnings):
    """glyph_coverage is near-certain ground truth: hard-fail below 0.98.

    box_over_ink is a blunter signal. A correctly-placed box that clips one
    stray character can cross any fixed absolute threshold without being a
    real false positive, so we never hard-fail on its absolute value. We
    fail only when it RISES by more than 0.01 relative to baseline AND that
    rise is accompanied by new offender ids (present in candidate, not in
    baseline) -- a rise with no new offenders is a warning, not a failure.
    """
    cand_guards = candidate.get("guards")
    base_guards = baseline.get("guards")

    if cand_guards is not None:
        glyph = cand_guards.get("glyph_coverage")
        if glyph is not None and glyph < GLYPH_COVERAGE_FLOOR:
            reasons.append(
                f"glyph_coverage {glyph:.4f} is below the {GLYPH_COVERAGE_FLOOR} "
                "floor (a checkbox glyph is near-certain ground truth)"
            )

    if cand_guards is None or base_guards is None:
        return  # nothing to diff against

    cand_boi = cand_guards.get("box_over_ink")
    base_boi = base_guards.get("box_over_ink")
    if cand_boi is None or base_boi is None:
        return

    rise = cand_boi - base_boi
    if rise > BOX_OVER_INK_RISE_LIMIT:
        new_ids = sorted(_offender_ids(cand_guards) - _offender_ids(base_guards))
        if new_ids:
            reasons.append(
                f"box_over_ink rose {rise:+.4f} ({base_boi:.4f} -> {cand_boi:.4f}) "
                f"with new offenders: {', '.join(new_ids)}"
            )
        else:
            warnings.append(
                f"box_over_ink rose {rise:+.4f} ({base_boi:.4f} -> {cand_boi:.4f}) "
                "but no new offenders appeared -- watch this, do not block on "
                "it alone (check the offender list before failing a run)"
            )


def gate(candidate_scores, baseline_scores) -> dict:
    reasons = []
    warnings = []

    _check_overall_f1(candidate_scores, baseline_scores, reasons)
    _check_holdout_f1(candidate_scores, baseline_scores, reasons, warnings)
    _check_family_drops(candidate_scores, baseline_scores, reasons)
    _check_new_crashes(candidate_scores, baseline_scores, reasons)
    _check_precision(candidate_scores, baseline_scores, reasons)
    _check_label_accuracy(candidate_scores, baseline_scores, reasons, warnings)
    _check_guards(candidate_scores, baseline_scores, reasons, warnings)

    return {"passed": len(reasons) == 0, "reasons": reasons, "warnings": warnings}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Acceptance gate: candidate vs baseline scores.")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline", required=True)
    args = ap.parse_args(argv)

    candidate = json.loads(Path(args.candidate).read_text())
    baseline = json.loads(Path(args.baseline).read_text())

    result = gate(candidate, baseline)

    for w in result["warnings"]:
        print(f"WARNING: {w}")
    if result["passed"]:
        print("GATE PASSED")
    else:
        print("GATE FAILED")
        for r in result["reasons"]:
            print(f"  - {r}")

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
