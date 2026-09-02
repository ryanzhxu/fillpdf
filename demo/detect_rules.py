"""Detection rules. THROWAWAY DEMO quality — the real one is track T1."""
import re

CHECK_GLYPHS = {"\uf063", "\uf06f"}          # Webdings box, Wingdings box
MASK_ONLY = set("()- $.")


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
    return out
