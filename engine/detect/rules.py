"""Detection rules. THROWAWAY DEMO quality — the real one is track T1."""
import re

CHECK_GLYPHS = {"\uf063", "\uf06f"}          # Webdings box, Wingdings box
MASK_ONLY = set("()- $.")
SIGNATURE = re.compile(r"signatur", re.I)      # signature lines get no input box

# R3: a column header that marks the cells below as off-limits to the
# applicant. Measured on the tuning corpus, every single "Office Use"-headed
# column R3 claimed was a false positive (318 of them, 0 true positives) --
# the cleanest, largest source of R3's false positives. Wording varies, so
# match the phrase family rather than one exact string.
OFFICE_USE = re.compile(
    r"office\s*use|staff\s*use|internal\s*use|official\s*use|"
    r"do\s*not\s*write|leave\s*blank", re.I)

# R9: a candidate whose area is largely covered by printed text is not a place
# to write -- it is on top of something already printed. Measured on the real
# corpus, 29% of emitted boxes sat on ink, which is the main precision failure.
# Glyphs a box is SUPPOSED to cover are excluded.
INK_EXEMPT = CHECK_GLYPHS | {"_", " ", "\xa0", ""}
INK_REJECT_AT = 0.25


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n] or "field"


def _merge_ruling_lines(rects, gap_tol=3, span_tol=2):
    """Collapse a single ruled line drawn as two stacked rects into one.

    Measured on four line-item tables (repeated-row quantity/make-model/
    systems columns), grid_cells() was pairing up every row TWICE: a thick
    stroke rect (e.g. height 1.44) is immediately followed, edge to edge or
    within a point or two, by a hairline rect (height ~0.1) spanning the
    SAME x-range -- both pass the `height < 3` ruling-line filter, so each
    row boundary produced two candidate `hr` tops a point or two apart, and
    every row below got detected twice: once matching truth, once as an
    extra false positive (or, for a non-row boundary, two false positives
    with nothing to match).

    Only merge rects whose x-span also matches closely (`span_tol`): a
    header row's own top border and the short decorative underline below
    each of its column headers can sit just as close vertically, but they
    do not share the same width. Merging on gap alone (tried first) swallowed
    that header row into the first data row and cost 21 real matches on the
    tuning corpus. Requiring both the gap and the span to agree keeps the
    merge to true double-strokes of one line, not any two close rects.
    """
    merged = []
    for r in sorted(rects, key=lambda r: (r["top"], r["x0"])):
        joined = False
        for m in merged:
            if (r["top"] <= m["bottom"] + gap_tol and r["bottom"] >= m["top"] - gap_tol
                    and abs(r["x0"] - m["x0"]) <= span_tol
                    and abs(r["x1"] - m["x1"]) <= span_tol):
                m["top"] = min(m["top"], r["top"])
                m["bottom"] = max(m["bottom"], r["bottom"])
                m["x0"] = min(m["x0"], r["x0"])
                m["x1"] = max(m["x1"], r["x1"])
                joined = True
                break
        if not joined:
            merged.append(dict(r))
    return merged


def grid_cells(page):
    """Word draws table borders as thin filled rects, not lines. Rebuild the cells."""
    v = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    h = [r for r in page.rects if r["height"] < 3 and r["width"] >= 5]
    h = _merge_ruling_lines(h)
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


R3_COL_TOL = 16    # points; see R3 column clustering below


def _cluster_columns(cells, tol=R3_COL_TOL):
    """Group grid cells into columns by proximity of the left edge.

    Real tables are ragged: a column's left edge drifts by a few points row
    to row, so an exact match on x0 scatters one logical column into many
    single-cell groups and no header is ever found above them. Cluster
    instead: a cell joins the first existing column whose running-mean x0
    is within `tol` points, else it starts a new column.

    Tried and rejected: also requiring the right edge (x1) to agree within
    `tol`. It did not reduce mislabeling (measured via a label-accuracy
    diagnostic outside the official gate, which does not check label text)
    and it cost recall -- a legitimate ragged column's x1 does not always
    drift in lock-step with its x0, so the extra constraint just splits
    real columns into more single-cell groups again.
    """
    groups = []
    for cell in sorted(cells, key=lambda c: c[0]):
        x0 = cell[0]
        match = None
        for g in groups:
            if abs(x0 - g["x0"]) <= tol:
                match = g
                break
        if match is None:
            groups.append({"x0": x0, "n": 1, "cells": [cell]})
        else:
            match["n"] += 1
            match["x0"] += (x0 - match["x0"]) / match["n"]
            match["cells"].append(cell)
    return [g["cells"] for g in groups]


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
            # Use the glyph's real bounding box. A fixed 10x10 rect only worked
            # because every checkbox glyph on safer.pdf happens to be 10pt; it
            # misplaces on any other size.
            out.append({"page": pno, "type": "checkbox", "label": "", "rule": "R1",
                        "confidence": 0.99,
                        "rect": [c["x0"], H - c["bottom"], c["x1"], H - c["top"]]})

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
    for group in _cluster_columns(cells):
        group.sort(key=lambda c: c[1])
        header = None
        for cell in group:
            txt = _text_in(words, cell)
            if txt:
                label = " ".join(w["text"] for w in sorted(txt, key=lambda w: w["x0"]))
                if 2 <= len(label) <= 60:
                    # An "office use" header marks its column off-limits: no
                    # blank cell below it should be claimed as a field until
                    # a real header appears further down.
                    header = None if OFFICE_USE.search(label) else label
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

    # ---- R10  blank cell whose LEFT neighbour in the same row is a label ----
    # "Last Name |          |" -- the label sits in the cell to the left of the
    # entry cell instead of inside it. Grid cells sharing a row band come from
    # the same pair of horizontal rules, so top/bot match exactly (row_eps
    # only guards float noise). A blank cell to the right of some text is a
    # common shape that is NOT a field -- table gutters, "TOTAL" summary rows
    # that reuse a data column's width, sentence fragments split across table
    # cells -- so this rule is deliberately narrow: short label, same-row
    # height capped to what real single/short-wrapped labels measured at on
    # this corpus, and a hard reject on a label that starts mid-sentence
    # (lowercase first letter) or is itself a Yes/No option marker.
    row_eps = 0.5
    for cell in cells:
        if cell in claimed:
            continue
        x0, top, x1, bot = cell
        if bot - top > 65 or _text_in(words, cell):
            continue
        left_cands = [o for o in cells if o[2] <= x0 + row_eps
                      and abs(o[1] - top) <= row_eps and abs(o[3] - bot) <= row_eps]
        if not left_cands:
            continue
        left = max(left_cands, key=lambda o: o[2])
        if left in claimed:
            continue          # already its own field (R2) -- don't reuse the label
        lx0, ltop, lx1, lbot = left
        linside = _text_in(words, left)
        if not linside:
            continue
        lines = {}
        for w in linside:
            lines.setdefault(round(w["top"] / 3), []).append(w)
        label = " ".join(" ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
                          for _, ws in sorted(lines.items()))
        if not (1 <= len(label) <= 60) or (x1 - x0) < 25:
            continue
        if label[0].islower() or label.strip().lower() in ("yes", "no"):
            continue          # a sentence fragment split across cells, or an option marker
        claimed.add(cell)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R10",
                    "confidence": 0.55,
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

    # ---- R11  runs of dot-leader characters are write-on lines --------------
    # Same idea as R5 (underscores) but for "Name . . . . . ." / "Amount ....."
    # leaders. Two specific false-positive traps, both handled below:
    #   - a prose ellipsis ("...") is sentence punctuation, not a leader. A
    #     width-only threshold (R5's 25pt) is not enough to rule it out on its
    #     own -- a large enough font could clear 25pt with 3 dots -- so also
    #     require a minimum dot COUNT, set high enough that no ordinary
    #     ellipsis (3, or a sloppy 4-5) reaches it.
    #   - a dotted line used as a table divider/border, not a fillable line.
    #     Two cheap tells: it runs flush against a cell's vertical rule (the
    #     same test R5b uses to reject a rect-drawn border), and it has no
    #     label sitting on its baseline -- a real leader is always introduced
    #     by a label, a decorative divider is not. Unlike R5, R11 does NOT
    #     fall back to a generic "line" label; an unlabelled run is dropped.
    # Geometry differs from R5 on purpose: an underscore is written ABOVE the
    # rule, but a dot leader is written ON the dots, so the box straddles the
    # run's baseline instead of sitting entirely above it.
    LEADER_CHARS = {".", "·", "․"}   # period, middle dot, one-dot-leader
    LEADER_MIN_DOTS = 6
    runs, cur, dots = [], [], 0
    for c in sorted((c for c in page.chars if c["text"] in LEADER_CHARS or c["text"] == " "),
                    key=lambda c: (round(c["top"]), c["x0"])):
        is_dot = c["text"] in LEADER_CHARS
        if cur and abs(c["top"] - cur[-1]["top"]) < 1.5 and c["x0"] - cur[-1]["x1"] < 8:
            cur.append(c)
            dots += is_dot
        else:
            if cur and dots >= LEADER_MIN_DOTS:
                runs.append(cur)
            cur = [c]
            dots = 1 if is_dot else 0
    if cur and dots >= LEADER_MIN_DOTS:
        runs.append(cur)
    for run in runs:
        x0, x1 = run[0]["x0"], run[-1]["x1"]
        if x1 - x0 < 25:
            continue
        base, top = run[0]["bottom"], run[0]["top"]
        if any(abs(x["x0"] - x0) < 3 or abs(x["x0"] - x1) < 3
               for x in vrules
               if x["top"] <= top + 3 and x["bottom"] >= top - 3):
            continue                       # flush against a cell border -- a divider, not a leader
        before = [w for w in words
                  if abs(w["bottom"] - base) < 6 and w["x1"] <= x0 + 2 and w["x1"] > x0 - 240]
        if not before:
            continue                       # nothing on this baseline to its left -- decorative
        label = " ".join(w["text"] for w in sorted(before, key=lambda w: w["x0"])[-6:])
        out.append({"page": pno, "type": "text", "label": label[:60], "rule": "R11",
                    "confidence": 0.6,
                    "rect": [x0 + 1, H - base - 2, x1 - 1, H - top + 9]})

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
