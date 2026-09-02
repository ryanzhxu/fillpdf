"""Acceptance tests for eval/synth/hard.py. Run with the project venv:
    .venv/bin/python -m pytest eval/synth/test_hard.py -v

Test 5 is the whole point of this module: the current, unmodified detector
must score f1 <= 0.85 on a 25-form hard corpus. If a future change to
hard.py raises that number back up, this test is the tripwire.
"""
import json

import pdfplumber
import pytest
from jsonschema import validate

from eval.synth.hard import generate_hard
from eval.score import score_one

SCHEMA = json.load(open("eval/contracts/truth.schema.json"))
MAX_ALLOWED_F1 = 0.85
FAIRNESS_MARGIN = 40  # points; matches the brief's "within 40pt" fairness bar


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    a_area = (ax1 - ax0) * (ay1 - ay0)
    b_area = (bx1 - bx0) * (by1 - by0)
    return inter / (a_area + b_area - inter)


def test_deterministic(tmp_path):
    p1, t1 = generate_hard(42, tmp_path / "a")
    p2, t2 = generate_hard(42, tmp_path / "b")
    assert p1.read_bytes() == p2.read_bytes()
    assert t1.read_bytes() == t2.read_bytes()


def test_truth_validates_against_schema(tmp_path):
    for seed in range(10):
        _, truth_path = generate_hard(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        validate(truth, SCHEMA)


def test_widget_rects_sane(tmp_path):
    """Inside its page, positive area, and big enough for a human to
    actually write in or tap: >=8pt tall, >=15pt wide."""
    for seed in range(15):
        _, truth_path = generate_hard(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        page_dims = {p["page"]: (p["width"], p["height"]) for p in truth["pages"]}
        for w in truth["widgets"]:
            x0, y0, x1, y1 = w["rect"]
            pw, ph = page_dims[w["page"]]
            assert x1 > x0 and y1 > y0, w
            assert 0 <= x0 and x1 <= pw, w
            assert 0 <= y0 and y1 <= ph, w
            assert (y1 - y0) >= 8, w
            assert (x1 - x0) >= 15, w


def test_no_overlapping_widgets(tmp_path):
    for seed in range(15):
        _, truth_path = generate_hard(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        by_page = {}
        for w in truth["widgets"]:
            by_page.setdefault(w["page"], []).append(w)
        for page, widgets in by_page.items():
            for i in range(len(widgets)):
                for j in range(i + 1, len(widgets)):
                    iou = _iou(widgets[i]["rect"], widgets[j]["rect"])
                    assert iou <= 0.1, (page, widgets[i], widgets[j], iou)


def test_current_detector_scores_at_or_below_threshold(tmp_path):
    """THE MAIN ONE. Generate a 25-form hard corpus and score it with the
    real, unmodified detector via eval.score.score_one. The corpus-level
    (pooled) precision/recall/f1 must show f1 <= 0.85 -- if it does not,
    hard.py is not hard enough yet."""
    tot_truth = tot_detected = tot_matched = 0
    for seed in range(25):
        pdf_path, truth_path = generate_hard(seed, tmp_path)
        m = score_one(pdf_path, truth_path)
        tot_truth += m["truth"]
        tot_detected += m["detected"]
        tot_matched += m["matched"]

    precision = (tot_matched / tot_detected) if tot_detected else 0.0
    recall = (tot_matched / tot_truth) if tot_truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    print(f"\nhard corpus: truth={tot_truth} detected={tot_detected} matched={tot_matched}")
    print(f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}")

    assert f1 <= MAX_ALLOWED_F1, (
        f"detector scores f1={f1:.3f} on the hard corpus (precision={precision:.3f}, "
        f"recall={recall:.3f}) -- not hard enough, must be <= {MAX_ALLOWED_F1}"
    )


def _page_structure_boxes(page):
    """(x0, top, x1, bottom) boxes, in pdfplumber's top-down coordinates,
    for every vector rect and every character on the page."""
    boxes = [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects]
    boxes += [(c["x0"], c["top"], c["x1"], c["bottom"]) for c in page.chars]
    return boxes


def _boxes_overlap(a, b):
    ax0, atop, ax1, abot = a
    bx0, btop, bx1, bbot = b
    return ax0 < bx1 and ax1 > bx0 and atop < bbot and abot > btop


def test_every_widget_has_supporting_structure(tmp_path):
    """Fairness check: nothing invisible or hallucinated. For every truth
    widget there must be some real page structure -- a vector rect, a
    character/glyph, or a text label -- within FAIRNESS_MARGIN points of
    the widget's own rect. Rects and chars both come from the same
    page.rects/page.chars pdfplumber already uses, so a label counts too
    (labels are made of chars)."""
    for seed in range(15):
        pdf_path, truth_path = generate_hard(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        page_dims = {p["page"]: p["height"] for p in truth["pages"]}
        by_page = {}
        for w in truth["widgets"]:
            by_page.setdefault(w["page"], []).append(w)

        with pdfplumber.open(pdf_path) as pdf:
            for pno, widgets in by_page.items():
                page = pdf.pages[pno - 1]
                H = page_dims[pno]
                structure = _page_structure_boxes(page)
                for w in widgets:
                    x0, y0, x1, y1 = w["rect"]
                    # convert bottom-left-origin truth rect to pdfplumber's
                    # top-down (top increases downward) coordinates
                    top, bottom = H - y1, H - y0
                    expanded = (x0 - FAIRNESS_MARGIN, top - FAIRNESS_MARGIN,
                                x1 + FAIRNESS_MARGIN, bottom + FAIRNESS_MARGIN)
                    assert any(_boxes_overlap(expanded, s) for s in structure), (
                        seed, pno, w, "no supporting rect/char within "
                        f"{FAIRNESS_MARGIN}pt"
                    )
