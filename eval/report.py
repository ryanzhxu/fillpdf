"""Human-readable report for one scoring run, optionally diffed against a
baseline. Plain text only -- no colour, no unicode box-drawing -- so it can
be pasted into a commit message or an email.

Library:
    report(scores_path, baseline_path=None) -> str

CLI:
    python -m eval.report scores/<sha>.json [--baseline scores/<other>.json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

EPS = 1e-9
WORST_N = 10

# Metrics where a decrease from baseline is a regression.
_HIGHER_IS_BETTER = {"precision", "recall", "f1", "holdout f1", "glyph_coverage"}
# Metrics where an increase from baseline is a regression.
_LOWER_IS_BETTER = {"box_over_ink", "too_small_fraction", "stacked_fraction"}

_RULE_RE = re.compile(r"^R(\d+)([A-Za-z]*)$")


def _load(path):
    return json.loads(Path(path).read_text())


def _delta_str(delta, digits=4):
    return f"{delta:+.{digits}f}"


def _regression_tag(metric_name, delta):
    if delta is None:
        return ""
    if metric_name in _HIGHER_IS_BETTER and delta < -EPS:
        return "  REGRESSION"
    if metric_name in _LOWER_IS_BETTER and delta > EPS:
        return "  REGRESSION"
    return ""


def _metric_line(label, metric_name, value, base_value=None, digits=4):
    line = f"  {label:<14} {value:.{digits}f}"
    if base_value is not None:
        delta = value - base_value
        line += f"   ({_delta_str(delta, digits)})"
        line += _regression_tag(metric_name, delta)
    return line


def _rule_sort_key(name):
    m = _RULE_RE.match(name)
    if m:
        return (0, int(m.group(1)), m.group(2))
    return (1, 0, name)


def _table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def _metrics_row(name, m, base_m=None, show_delta=False):
    f1 = m.get("f1", 0.0)
    row = [
        name,
        str(m.get("truth", 0)),
        str(m.get("detected", 0)),
        str(m.get("matched", 0)),
        f"{m.get('precision', 0.0):.4f}",
        f"{m.get('recall', 0.0):.4f}",
        f"{f1:.4f}",
        str(m.get("near_miss", 0)),
    ]
    if show_delta:
        if base_m is not None:
            delta = f1 - base_m.get("f1", 0.0)
            row.append(_delta_str(delta) + _regression_tag("f1", delta))
        else:
            row.append("n/a (new)")
    return row


def _metrics_headers(show_delta):
    headers = ["name", "truth", "detected", "matched", "precision", "recall", "f1", "near_miss"]
    if show_delta:
        headers.append("f1 delta")
    return headers


def report(scores_path, baseline_path=None) -> str:
    scores = _load(scores_path)
    baseline = _load(baseline_path) if baseline_path else None
    show_delta = baseline is not None

    lines = []
    lines.append("FORMFILL DETECTION EVAL REPORT")
    lines.append(f"candidate: {scores.get('git_sha', '?')}  ({scores.get('started_at', '?')})")
    if baseline is not None:
        lines.append(f"baseline:  {baseline.get('git_sha', '?')}  ({baseline.get('started_at', '?')})")
    lines.append("")

    # --- Headline ------------------------------------------------------
    overall = scores.get("overall", {})
    base_overall = baseline.get("overall") if baseline else None
    holdout = scores.get("holdout", {})
    base_holdout = baseline.get("holdout") if baseline else None

    lines.append("OVERALL")
    lines.append(_metric_line("precision", "precision", overall.get("precision", 0.0),
                               base_overall.get("precision") if base_overall else None))
    lines.append(_metric_line("recall", "recall", overall.get("recall", 0.0),
                               base_overall.get("recall") if base_overall else None))
    lines.append(_metric_line("f1", "f1", overall.get("f1", 0.0),
                               base_overall.get("f1") if base_overall else None))
    if holdout.get("truth", 0) == 0:
        lines.append("  holdout f1     n/a (holdout has zero truth widgets -- not guarded)")
    else:
        base_holdout_f1 = None
        if base_holdout is not None and base_holdout.get("truth", 0) > 0:
            base_holdout_f1 = base_holdout.get("f1")
        lines.append(_metric_line("holdout f1", "holdout f1", holdout.get("f1", 0.0), base_holdout_f1))
    lines.append("")

    # --- Per-rule --------------------------------------------------------
    lines.append("PER-RULE (R1..R8)")
    per_rule = scores.get("per_rule", {})
    base_per_rule = baseline.get("per_rule", {}) if baseline else {}
    rule_names = sorted(per_rule.keys(), key=_rule_sort_key)
    rows = [_metrics_row(r, per_rule[r], base_per_rule.get(r), show_delta) for r in rule_names]
    lines.append(_table(_metrics_headers(show_delta), rows))
    lines.append("")

    # --- Per-family ------------------------------------------------------
    lines.append("PER-FAMILY")
    per_family = scores.get("per_family", {})
    base_per_family = baseline.get("per_family", {}) if baseline else {}
    fam_names = sorted(per_family.keys())
    rows = [_metrics_row(f, per_family[f], base_per_family.get(f), show_delta) for f in fam_names]
    lines.append(_table(_metrics_headers(show_delta), rows))
    lines.append("")

    # --- Worst forms -------------------------------------------------------
    lines.append(f"WORST {WORST_N} FORMS BY F1")
    per_form = scores.get("per_form", {})
    worst = sorted(per_form.items(), key=lambda kv: (kv[1].get("f1", 0.0), kv[0]))[:WORST_N]
    rows = [_metrics_row(name, m) for name, m in worst]
    lines.append(_table(_metrics_headers(False), rows))
    lines.append("")

    # --- Guards ------------------------------------------------------------
    lines.append("GUARDS")
    guards = scores.get("guards")
    base_guards = baseline.get("guards") if baseline else None
    if guards is None:
        lines.append("  (no guards data in this run)")
    else:
        base_boi = base_guards.get("box_over_ink") if base_guards else None
        base_glyph = base_guards.get("glyph_coverage") if base_guards else None
        base_wf = base_guards.get("whitespace_fit") if base_guards else None

        lines.append(_metric_line("box_over_ink", "box_over_ink", guards.get("box_over_ink", 0.0), base_boi))
        lines.append(_metric_line("glyph_coverage", "glyph_coverage", guards.get("glyph_coverage", 0.0), base_glyph))
        wf = guards.get("whitespace_fit", {})
        lines.append(_metric_line(
            "too_small", "too_small_fraction", wf.get("too_small_fraction", 0.0),
            base_wf.get("too_small_fraction") if base_wf else None,
        ))
        lines.append(_metric_line(
            "stacked", "stacked_fraction", wf.get("stacked_fraction", 0.0),
            base_wf.get("stacked_fraction") if base_wf else None,
        ))
    lines.append("")

    # --- Failures ------------------------------------------------------
    lines.append("FAILURES")
    failures = scores.get("failures", [])
    if not failures:
        lines.append("  none")
    else:
        by_reason = {}
        for f in failures:
            by_reason.setdefault(f.get("reason", "unknown"), []).append(f)
        for reason in sorted(by_reason):
            group = by_reason[reason]
            lines.append(f"  {reason} ({len(group)}):")
            for f in sorted(group, key=lambda x: x.get("form", "")):
                lines.append(f"    {f.get('form', '?')}: {f.get('detail', '')}")
    lines.append("")

    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Human-readable report for a scoring run.")
    ap.add_argument("scores_path")
    ap.add_argument("--baseline", default=None)
    args = ap.parse_args(argv)
    print(report(args.scores_path, args.baseline))


if __name__ == "__main__":
    main()
