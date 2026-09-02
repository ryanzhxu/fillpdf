"""Score detector output against truth, per eval/contracts/scores.schema.json.

Library:
    score_one(pdf_path, truth_path)        -> metrics dict for one form
    score_corpus(pairs, out_path=None)     -> full run dict, and (if out_path
                                               is given) writes
                                               <out_path>/<git-sha>.json

CLI:
    python -m eval.score --corpus DIR --holdout DIR [--adversarial DIR] --out DIR

Robustness: score_corpus scores every form in a subprocess, capped at a
wall-clock timeout (also applied as a best-effort CPU rlimit inside the
child) and a memory rlimit. A crash, timeout, or memory kill is recorded in
"failures" and the run continues. Progress is checkpointed to
<out_path>/<git-sha>.partial.json so a killed run can resume.
"""
import argparse
import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.match import match, normalize_label

DEFAULT_TIMEOUT_S = 30
DEFAULT_MEM_LIMIT_MB = 512
DEFAULT_DETECT_SPEC = "engine.detect:detect"

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Metric buckets: a small accumulator shape shared by whole-form, per-rule,
# per-family and overall/holdout aggregation, finalized into the ratios the
# scores schema wants only at the end.
# ---------------------------------------------------------------------------

def _empty_bucket():
    return {"truth": 0, "detected": 0, "matched": 0, "iou_sum": 0.0, "near_miss": 0,
            "label_pairs": 0, "label_agree": 0}


def _add_bucket(acc, b):
    acc["truth"] += b["truth"]
    acc["detected"] += b["detected"]
    acc["matched"] += b["matched"]
    acc["iou_sum"] += b["iou_sum"]
    acc["near_miss"] += b["near_miss"]
    acc["label_pairs"] += b.get("label_pairs", 0)
    acc["label_agree"] += b.get("label_agree", 0)


def _clamp(x):
    """Metrics are fractions. A value outside [0,1] is a bug, not a result.

    The clamp is a safety net, not the fix. See attribution_ok below: when a
    per-rule bucket reports more matches than truth, its truth side was never
    attributed and its recall and f1 are meaningless, not merely clipped.
    """
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _finalize_bucket(b):
    truth, detected, matched = b["truth"], b["detected"], b["matched"]
    precision = (matched / detected) if detected else 0.0
    recall = (matched / truth) if truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    placement = (b["iou_sum"] / matched) if matched else 0.0
    # Recall needs truth attributed to the same bucket. per_rule truth comes
    # from a truth widget's "expects_rule", which only synthetic corpora carry.
    # On real forms there is none, so matched can exceed truth -- that bucket's
    # recall and f1 must be read as unavailable, not as a score.
    attribution_ok = b["truth"] >= b["matched"]
    # label_accuracy needs a truth "label" to check against, which only
    # synthetic corpora carry (real stripped forms have none at all). When no
    # matched pair in this bucket had one to check, report UNAVAILABLE --
    # never 1.0 (vacuously "all agree") or 0.0 (looks like total failure).
    label_pairs = b.get("label_pairs", 0)
    label_accuracy = (b.get("label_agree", 0) / label_pairs) if label_pairs else "UNAVAILABLE"
    return {
        "attribution_ok": attribution_ok,
        "truth": truth, "detected": detected, "matched": matched,
        "precision": _clamp(precision), "recall": _clamp(recall), "f1": _clamp(f1),
        "placement": _clamp(placement), "near_miss": b["near_miss"],
        "label_accuracy": label_accuracy, "label_pairs": label_pairs,
    }


def _score_detections(detections, truth_widgets):
    """Match detections to truth and build the whole-form bucket plus
    per-rule buckets (detections attributed by their own "rule" field,
    truth widgets by "expects_rule", falling back to "unknown").
    """
    result = match(detections, truth_widgets)

    form = _empty_bucket()
    form["truth"] = len(truth_widgets)
    form["detected"] = len(detections)
    form["matched"] = len(result["matches"])
    form["iou_sum"] = sum(iou_val for _, _, iou_val in result["matches"])
    form["near_miss"] = len(result["near_miss"])

    rule_buckets = {}

    def bucket(rule):
        return rule_buckets.setdefault(rule, _empty_bucket())

    for t in truth_widgets:
        bucket(t.get("expects_rule", "unknown"))["truth"] += 1
    for d in detections:
        bucket(d.get("rule", "unknown"))["detected"] += 1
    for di, ti, iou_val in result["matches"]:
        rb = bucket(detections[di].get("rule", "unknown"))
        rb["matched"] += 1
        rb["iou_sum"] += iou_val
        t_label = truth_widgets[ti].get("label")
        if t_label:  # only synthetic truth carries a label to check against
            agree = normalize_label(t_label) == normalize_label(detections[di].get("label", ""))
            # Per-rule always counts, so the checkbox gap stays visible as R1=0.0.
            rb["label_pairs"] += 1
            rb["label_agree"] += int(agree)
            # The form-level figure excludes checkboxes. The detector gives them
            # no label at all, so they are a permanent 0% floor: including them
            # would let a change that merely shifts the checkbox/text mix move
            # the gated number without touching naming accuracy at all. Same
            # class of false alarm as gating on an absolute box_over_ink value.
            if detections[di].get("type") != "checkbox":
                form["label_pairs"] += 1
                form["label_agree"] += int(agree)
    for ti in result["near_miss"]:
        bucket(truth_widgets[ti].get("expects_rule", "unknown"))["near_miss"] += 1

    return result, form, rule_buckets


def score_one(pdf_path, truth_path, detect_fn=None) -> dict:
    """Metrics for a single form (no subprocess, no resource limits)."""
    truth_doc = json.loads(Path(truth_path).read_text())
    truth_widgets = truth_doc["widgets"]
    if detect_fn is None:
        from engine.detect import detect as detect_fn
    fields_doc = detect_fn(str(pdf_path))
    detections = fields_doc["fields"]
    _, form, _ = _score_detections(detections, truth_widgets)
    return _finalize_bucket(form)


# ---------------------------------------------------------------------------
# Subprocess worker: one form, CPU/memory-capped, wall-clock-timed-out by
# the caller. Invoked as `python -m eval.score --_worker <pdf> <truth>
# <detect_spec> <mem_limit_mb> <cpu_limit_s>`; prints one JSON line.
# ---------------------------------------------------------------------------

def _load_detect_fn(spec):
    module_name, func_name = spec.split(":", 1)
    mod = importlib.import_module(module_name)
    return getattr(mod, func_name)


def _set_resource_limits(mem_limit_mb, cpu_limit_s):
    # Best effort only. macOS silently refuses to lower RLIMIT_AS, so the
    # parent-side MemoryWatchdog in _run_worker is the cap that actually holds.
    from eval.limits import apply_child_limits
    apply_child_limits(mem_limit_mb, cpu_limit_s)


def _worker_main(argv):
    pdf_path, truth_path, detect_spec, mem_limit_mb, cpu_limit_s = argv[:5]
    _set_resource_limits(mem_limit_mb, max(1, int(round(float(cpu_limit_s)))))
    try:
        truth_doc = json.loads(Path(truth_path).read_text())
        truth_widgets = truth_doc["widgets"]
        detect_fn = _load_detect_fn(detect_spec)
        fields_doc = detect_fn(pdf_path)
        detections = fields_doc["fields"]
        _, form, rule_buckets = _score_detections(detections, truth_widgets)
        try:
            from eval.guards import guards as _guards
            guard_data = _guards(pdf_path, detections)
        except Exception as e:      # a guard must never fail a form's score
            guard_data = {"error": f"{type(e).__name__}: {e}"[:200]}
        out = {
            "ok": True,
            "guards": guard_data,
            "source_pdf": truth_doc.get("source_pdf", Path(pdf_path).name),
            "family": truth_doc.get("family", "unknown"),
            "metrics": _finalize_bucket(form),
            "raw": form,
            "rule_buckets": rule_buckets,
        }
    except MemoryError as e:
        out = {"ok": False, "reason": "memory", "detail": str(e)[:500]}
    except (json.JSONDecodeError, KeyError) as e:
        out = {"ok": False, "reason": "malformed", "detail": f"{type(e).__name__}: {e}"[:500]}
    except Exception as e:
        msg = str(e).lower()
        reason = "encrypted" if ("password" in msg or "encrypt" in msg) else "crash"
        out = {"ok": False, "reason": reason, "detail": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out))
    sys.exit(0)


def _run_worker(pdf_path, truth_path, detect_spec, timeout_s, mem_limit_mb):
    cmd = [
        sys.executable, "-m", "eval.score", "--_worker",
        str(pdf_path), str(truth_path), detect_spec, str(mem_limit_mb), str(timeout_s),
    ]
    from eval.limits import MemoryWatchdog
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    with MemoryWatchdog(proc, mem_limit_mb) as wd:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"ok": False, "reason": "timeout",
                    "detail": f"exceeded {timeout_s}s wall clock"}
    if wd.fired.is_set():
        return {"ok": False, "reason": "memory",
                "detail": f"RSS crossed {mem_limit_mb}MB (watchdog); force-killed"}

    class _P:  # keep the shape the code below expects
        pass
    proc_result = _P()
    proc_result.stdout, proc_result.stderr = stdout, stderr
    proc_result.returncode = proc.returncode
    proc = proc_result

    lines = proc.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (ValueError, IndexError):
        detail = (proc.stderr or "").strip()[-500:]
        return {"ok": False, "reason": "crash", "detail": detail or f"exit code {proc.returncode}, no output"}


# ---------------------------------------------------------------------------
# Corpus discovery and full-run scoring
# ---------------------------------------------------------------------------

def _discover_pairs(corpus_dir):
    """Find (pdf_path, truth_path) pairs under a directory.

    Convention: a truth JSON file is any *.json under corpus_dir with a
    "widgets" key. It names its PDF via "source_pdf" (a bare filename); the
    PDF is looked up beside the truth file first, then anywhere else under
    corpus_dir with that name.
    """
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.exists():
        return []

    pdf_index = {}
    for p in corpus_dir.rglob("*.pdf"):
        pdf_index.setdefault(p.name, p)

    pairs = []
    for tf in sorted(corpus_dir.rglob("*.json")):
        try:
            doc = json.loads(tf.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(doc, dict) or "widgets" not in doc:
            continue
        source_pdf = doc.get("source_pdf")
        if not source_pdf:
            continue
        candidate = tf.parent / source_pdf
        pdf_path = candidate if candidate.exists() else pdf_index.get(source_pdf)
        if pdf_path is None:
            continue
        pairs.append((pdf_path, tf))
    return sorted(pairs, key=lambda pt: str(pt[0]))


def _git_sha():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                            capture_output=True, text=True, timeout=5)
        sha = r.stdout.strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"


def score_corpus(pairs, out_path=None, *, holdout_pairs=None, adversarial_pairs=None,
                  timeout=DEFAULT_TIMEOUT_S, mem_limit_mb=DEFAULT_MEM_LIMIT_MB,
                  detect_spec=DEFAULT_DETECT_SPEC, git_sha=None) -> dict:
    holdout_pairs = holdout_pairs or []
    adversarial_pairs = adversarial_pairs or []
    git_sha = git_sha or _git_sha()
    started_at = datetime.now(timezone.utc).isoformat()

    out_dir = Path(out_path) if out_path else None
    partial_path = (out_dir / f"{git_sha}.partial.json") if out_dir else None
    final_path = (out_dir / f"{git_sha}.json") if out_dir else None

    done = {}
    if partial_path and partial_path.exists():
        try:
            done = json.loads(partial_path.read_text())
        except (ValueError, OSError):
            done = {}

    all_work = (
        [(p, t, "tuning") for p, t in pairs]
        + [(p, t, "holdout") for p, t in holdout_pairs]
        + [(p, t, "adversarial") for p, t in adversarial_pairs]
    )

    for pdf_path, truth_path, tag in all_work:
        key = f"{tag}:{pdf_path}"
        if key in done:
            continue
        result = _run_worker(pdf_path, truth_path, detect_spec, timeout, mem_limit_mb)
        result["_tag"] = tag
        result["_pdf_path"] = str(pdf_path)
        done[key] = result
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            partial_path.write_text(json.dumps(done, sort_keys=True))

    overall, holdout = _empty_bucket(), _empty_bucket()
    per_rule, per_family, per_form = {}, {}, {}
    failures = []
    guard_boxes = guard_inked = 0          # box_over_ink, pooled over the corpus
    guard_glyphs = guard_glyphs_hit = 0    # glyph_coverage
    guard_offenders = []
    guard_stacked = []

    for key in sorted(done):
        result = done[key]
        tag = result.get("_tag", "tuning")
        if not result.get("ok", False):
            failures.append({
                "form": result.get("_pdf_path", key),
                "reason": result.get("reason", "crash"),
                "detail": result.get("detail", ""),
            })
            continue
        if tag == "adversarial":
            continue  # scored only for robustness; not mixed into any metric bucket

        raw = result["raw"]
        if tag == "tuning":
            _add_bucket(overall, raw)
        elif tag == "holdout":
            _add_bucket(holdout, raw)

        for rule, b in result.get("rule_buckets", {}).items():
            _add_bucket(per_rule.setdefault(rule, _empty_bucket()), b)

        family = result.get("family", "unknown")
        _add_bucket(per_family.setdefault(family, _empty_bucket()), raw)

        source_pdf = result.get("source_pdf", result.get("_pdf_path", key))
        per_form[source_pdf] = result["metrics"]

        # guards() returns box_over_ink and glyph_coverage as plain fractions
        # per form. Weight each by that form's box / glyph count so the pooled
        # corpus figure is not a mean of means.
        g = result.get("guards") or {}
        n_boxes = raw.get("detected", 0)
        guard_boxes += n_boxes
        guard_inked += (g.get("box_over_ink") or 0.0) * n_boxes
        for off in (g.get("box_over_ink_offenders") or []):
            guard_offenders.append({**off, "form": source_pdf})
        n_glyphs = g.get("glyph_count")
        if n_glyphs is None:
            n_glyphs = 1 if g.get("glyph_coverage") is not None else 0
        guard_glyphs += n_glyphs
        guard_glyphs_hit += (g.get("glyph_coverage") or 0.0) * n_glyphs
        guard_stacked.append(((g.get("whitespace_fit") or {}).get("stacked_fraction") or 0.0))

    output = {
        "version": 1,
        "git_sha": git_sha,
        "started_at": started_at,
        "corpus": {
            "tuning": len(pairs), "holdout": len(holdout_pairs),
            "adversarial": len(adversarial_pairs), "excluded": 0,
        },
        "overall": _finalize_bucket(overall),
        "holdout": _finalize_bucket(holdout),
        "per_rule": {k: _finalize_bucket(per_rule[k]) for k in sorted(per_rule)},
        "per_family": {k: _finalize_bucket(per_family[k]) for k in sorted(per_family)},
        "per_form": {k: per_form[k] for k in sorted(per_form)},
        "failures": sorted(failures, key=lambda f: f["form"]),
        "guards": {
            "box_over_ink": (guard_inked / guard_boxes) if guard_boxes else 0.0,
            "glyph_coverage": (guard_glyphs_hit / guard_glyphs) if guard_glyphs else 1.0,
            "stacked_fraction": (sum(guard_stacked) / len(guard_stacked)) if guard_stacked else 0.0,
            "offenders": sorted(guard_offenders,
                                key=lambda o: -(o.get("coverage") or 0))[:40],
        },
    }

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        final_path.write_text(json.dumps(output, sort_keys=True, indent=2))
        if partial_path.exists():
            partial_path.unlink()

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--_worker":
        _worker_main(argv[1:])
        return

    ap = argparse.ArgumentParser(description="Score detector output against truth.")
    ap.add_argument("--corpus", required=True, help="tuning corpus directory")
    ap.add_argument("--holdout", help="holdout corpus directory")
    ap.add_argument("--adversarial", help="adversarial corpus directory (robustness only)")
    ap.add_argument("--out", required=True, help="directory to write scores/<git-sha>.json")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--mem-limit-mb", type=int, default=DEFAULT_MEM_LIMIT_MB)
    args = ap.parse_args(argv)

    pairs = _discover_pairs(args.corpus)
    holdout_pairs = _discover_pairs(args.holdout) if args.holdout else []
    adversarial_pairs = _discover_pairs(args.adversarial) if args.adversarial else []

    result = score_corpus(
        pairs, out_path=args.out, holdout_pairs=holdout_pairs,
        adversarial_pairs=adversarial_pairs, timeout=args.timeout,
        mem_limit_mb=args.mem_limit_mb,
    )
    print(json.dumps({
        "overall": result["overall"], "holdout": result["holdout"],
        "failures": len(result["failures"]),
    }, indent=2))


if __name__ == "__main__":
    main()
