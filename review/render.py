"""Render a page with detected boxes drawn, for Ryan's async review.

Never blocks the loop. Writes PNGs plus an index.md naming what changed and
what to look for.
"""
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCALE = 2


def render(pdf_path, fields, page_no, dest, title=""):
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[page_no - 1]
    img = page.render(scale=SCALE).to_pil().convert("RGB")
    H = page.get_height()
    d = ImageDraw.Draw(img, "RGBA")
    for f in fields:
        if f["page"] != page_no:
            continue
        x0, y0, x1, y1 = f["rect"]
        box = [x0 * SCALE, (H - y1) * SCALE, x1 * SCALE, (H - y0) * SCALE]
        colour = (21, 128, 61, 60) if f["type"] == "checkbox" else (47, 111, 208, 55)
        d.rectangle(box, fill=colour, outline=colour[:3] + (220,), width=2)
    if title:
        d.rectangle([0, 0, img.width, 34], fill=(255, 255, 255, 235))
        d.text((10, 10), title, fill=(20, 20, 20))
    img.save(dest)
    return dest


def side_by_side(before, after, dest):
    a, b = Image.open(before), Image.open(after)
    h = max(a.height, b.height)
    out = Image.new("RGB", (a.width + b.width + 24, h), (246, 246, 244))
    out.paste(a, (0, 0)); out.paste(b, (a.width + 24, 0))
    out.save(dest)
    return dest
