#!/usr/bin/env python3
"""THROWAWAY DEMO. Feel-test for FormFill, not the foundation.

Usage:  python demo.py <any.pdf>
Runs detection, renders pages, writes ./out/, serves it, opens a browser.
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

HERE = Path(__file__).parent
OUT = HERE / "out"
CHECK_GLYPHS = {"", ""}   # Webdings box, Wingdings box
SCALE = 2


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n] or "field"


def grid_cells(page):
    """Word draws table borders as thin filled rects, not lines. Rebuild cells."""
    v = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    h = [r for r in page.rects if r["height"] < 3 and r["width"] >= 5]
    if len(v) + len(h) > 2000:       # complexity guard from the spec
        return []
    cells = []
    for hr in h:
        below = [b for b in h if b["top"] > hr["top"] + 4
                 and b["x0"] < hr["x1"] - 4 and b["x1"] > hr["x0"] + 4]
        if not below:
            continue
        bot = min(below, key=lambda b: b["top"])
        vs = sorted({round(x["x0"], 1) for x in v
                     if x["top"] <= hr["top"] + 3 and x["bottom"] >= bot["top"] - 3
                     and hr["x0"] - 2 <= x["x0"] <= hr["x1"] + 2})
        edges = sorted(set([round(hr["x0"], 1), round(hr["x1"], 1)] + vs))
        for a, b in zip(edges, edges[1:]):
            if b - a > 20 and bot["top"] - hr["top"] > 14:
                cells.append((a, hr["top"], b, bot["top"]))
    return sorted(set(cells))


def detect(page, pno):
    """Rules R1 (checkbox glyphs) and R2 (labelled table cells). R3-R7 are the build."""
    H = page.height
    found = []

    for c in page.chars:                                   # R1
        if c["text"] in CHECK_GLYPHS:
            found.append({"page": pno, "type": "checkbox", "label": "",
                          "rule": "R1", "confidence": 0.99,
                          "rect": [c["x0"], H - c["bottom"],
                                   c["x0"] + 10, H - c["bottom"] + 10]})

    words = page.extract_words()
    for (x0, top, x1, bot) in grid_cells(page):            # R2
        inside = [w for w in words if w["x0"] >= x0 - 1 and w["x1"] <= x1 + 1
                  and w["top"] >= top - 1 and w["bottom"] <= bot + 1]
        if not inside:
            continue
        first = min(w["top"] for w in inside)
        header = [w for w in inside if w["top"] < first + 6]
        if [w for w in inside if w["top"] >= first + 6]:
            continue
        label = " ".join(w["text"] for w in sorted(header, key=lambda w: w["x0"]))
        entry_top = max(w["bottom"] for w in header) + 1
        if bot - entry_top < 11 or not (2 <= len(label) <= 60):
            continue
        if label.endswith(":") and (x1 - x0) > page.width * 0.8:   # R7
            continue
        found.append({"page": pno, "type": "text", "label": label,
                      "rule": "R2", "confidence": 0.8,
                      "rect": [x0 + 2, H - bot + 1.5, x1 - 2, H - entry_top - 1.5]})
    return found


def build(pdf_path: Path):
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copy(pdf_path, OUT / "source.pdf")
    shutil.copy(HERE / "index.html", OUT / "index.html")

    fields, seen, pages = [], {}, []
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) > 30:
            sys.exit("demo caps at 30 pages")
        for i, pg in enumerate(pdf.pages, 1):
            pages.append({"page": i, "width": pg.width, "height": pg.height})
            fields += detect(pg, i)

    for f in fields:
        base = f"p{f['page']}_" + (slug(f["label"]) if f["type"] == "text" else "chk")
        seen[base] = seen.get(base, 0) + 1
        f["id"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
        f["origin"] = "detected"

    doc = pdfium.PdfDocument(str(pdf_path))
    for i in range(len(doc)):
        doc[i].render(scale=SCALE).to_pil().save(OUT / f"page_{i+1}.png")

    json.dump({"version": 1, "source": {"pages": len(pages)},
               "pages": pages, "fields": fields},
              open(OUT / "fields.json", "w"), indent=1)

    n_txt = sum(1 for f in fields if f["type"] == "text")
    print(f"detected {len(fields)} fields  ({n_txt} text, {len(fields)-n_txt} checkbox) "
          f"across {len(pages)} pages")
    return len(fields)


def serve():
    os.chdir(OUT)
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}/index.html"
        print(f"\n  {url}\n  ctrl-c to stop\n")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("stopped")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python demo.py <any.pdf>")
    src = Path(sys.argv[1]).expanduser()
    if not src.exists():
        sys.exit(f"no such file: {src}")
    build(src)
    serve()
