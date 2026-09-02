"""Acceptance tests for eval/synth/generate.py. Run with the project venv:
    .venv/bin/python -m pytest eval/synth/test_generate.py -v
"""
import json

import pdfplumber
import pytest
from jsonschema import validate

from eval.synth.generate import generate

SCHEMA = json.load(open("eval/contracts/truth.schema.json"))


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
    p1, t1 = generate(42, tmp_path / "a")
    p2, t2 = generate(42, tmp_path / "b")
    assert p1.read_bytes() == p2.read_bytes()
    assert t1.read_bytes() == t2.read_bytes()


def test_truth_validates_against_schema(tmp_path):
    for seed in range(10):
        _, truth_path = generate(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        validate(truth, SCHEMA)


def test_word_like_structure_present(tmp_path):
    """Reopen 20 generated PDFs and confirm the claimed structures are really
    there - do not just trust reportlab."""
    total_vrects = total_hrects = total_glyphs = 0
    for seed in range(20):
        pdf_path, truth_path = generate(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        with pdfplumber.open(pdf_path) as pdf:
            vrects = hrects = glyphs = 0
            for page in pdf.pages:
                vrects += len([r for r in page.rects if r["width"] < 3 and r["height"] >= 5])
                hrects += len([r for r in page.rects if r["height"] < 3 and r["width"] >= 5])
                glyphs += len([c for c in page.chars if c["text"] in ("", "")])
            total_vrects += vrects
            total_hrects += hrects
            total_glyphs += glyphs
            n_checkbox_truth = sum(1 for w in truth["widgets"] if w["type"] == "checkbox")
            # every truth checkbox must correspond to a real glyph in the PDF
            assert glyphs >= n_checkbox_truth, (seed, glyphs, n_checkbox_truth)
    assert total_vrects > 0
    assert total_hrects > 0
    assert total_glyphs > 0


def test_widget_rects_inside_page_and_positive_area(tmp_path):
    for seed in range(15):
        _, truth_path = generate(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        page_dims = {p["page"]: (p["width"], p["height"]) for p in truth["pages"]}
        for w in truth["widgets"]:
            x0, y0, x1, y1 = w["rect"]
            pw, ph = page_dims[w["page"]]
            assert x1 > x0 and y1 > y0, w
            assert 0 <= x0 and x1 <= pw, w
            assert 0 <= y0 and y1 <= ph, w


def test_no_overlapping_widgets(tmp_path):
    for seed in range(15):
        _, truth_path = generate(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        by_page = {}
        for w in truth["widgets"]:
            by_page.setdefault(w["page"], []).append(w)
        for page, widgets in by_page.items():
            for i in range(len(widgets)):
                for j in range(i + 1, len(widgets)):
                    iou = _iou(widgets[i]["rect"], widgets[j]["rect"])
                    assert iou <= 0.1, (page, widgets[i], widgets[j], iou)


def test_signature_lines_produce_no_widget(tmp_path):
    """Signature lines must never appear as a truth widget with label
    'Signature', regardless of which section drew them."""
    for seed in range(30):
        _, truth_path = generate(seed, tmp_path)
        truth = json.loads(truth_path.read_text())
        for w in truth["widgets"]:
            assert w.get("label") != "Signature", (seed, w)
