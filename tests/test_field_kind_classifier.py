"""Tests for classifyFieldKind() (date/month/integer field detection) in
demo/index.html.

This runs the classifier's own real JS in Node rather than re-implementing
its regexes in Python -- the same reasoning tests/render/inject.py gives for
shelling out to tools/inject_cli.mjs instead of re-implementing pdf-lib
injection: the tests must exercise the exact code the demo runs, not a
lookalike that can silently drift from it.

Labels below are drawn from fixtures/safer.pdf AND a diverse sample of real
eval/corpus/tuning forms, not just safer.pdf, chosen to cover: scrambled word
order (the label-detection rules can reorder words), uppercase DD/MM/YYYY,
the less common "ccyy" year token, a bare "Date"/"DOB" with no format hint,
Month/Year granularity, and the case that first broke a naive "number of X"
heuristic during development -- "Phone Number of applicant, printed", which
must NOT be treated as an integer field despite containing "Number of".
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "demo" / "index.html"

_START = "// FIELD_KIND_CLASSIFIER_START"
_END = "// FIELD_KIND_CLASSIFIER_END"


def _extract_classifier_js() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index(_START)
    end = html.index(_END, start)
    return html[start:end]


CASES = [
    # (label, expected kind: 'date' | 'month' | 'integer' | None)
    # -- explicit day/month/year format tokens, including scrambled word
    #    order and non-lowercase tokens --
    ("Birth Date (dd/mm/yyyy)", "date"),
    ("Birth (dd/mm/yyyy) Date*", "date"),
    ("Date to (dd/mm/yyyy) Canada moved", "date"),
    ("(dd/mm/yyyy) From Date", "date"),
    ("Date Signed (DD/MM/YYYY)", "date"),                  # eval/corpus/tuning/24b613cc915b.pdf
    ("Date (dd/mm/yyyy):", "date"),                        # eval/corpus/tuning/2312b040cb7e.pdf
    ("(mm/dd/ccyy) (Unknown)", "date"),                    # eval/corpus/tuning/2c5f89f13df6.pdf -- "ccyy", not "yyyy"
    # -- date-ish keyword, no format hint -> ISO fallback --
    ("Date:", "date"),
    ("Date", "date"),
    ("DOB", "date"),                                       # eval/corpus/tuning/108621d8eae3.pdf
    ("Date of Birth:", "date"),
    ("Expiration Date", "date"),
    # -- month/year granularity --
    ("when was the last payment received? (Month/Year)", "month"),
    ("yes, when did you last work? (Month/Year)", "month"),
    # -- integer: true counts --
    ("Age", "integer"),
    ("Approximate Age", "integer"),                        # eval/corpus/tuning/741655e96b58.pdf
    ("If yes, how many:", "integer"),                      # eval/corpus/tuning/b551e45bf66a.pdf
    ("a) Number of daily work shifts:", "integer"),        # eval/corpus/tuning/3aab50fae7c3.pdf
    ("Total Number of Rent Increase Units", "integer"),    # eval/corpus/tuning/f6b53f5f6901.pdf
    # -- must NOT become an integer field: numeric-ID-style labels stay free
    #    text, since they may carry formatting (dashes, leading zeros) --
    ("Social Insurance Number", None),
    ("Landlord Phone #", None),
    ("Account Number", None),
    ("Bank Number", None),
    ("Transit Number", None),
    ("Phone Number of applicant, printed", None),          # eval/corpus/tuning/hard_00005.pdf
    # -- ordinary text, unaffected --
    ("First Name(s)", None),
    ("Gender", None),
]


@pytest.fixture(scope="module")
def classify():
    """Run classifyFieldKind() for every CASES label in one Node process and
    return {label: kind_or_None}."""
    js = _extract_classifier_js()
    labels = [label for label, _ in CASES]
    driver = js + "\nconsole.log(JSON.stringify(" + json.dumps(labels) + \
        ".map(function(label){ return classifyFieldKind({type: 'text', label: label}).kind; })));"
    result = subprocess.run(["node", "-e", driver], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, f"node failed:\n{result.stderr}"
    kinds = json.loads(result.stdout)
    return dict(zip(labels, kinds))


def test_classifier_block_markers_present():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert _START in html and _END in html, \
        "FIELD_KIND_CLASSIFIER_START/END markers missing from demo/index.html"


@pytest.mark.parametrize("label,expected", CASES)
def test_field_kind_classification(classify, label, expected):
    assert classify[label] == expected, \
        f"{label!r} classified as {classify[label]!r}, expected {expected!r}"
