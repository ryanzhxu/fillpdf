"""Label-free quality guards for detector output.

These guards need no answer key. They catch failures a human can see at a
glance -- a box drawn on top of printed text, a checkbox glyph nobody found
a box for, a field too small to write in. See eval/contracts/README.md and
eval/contracts/scores.schema.json (the "guards" object).

Coordinates throughout are PDF points, origin bottom-left, matching
eval/contracts/fields.schema.json. pdfplumber's char/rect "top"/"bottom" are
measured from the TOP of the page, so every conversion here does:
    y0 = page.height - char["bottom"]
    y1 = page.height - char["top"]
"""
import pdfplumber

# Webdings/Wingdings checkbox glyphs the detector's R1 rule looks for.
CHECK_GLYPHS = {"", ""}

# Characters a detected box is *supposed* to sit on top of, so they don't
# count as "ink" for box_over_ink.
INK_EXCLUDE = CHECK_GLYPHS | {"_"}


def _is_ink(char):
    if char["text"] in INK_EXCLUDE:
        return False
    if char["text"].isspace():
        return False
    return True


def _char_rect(page, char):
    """Convert a pdfplumber char to a (x0, y0, x1, y1) PDF-points rect."""
    return (char["x0"], page.height - char["bottom"], char["x1"], page.height - char["top"])


def _overlap_area(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    return iw * ih


def _iou(a, b):
    inter = _overlap_area(a, b)
    if inter <= 0:
        return 0.0
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def box_over_ink(pdf_path, fields):
    """Fraction of detected boxes that overlap printed glyphs.

    A box that sits on top of real text (not a checkbox glyph, not an
    underscore, not whitespace) is almost certainly a false positive.
    LOWER IS BETTER.
    """
    with pdfplumber.open(str(pdf_path)) as pdf:
        ink_by_page = {}
        for i, page in enumerate(pdf.pages, 1):
            ink_by_page[i] = [_char_rect(page, c) for c in page.chars if _is_ink(c)]

    offenders = []
    over_threshold = 0
    for f in fields:
        x0, y0, x1, y1 = f["rect"]
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area <= 0:
            continue
        covered = sum(_overlap_area(f["rect"], cr) for cr in ink_by_page.get(f["page"], []))
        coverage = min(covered / area, 1.0)
        if coverage > 0.10:
            over_threshold += 1
            offenders.append({"id": f["id"], "page": f["page"], "coverage": round(coverage, 4)})

    offenders.sort(key=lambda o: -o["coverage"])
    return {
        "fraction": over_threshold / len(fields) if fields else 0.0,
        "offenders": offenders,
    }


def glyph_coverage(pdf_path, fields):
    """Fraction of Webdings/Wingdings checkbox glyphs matched by a detected
    checkbox box (IoU > 0.3). A checkbox glyph IS a checkbox -- this is free,
    label-free recall ground truth. HIGHER IS BETTER.
    """
    checkbox_rects_by_page = {}
    for f in fields:
        if f["type"] == "checkbox":
            checkbox_rects_by_page.setdefault(f["page"], []).append(tuple(f["rect"]))

    total = 0
    covered = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            boxes = checkbox_rects_by_page.get(i, [])
            for c in page.chars:
                if c["text"] not in CHECK_GLYPHS:
                    continue
                total += 1
                grect = _char_rect(page, c)
                if any(_iou(grect, box) > 0.3 for box in boxes):
                    covered += 1

    return {
        "fraction": covered / total if total else 1.0,
        "total_glyphs": total,
        "covered_glyphs": covered,
    }


def whitespace_fit(pdf_path, fields):
    """A field a human can write in must have room.

    too_small: fraction of text/multiline boxes with height < 8pt or
    width < 15pt. stacked: fraction of text/multiline boxes that overlap
    another detected box by IoU > 0.3. LOWER IS BETTER for both.
    """
    text_fields = [f for f in fields if f["type"] in ("text", "multiline")]
    if not text_fields:
        return {"too_small_fraction": 0.0, "stacked_fraction": 0.0}

    too_small = 0
    stacked = 0
    for f in text_fields:
        x0, y0, x1, y1 = f["rect"]
        if (y1 - y0) < 8 or (x1 - x0) < 15:
            too_small += 1
        for g in fields:
            if g is f or g["page"] != f["page"]:
                continue
            if _iou(tuple(f["rect"]), tuple(g["rect"])) > 0.3:
                stacked += 1
                break

    n = len(text_fields)
    return {"too_small_fraction": too_small / n, "stacked_fraction": stacked / n}


def guards(pdf_path, fields):
    """Run all label-free guards. Returns the "guards" object for scores.json."""
    boi = box_over_ink(pdf_path, fields)
    gc = glyph_coverage(pdf_path, fields)
    wf = whitespace_fit(pdf_path, fields)
    return {
        "box_over_ink": boi["fraction"],
        "box_over_ink_offenders": boi["offenders"],
        "glyph_coverage": gc["fraction"],
        "whitespace_fit": wf,
    }
