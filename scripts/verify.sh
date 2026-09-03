#!/usr/bin/env bash
# The project's real gate. Exits 0 only if a change is safe to land.
#
# pytest ALONE IS NOT ENOUGH HERE. The tests check behaviour and contracts;
# they say nothing about whether the detector got better or worse at finding
# fields. A change can pass all 121 tests and quietly destroy recall across the
# 165-form corpus. So this script runs both:
#
#   1. the test suite
#   2. the detection eval, gated against scores/HEAD_BASELINE.json
#
# The eval step takes about two minutes. That is the cost of not shipping a
# silent regression, and it is worth paying on every pass.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY=./.venv/bin/python
OUT=.autobuild/verify_score

step() { printf '\n=== %s ===\n' "$1"; }

step "tests"
"$PY" -m pytest -q --ignore=eval/adversarial || { echo "VERIFY FAIL: tests"; exit 1; }

step "detection eval (about two minutes)"
rm -rf "$OUT"
"$PY" -m eval.score --corpus eval/corpus/tuning --holdout eval/holdout --out "$OUT" >/dev/null \
  || { echo "VERIFY FAIL: scoring crashed"; exit 1; }

CAND=$(ls "$OUT"/*.json 2>/dev/null | head -1)
[ -n "$CAND" ] || { echo "VERIFY FAIL: scorer produced no score file"; exit 1; }

step "gate vs baseline"
"$PY" -m eval.gate --candidate "$CAND" --baseline scores/HEAD_BASELINE.json \
  || { echo "VERIFY FAIL: gate rejected the change"; exit 1; }

# Report the numbers so a landed change is self-documenting in the loop log.
"$PY" - "$CAND" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
o, h = d["overall"], d["holdout"]
print("tuning  f1 %.6f  P %.6f  R %.6f" % (o["f1"], o["precision"], o["recall"]))
print("holdout f1 %.6f  P %.6f  R %.6f" % (h["f1"], h["precision"], h["recall"]))
PY

echo "VERIFY OK"
