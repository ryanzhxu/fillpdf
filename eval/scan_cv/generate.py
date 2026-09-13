# eval/scan_cv/generate.py
"""Generates the golden corpus for engine/scan_cv's shared CV pipeline.

Run once, at dev time, whenever a fixture needs to change:
    .venv/bin/python -m eval.scan_cv.generate

Output is committed to eval/scan_cv/golden/ -- these are small, deterministic
PNGs, not the large real-scan corpus (which stays gitignored under
eval/corpus/). Both this project's Python implementation (sub-project 2b)
and a future JS/OpenCV.js implementation (sub-project 3) run their own
detection against these same static files and must match the recorded
expected output within a defined tolerance -- that comparison is what
actually verifies "matching quality" between the two, rather than shared
prose alone. See docs/superpowers/specs/2026-09-12-scan-cv-algorithm-design.md.
"""
import json
from pathlib import Path

import cv2
import numpy as np

DPI = 300
SCALE = DPI / 72.0
PAGE_W_PT, PAGE_H_PT = 200.0, 100.0
W, H = round(PAGE_W_PT * SCALE), round(PAGE_H_PT * SCALE)

OUT = Path(__file__).parent / "golden"


def _blank_page():
    return np.full((H, W, 3), 255, dtype=np.uint8)


def _draw_line(img, x0_pt, x1_pt, y_pt, thickness=2):
    cv2.line(img, (round(x0_pt * SCALE), round(y_pt * SCALE)),
              (round(x1_pt * SCALE), round(y_pt * SCALE)), (0, 0, 0), thickness)


def _draw_square(img, x0_pt, y0_pt, x1_pt, y1_pt, thickness=2):
    cv2.rectangle(img, (round(x0_pt * SCALE), round(y0_pt * SCALE)),
                  (round(x1_pt * SCALE), round(y1_pt * SCALE)), (0, 0, 0), thickness)


LINES_PT = [(10.0, 190.0, y) for y in (15.0, 35.0, 55.0, 75.0)]
CHECKBOX_PT = (10.0, 50.0, 30.0, 70.0)


def _lines_rects():
    return [
        {"x0": x0, "x1": x1, "top": y - 1, "bottom": y + 1,
         "width": x1 - x0, "height": 2.0, "fill": False, "stroke": True}
        for x0, x1, y in LINES_PT
    ]


def _checkbox_rects():
    x0, y0, x1, y1 = CHECKBOX_PT
    return [{"x0": x0, "x1": x1, "top": y0, "bottom": y1,
             "width": x1 - x0, "height": y1 - y0, "fill": True, "stroke": False}]


def generate():
    OUT.mkdir(parents=True, exist_ok=True)

    img1 = _blank_page()
    for x0, x1, y in LINES_PT:
        _draw_line(img1, x0, x1, y)
    cv2.imwrite(str(OUT / "fixture_lines.png"), img1)
    (OUT / "fixture_lines.json").write_text(
        json.dumps({"dpi": DPI, "rects": _lines_rects()}, indent=2))

    img2 = _blank_page()
    _draw_square(img2, *CHECKBOX_PT)
    cv2.imwrite(str(OUT / "fixture_checkbox.png"), img2)
    (OUT / "fixture_checkbox.json").write_text(
        json.dumps({"dpi": DPI, "rects": _checkbox_rects()}, indent=2))

    img3 = _blank_page()
    for x0, x1, y in LINES_PT:
        _draw_line(img3, x0, x1, y)
    _draw_square(img3, *CHECKBOX_PT)
    M = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), 3.0, 1.0)
    img3 = cv2.warpAffine(img3, M, (W, H), borderValue=(255, 255, 255))
    cv2.imwrite(str(OUT / "fixture_rotated.png"), img3)
    (OUT / "fixture_rotated.json").write_text(json.dumps({
        "dpi": DPI, "true_skew_deg": 3.0,
        "rects": _lines_rects() + _checkbox_rects(),
    }, indent=2))


if __name__ == "__main__":
    generate()
    print(f"wrote golden corpus to {OUT}")
