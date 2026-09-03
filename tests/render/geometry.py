"""PDF-point-rect -> pixel-crop math, shared by both render engines.

Cropping is engine-agnostic: it only needs the rendered page image, the
page height in PDF points, the render scale, and the field's rect. Keeping
it in one place means the pdfium and pdf.js golden tests crop identically,
so a mismatch between their two goldens reflects a real rendering
difference between the engines, not a difference in how the test cropped.
"""
from __future__ import annotations

from PIL import Image


def crop_rect(
    img: Image.Image,
    page_height_pt: float,
    rect: tuple[float, float, float, float],
    scale: float,
    pad_pt: float = 4.0,
) -> Image.Image:
    """Crop the pixel region corresponding to a [x0,y0,x1,y1] PDF-point rect.

    `rect` uses engine.detect's convention: PDF points, origin bottom-left.
    Image pixel space has origin top-left, so the y axis is flipped here.
    `pad_pt` adds a small margin so the crop shows the field's printed guide
    box (drawn at the same rect on the flat page) around the widget, which is
    what makes "did the checkbox land on its box" and "was the text clipped"
    into something a pixel diff can actually catch.
    """
    x0, y0, x1, y1 = rect
    x0, y0, x1, y1 = x0 - pad_pt, y0 - pad_pt, x1 + pad_pt, y1 + pad_pt
    left = max(0, round(x0 * scale))
    right = min(img.width, round(x1 * scale))
    top = max(0, round((page_height_pt - y1) * scale))
    bottom = min(img.height, round((page_height_pt - y0) * scale))
    return img.crop((left, top, right, bottom))
