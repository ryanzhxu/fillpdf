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


def _merged_answer_rows(h):
    """Rows whose top border is inherited column segmentation, not real columns.

    Measured on the Microsoft(R)/Publisher forms (the real/Microsoft(R) family,
    f1 0.26 against real/Adobe's 0.74): a wide comment/answer row sits directly
    below a genuine multi-column header, and Word draws that row's OWN top
    border by literally reusing the header row's bottom border -- so it comes
    out as the same N segments, even though the answer row itself has no
    vertical dividers and its own bottom border is one continuous bar. Every
    per-hr cell below then inherits a header column's width instead of the
    row's true full span, producing a near-miss box a few pixels off vertically
    but hundreds of points too narrow horizontally.

    A genuine multi-column table has matching segmentation on BOTH the top
    and bottom border of a row (checked on the real/Adobe forms, where this
    shape does not occur), so the tell is specific: >1 segment on top,
    exactly 1 segment on bottom, and that one bottom segment spans the full
    width the top segments together cover. Returns {row_top: (lo, hi, bot_top)}
    for rows matching that shape, so the caller can emit one wide cell for
    the whole row instead of one per inherited segment.
    """
    rows = {}
    for r in h:
        rows.setdefault(r["top"], []).append(r)
    row_tops = sorted(rows)
    merged = {}
    for t in row_tops:
        segs = rows[t]
        if len(segs) < 2:
            continue
        below_tops = [bt for bt in row_tops if bt > t + 4]
        if not below_tops:
            continue
        bt = min(below_tops)
        below_segs = rows[bt]
        if len(below_segs) != 1:
            continue
        lo = min(s["x0"] for s in segs)
        hi = max(s["x1"] for s in segs)
        b = below_segs[0]
        if b["x0"] <= lo + 3 and b["x1"] >= hi - 3:
            merged[t] = (lo, hi, bt)
    return merged


def grid_cells(page):
    """Word draws table borders as thin filled rects, not lines. Rebuild the cells."""
    v = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    h = [r for r in page.rects if r["height"] < 3 and r["width"] >= 5]
    h = _merge_ruling_lines(h)
    if len(v) + len(h) > 2000:                      # complexity guard from the spec
        return []
    merged_rows = _merged_answer_rows(h)
    cells = []
    merged_done = set()
    for hr in h:
        if hr["top"] in merged_rows:
            lo, hi, bt = merged_rows[hr["top"]]
            key = (hr["top"], bt)
            if key in merged_done:
                continue
            merged_done.add(key)
            if hi - lo > 20 and bt - hr["top"] > 14:
                cells.append((round(lo, 1), hr["top"], round(hi, 1), bt))
            continue
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


# R1 label: a checkbox's option text sits right next to its glyph, usually to
# the right ("[x] Yes"), sometimes to the left ("Yes [x]"). Measured on this
# corpus, the gap from a glyph to its own label word, and the gap between two
# words already inside one label, never exceeds ~9pt -- it is ordinary word
# spacing. A gap past that is where unrelated trailing text starts (e.g. an
# inline "...[ ] No  If yes, please describe..." -- "No" must not swallow the
# instruction that follows it), so it is where the scan stops.
#
# When the line also carries a question before the first checkbox on it
# ("Are you a U.S. citizen? [ ] Yes [ ] No"), that question is the part that
# actually says what the box means -- "Yes" alone, ticked on some unlabeled
# box, tells nobody anything. The label becomes "question (option)". Line
# grouping is intentionally tight (not just "roughly the same height"): a
# same-size header sitting a few points off the true baseline (e.g. a
# "FOR OFFICE USE ONLY" box floating beside the real question) must not be
# swept in as if it were more of the question.
CHECK_LABEL_GAP_MAX = 11        # points; see comment above
CHECK_LABEL_LINE_TOL = 2        # points; vertical tolerance for "same line"
CHECK_LABEL_WRAP_GAP = 14       # points; how far below a line its wrapped
                                 # continuation (no glyph of its own) may start
CHECK_LABEL_WRAP_MAX = 3        # cap on wrapped continuation lines folded in

# The gap between a question and the row of checkboxes answering it is a
# deliberate visual break, wider than ordinary word spacing -- measured
# 5.6-45.8pt on the real corpus, a fixed 18pt on the synthetic one. Only the
# single connection from the question's last word to the first glyph gets
# this wider allowance; every earlier word-to-word step within the question
# still uses CHECK_LABEL_GAP_MAX, so a second, unrelated block of text
# further back on the same line is not swept in with it.
CHECK_LABEL_QUESTION_GAP_MAX = 60


def _checkbox_label(c, words, page_width, max_len=90):
    """Read a checkbox's label off the page, or "" if none is found.

    The option text is bounded by the next checkbox glyph on the same line,
    so two boxes on one line ("[x] Yes   [x] No") get "Yes" and "No", never
    both "Yes No" -- and by a gap bigger than normal word spacing, so
    trailing text past the true label is left out. It is read from the RIGHT
    of the glyph (the usual layout) if present, else from the LEFT. A short
    continuation line directly below, carrying no glyph of its own, is
    folded in as wrapped option text.

    If the line also carries a question before its first checkbox, the
    result is "question (option)"; otherwise it is the option text alone.
    """
    cy1 = c["bottom"]
    cmid = (c["top"] + cy1) / 2
    cx0, cx1 = c["x0"], c["x1"]

    line = sorted((w for w in words if abs(((w["top"] + w["bottom"]) / 2) - cmid)
                   < CHECK_LABEL_LINE_TOL), key=lambda w: w["x0"])
    glyphs = [w for w in line if w["text"] in CHECK_GLYPHS]
    text_line = [w for w in line if w["text"] not in CHECK_GLYPHS]

    def scan(cands, x_lo, x_hi, cur_bottom):
        picked, prev_edge = [], x_lo
        for w in sorted((w for w in cands if w["x0"] >= x_lo - 1 and w["x1"] <= x_hi + 1),
                         key=lambda w: w["x0"]):
            if w["x0"] - prev_edge > CHECK_LABEL_GAP_MAX:
                break
            picked.append(w)
            prev_edge = w["x1"]
        bottom = cur_bottom
        for _ in range(CHECK_LABEL_WRAP_MAX):
            band = [w for w in words if bottom - 1 <= w["top"] <= bottom + CHECK_LABEL_WRAP_GAP]
            if not band:
                break
            nxt_top = min(w["top"] for w in band)
            nxt_line = sorted((w for w in band if abs(w["top"] - nxt_top) < 2),
                               key=lambda w: w["x0"])
            window = [w for w in nxt_line if w["x0"] >= x_lo - 1 and w["x1"] <= x_hi + 1]
            # A fresh option row has its own glyph -- check the whole candidate
            # line for one, not just this option's window: that glyph usually
            # starts a new row's left margin, which can sit just outside the
            # window (to the left of x_lo) rather than inside it.
            if not window or any(w["text"] in CHECK_GLYPHS for w in nxt_line):
                break                    # a fresh option row, not a continuation
            # A genuine wrap starts fresh: nothing before it on this line, or a
            # real gap (a column break) before it. A tight gap means the window
            # is just where an unrelated, wider line (e.g. a different, later
            # question that has not reached its own checkbox yet) happens to
            # cross our x-range -- that is the middle of someone else's
            # sentence, not a continuation of ours.
            before = [w for w in nxt_line if w["x1"] <= window[0]["x0"] + 1]
            if before and window[0]["x0"] - max(w["x1"] for w in before) <= CHECK_LABEL_GAP_MAX:
                break
            wrapped, prev_edge = [], x_lo
            for w in window:
                if w["x0"] - prev_edge > CHECK_LABEL_GAP_MAX:
                    break
                wrapped.append(w)
                prev_edge = w["x1"]
            if not wrapped:
                break
            picked += wrapped
            bottom = max(w["bottom"] for w in nxt_line)
        return picked

    right_bound = min((g["x0"] for g in glyphs if g["x0"] > cx1), default=page_width)
    right = scan(text_line, cx1, right_bound, cy1)
    if right:
        option = " ".join(w["text"] for w in right)
    else:
        left_bound = max((g["x1"] for g in glyphs if g["x1"] < cx0), default=0)
        picked, prev_edge = [], cx0
        for w in sorted((w for w in text_line if w["x1"] <= cx0 + 1 and w["x1"] >= left_bound - 1),
                         key=lambda w: -w["x0"]):
            if prev_edge - w["x1"] > CHECK_LABEL_GAP_MAX:
                break
            picked.append(w)
            prev_edge = w["x0"]
        picked.reverse()
        option = " ".join(w["text"] for w in picked)

    if not option:
        return ""

    # The question, if any, is whatever precedes the FIRST checkbox on the
    # line -- shared by every option on that line ("Do you: (Rent)",
    # "Do you: (Own)", ...). Skip it when the word right against that first
    # glyph is itself an option word: that means this line uses the LEFT
    # layout ("Yes [x]  No [ ]") and there is no real question to recover.
    first_glyph_x0 = min((g["x0"] for g in glyphs), default=cx0)
    picked, prev_edge = [], first_glyph_x0
    gap_max = CHECK_LABEL_QUESTION_GAP_MAX
    for w in sorted((w for w in text_line if w["x1"] <= first_glyph_x0 + 1),
                     key=lambda w: -w["x0"]):
        if prev_edge - w["x1"] > gap_max:
            break
        picked.append(w)
        prev_edge = w["x0"]
        gap_max = CHECK_LABEL_GAP_MAX      # later hops: ordinary word spacing only
    picked.reverse()
    if picked and picked[-1]["text"].strip().lower() not in ("yes", "no"):
        question = " ".join(w["text"] for w in picked)
        tail = f" ({option})"
        if len(question) + len(tail) <= max_len:
            return question + tail
        # Truncate the question, not the option: "(Yes)"/"(No)" cut off looks
        # like nothing was found at all, which is worse than a shorter question.
        keep = max_len - len(tail)
        return (question[:keep].rstrip() + tail) if keep > 0 else option[:max_len]
    return option[:max_len]


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


ROW_EPS = 0.5           # points; same-row tolerance, matches R10's row_eps
ROW_SPAN_MAX_CELLS = 10  # safety cap on how many blank cells one span absorbs


def _looks_like_checkbox_cell(cell):
    """True when a cell's own shape is R6's small-square checkbox footprint.

    Used only to keep row-span extension from swallowing a checkbox cell
    that happens to sit blank and unclaimed to the right of a text field --
    R6 claims cells like this on its own later in detect().
    """
    x0, top, x1, bot = cell
    w_, h_ = x1 - x0, bot - top
    return 20 <= w_ <= 34 and 14 <= h_ <= 34 and abs(w_ - h_) < 14


def _extend_row_span(cell, cells, claimed, words, vrules):
    """Extend a claimed cell rightward through blank cells in the same row.

    Word draws a table row's top border as one rect PER underlying grid
    column, not one rect for the whole row. So a row can be split into more
    grid_cells() cells than the form actually shows dividers for: a label
    cell and the blank cell(s) beside it are visually one continuous white
    strip, with no drawn vertical rule between them, and a person filling
    the form writes straight across the whole strip -- not just the first
    narrow slice of it. Measured on the tuning corpus, this was the cause of
    every near-miss the detector produced: the claimed cell matched truth's
    left edge and top/bottom almost exactly, but stopped short of truth's
    right edge by tens to hundreds of points.

    Stop absorbing at the first real division: a vertical rule spanning the
    row, a cell that carries its own text (a new field, not blank space), a
    cell already claimed by another rule, or a cell shaped like a checkbox
    (left for R6). Returns the new right edge and the list of absorbed cells
    (the caller marks them claimed so no other rule reuses that space).
    """
    x0, top, x1, bot = cell
    absorbed = []
    while len(absorbed) < ROW_SPAN_MAX_CELLS:
        nxt = [c for c in cells if c not in claimed and c not in absorbed
               and abs(c[0] - x1) <= ROW_EPS
               and abs(c[1] - top) <= ROW_EPS and abs(c[3] - bot) <= ROW_EPS]
        if not nxt:
            break
        nxt_cell = min(nxt, key=lambda c: c[0])
        if _text_in(words, nxt_cell) or _looks_like_checkbox_cell(nxt_cell):
            break
        if any(abs(v["x0"] - x1) <= 2
               for v in vrules
               if v["top"] <= top + 3 and v["bottom"] >= bot - 3):
            break                       # a real rule marks a true division
        absorbed.append(nxt_cell)
        x1 = nxt_cell[2]
    return x1, absorbed


# R12: a large blank cell whose only content is the instruction introducing
# it -- "Describe the reason(s) why you are requesting possession of the unit
# or site:" sits at the top of a grid cell drawn hundreds of points tall, with
# the rest of the cell left blank for a multi-line answer. R2 already
# recognises exactly this shape (label band at the top, blank space below),
# but two of its guards were sized for short single-line fields and reject it
# here: R3's 70pt cap on a claimed cell, and R2's own "a label spanning more
# than 80% of the page width is a section header, not a field label" guard --
# every one of these full-width comment boxes trips that guard, because the
# instruction sentence is long enough to span most of the row.
#
# R12 lifts both, but only for a shape guarded tightly enough to still be
# safe at this size:
#   - the label may wrap onto up to R12_MAX_HEADER_LINES lines (a short
#     instruction wraps once or twice; unrelated running prose keeps going),
#   - nothing may follow those header lines anywhere else in the cell --
#     measured on the tuning corpus, this is what separates a real comment box
#     from a bordered paragraph of instructional prose: the prose fills most
#     of its box with text at every height, while a real comment box is
#     empty below its one instruction,
#   - the blank space left below the header must itself be large
#     (R12_MIN_BLANK_GAP) -- a short label with only a little room below it
#     is the shape R2 already owns, not this one,
#   - OFFICE_USE reuses R3's regex: an office-only comment box is off limits
#     to the applicant the same as an office-only column,
#   - a cell that already contains a finer ruled grid (_cell_is_container) is
#     a section wrapper, not one answer box -- left for whatever rule claims
#     the smaller cells inside it,
#   - width and height are bounded (R12_MIN_WIDTH_FRAC, R12_MAX_H) so neither
#     a narrow leftover slice nor a full-page frame can be claimed.
#
# Measured on the tuning corpus: 16 cells match this shape, all in the real/
# Microsoft(R) and real/Adobe families, and all 16 match truth.
R12_MIN_H = 70              # points; below this R2/R3 already own the cell
R12_MAX_H = 350             # points; a bound against a full-page frame
R12_MIN_WIDTH_FRAC = 0.55   # of page width; excludes narrow leftover slices
R12_WRAP_GAP = 20           # points; gap allowed between wrapped header lines
R12_MAX_HEADER_LINES = 3
R12_MAX_LABEL_LEN = 130
R12_MIN_BLANK_GAP = 40      # points; blank room required below the header


def _cell_is_container(cell, cells):
    """True when a finer grid already subdivides this cell.

    A large blank-looking cell that actually contains its own ruled sub-cells
    is a section wrapper, not one answer box -- whatever rule claims the
    smaller cells inside it should have this space, not R12.
    """
    x0, top, x1, bot = cell
    area = (x1 - x0) * (bot - top)
    for o in cells:
        if o == cell:
            continue
        ox0, otop, ox1, obot = o
        if (ox0 >= x0 - 1 and ox1 <= x1 + 1 and otop >= top - 1 and obot <= bot + 1
                and (ox1 - ox0) * (obot - otop) < area):
            return True
    return False


def _multiline_label(cell, words):
    """The cell's own top instruction, or None if the shape is not R12's.

    Returns (label, entry_top): entry_top is the y just below the last header
    line, so the caller draws the answer box from there down and never over
    the label itself (the same convention R2 uses).
    """
    x0, top, x1, bot = cell
    inside = _text_in(words, cell)
    if not inside:
        return None
    line_tops = sorted(set(round(w["top"], 1) for w in inside))
    header_tops = [line_tops[0]]
    for t in line_tops[1:]:
        if t - header_tops[-1] <= R12_WRAP_GAP:
            header_tops.append(t)
        else:
            break
    if len(header_tops) > R12_MAX_HEADER_LINES:
        return None
    header = [w for w in inside if round(w["top"], 1) in header_tops]
    if len(header) != len(inside):
        return None                # text remains below the header -- not blank
    label = " ".join(w["text"] for ln in header_tops
                      for w in sorted((w for w in header if round(w["top"], 1) == ln),
                                       key=lambda w: w["x0"]))
    if not (2 <= len(label) <= R12_MAX_LABEL_LEN) or OFFICE_USE.search(label):
        return None
    entry_top = max(w["bottom"] for w in header) + 1
    if bot - entry_top < R12_MIN_BLANK_GAP:
        return None
    return label, entry_top


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
            label = _checkbox_label(c, words, W)
            out.append({"page": pno, "type": "checkbox", "label": label, "rule": "R1",
                        "confidence": 0.99,
                        "rect": [c["x0"], H - c["bottom"], c["x1"], H - c["top"]]})

    cells = grid_cells(page)
    claimed = set()
    vrules = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]

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
        ext_x1, absorbed = _extend_row_span(cell, cells, claimed, words, vrules)
        claimed.add(cell)
        claimed.update(absorbed)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R2",
                    "confidence": 0.8,
                    "rect": [x0 + 2, H - bot + 1.5, ext_x1 - 2, H - entry_top - 1.5]})

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
            ext_x1, absorbed = _extend_row_span(cell, cells, claimed, words, vrules)
            claimed.add(cell)
            claimed.update(absorbed)
            out.append({"page": pno, "type": "text", "label": header, "rule": "R3",
                        "confidence": 0.6,
                        "rect": [x0 + 2, H - bot + 2, ext_x1 - 2, H - top - 2]})

    # ---- R12  large blank cell, labelled by its own top instruction ---------
    for cell in cells:
        if cell in claimed:
            continue
        x0, top, x1, bot = cell
        if not (R12_MIN_H <= bot - top <= R12_MAX_H) or (x1 - x0) < R12_MIN_WIDTH_FRAC * W:
            continue
        if _cell_is_container(cell, cells):
            continue
        found = _multiline_label(cell, words)
        if found is None:
            continue
        label, entry_top = found
        ext_x1, absorbed = _extend_row_span(cell, cells, claimed, words, vrules)
        claimed.add(cell)
        claimed.update(absorbed)
        out.append({"page": pno, "type": "multiline", "label": label, "rule": "R12",
                    "confidence": 0.55,
                    "rect": [x0 + 2, H - bot + 2, ext_x1 - 2, H - entry_top - 2]})

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
    # someone to type a name into them. Checkboxes are exempt: before this
    # change their label was always "", so this filter could never touch one,
    # and R1 now giving them real text should not open that up as a side effect.
    out = [f for f in out if f["type"] == "checkbox" or not SIGNATURE.search(f["label"])]

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
