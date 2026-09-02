"""Detection rules. THROWAWAY DEMO quality — the real one is track T1."""
import re

CHECK_GLYPHS = {"\uf063", "\uf06f"}          # Webdings box, Wingdings box
MASK_ONLY = set("()- $.")
SIGNATURE = re.compile(r"signatur", re.I)      # signature lines get no input box

# R9: a candidate whose area is largely covered by printed text is not a place
# to write -- it is on top of something already printed. Measured on the real
# corpus, 29% of emitted boxes sat on ink, which is the main precision failure.
# Glyphs a box is SUPPOSED to cover are excluded.
INK_EXEMPT = CHECK_GLYPHS | {"_", " ", "\xa0", ""}
INK_REJECT_AT = 0.25


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n] or "field"


def grid_cells(page):
    """Word draws table borders as thin filled rects, not lines. Rebuild the cells."""
    v = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    h = [r for r in page.rects if r["height"] < 3 and r["width"] >= 5]
    if len(v) + len(h) > 2000:                      # complexity guard from the spec
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


def _text_in(words, cell, pad=1):
    x0, top, x1, bot = cell
    return [w for w in words if w["x0"] >= x0 - pad and w["x1"] <= x1 + pad
            and w["top"] >= top - pad and w["bottom"] <= bot + pad]


def _ink_boxes(page):
    """Printed character boxes, in PDF points, origin bottom-left.

    Excludes glyphs a field is legitimately drawn over: checkbox glyphs and
    the underscore runs that form write-on lines.
    """
    H = page.height
    return [(c["x0"], H - c["bottom"], c["x1"], H - c["top"])
            for c in page.chars if c["text"] not in INK_EXEMPT and c["text"].strip()]


def _runs_past(rect, page_chars, exempt):
    """True when text covered by rect belongs to a line continuing outside it.

    This is the distinction that matters. A box sitting on a heading or a
    paragraph covers part of a line that carries on past the box edge. A box
    that legitimately contains its own short label covers text that ends inside
    it. Only the first is a false positive.
    """
    x0, y0, x1, y1 = rect
    H = _page_height(page_chars)
    inside_lines = set()
    for c in page_chars:
        if c["text"] in exempt or not c["text"].strip():
            continue
        cy0, cy1 = H - c["bottom"], H - c["top"]
        if c["x1"] > x0 and c["x0"] < x1 and cy1 > y0 and cy0 < y1:
            inside_lines.add(round(c["top"], 0))
    if not inside_lines:
        return False
    for c in page_chars:
        if c["text"] in exempt or not c["text"].strip():
            continue
        if round(c["top"], 0) not in inside_lines:
            continue
        # a character on the same line, clearly outside the box horizontally
        if c["x1"] < x0 - 2 or c["x0"] > x1 + 2:
            return True
    return False


def _page_height(page_chars):
    return _PAGE_H[0]


_PAGE_H = [792.0]


def _ink_fraction(rect, ink):
    x0, y0, x1, y1 = rect
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return 1.0
    covered = 0.0
    for a0, b0, a1, b1 in ink:
        w = min(x1, a1) - max(x0, a0)
        h = min(y1, b1) - max(y0, b0)
        if w > 0 and h > 0:
            covered += w * h
    return min(covered / area, 1.0)


def detect(page, pno):
    H, W = page.height, page.width
    words = page.extract_words()
    out = []

    # ---- R1  checkbox glyphs ------------------------------------------------
    for c in page.chars:
        if c["text"] in CHECK_GLYPHS:
            out.append({"page": pno, "type": "checkbox", "label": "", "rule": "R1",
                        "confidence": 0.99,
                        "rect": [c["x0"], H - c["bottom"], c["x0"] + 10, H - c["bottom"] + 10]})

    cells = grid_cells(page)
    claimed = set()

    # ---- R2  labelled cell: label in the top band, blank below --------------
    for cell in cells:
        x0, top, x1, bot = cell
        inside = _text_in(words, cell)
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
        if label.endswith(":") and (x1 - x0) > W * 0.8:           # R7
            continue
        claimed.add(cell)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R2",
                    "confidence": 0.8,
                    "rect": [x0 + 2, H - bot + 1.5, x1 - 2, H - entry_top - 1.5]})

    # ---- R3  empty cell, name inherited from the column header above --------
    cols = {}
    for cell in cells:
        cols.setdefault(round(cell[0]), []).append(cell)
    for _, group in cols.items():
        group.sort(key=lambda c: c[1])
        header = None
        for cell in group:
            txt = _text_in(words, cell)
            if txt:
                label = " ".join(w["text"] for w in sorted(txt, key=lambda w: w["x0"]))
                if 2 <= len(label) <= 60:
                    header = label
                continue
            x0, top, x1, bot = cell
            if header is None or cell in claimed:
                continue
            if not (11 <= bot - top <= 70) or (x1 - x0) < 30:
                continue
            claimed.add(cell)
            out.append({"page": pno, "type": "text", "label": header, "rule": "R3",
                        "confidence": 0.6,
                        "rect": [x0 + 2, H - bot + 2, x1 - 2, H - top - 2]})

    # ---- R4  cell holding only a mask, e.g. "(   ) -" or "$" ---------------
    for cell in cells:
        if cell in claimed:
            continue
        inside = _text_in(words, cell)
        if not inside:
            continue
        txt = "".join(w["text"] for w in inside)
        if not txt or not set(txt) <= MASK_ONLY:
            continue
        x0, top, x1, bot = cell
        claimed.add(cell)
        out.append({"page": pno, "type": "text", "label": txt.strip() or "value",
                    "rule": "R4", "confidence": 0.55,
                    "rect": [x0 + 2, H - bot + 2, x1 - 2, H - top - 2]})

    # ---- R5  runs of underscores are write-on lines -------------------------
    runs, cur = [], []
    for c in sorted((c for c in page.chars if c["text"] == "_"),
                    key=lambda c: (round(c["top"]), c["x0"])):
        if cur and abs(c["top"] - cur[-1]["top"]) < 1.5 and c["x0"] - cur[-1]["x1"] < 2:
            cur.append(c)
        else:
            if cur:
                runs.append(cur)
            cur = [c]
    if cur:
        runs.append(cur)
    for run in runs:
        x0, x1 = run[0]["x0"], run[-1]["x1"]
        if x1 - x0 < 25:
            continue
        base, top = run[0]["bottom"], run[0]["top"]
        before = [w for w in words
                  if abs(w["bottom"] - base) < 6 and w["x1"] <= x0 + 2 and w["x1"] > x0 - 240]
        label = " ".join(w["text"] for w in sorted(before, key=lambda w: w["x0"])[-6:]) or "line"
        out.append({"page": pno, "type": "text", "label": label[:60], "rule": "R5",
                    "confidence": 0.65,
                    "rect": [x0 + 1, H - base + 1, x1 - 1, H - top + 11]})

    # ---- R5b  write-on line drawn as a thin rect, not underscore characters --
    # A cell border has vertical rules standing at its endpoints. A write-on line
    # has none. That single test separates them cleanly on this corpus.
    vrules = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    for r in page.rects:
        if r["height"] >= 3 or not (40 <= r["width"] <= W * 0.77):
            continue
        if any(abs(x["x0"] - r["x0"]) < 3 or abs(x["x0"] - r["x1"]) < 3
               for x in vrules
               if x["top"] <= r["top"] + 3 and x["bottom"] >= r["top"] - 3):
            continue
        left = [w for w in words if abs(w["bottom"] - r["top"]) < 9
                and w["x1"] <= r["x0"] + 3 and w["x1"] > r["x0"] - 260]
        under = [w for w in words if 0 < w["top"] - r["top"] < 14
                 and w["x1"] > r["x0"] - 2 and w["x0"] < r["x1"] + 2]
        src = sorted(left, key=lambda w: w["x0"])[-7:] or sorted(under, key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in src).strip()
        if not label:                      # unlabelled rules are decorative
            continue
        out.append({"page": pno, "type": "text", "label": label[:60], "rule": "R5b",
                    "confidence": 0.6,
                    "rect": [r["x0"] + 1, H - r["top"] + 1, r["x1"] - 1, H - r["top"] + 15]})

    # ---- R6  small empty square cell drawn with rules, not a glyph ----------
    for cell in cells:
        if cell in claimed:
            continue
        x0, top, x1, bot = cell
        w_, h_ = x1 - x0, bot - top
        if 20 <= w_ <= 34 and 14 <= h_ <= 34 and abs(w_ - h_) < 14 and not _text_in(words, cell):
            claimed.add(cell)
            out.append({"page": pno, "type": "checkbox", "label": "", "rule": "R6",
                        "confidence": 0.5,
                        "rect": [x0 + 2, H - bot + 2, x0 + 14, H - bot + 14]})

    # A signature must be signed, not typed. Drop those boxes rather than invite
    # someone to type a name into them.
    out = [f for f in out if not SIGNATURE.search(f["label"])]

    # R9: drop text candidates sitting on top of printed text. Checkboxes are
    # exempt -- they are small and deliberately placed on a glyph.
    _PAGE_H[0] = H
    ink = _ink_boxes(page)
    chars = page.chars
    if ink:
        keep = []
        for f in out:
            if f["type"] == "checkbox":
                keep.append(f); continue
            if _ink_fraction(f["rect"], ink) <= INK_REJECT_AT:
                keep.append(f); continue
            if not _runs_past(f["rect"], chars, INK_EXEMPT):
                keep.append(f)          # contained label, not running text
        out = keep
    return out
