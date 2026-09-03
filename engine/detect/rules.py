"""Detection rules. THROWAWAY DEMO quality — the real one is track T1."""
import re

CHECK_GLYPHS = {"\uf063", "\uf06f"}          # Webdings box, Wingdings box
MASK_ONLY = set("()- $.")
SIGNATURE = re.compile(r"signatur", re.I)      # signature lines get no input box

# R2's largest false-positive group, measured on the tuning corpus: a "label"
# made up of no actual words at all -- a printed dollar amount ("$31,200"), a
# bare percentage, a blank-fill underscore run ("$_____________"), or a bare
# list index ("1.", "2.", "3."). None of these NAME a field; they are either
# already-printed data (a filled specimen table's own answer) or the visual
# blank line itself, sitting in a table row whose real entry lives in a
# sibling column, not in the space below THIS cell. A genuine field label
# always contains at least one word. Measured: 85 of 369 tuning-corpus R2
# false positives are letterless by this test, spread across all three real
# families (Adobe, Microsoft, unknown) -- and zero of R2's 594 true positives
# are, so this costs no matched recall.
HAS_LETTER = re.compile(r"[A-Za-z]")

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

# Label-building gutter guard, shared by every rule that reads a label off
# words sitting near a field rather than strictly inside one bounded cell
# (R2, R3, R5, R5b, R10). Measured on the real corpus (see
# eval/guards.py's label_plausibility "internal_gap" signal, which is what
# surfaced this): each of these rules collects every word within a fixed
# reach or count along a baseline, with no notion of when it has crossed
# the physical gap BETWEEN two column headers -- "Name*: Last Name*:" is
# two adjacent column headers glued together this way, not one label.
# LABEL_GAP_MULTIPLIER/FLOOR/CEIL mirror eval/guards.py's own constants of
# the same name exactly, so a label this detector builds and the guard that
# checks it agree on what "too wide" means.
LABEL_GAP_MULTIPLIER = 3.0   # normal-gap multiplier for the cut threshold
LABEL_GAP_FLOOR = 10.0       # points; never more sensitive than this
LABEL_GAP_CEIL = 30.0        # points; never less sensitive than this
LABEL_LINE_TOL = 2.5         # points; same-line clustering tolerance


def _page_gap_threshold(words):
    """The page's own typical inter-word gap, turned into a cut threshold.

    Real forms vary in font size and word spacing, so a fixed threshold
    either misses cramped forms or fires on loosely-set ones -- this scales
    with the page's own median gap between words on the same line, floored
    and ceilinged so a page with almost no prose still gets a sane value.
    """
    lines = []
    for w in sorted(words, key=lambda w: w["top"]):
        for ln in lines:
            if abs(ln["top"] - w["top"]) <= LABEL_LINE_TOL:
                ln["words"].append(w)
                break
        else:
            lines.append({"top": w["top"], "words": [w]})
    gaps = []
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        for a, b in zip(ws, ws[1:]):
            g = b["x0"] - a["x1"]
            if g > 0.1:
                gaps.append(g)
    gaps.sort()
    n = len(gaps)
    if n == 0:
        med = 3.0
    else:
        mid = n // 2
        med = gaps[mid] if n % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0
    return min(max(med * LABEL_GAP_MULTIPLIER, LABEL_GAP_FLOOR), LABEL_GAP_CEIL)


def _cut_at_gutter(words, gap_threshold, keep):
    """Trim a left-to-right (ascending x0) word list at the first gap wider
    than `gap_threshold`, keeping only the run adjacent to the field.

    `keep="first"` is for a label whose field starts at the label's own
    left edge (R2/R3's header band, R5b's below-the-line caption) -- the
    leftmost run is the one aligned with where the field begins.
    `keep="last"` is for a label read backward from the field it
    introduces (R5's write-on line, R5b's left-of-line caption, R10's
    left-neighbour cell) -- the run nearest the entry, at the right end,
    wins.
    """
    # A run consisting only of mask characters ("$", "#", "(", ")", "-")
    # names nothing on its own -- measured on the real corpus, "Amount of
    # rent increase: $" would otherwise cut to the bare "$" the moment a
    # currency symbol's own wider-than-word spacing crossed gap_threshold,
    # losing the actual label. So the cut does not stop at the first wide
    # gap until the run it has kept so far contains at least one letter;
    # once it does, a later wide gap still stops it exactly as before.
    def has_letter(ws):
        return any(HAS_LETTER.search(w["text"]) for w in ws)

    if not words:
        return words
    if keep == "last":
        rev = list(reversed(words))
        out = [rev[0]]
        for w in rev[1:]:
            if out[0]["x0"] - w["x1"] > gap_threshold and has_letter(out):
                break
            out.insert(0, w)
        return out
    out = [words[0]]
    for w in words[1:]:
        if w["x0"] - out[-1]["x1"] > gap_threshold and has_letter(out):
            break
        out.append(w)
    return out


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


# A genuine ruling/write-on line is thin (height < 3) by construction. A
# thin rect that is instead the TOP or BOTTOM edge of a large filled,
# unstroked panel -- same x-range, touching it -- COULD be the border of a
# shaded structural block (an instruction panel, a section band) rather
# than a real table/write-on rule. But height and fill alone do not tell
# the two apart: a legitimate freeform answer box (R3's own documented
# "please describe" case, measured at 183pt tall on a holdout form) is
# built the exact same way -- a tall, unstroked, filled rect with a thin
# rule at its edge -- and is meant to be READ AS BLANK, ready for the
# applicant's own writing. The tell that actually separates them is
# content: a real answer box is empty (or carries one short caption); a
# decorative panel is already full of the FORM'S OWN printed prose,
# multiple sentences deep. Measured on safer.pdf's two page-1 panels: 6 and
# 19 distinct text lines already printed inside them. PANEL_MIN_LINES sits
# well clear of a single caption line while remaining well below either.
PANEL_MIN_H = 45
PANEL_MIN_LINES = 3
PANEL_X_TOL = 2
PANEL_Y_TOL = 1.5


def _prose_panels(rects, words, min_h=PANEL_MIN_H, min_lines=PANEL_MIN_LINES):
    """Large unstroked fill rects whose interior is already dense running
    prose (see the module comment above) -- structural panels, not blank
    answer boxes."""
    out = []
    for f in rects:
        if not (f.get("fill") and not f.get("stroke") and f["height"] > min_h):
            continue
        inside = [w for w in words
                  if w["x0"] >= f["x0"] - 2 and w["x1"] <= f["x1"] + 2
                  and w["top"] >= f["top"] - 1 and w["bottom"] <= f["bottom"] + 1]
        if len({round(w["top"]) for w in inside}) >= min_lines:
            out.append(f)
    return out


def _borders_shaded_panel(rect, panel_fills, vrules, x_tol=PANEL_X_TOL, y_tol=PANEL_Y_TOL):
    # A real ruled table's row divider stands on real vertical rules at its
    # endpoints (this is R5b's own cell-border tell, reused here in the
    # other direction). safer.pdf's page-1 panel has none anywhere on the
    # page. Measured on a genuine "real/Adobe" form with a repeated,
    # shaded, multi-line answer cell (one even carries its own printed
    # "Example: ..." sentence) that would otherwise match on fill height
    # and prose alone: every one of its row dividers stands on a real
    # vertical, so this guard clears it and the field survives.
    if any((abs(v["x0"] - rect["x0"]) < 3 or abs(v["x0"] - rect["x1"]) < 3)
           and v["top"] <= rect["top"] + 3 and v["bottom"] >= rect["top"] - 3
           for v in vrules):
        return False
    return any(abs(f["x0"] - rect["x0"]) <= x_tol and abs(f["x1"] - rect["x1"]) <= x_tol
               and (abs(f["top"] - rect["bottom"]) <= y_tol
                    or abs(f["bottom"] - rect["top"]) <= y_tol)
               for f in panel_fills)


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
    # A thin rect bordering a large shaded PROSE panel (see _prose_panels
    # above) is a panel edge, not a table rule -- drop it before it can be
    # paired into a manufactured cell. Confirmed on safer.pdf's page-1
    # instruction panel: a decorative top-border hairline, paired by the
    # loop below with the next unrelated rule further down the page, was
    # producing a spurious cell across the panel's own blank margin. Scoped
    # to prose fills specifically (not just "large and filled") so a
    # genuine large blank/lightly-captioned answer box keeps its cell, and
    # further scoped to rules with no real vertical-rule support (see
    # _borders_shaded_panel) so a genuine gridded table -- verticals and
    # all -- is never touched even if one of its shaded cells is prose-dense.
    panel_fills = _prose_panels(page.rects, page.extract_words())
    if panel_fills:
        h = [r for r in h if not _borders_shaded_panel(r, panel_fills, v)]
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

# R3 table-break guard: _cluster_columns groups cells page-wide by left edge
# alone (see above), so a column-cluster can span TWO unrelated things that
# merely start at the same x0 -- a real table, and, further down the page,
# some other cell that happens to share its left margin (a heading's
# underline row, a different table's column, a comment box). Walking that
# cluster top to bottom, R3 must stop inheriting a header once the table it
# belongs to has actually ended, or it hands that header to a cell that has
# nothing to do with it.
#
# Neither signal alone is safe, and each covers the other's blind spot:
#
# A row-height heuristic (a candidate row's height compared against the
# column's own established pitch) catches both known false positives -- the
# "Name of Sponsor" and "Landlord Name" break rows measure 1.50x and 0.65x
# their column's real rows. But height alone also flags a real, genuinely
# ruled row that is legitimately larger than its neighbours: measured on a
# holdout Alberta form, a truth-matched freeform "please describe" box runs
# 183pt in a column whose other rows run 50-60pt (3.5x) -- rejecting purely
# on height there deletes a real field, and no ratio threshold separates
# that case from the two real breaks (its deviation is larger, not smaller).
#
# A vertical-ruling check (does a real vertical rule span this row's full
# height on both edges?) directly targets the actual mechanism: grid_cells()
# builds a row by pairing a horizontal ruling line with the CLOSEST one
# below it that overlaps in x; when a table's real last row has no further
# ruling below it, that pairing reaches past the table onto some unrelated
# rule, and no vertical spans the manufactured row because the table's own
# verticals stopped at its true end. This correctly clears the holdout
# form's tall freeform row (it IS ruled on both edges, same as its
# neighbours) and correctly flags both safer.pdf breaks (neither is ruled).
# But used alone it also fires on ordinary rows in *other* tables where a
# row's vertical happens to fall a point or two outside the matching
# window -- real fields the height check would have left alone, since nothing
# about their height looked wrong.
#
# So: require BOTH. A row only ends the table when its height breaks the
# column's own established pitch AND it lacks the vertical support its
# column's other rows have shown. A height anomaly with real ruling behind
# it is a genuine oversized/undersized row, not a break; ruling noise on a
# row of ordinary height is not treated as a break either, since only the
# height check can trigger the question in the first place.
R3_ROW_PITCH_TOL = 1.4     # ratio; see the two-agreeing-rows note below
R3_VRULE_TOL_X = 2         # points; x-position match, mirrors grid_cells'
                           # own vertical-rule window (see grid_cells above)
R3_VRULE_TOL_Y = 3         # points; top/bottom coverage slack, mirrors
                           # grid_cells' own hr/bot pairing tolerance

# A single leading row is not enough to establish a column's pitch: the
# first row a header inherits can itself be a stray member of the same
# page-wide x0 cluster (measured on a second holdout form: a narrow row-
# number box sits directly above eight genuine full-width answer rows, and
# its height has nothing to do with theirs). So the pitch is only
# ESTABLISHED once two consecutive accepted rows agree with each other
# (within R3_ROW_PITCH_TOL); before that, a disagreement is not judged as a
# break -- there is no baseline yet to break from -- and the row is simply
# accepted, becoming the new thing the next row is compared to.


def _row_has_vertical_support(cell, vrules):
    """True if a real vertical rule spans this cell's full height on BOTH
    its left and right edge -- see the R3 table-break guard above."""
    x0, top, x1, bot = cell
    def edge(x):
        return any(abs(v["x0"] - x) <= R3_VRULE_TOL_X
                   and v["top"] <= top + R3_VRULE_TOL_Y
                   and v["bottom"] >= bot - R3_VRULE_TOL_Y
                   for v in vrules)
    return edge(x0) and edge(x1)


def _next_cell_top(cell, cells, tol):
    """Top of the nearest OTHER grid cell below `cell` sharing roughly the
    same x-range -- used by R16 to judge whether a caption sitting between
    two stacked cells is closer to this one or to the next (see R16 below)."""
    x0, top, x1, bot = cell
    below = [c for c in cells if c != cell and c[1] >= bot
             and abs(c[0] - x0) <= tol and abs(c[2] - x1) <= tol]
    return min((c[1] for c in below), default=None)


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


# R3c: a continuation of an R3 table across a page break. The header row
# prints once, on the page where the table starts; the continuation on the
# next page repeats the column edges but no header, usually with a
# "(continued)" note. detect() is called one page at a time, so the caller
# (engine/detect/__init__.py) threads the previous page's still-active R3
# column headers in as `carry_in` and gets this page's back out to hand to
# the next.
#
# Tried and rejected: trusting column geometry alone (matching x0/x1 within
# R3_COL_TOL, same column count and order, first row within
# CARRY_TOP_MARGIN of the page top) with no cue requirement. On a
# multi-table form that reuses a standard column layout across unrelated
# tables (common -- many forms are built from the same 2/3-equal-column
# template), that geometry can coincidentally match the START of a new,
# unrelated table sitting near the top of the next page, and R3c would then
# hand it someone else's header -- a confidently wrong field name, worse
# than leaving the row undetected. Requiring the cue costs recall on real
# forms that omit it, but it is the difference between "carried" and
# "guessed", and label_accuracy is gated on exactly this kind of mistake.
CARRY_TOP_MARGIN = 120   # points; how close to the page top a continuation
                         # table's first row must start
CARRY_CUE_ZONE = 160     # points; where a "(continued)" cue is looked for
CARRY_HEADER_ROW_TOL = 2 # points; same-row tolerance for the multi-column
                         # corroboration check in detect()'s R3 section
CONTINUED_CUE = re.compile(r"\bcontinu(?:ed|ation)\b", re.I)


def _match_carried_columns(groups, carry_in, words):
    """Match this page's blank, near-top column bands against the header
    columns carried from the previous page. All-or-nothing: every carried
    column must find a same-position, same-order match on this page, and
    the page must carry a "(continued)"-style cue near its top. Returns
    {id(group): spec} (the matching carry_in entry) for the groups that
    qualify -- see R3c above.
    """
    if not carry_in:
        return {}
    if not any(CONTINUED_CUE.search(w["text"]) for w in words if w["top"] <= CARRY_CUE_ZONE):
        return {}
    candidates = []
    for g in groups:
        top = min(c[1] for c in g)
        if top > CARRY_TOP_MARGIN:
            continue
        topmost = min(g, key=lambda c: c[1])
        if _text_in(words, topmost):
            continue                      # this band already prints its own header
        candidates.append((g, topmost))
    if len(candidates) != len(carry_in):
        return {}
    matches = {}
    for (g, cell), spec in zip(candidates, carry_in):
        if abs(cell[0] - spec["x0"]) > R3_COL_TOL or abs(cell[2] - spec["x1"]) > R3_COL_TOL:
            return {}
        matches[id(g)] = spec
    return matches


def _text_in(words, cell, pad=1):
    x0, top, x1, bot = cell
    return [w for w in words if w["x0"] >= x0 - pad and w["x1"] <= x1 + pad
            and w["top"] >= top - pad and w["bottom"] <= bot + pad]


# R3 group header: a wide, undivided row (a single grid_cells() cell) sitting
# directly above a row that IS divided into sub-columns, e.g.
#
#     |        Sponsored Immigrants Only        |
#     |   Name of Sponsor   |   End Date         |
#
# _cluster_columns groups purely by a cell's own x0, so the wide cell's x0
# only ever matches the LEFTMOST sub-column's cluster -- every other
# sub-column's own group never sees it at all. When the row directly below
# is itself blank (the group header is the ONLY header text, Word simply
# does not draw a divider through it), that leaves every non-leftmost
# sub-column with no header to inherit and the leftmost stuck with the
# whole row's text glued together ("Item Quantity Amount" instead of
# "Item"). Measured on the tuning corpus (hard_*.pdf), this exact shape
# repeats often: a one-line "Item / Quantity / Amount" (or similar) title
# prints as one wide cell, directly above blank per-column entry rows.
#
# Fix: read the wide row's own words against the X-RANGES of the divided
# row below it, splitting its text into one label per sub-column -- this
# reconstructs "Item", "Quantity", "Amount" individually. Truth on this
# corpus (hard_00013.json etc.) confirms the expected label is the bare
# per-column text, e.g. "Item", "Street Number and Name" -- never the wide
# row's text as a qualifying prefix -- so no group-name qualification is
# added.
#
# Deliberately narrow, to avoid two traps seen in this same corpus:
#   - a wide row that is a section TITLE or a Yes/No question, not a group
#     header (hard_00015.pdf: "Do you receive benefits? [ ] Yes [ ] No"
#     sits directly above an unrelated "Marital Status | Middle Initial |
#     Name" row purely by page-layout coincidence). Any checkbox glyph in
#     the wide row's text rules this out outright.
#   - a dot-leader caption that happens to run the full row width (hard_
#     00017.pdf: "Social Security Number: ....................." sits
#     above an unrelated "Item | Quantity | Amount" row). Splitting it by
#     column still lands SOME character in every column (the leader dots
#     span the whole width) but none of those slices contain a letter or
#     digit, so requiring each split label to have one rules it out.
#   - a sub-column that already prints its OWN header text (the genuine
#     two-level case, e.g. safer.pdf's "Name of Sponsor" / "End Date of
#     Sponsorship Agreement") is left alone entirely -- that text is more
#     specific than anything split out of the row above it, and R3 already
#     picks it up on its own via each column's own header-setting cell.
#   - the wide row being an R12-sized full answer area, or an outer table
#     border: both run much taller than an ordinary header band, so a
#     height cap excludes them.
#   - a wide row spanning the table's FULL width, with no other columns
#     beside it, is ambiguous: it could be this nested-group shape, or it
#     could just as easily be an ordinary one-level table whose header
#     merely lacks a ruled divider -- a case this corpus's hard_* forms
#     use heavily on purpose to keep the detector honest (a pytest
#     tripwire holds the hard corpus's f1 down for exactly this reason).
#     safer.pdf's own group header is nested INSIDE a wider row that also
#     carries other, differently-headed columns to its left ("Name",
#     "Current status in Canada", ...), so its sub-columns are narrower
#     than the columns of a table that fills the whole row by itself.
#     Capping how wide a sub-column may be keeps this rule aimed at that
#     nested shape.
GROUP_HEADER_MAX_H = 40      # points; header-band height cap (R12 starts at 70)
GROUP_HEADER_TOL = 3         # points; x-range match between the wide row and
                             # the divided row's combined span
GROUP_HEADER_MAX_SUBCOL_W = 150   # points; see "full width" trap above


def _group_header_splits(cells, words):
    """Split a wide header row's text across the sub-columns below it.

    Returns (wide_labels, seeds):
      wide_labels: {wide_cell: label} -- the LEFTMOST sub-column's share of
        the split text plus that sub-column's OWN (x0, x1) -- not the wide
        cell's full width -- keyed by the wide cell itself (it lives in the
        leftmost sub-column's own x0-cluster, so this is consulted in place
        of that cell's full, unsplit text).
      seeds: [(sub_cell, label, wide_row_top), ...] for every OTHER
        sub-column -- the caller seeds each one's own column-cluster with
        this label before that cluster's cells are walked, the same way a
        carried-in header is seeded (see _match_carried_columns).
        wide_row_top is carried along so the seed's header_top matches the
        wide row's own top exactly like the leftmost column's real one
        does, so sibling columns corroborate each other for R3c carry-out.
    """
    rows = {}
    for c in cells:
        rows.setdefault(c[1], []).append(c)
    wide_labels = {}
    seeds = []
    for wtop, row in rows.items():
        if len(row) != 1:
            continue
        wide = row[0]
        wx0, _, wx1, wbot = wide
        if wbot - wtop > GROUP_HEADER_MAX_H or wx1 - wx0 < 60:
            continue
        wide_txt = _text_in(words, wide)
        if not wide_txt or any(w["text"] in CHECK_GLYPHS for w in wide_txt):
            continue
        subrow = rows.get(wbot)
        if not subrow or len(subrow) < 2:
            continue
        subs = sorted(subrow, key=lambda c: c[0])
        if abs(subs[0][0] - wx0) > GROUP_HEADER_TOL or abs(subs[-1][2] - wx1) > GROUP_HEADER_TOL:
            continue
        if any((s[2] - s[0]) > GROUP_HEADER_MAX_SUBCOL_W for s in subs):
            continue
        if any(_text_in(words, s) for s in subs):
            continue          # a sub-column already prints its own header
        labels = []
        for sx0, _, sx1, _ in subs:
            in_col = [w for w in wide_txt
                      if sx0 - GROUP_HEADER_TOL <= (w["x0"] + w["x1"]) / 2 <= sx1 + GROUP_HEADER_TOL]
            labels.append(" ".join(w["text"] for w in sorted(in_col, key=lambda w: w["x0"])))
        if not all(re.search(r"[A-Za-z0-9]", label) for label in labels):
            continue
        wide_labels[wide] = (labels[0], subs[0][0], subs[0][2])
        seeds += [(sub_cell, label, wtop) for sub_cell, label in zip(subs[1:], labels[1:])]
    return wide_labels, seeds


# R3 floating header: a column heading that prints as ordinary page text,
# enclosed by no ruling at all, directly above a column whose first ruled
# row is blank. Measured in the missed-fields diagnosis: 185 widgets sit in
# a correctly-ruled, correctly-claimed column whose true header exists on
# the page as free text, but grid_cells() never turns it into a cell, so
# R3's per-cell scan (the `if txt:` branch in detect()) never sees it.
#
# Loose x-band matching is not safe on its own: diagnosed on
# 077cfa584877.pdf page 1, where the nearest above-and-aligned text for one
# column was unrelated address-line text from a different section that
# merely shared its x-position. Guards apply together, and the last one is
# the strongest:
#   - tight horizontal alignment to the column's OWN first-row cell, not
#     the page-wide cluster tolerance (FLOAT_COL_TOL, well under R3_COL_TOL)
#   - tight vertical proximity: the text must sit just above that row, not
#     merely higher up the page (FLOAT_GAP_MAX)
#   - heading shape: short and word-like, not a sentence (a length cap, a
#     word-count cap, and a ban on sentence-final punctuation)
#   - corroboration: a real header row supplies headers for several
#     ADJACENT columns from the SAME text line at once; a stray aligned
#     line, by construction, only ever matches one column. A candidate is
#     kept only when its immediate neighbor (left or right) also has a
#     candidate at essentially the same top -- a lone match is discarded,
#     full stop, regardless of how heading-like it looks.
#   - no bridging word: even two adjacent columns both getting a candidate
#     is not enough on its own. Measured on two further tuning forms
#     (741655e96b58.pdf, 8cc1430f0065.pdf): a single continuous title/
#     sentence ("Name of Person / Summons given to", "Requires Service of
#     Process Under...") can straddle a real column boundary, dropping a
#     mid-sentence word (the one that would not fit either column's tight
#     window) and leaving what LOOKS like two clean, corroborating headers
#     on either side of it. The tell is that dropped word: it sits, on the
#     same line, in the actual gap between the two cells themselves ("of
#     Process" spanning the gap in the Illinois example). A genuine two-
#     column header has nothing printed in that gap -- the whole point of
#     drawing two columns is that the two headings are separate phrases.
#     So a pair is only accepted as corroborating when no floating word on
#     their shared line overlaps the space between their two cells.
FLOAT_COL_TOL = 6      # points; tight column-alignment window, see above
FLOAT_GAP_MAX = 24     # points; header must sit just above its first row
FLOAT_ROW_TOL = 3      # points; "same text line" window for corroboration
FLOAT_MAX_WORDS = 6    # a heading is a few words, not a sentence


def _floating_headers(groups, cells, words):
    """Seed a header for a column whose own cells never carry text, read
    from free page text sitting just above its first row -- see R3 floating
    header above. `groups` must already be sorted so each group's cells are
    ordered top to bottom (as detect() does before calling this).

    Returns {id(group): {"label", "x0", "x1", "top"}} for the columns whose
    candidate survives the corroboration check.
    """
    in_cell = set()
    for c in cells:
        for w in _text_in(words, c):
            in_cell.add(id(w))
    floating = [w for w in words if id(w) not in in_cell]

    candidates = []
    for g in groups:
        top_cell = g[0]
        if _text_in(words, top_cell):
            continue                 # own cell already carries text
        x0, top, x1, _bot = top_cell
        band = [w for w in floating
                if w["bottom"] <= top and w["bottom"] >= top - FLOAT_GAP_MAX
                and w["x0"] >= x0 - FLOAT_COL_TOL and w["x1"] <= x1 + FLOAT_COL_TOL]
        if not band:
            continue
        line_top = max(w["top"] for w in band)     # nearest line above the row
        line = sorted((w for w in band if abs(w["top"] - line_top) <= FLOAT_ROW_TOL),
                      key=lambda w: w["x0"])
        if len(line) > FLOAT_MAX_WORDS:
            continue
        label = " ".join(w["text"] for w in line)
        if not (2 <= len(label) <= 40):
            continue
        if not HAS_LETTER.search(label) or OFFICE_USE.search(label):
            continue
        if label.rstrip()[-1:] in ".?":
            continue
        if "=" in label:
            # A column captioned with an equation ("Column F = Col. E -
            # Col. D") names a COMPUTED result, not a field the applicant
            # fills in by hand -- measured on 077cfa584877.pdf, the one
            # tuning form where this pattern occurs: the real header row
            # legitimately corroborates (Column D | Column E | the formula
            # column, all from one clean line, no bridging word), but truth
            # carries no widget at all under the formula column, so the
            # heading-shape guards above do not, on their own, keep this out.
            continue
        # x0/x1 here are the CELL's own edges (not the label's word span) --
        # bridged() below needs the actual ruled gap between columns.
        candidates.append({"group": g, "label": label, "top": line_top, "x0": x0, "x1": x1})

    candidates.sort(key=lambda c: c["x0"])

    def bridged(a, b):
        lo, hi = a["x1"], b["x0"]
        line_top = (a["top"] + b["top"]) / 2
        return any(abs(w["top"] - line_top) <= FLOAT_ROW_TOL and w["x0"] < hi and w["x1"] > lo
                   for w in floating)

    seeds = {}
    for i, c in enumerate(candidates):
        prev_ok = (i > 0 and abs(candidates[i - 1]["top"] - c["top"]) <= FLOAT_ROW_TOL
                   and not bridged(candidates[i - 1], c))
        next_ok = (i + 1 < len(candidates) and abs(candidates[i + 1]["top"] - c["top"]) <= FLOAT_ROW_TOL
                   and not bridged(c, candidates[i + 1]))
        if not (prev_ok or next_ok):
            continue
        seeds[id(c["group"])] = {"label": c["label"], "x0": c["x0"], "x1": c["x1"], "top": c["top"]}
    return seeds


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
# The line count above already bounds *how many* wrapped lines a header may
# take, but not how much text those lines hold: a wide cell can pack a long
# paragraph into two or three visually-wrapped lines, so a character cap
# still does independent work the line count does not.
#
# 130 was never measured -- it happened to sit just under one real label
# ("The following rule will be added to the park rules: What is the new
# park rule(s)? Copy it exactly as it will appear in the park rules." --
# 134 chars) and rejected it along with 8 other genuine instructions sharing
# its shape, all confirmed against truth (best-cell IoU 0.68-0.79) on the
# tuning corpus: 134, 150, 151, 156, 160, 167, 171, 173, 183 chars. Every
# rejected non-field candidate found in the same corpus -- a signature line,
# a Yes/No/Unknown checkbox row, informational prose ending in a link -- is
# already caught by R12_MIN_BLANK_GAP below regardless of its length, except
# one 217-char informational paragraph (a link-and-login blurb) that still
# clears the blank-gap floor.
#
# So the real label population measured on tuning tops out at 183 and the
# nearest confirmed non-field prose starts at 217 -- a 34-char gap with
# nothing in it. 200 sits at the midpoint, covering every real label found
# while still rejecting that one prose case.
R12_MAX_LABEL_LEN = 200
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


# R14: a comb field -- one small box per character, divided by tick marks
# spaced closer than grid_cells()'s own 20pt minimum cell width (see
# sec_comb_field in eval/synth/hard.py). A postal code, SIN, phone number or
# date often uses this convention: one outer box with N evenly-spaced
# internal dividers, none of them 20pt apart, so grid_cells() finds zero
# cells in the whole row and no cell-based rule ever sees it.
#
# Measured on the real corpus (16 forms, eval/corpus/real_all2): only one
# form draws anything resembling this shape (43b1efcebeb7.pdf, a Canadian
# family-info form, 7 instances across 2 pages for a Year/Month/Day date
# entry) -- and in every one of those 7 places the PDF's own original
# AcroForm (this corpus's truth, origin "stripped") has NO widget at all; its
# 6 real widgets sit elsewhere on the page entirely. The real ticks there are
# also drawn as ~3pt baseline serifs, not full-height dividers, unlike the
# synthetic construct below -- a further reason not to loosen the height
# floor chasing that one instance. So on the real corpus this rule can only
# cost precision, never gain recall: a caveat recorded here rather than
# hidden, same position R11 (dot leaders) landed in.
#
# Two other candidate shapes turned up in the same survey and are
# deliberately excluded by the bounds below:
#   - a genuine 2-cell YYYY|MM sub-table (a5ffdc173722.pdf, a LiveCycle
#     Designer form) -- gaps of 25-36pt, past COMB_MAX_GAP, and only 2 of
#     them, well under COMB_MIN_GAPS.
#   - a rotated-text data table with narrow columns (b642646180f3.pdf) and a
#     shaded schedule table (86b3dc306803.pdf) -- both have dividers spanning
#     many rows (heights of 80-115pt), past COMB_MAX_H.
#
# The construct DOES appear as designed on the synthetic tuning/holdout
# corpora (sec_comb_field, eval/synth/hard.py) -- full-height dividers, an
# outer bounding box, and a caption directly above -- which is where this
# rule's recall gain is real (measured recall on the construct there was
# 0.098 before it). Truth for that synthetic construct is ONE widget
# spanning the whole comb, not one per character cell, so R14 emits one
# field per comb to match.
COMB_TICK_MAX_W = 3        # points; a tick is a thin rect, like grid_cells' v
COMB_TICK_MIN_H = 10       # points; excludes the real corpus's baseline serifs
COMB_TICK_MAX_H = 40       # points; excludes a real table's multi-row dividers
COMB_TICK_TOL_Y = 1.5      # points; ticks in one comb share an exact top/bottom
COMB_MIN_GAP = 6           # points; a per-character cell has some real width
COMB_MAX_GAP = 20          # points; must stay under grid_cells' own cell floor
COMB_GAP_TOL = 3           # points; cells in one comb are uniform width
COMB_MIN_GAPS = 6          # matches sec_comb_field's COMB_MIN_N floor of 6 boxes
COMB_BORDER_TOL_X = 3      # points; bounding h-rule x-span match
COMB_BORDER_TOL_Y = 2      # points; bounding h-rule y match
COMB_LABEL_ABOVE_GAP = 14  # points; caption-above gap, mirrors R12's own band
COMB_LABEL_LEFT_REACH = 200  # points; how far left a same-line label may start


def _comb_runs(page):
    """Group vertical tick rects into candidate comb fields.

    A tick joins a run only while its gap to the last tick is both within
    COMB_MIN_GAP..COMB_MAX_GAP and close to the run's own already-established
    gap (uniform cell width) -- a table's ragged column dividers do not pass
    this, and COMB_MIN_GAPS is re-checked on the finished run so a lone pair
    of ticks that happens to match is not enough on its own.
    """
    v = [r for r in page.rects if r["width"] < COMB_TICK_MAX_W
         and COMB_TICK_MIN_H <= r["height"] <= COMB_TICK_MAX_H]
    v.sort(key=lambda r: r["x0"])
    runs, cur = [], []
    for r in v:
        if cur:
            last = cur[-1]
            gap = r["x0"] - last["x0"]
            same_row = (abs(r["top"] - last["top"]) <= COMB_TICK_TOL_Y
                        and abs(r["bottom"] - last["bottom"]) <= COMB_TICK_TOL_Y)
            gap_ok = COMB_MIN_GAP <= gap <= COMB_MAX_GAP
            established_ok = (len(cur) < 2
                               or abs(gap - (cur[-1]["x0"] - cur[-2]["x0"])) <= COMB_GAP_TOL)
            if same_row and gap_ok and established_ok:
                cur.append(r)
                continue
            if len(cur) - 1 >= COMB_MIN_GAPS:
                runs.append(cur)
            cur = [r]
        else:
            cur = [r]
    if cur and len(cur) - 1 >= COMB_MIN_GAPS:
        runs.append(cur)
    return runs


def _comb_bounded(run, page):
    """True when a real horizontal rule bounds the run on both top and
    bottom, spanning its full width -- a bare row of ticks with no box
    around it is a ruler or a scale, not a comb field."""
    x0, x1 = run[0]["x0"], run[-1]["x0"]
    h = [r for r in page.rects if r["height"] < 3 and r["width"] >= 5]
    def has_h(y):
        return any(abs(r["top"] - y) <= COMB_BORDER_TOL_Y
                   and r["x0"] <= x0 + COMB_BORDER_TOL_X
                   and r["x1"] >= x1 - COMB_BORDER_TOL_X
                   for r in h)
    return has_h(run[0]["top"]) and has_h(run[0]["bottom"])


def _comb_label(run, words, max_len=60):
    """A comb's label, read from the left ("Postal Code: |_|_|_|") or from a
    caption directly above (sec_comb_field's convention), or None."""
    x0, x1 = run[0]["x0"], run[-1]["x0"]
    top, bot = run[0]["top"], run[0]["bottom"]
    cy = (top + bot) / 2
    left = [w for w in words if w["x1"] <= x0 + 1
            and abs(((w["top"] + w["bottom"]) / 2) - cy) < COMB_TICK_TOL_Y + 3
            and x0 - w["x1"] < COMB_LABEL_LEFT_REACH]
    if left:
        label = " ".join(w["text"] for w in sorted(left, key=lambda w: w["x0"])[-6:])
        label = label.rstrip(":").strip()
        if 2 <= len(label) <= max_len and not OFFICE_USE.search(label):
            return label
    above = [w for w in words if 0 < top - w["bottom"] < COMB_LABEL_ABOVE_GAP
             and w["x0"] < x1 and w["x1"] > x0 - 20]
    if above:
        line_bot = max(w["bottom"] for w in above)
        line = [w for w in above if abs(w["bottom"] - line_bot) < 2]
        label = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"]))
        label = label.rstrip(":").strip()
        if 2 <= len(label) <= max_len and not OFFICE_USE.search(label):
            return label
    return None


def _qualified_write_on_lines(page, words, gap_threshold):
    """Every hrule on the page that, on its own, passes the full R5b test
    for a genuine write-on line -- see the R5b section below for the
    reasoning behind each guard here (this is that same test, factored out
    so it can be run once and reused before R5b's own loop -- see the cell
    filter that calls this, right below). Returns a list of
    {"rule": r, "label": label} for every qualifying hrule.
    """
    W = page.width
    panel_fills = _prose_panels(page.rects, words)
    vrules = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    hrules = _merge_ruling_lines(
        [r for r in page.rects if r["height"] < 3 and r["width"] >= 5])
    ON_RULE_TOL_Y = 3
    ON_RULE_MAX_COVER = 0.35
    results = []
    for r in hrules:
        rule_w = r["x1"] - r["x0"]
        if not (40 <= rule_w <= W * 0.77):
            continue
        if any(abs(x["x0"] - r["x0"]) < 3 or abs(x["x0"] - r["x1"]) < 3
               for x in vrules
               if x["top"] <= r["top"] + 3 and x["bottom"] >= r["top"] - 3):
            continue
        if _borders_shaded_panel(r, panel_fills, vrules):
            continue                       # top/bottom edge of a shaded panel, not a rule
        covered = sum(max(0.0, min(w["x1"], r["x1"]) - max(w["x0"], r["x0"]))
                      for w in words if abs(w["bottom"] - r["top"]) < ON_RULE_TOL_Y)
        if rule_w > 0 and min(covered, rule_w) / rule_w >= ON_RULE_MAX_COVER:
            continue                       # underlines existing print, not blank space
        left = [w for w in words if abs(w["bottom"] - r["top"]) < 9
                and w["x1"] <= r["x0"] + 3 and w["x1"] > r["x0"] - 260]
        under = [w for w in words if 0 < w["top"] - r["top"] < 14
                 and w["x1"] > r["x0"] - 2 and w["x0"] < r["x1"] + 2]
        left_sorted = sorted(left, key=lambda w: w["x0"])
        under_sorted = sorted(under, key=lambda w: w["x0"])
        if left_sorted:
            org_label = " ".join(w["text"] for w in left_sorted)
            src = _cut_at_gutter(left_sorted, gap_threshold, "last")[-7:]
        else:
            org_label = " ".join(w["text"] for w in under_sorted)
            src = _cut_at_gutter(under_sorted, gap_threshold, "first")
        label = " ".join(w["text"] for w in src).strip()
        if not label:                      # unlabelled rules are decorative
            continue
        if OFFICE_USE.search(org_label):   # office-only block -- not for the applicant
            continue
        results.append({"rule": r, "label": label[:60]})
    return results


def _drop_double_writeon_cells(cells, write_on_lines):
    """Drop a grid cell whose top AND bottom border are each, independently,
    one of `write_on_lines` -- lines that already qualify, entirely on
    their own, as an R5b write-on field (see _qualified_write_on_lines).

    grid_cells() builds a cell by pairing an hrule with the closest one
    below it that shares its x-range, with no notion of whether either
    line already stands as a complete field in its own right. A common
    form shape defeats that: two independent, vertically stacked write-on
    lines that share an x-range purely because they belong to two
    same-shaped rows (e.g. "Print Name ____  Date ____" repeated for a
    second signer directly below the first). grid_cells() pairs the first
    line's own rule with the second line's own rule and manufactures a
    cell spanning both, reading whatever caption sits near the first line
    as that cell's label -- one spurious "ruled box" straddling two real,
    independent write-on fields.

    Confirmed on safer.pdf: the page-2 signature block's two "Date" write-
    on lines (each correctly found by R5b on its own) get paired this way
    into one manufactured cell, which R2 then claims as a third, 29.9pt-
    tall "Date" box overlapping both real ones.

    Requiring BOTH edges to independently qualify, rather than just one, was
    measured NOT to be enough on its own: on a dense, prose-heavy form
    (595e918dae36.pdf, an appellate court form) nearly every line of running
    text is narrow and captioned by the next line below it, so most of its
    ordinary paragraph lines independently pass _qualified_write_on_lines
    too -- dropping every cell bounded by two such lines cost 92 real
    matches across the tuning/holdout corpus (measured), almost all on
    forms with nothing to do with a stacked signature block.

    The tell that actually separates the two shapes is not "both edges
    qualify" but WHAT they are captioned: two independent copies of the
    SAME repeated field (the shape this rule targets -- "Date" printed
    twice, once per signer) get the SAME label from
    _qualified_write_on_lines, because each is read the same way in
    isolation. Two borders of one real table row, or two consecutive lines
    of running prose, essentially never share an exact caption -- each
    line's own words differ. Requiring the two labels to match (after
    normalizing case and whitespace) keeps the appellate form's cells (no
    two of its "qualifying" neighbours ever share a caption) while still
    catching safer.pdf's page-2 block (both borders read "Date").
    """
    by_top = {}
    for entry in write_on_lines:
        by_top.setdefault(round(entry["rule"]["top"], 1), entry["label"])
    if len(by_top) < 2:
        return cells
    def edge_label(y):
        for t, label in by_top.items():
            if abs(y - t) <= 1:
                return label
        return None
    def norm(s):
        return " ".join(s.split()).casefold() if s else s
    kept = []
    for cell in cells:
        top_label = edge_label(cell[1])
        bot_label = edge_label(cell[3])
        if top_label is not None and bot_label is not None and norm(top_label) == norm(bot_label):
            continue
        kept.append(cell)
    return kept


RECT_CURVE_CORNER_TOL = 6   # points; see _rect_like_curves


def _rect_like_curves(page):
    """Curve objects whose path draws a rectangle -- square-cornered or
    rounded -- instead of a plain pdfplumber "rect". Some form producers
    draw a checkbox or radio button this way (four straight edges plus
    four short corner arcs, "m c l c l c l c h"), and every rule below
    reads page.rects only, so page.curves is otherwise invisible to them
    no matter how the box is captioned.

    Confirmed while chasing eval.blind's "structured, zero fields" list:
    13 of 16 curve-heavy fetched real PDFs (no ground truth, so this
    can't move tuning/holdout) share an IDENTICAL 16.8x16.8 rounded-
    square curve, corner radius ~2.8pt, drawing a Yes/No radio pair on
    eval/corpus/real/1bdaa5e8fd5eaace.pdf -- undetected because R1 keys
    on a glyph character and R18 reads page.rects, and this box is
    neither.

    A curve counts as rect-like only if EVERY anchor point in its path
    sits within RECT_CURVE_CORNER_TOL of the path's own bounding-box
    corner -- true of a rectangle at any corner radius, false of an
    arbitrary vector shape (an arrow, a tick mark, a logo) that would
    otherwise slip into a rect-based rule as a false positive. Returns
    dicts shaped like page.rects entries (x0, x1, top, bottom, width,
    height, fill, stroke).
    """
    out = []
    for c in page.curves:
        path = c.get("path")
        if not path or path[0][0] != "m" or path[-1][0] != "h":
            continue
        if any(op[0] not in ("m", "l", "c", "h") for op in path):
            continue
        x0, x1, top, bot = c["x0"], c["x1"], c["top"], c["bottom"]
        w, h = x1 - x0, bot - top
        if w <= 0 or h <= 0:
            continue
        tol = min(RECT_CURVE_CORNER_TOL, min(w, h) / 2 + 0.5)
        pts = [op[1] for op in path if op[0] != "h"]
        if not all(min(abs(x - x0), abs(x - x1)) <= tol
                   and min(abs(y - top), abs(y - bot)) <= tol
                   for x, y in pts):
            continue
        out.append({"x0": x0, "x1": x1, "top": top, "bottom": bot,
                     "width": w, "height": h,
                     "fill": bool(c.get("fill")), "stroke": bool(c.get("stroke"))})
    return out


def detect(page, pno, carry_in=None):
    """Detect fields on one page.

    `carry_in` is the previous page's R3 column headers (see R3c below),
    or None. Returns (fields, carry_out) -- carry_out is this page's own
    still-active R3 column headers, for the caller to pass to the next page.
    """
    H, W = page.height, page.width
    words = page.extract_words()
    out = []
    gap_threshold = _page_gap_threshold(words)

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
    # See _drop_double_writeon_cells: a "cell" whose top and bottom border
    # are each independently a complete R5b write-on line is not a real
    # box, just two independent write-on lines grid_cells() paired by
    # x-range proximity. Computed once, up front, so every rule that
    # consumes `cells` (R2, R3, R12, R16, ...) sees the same corrected
    # grid -- not just R2, which is where this shape was first caught.
    write_on_lines = _qualified_write_on_lines(page, words, gap_threshold)
    cells = _drop_double_writeon_cells(cells, write_on_lines)

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
        header_sorted = sorted(header, key=lambda w: w["x0"])
        # Guards below run on `org_label` -- the FULL pre-cut header text --
        # not the gutter-cut `label` that actually gets stored. A wide,
        # colon-ending, over-length or signature-mentioning header is a
        # signal about the CELL (it is a section title / office-only block
        # / signature line, not a field), and that signal lives in the full
        # text the cell prints, even once the emitted label is narrowed to
        # the word(s) nearest the field. Checking the cut version instead
        # would let exactly the run-on cells these guards exist for slip
        # back in, just because trimming happened to remove the tell.
        org_label = " ".join(w["text"] for w in header_sorted)
        label = " ".join(w["text"] for w in _cut_at_gutter(header_sorted, gap_threshold, "first"))
        entry_top = max(w["bottom"] for w in header) + 1
        if bot - entry_top < 11 or not (2 <= len(org_label) <= 60):
            continue
        if not HAS_LETTER.search(label):                          # see HAS_LETTER above
            continue
        if SIGNATURE.search(org_label):
            continue
        if org_label.endswith(":") and (x1 - x0) > W * 0.8:       # R7
            continue
        ext_x1, absorbed = _extend_row_span(cell, cells, claimed, words, vrules)
        claimed.add(cell)
        claimed.update(absorbed)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R2",
                    "confidence": 0.8,
                    "rect": [x0 + 2, H - bot + 1.5, ext_x1 - 2, H - entry_top - 1.5]})

    # ---- R3  empty cell, name inherited from the column header above --------
    groups = _cluster_columns(cells)
    for group in groups:
        group.sort(key=lambda c: c[1])
    groups.sort(key=lambda g: min(c[0] for c in g))
    carry_matches = _match_carried_columns(groups, carry_in, words)
    wide_labels, group_header_seeds = _group_header_splits(cells, words)
    # Match each group-header seed (see _group_header_splits above) to the
    # one column-cluster containing its sub-cell, the same way a carried-in
    # header is matched to a group by membership.
    split_seed_by_group = {}
    for sub_cell, label, wide_top in group_header_seeds:
        for g in groups:
            if sub_cell in g:
                split_seed_by_group[id(g)] = {"label": label, "x0": sub_cell[0], "x1": sub_cell[2],
                                               "top": wide_top}
                break
    # A floating header (see _floating_headers above) only fills a column
    # that gets no header from any other source -- a real header row, split
    # header, or carried-in header always takes precedence.
    float_seed_by_group = _floating_headers(groups, cells, words)
    raw_carry = []          # (header_top or None, x0, x1, label) per group, before the
                             # multi-column corroboration filter below
    for group in groups:
        seeded = carry_matches.get(id(group))
        # A group-header split only seeds when nothing was carried in from a
        # previous page -- carry_in already passed this same corroboration
        # check once, and takes precedence.
        split_seed = split_seed_by_group.get(id(group)) if seeded is None else None
        float_seed = (float_seed_by_group.get(id(group))
                      if seeded is None and split_seed is None else None)
        active_seed = seeded or split_seed or float_seed
        header = active_seed["label"] if active_seed else None
        # (x0, x1) of the specific cell that carries `header` -- the header's
        # OWN box, not the column-band's page-wide extent. _cluster_columns
        # groups purely by x0 proximity, so one page-wide band can span a
        # real table column AND, lower down the same page, an unrelated
        # wide cell (a comment box, a prose paragraph) that merely starts at
        # the same left margin. Using the band's aggregate min/max would
        # inherit that unrelated cell's width -- the header's own box never
        # does.
        header_extent = (active_seed["x0"], active_seed["x1"]) if active_seed else None
        # top of the cell that most recently SET header on this page; None if
        # header is still just a carried-in seed. A group-header split seed
        # and a floating-header seed are NOT carried in -- their header_top
        # is the source line's own top, so each corroborates its sibling
        # columns exactly like a real multi-column header row would (see
        # the carry_out filter below).
        if split_seed is not None:
            header_top = split_seed["top"]
        elif float_seed is not None:
            header_top = float_seed["top"]
        else:
            header_top = None
        carried = seeded is not None
        # This column's own row-pitch baseline since `header` was last set.
        # `pending` is one accepted row's height not yet corroborated by a
        # second; `established` is the pitch once two rows have agreed --
        # see the two-agreeing-rows note above R3_ROW_PITCH_TOL.
        pending = None
        established = None
        for cell in group:
            txt = _text_in(words, cell)
            if txt:
                label = " ".join(w["text"] for w in sorted(txt, key=lambda w: w["x0"]))
                forced = wide_labels.get(cell)
                own_extent = (cell[0], cell[2])
                if forced is not None:
                    label, own_extent = forced[0], (forced[1], forced[2])
                if 2 <= len(label) <= 60:
                    # An "office use" header marks its column off-limits: no
                    # blank cell below it should be claimed as a field until
                    # a real header appears further down.
                    if OFFICE_USE.search(label):
                        header, header_top, header_extent = None, None, None
                    else:
                        header, header_top = label, cell[1]
                        header_extent = own_extent
                    pending, established = None, None   # new header -- old baseline no longer applies
                carried = False           # a header printed on this page always wins
                continue
            x0, top, x1, bot = cell
            if header is None or cell in claimed:
                continue
            if not (11 <= bot - top <= 70) or (x1 - x0) < 30:
                continue
            height = bot - top
            height_breaks_pitch = False
            if established is not None:
                height_breaks_pitch = (height > established * R3_ROW_PITCH_TOL
                                        or height < established / R3_ROW_PITCH_TOL)
            elif pending is not None:
                if pending * R3_ROW_PITCH_TOL >= height >= pending / R3_ROW_PITCH_TOL:
                    established = (pending + height) / 2    # two rows agree -- pitch is now trusted
            if height_breaks_pitch and not _row_has_vertical_support(cell, vrules):
                # Both signals agree: this row breaks the column's own
                # established pitch AND lacks the ruling its real rows have
                # -- grid_cells has latched onto ruling past the table's
                # real end. The table is over; stop inheriting.
                header, header_top, header_extent = None, None, None
                continue
            if not height_breaks_pitch:
                # A row that passed the pitch check (or that there was no
                # baseline yet to check it against) feeds the baseline.
                # A row only accepted on the strength of its ruling is a
                # deliberate one-off (e.g. a freeform box) and is left out,
                # so it cannot drag the pitch toward its own outlier size.
                if established is None:
                    pending = height
            ext_x1, absorbed = _extend_row_span(cell, cells, claimed, words, vrules)
            claimed.add(cell)
            claimed.update(absorbed)
            out.append({"page": pno, "type": "text", "label": header,
                        "rule": "R3c" if carried else "R3",
                        "confidence": 0.55 if carried else 0.6,
                        "rect": [x0 + 2, H - bot + 2, ext_x1 - 2, H - top - 2]})
        if header is not None:
            raw_carry.append((header_top, header_extent[0], header_extent[1], header))

    # A header is only worth carrying to the next page when it is part of a
    # genuine multi-column header ROW -- another column on this same page
    # whose header was set at (essentially) the same top. Page-wide column
    # clustering (_cluster_columns) groups cells purely by x-position, so an
    # isolated, unrelated single-column label (a wrapped_label's own caption,
    # a nested sub-field, ...) can land in its own x-band and still look like
    # an "active header" at the bottom of the page. Carrying THAT to the next
    # page is exactly the false-positive this rule must avoid: it would hand
    # a confidently wrong name to a real continuation table that merely
    # shares that column's x-position by coincidence. A header carried IN
    # from a previous page (header_top is None -- nothing on this page set
    # it) already passed this same check when it was first carried, so it is
    # exempt: forward it as-is for a table that spans 3+ pages.
    fresh_tops = [ht for ht, *_ in raw_carry if ht is not None]
    carry_out = [
        {"x0": x0, "x1": x1, "label": label}
        for header_top, x0, x1, label in raw_carry
        if header_top is None
        or sum(abs(header_top - t) <= CARRY_HEADER_ROW_TOL for t in fresh_tops) > 1
    ]

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

    # ---- R14  comb field: one box per character, ticks under grid_cells' floor
    for run in _comb_runs(page):
        if not _comb_bounded(run, page):
            continue
        label = _comb_label(run, words)
        if label is None:
            continue
        x0, x1 = run[0]["x0"], run[-1]["x0"]
        top, bot = run[0]["top"], run[0]["bottom"]
        out.append({"page": pno, "type": "text", "label": label, "rule": "R14",
                    "confidence": 0.55,
                    "rect": [x0 + 2, H - bot + 2, x1 - 2, H - top - 2]})

    # ---- R16  caption prints BELOW a fully blank write-on cell -------------
    # A grid cell with no text of its own at all, whose caption is ordinary
    # page text sitting just under its own bottom border instead of inside
    # it. Measured on the tuning corpus's own real/Adobe form
    # (077cfa584877.pdf): a day/month/year rent-increase date box draws as
    # three empty, fully-ruled cells with "day"/"month"/"year" printed a few
    # points below each cell's own bottom border -- no existing rule reads
    # text that never enters a cell at all, so this recovers a real,
    # currently-missed match on that form's own truth (three widgets).
    #
    # Guard against a false pairing: grid_cells() will happily manufacture a
    # "cell" out of two horizontal rules that do not actually belong
    # together -- measured on the tuning corpus, a real field's own bottom
    # border paired with an unrelated section heading's decorative underline
    # a little further down the page, both spanning a similar x-range. Every
    # such false cell lacks a vertical rule spanning its manufactured height,
    # while a genuine ruled box has real ruling on both edges the whole way
    # down -- so requiring _row_has_vertical_support (already used by R3's
    # own table-break guard) is required here too.
    #
    # The real risk this rule has to resolve: in a stacked column of several
    # boxes, the text between box N's bottom and box N+1's top could belong
    # to EITHER -- box N's caption-below, or box N+1's caption-above (the
    # ordinary R2/R3 convention). Resolved by measuring, not assuming: a
    # caption genuinely belonging to a box sits a small, fixed gap under its
    # own border (this corpus's real instance: ~5.5pt); a stray label for
    # whatever comes next would sit closer to THAT box instead. So a below-
    # caption is only accepted when it sits closer to THIS cell's bottom
    # edge than to the top edge of the nearest OTHER grid cell sharing its
    # column -- the nearer border wins, and a genuine tie or reversal is
    # left alone rather than guessed at.
    #
    # A second, bordered-cell shape was measured here and set aside for a
    # time: the caption sitting INSIDE the cell's own bottom band, blank
    # space above it in the same box (the synthetic sec_label_below
    # construct, eval/synth/hard.py -- a boxed line captioned "Print Name" or
    # "Date" hard against its own bottom border). That shape is real and
    # R2/R12 genuinely miss it, and it measured airtight (every candidate
    # matched truth, zero false positives) -- but including it pushed the
    # 25-form hard corpus's own f1 from 0.653 to 0.679, past the 0.66
    # tripwire eval/synth/test_hard.py holds the corpus to on purpose. That
    # tripwire exists to flag when the corpus needs new difficulty, not to
    # block a correct detector change, so the shape is now implemented below
    # as R17 rather than merged into this rule -- the two are siblings, not
    # variants of one shape: here the caption is free page text that never
    # entered a cell at all, there it is ordinary text already inside one.
    #
    # Two false positives turned up on the real corpus once this rule ran
    # against the full tuning+holdout set, both a fully-ruled blank cell
    # that is real but is NOT a fillable field:
    #   - a page-footer bar ("page 2 of 6 pages") drawn as a genuinely
    #     ruled, page-wide ~418pt box -- every true match measured here
    #     tops out at 278pt wide (a full-name entry line), so a width cap
    #     well clear of both (60% of the page) keeps the footer out without
    #     touching any real one.
    #   - an instruction box ("Complete the details below at...", cut off by
    #     the word/line grouping) whose cell itself runs 111pt tall -- every
    #     true match here is a single entry line, 16-19pt tall. R12 already
    #     owns a genuinely tall blank cell (its own floor is 70pt); capping
    #     at 40 here (mirroring GROUP_HEADER_MAX_H's own bottom-of-header-
    #     band ceiling) leaves headroom for a slightly larger real line
    #     without reopening the door to a paragraph-sized box.
    R16_MAX_LABEL_LEN = 60       # matches R2
    R16_MAX_H = 40               # points; see the instruction-box false positive above
    R16_MAX_W_FRAC = 0.6         # of page width; see the page-footer false positive above
    R16_BELOW_GAP_MAX = 14       # points; how far below a blank cell's own
                                 # bottom border a floating caption may sit
                                 # and still read as belonging to it --
                                 # mirrors R5b/R11's own "under"/leader reach
    R16_BELOW_COL_TOL = 4        # points; x-alignment between a below-cell
                                 # caption and the cell's own edges -- a real
                                 # ruled edge is a tighter anchor than a
                                 # page-wide column band, so this stays well
                                 # under FLOAT_COL_TOL
    vrules_r16 = [r for r in page.rects if r["width"] < 3 and r["height"] >= 5]
    in_any_cell = set()
    for c in cells:
        for w in _text_in(words, c):
            in_any_cell.add(id(w))
    floating_r16 = [w for w in words if id(w) not in in_any_cell]

    for cell in cells:
        if cell in claimed:
            continue
        x0, top, x1, bot = cell
        if _text_in(words, cell):
            continue                      # only a fully blank cell qualifies
        if bot - top > R16_MAX_H or x1 - x0 > R16_MAX_W_FRAC * W:
            continue
        if not _row_has_vertical_support(cell, vrules_r16):
            continue

        band = [w for w in floating_r16
                if 0 < w["top"] - bot <= R16_BELOW_GAP_MAX
                and w["x0"] >= x0 - R16_BELOW_COL_TOL and w["x1"] <= x1 + R16_BELOW_COL_TOL]
        if not band:
            continue
        line_top = min(w["top"] for w in band)        # nearest line below the cell
        line = sorted((w for w in band if abs(w["top"] - line_top) <= 2),
                      key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in line)
        if not (2 <= len(label) <= R16_MAX_LABEL_LEN):
            continue
        if not HAS_LETTER.search(label) or OFFICE_USE.search(label):
            continue
        if label.rstrip()[-1:] in ".?":
            continue
        gap_here = line_top - bot
        line_bottom = max(w["bottom"] for w in line)
        next_top = _next_cell_top(cell, cells, R16_BELOW_COL_TOL)
        if next_top is not None and (next_top - line_bottom) <= gap_here:
            continue                      # ambiguous -- as close to the next box
        claimed.add(cell)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R16",
                    "confidence": 0.5,
                    "rect": [x0 + 2, H - bot + 2, x1 - 2, H - top - 2]})

    # ---- R17  caption INSIDE a bordered cell's own bottom band, blank above -
    # The second shape measured and dropped from R16 above (see the comment
    # there): unlike R16, the caption here never leaves the cell at all -- it
    # is ordinary cell text, sitting in a one-line band hard against the
    # cell's own bottom border, with the writing space above it in the SAME
    # box (a "Print Name" / "Date" line laid out like a signature block,
    # minus the word "signature" that the global SIGNATURE filter drops).
    #
    # R2 already looks at exactly this cell and rejects it: R2 takes the
    # cell's only line of text as its "header" (there is nothing else to
    # call it) and then demands blank space BELOW that header down to the
    # cell's own bottom border -- here there is none, the label already sits
    # on that border, so R2's own entry_top-vs-bot check always fails. This
    # rule is R2 turned upside down: same one-line-of-text requirement, but
    # the blank space it demands is ABOVE the label, up to the cell's own
    # top border, and it is only reached once R2 has already passed it by.
    #
    # Measured on the full tuning+holdout set, this shape's loose geometry
    # alone (one line of text, hard against the bottom border, a real blank
    # band above it) also matches two real-corpus layouts that are NOT a
    # signature-style field:
    #   - a table row or header row: "Permit Number | Description | Issued
    #     by | Date Issued" as four sibling column cells, or a financial-
    #     statement's own row labels ("Other income", "Total operating
    #     costs") -- every one of these false positives shares its exact
    #     row band (same top AND bottom, within a hairline) with at least
    #     one OTHER grid cell. A genuine sec_label_below-style box is a
    #     standalone field, not one column of a row shared with others --
    #     mirrors R10's own row_eps test for a row-mate cell.
    #   - a page-wide printed notice or acknowledgement bar ("Email:
    #     RTBCompliance@gov.bc.ca", "Landlord (or authorized agent) Tenant
    #     (or authorized agent)") drawn as one full-width ruled box -- every
    #     true match measured here tops out at 220pt wide (this construct's
    #     own generator caps cell width there), so R16's own page-footer
    #     width cap (0.6 of page width) excludes these too without touching
    #     a real one.
    #   - a section-header cell: a tall container whose only DIRECT text is
    #     its own caption ("Date of Last Rent Increase"), sitting somewhere
    #     in its upper portion with real blank space on BOTH sides -- the
    #     actual instructions and entry boxes this caption introduces are
    #     further sub-cells below it, outside this cell's own text. Unlike a
    #     genuine sec_label_below box, the caption here does not sit hard
    #     against the cell's own bottom border: measured, every true match's
    #     text bottom lands within 1pt of the cell's own bottom edge (this
    #     construct always draws its caption's baseline a fixed few points
    #     off that border), while this false positive's sits 11.8pt clear
    #     of it. A small floor on that gap -- generous next to the 1pt true
    #     cluster, nowhere near the false positive -- keeps the caption
    #     genuinely flush against the border, which is the shape's own
    #     definition (see sec_label_below's docstring: "hard against its
    #     own bottom border").
    # Applying all three together against the full tuning+holdout set: every
    # false positive measured is gone, at the cost of 3 of 47 true matches
    # (two coincidentally row-aligned across a two-column page layout, one a
    # coincidental IoU match with different geometry).
    R17_MAX_W_FRAC = R16_MAX_W_FRAC   # same page-wide-bar failure shape as R16
    R17_ROW_EPS = 0.5                 # points; mirrors R10's own row_eps
    R17_BOTTOM_GAP_MAX = 4            # points; true matches cluster at ~1pt
    for cell in cells:
        if cell in claimed:
            continue
        x0, top, x1, bot = cell
        if x1 - x0 > R17_MAX_W_FRAC * W:
            continue
        if any(abs(o[1] - top) <= R17_ROW_EPS and abs(o[3] - bot) <= R17_ROW_EPS
               for o in cells if o is not cell):
            continue                      # a row-mate cell -- a table row, not a standalone field
        inside = _text_in(words, cell)
        if not inside:
            continue
        last = max(w["bottom"] for w in inside)
        if bot - last > R17_BOTTOM_GAP_MAX:
            continue                      # caption doesn't sit hard against the bottom border
        footer = [w for w in inside if w["bottom"] > last - 6]
        if [w for w in inside if w["bottom"] <= last - 6]:
            continue                      # more than one line of text -- not this shape
        label = " ".join(w["text"] for w in sorted(footer, key=lambda w: w["x0"]))
        entry_bot = min(w["top"] for w in footer) - 1
        if entry_bot - top < 11 or not (2 <= len(label) <= 60):
            continue
        if not HAS_LETTER.search(label) or OFFICE_USE.search(label):
            continue
        if not _row_has_vertical_support(cell, vrules):
            continue
        claimed.add(cell)
        out.append({"page": pno, "type": "text", "label": label, "rule": "R17",
                    "confidence": 0.5,
                    "rect": [x0 + 2, H - entry_bot + 1.5, x1 - 2, H - top - 1.5]})

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
        linside = _text_in(words, left)
        if not linside:
            # `left` may be a narrow, purely decorative gutter column --
            # some grid-template forms keep every row's entry box aligned
            # by inserting a fixed-width blank cell between the label and
            # the box, rather than sizing the label cell to fit. A gutter
            # is narrower than R10's own 25pt floor for a candidate field
            # (see below); anything that wide is a real cell, not a
            # gutter, so do not walk past it. Only one such cell is
            # crossed -- chaining past more than one blank cell risks
            # reaching into an unrelated part of the row and inventing a
            # confident wrong label.
            glx0, gltop, glx1, glbot = left
            if (glx1 - glx0) >= 25:
                continue
            further_cands = [o for o in cells if o[2] <= glx0 + row_eps
                              and abs(o[1] - gltop) <= row_eps and abs(o[3] - glbot) <= row_eps]
            if not further_cands:
                continue
            left = max(further_cands, key=lambda o: o[2])
            if left in claimed:
                continue
            linside = _text_in(words, left)
            if not linside:
                continue
        lx0, ltop, lx1, lbot = left
        lines = {}
        for w in linside:
            lines.setdefault(round(w["top"] / 3), []).append(w)
        sorted_lines = [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]
        # org_label is the FULL pre-cut left-cell text; the length and
        # SIGNATURE guards below run on it for the same reason as R2's own
        # org_label/label split -- a length or signature tell can live in
        # words the per-line gutter-cut discards.
        org_label = " ".join(" ".join(w["text"] for w in ws) for ws in sorted_lines)
        if not (1 <= len(org_label) <= 60) or (x1 - x0) < 25:
            continue
        if SIGNATURE.search(org_label):
            continue
        label = " ".join(
            " ".join(w["text"] for w in _cut_at_gutter(ws, gap_threshold, "last"))
            for ws in sorted_lines)
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
    # SHORT_RUN_FLOOR/CEIL: below 25pt was an all-or-nothing guard against
    # decorative mid-sentence underscore use. Measured on the real corpus
    # (see the scan behind this change): genuine single-character fields
    # (middle initial, suffix, apartment letter, and comb-style land-survey
    # / phone-number blanks) draw runs from about 12pt to just under 25pt,
    # and are silently skipped by the old unconditional floor.
    #
    # A run in that narrow band is claimed only with three extra checks,
    # each earning its keep against a specific false-positive family found
    # by the same scan:
    #   - SHORT_RUN_GAP_MAX (contiguous caption): the immediate preceding
    #     word must sit within 4pt of the run's own start. This is what
    #     separates a genuine "Qtr____Sec____Twp____" comb (gap ~2.2, every
    #     short run directly touches its own short caption) from a run
    #     sitting well after unrelated prose ("Contact Phone" then a run
    #     41pt later, "Unit:" then a run 30pt later) -- neither of those is
    #     in the real corpus's own ground truth.
    #   - HAS_LETTER + >=2 alpha characters on that immediate word: rejects
    #     a chain continuation (the previous token is itself a run of
    #     underscores/punctuation, as in a "$___,___.__" digit comb, where
    #     only the FIRST run in the chain carries a real caption) and
    #     rejects a single stray glyph a scanned form's header sometimes
    #     carries next to a short underscore run (measured on this corpus:
    #     a `'L_` artifact sitting at the very top of the page, no ground
    #     truth widget anywhere near it).
    #   - SHORT_RUN_STOPWORDS: the immediate word, punctuation-stripped, is
    #     rejected if it is a bare function word ("this", "of", ...). This
    #     is what a notarization clause's own fill-in blank looks like --
    #     "given under my hand this ____ day of ________," -- contiguous,
    #     lettered, but captioned by a word that names no field. The other
    #     recurring false-positive family, a "Page __ of __" footer stamp,
    #     is already excluded by SHORT_RUN_FLOOR (measured width 10pt on
    #     every occurrence in this corpus, under the 12pt floor) without
    #     needing a stopword at all.
    #
    # Measured on the tuning + holdout corpora: this recovers the land-
    # survey comb (c83888e63ca7.pdf, ~20 genuine fields on one page), a
    # "$_,___.__" digit comb and a contiguous "REQUESTED @ $2 EACH:" field
    # (cc15fd260573.pdf), and part of a "vacant? ___ weeks" comb
    # (1f697fad5785.pdf, holdout) and a parking-count field
    # (63d383a1290d.pdf, holdout) -- while rejecting every false-positive
    # shape found by the same scan (page-footer stamps, a notary date
    # clause, and a scanned-glyph artifact).
    SHORT_RUN_FLOOR = 12
    SHORT_RUN_GAP_MAX = 4
    SHORT_RUN_STOPWORDS = {
        "this", "that", "these", "those", "the", "a", "an", "of", "on",
        "in", "at", "and", "or", "is", "are", "was", "were", "to", "for",
        "by", "with", "as", "it", "its", "be",
    }
    # A run of underscore/fill characters is not label text -- it is the
    # blank itself. pdfplumber's word tokenizer can merge a run flush
    # against real text with no space (safer.pdf p7: an underscore run
    # tokenized as one word with the NEXT field's own caption, "___...
    # confirm"), and a run sitting alone between two captions comes
    # through as its own "word" with no letters at all ("___..." then
    # "since"). FILL_CHARS mirrors eval/label.py's MARK_CHARS (checkbox
    # glyphs, underscore, dot leaders); strip a leading/trailing run of
    # them off each candidate word, and drop a word that is fill
    # characters only.
    FILL_CHARS = CHECK_GLYPHS | {"_", ".", "·", "․"}

    def _strip_fill(text):
        i, j = 0, len(text)
        while i < j and text[i] in FILL_CHARS:
            i += 1
        while j > i and text[j - 1] in FILL_CHARS:
            j -= 1
        return text[i:j]

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
        run_w = x1 - x0
        if run_w < SHORT_RUN_FLOOR:
            continue
        base, top = run[0]["bottom"], run[0]["top"]
        before = [w for w in words
                  if abs(w["bottom"] - base) < 6 and w["x1"] <= x0 + 2 and w["x1"] > x0 - 240]
        before_sorted = sorted(before, key=lambda w: w["x0"])
        if run_w < 25:
            last = before_sorted[-1] if before_sorted else None
            if last is None or (x0 - last["x1"]) > SHORT_RUN_GAP_MAX:
                continue
            if not HAS_LETTER.search(last["text"]):
                continue
            if sum(1 for ch in last["text"] if ch.isalpha()) < 2:
                continue
            stripped = last["text"].strip(".,:;?!'\"()").lower()
            if stripped in SHORT_RUN_STOPWORDS:
                continue
        # Unlike R2/R10's cell-bounded org_label, `before` is a fixed 240pt
        # reach across the whole page -- it can cross MORE than one gutter
        # (measured: safer.pdf's "Landlord Signature" caption sits within
        # reach of the unrelated "Landlord Phone #" line two columns over).
        # Checking SIGNATURE against that unbounded text would reject the
        # Phone # field for a word naming a different one; the global
        # end-of-detect SIGNATURE filter (on the cut, final label) is the
        # right scope here, same as before this change.
        before_cut = _cut_at_gutter(before_sorted, gap_threshold, "last")
        cleaned = [t for t in (_strip_fill(w["text"]) for w in before_cut[-6:]) if t]
        label = " ".join(cleaned) or "line"
        out.append({"page": pno, "type": "text", "label": label[:60], "rule": "R5",
                    "confidence": 0.65,
                    "rect": [x0 + 1, H - base + 1, x1 - 1, H - top + 11]})

    # ---- R5b  write-on line drawn as a thin rect, not underscore characters --
    # A cell border has vertical rules standing at its endpoints. A write-on line
    # has none. That single test separates them cleanly on this corpus.
    #
    # Two office-only guards it borrows from siblings, made consistent rather
    # than reimplemented:
    #   - grid_cells() already merges a visual line drawn as 2-3 stacked thin
    #     rects (a thick stroke plus a hairline over the same x-range) via
    #     _merge_ruling_lines(); R5b used to scan raw rects instead and could
    #     emit the same line more than once (confirmed on bd17a3fe43d6.pdf,
    #     which draws each line as 3 stacked rects).
    #   - R2, R3, R12 and R14 all reject a label matching OFFICE_USE; R5b did
    #     not, so it could claim a write-on line sitting inside an office-only
    #     block.
    # R5b's largest false-positive group, measured on the tuning corpus: a rule
    # that is a decorative underline drawn beneath ALREADY-PRINTED text (a
    # heading, a caption, a filled specimen value), not blank space for someone
    # to write on -- "Total Gross Expected Income", "SECTION 4: DECLARATION",
    # a manually-underlined phrase in a Word export, and the "underline_trap"
    # synthetic construct (a heading's own decorative rule, tight above a
    # caption that merely happens to read like a field name) all take this
    # shape. The tell is coverage, not mere proximity: text sitting on the
    # rule's own baseline that spans a large FRACTION of the rule's own width
    # is that rule's underline; an ordinary field's same-row label brushing a
    # much WIDER write-on rule by a few points (font-metric bleed, or this
    # corpus's most tightly-packed real form) covers only a small sliver of
    # it. Measured: every true positive on this corpus covers at most 27% of
    # its own rule this way; the largest false positive below that line
    # belongs to a near-miss (wrong geometry, not a wrong claim) a few points
    # further down the same crowded form -- so the cut sits at 35%, clear of
    # both. 42 false positives removed, 0 true positives lost.
    #
    # A second, geometrically distinct trap: a thin rect that is not a stray
    # rule at all, but the TOP or BOTTOM edge of a large filled, unstroked
    # panel already dense with the FORM'S OWN prose -- a shaded instruction
    # block, printed with its own caption ("PLEASE:", "Avoid Processing
    # Delays:") sitting just inside it. Unlike the decorative-underline trap
    # above (heading THEN rule, text sitting ON the rule's own baseline),
    # this shape is inverted: rule THEN heading, so the ON_RULE_MAX_COVER
    # check above -- which only ever looks for text ABOVE the rule -- cannot
    # see it. Scoped to prose fills specifically, not just "large and
    # filled": a real write-on line can legitimately sit at the edge of a
    # large BLANK shaded answer box too (R3's own documented 183pt freeform
    # box), and that box must keep its rule. Same _prose_panels test
    # grid_cells() uses above, for the same reason -- R5b builds its own
    # `hrules` straight from page.rects rather than reusing grid_cells()'
    # filtered `h`, so it needs its own copy of the check.
    #
    # The full test (rule-width bounds, no anchoring vertical rule, no
    # shaded-panel edge, no on-rule text coverage, a real caption, no
    # office-use block) lives in _qualified_write_on_lines, computed once
    # near the top of detect() and reused here -- the cell filter right
    # after `cells = grid_cells(page)` needs the exact same qualifying set
    # to know which manufactured cells are really two of these lines.
    for entry in write_on_lines:
        r = entry["rule"]
        out.append({"page": pno, "type": "text", "label": entry["label"], "rule": "R5b",
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

    # ---- R18  a checkbox drawn as a filled square, not a glyph ----------
    # grid_cells() turns a rect into a cell only when it is a thin ruling
    # line (height<3 horizontal, width<3 vertical), so a rect with real
    # width AND height satisfies neither test and no cell-walking rule
    # (R2/R3/R4/R10/R12/R14/R16/R17) ever reaches it, even though a person
    # sees exactly where to tick. R1 does not reach it either: R1 keys on
    # the two Webdings/Wingdings codepoints in CHECK_GLYPHS, and these
    # boxes carry no glyph at all.
    #
    # Confirmed on the SAFER application form: page 2's "Option 1: Consent
    # Granted" / "Option 2: Consent Not Granted" CRA consent boxes, drawn
    # as 25 x 25.9pt filled squares, reported undetected by the product
    # owner using the app.
    #
    # SCOPE NOTE. This rule also claimed shaded rects as TEXT fields --
    # safer.pdf page 4 draws Home/Work/Cell Phone # and Email as plain grey
    # 84.6 x 30.75pt boxes beside their captions, and it found all six.
    # That path was REMOVED at the product owner's request after seeing it
    # in the app. It is not being carried as dead code: the text band, the
    # row-mate guard, the caption reader and the min-height floor all went
    # with it. Recovering it means `git show ed8f92a`, which has the whole
    # thing with its measurements (66 candidates at 18% precision from a
    # size-only cut, brought to 84% by three guards). Do not rebuild it
    # from scratch.
    #
    # What remains is the checkbox band only, with the guards that band
    # needs:
    #   - CONTAINMENT: a shaded panel containing another qualifying rect is
    #     a section wrapper, not a field -- keep only the innermost.
    #   - EDGE-TRIM: a square with real content well clear of both its own
    #     edges is not empty and is not a tick box.
    #   - ALREADY-CLAIMED: skip anything R1-R17 has taken (30% overlap).
    #   - A caption is required, and supplies the label.
    #
    # Known residual risk, measured: in 1f95bd47c4b0.pdf a genuine consent
    # checkbox and a decorative list bullet are visually identical, and
    # geometry cannot separate them.
    R18_CHK_MIN, R18_CHK_MAX = 18, 32
    # Curve-drawn candidates (see _rect_like_curves) get their own, lower
    # floor: R18_CHK_MIN was measured from SAFER's 25x25.9 plain-rect
    # boxes, but the recurring curve-drawn checkbox/radio size measured
    # across 13 real fetched PDFs is 16.8x16.8 -- below it. Widening
    # R18_CHK_MIN itself would also loosen the rect path on the already-
    # tuned corpus for no reason; this only affects curve-sourced candidates.
    R18_CURVE_CHK_MIN = 14
    R18_CHK_SQUARE_TOL = 8       # points; |width - height| tolerance for "square"
    R18_EDGE_TRIM_TOL = 8        # points; how close to ITS OWN nearer edge a word
                                 # must sit to be trimmed rather than disqualifying
    R18_CLAIMED_OVERLAP = 0.3    # fraction; already claimed by an earlier rule

    def _r18_is_chk_band(r):
        return (R18_CHK_MIN <= r["width"] <= R18_CHK_MAX
                and R18_CHK_MIN <= r["height"] <= R18_CHK_MAX
                and abs(r["width"] - r["height"]) <= R18_CHK_SQUARE_TOL)

    def _r18_is_chk_band_curve(r):
        return (R18_CURVE_CHK_MIN <= r["width"] <= R18_CHK_MAX
                and R18_CURVE_CHK_MIN <= r["height"] <= R18_CHK_MAX
                and abs(r["width"] - r["height"]) <= R18_CHK_SQUARE_TOL)

    def _r18_contains(outer, inner, tol=1.0):
        return (outer["x0"] <= inner["x0"] + tol and outer["x1"] >= inner["x1"] - tol
                and outer["top"] <= inner["top"] + tol and outer["bottom"] >= inner["bottom"] - tol
                and (outer["width"] * outer["height"]) > (inner["width"] * inner["height"]) + 1)

    def _r18_trimmed_bounds(cand):
        """(top, bottom) after trimming edge-hugging words, or None if
        genuine deep-interior content is found -- see EDGE-TRIM above.
        Uses OVERLAP, not full containment: a neighbour's caption can dip
        a point or two past the candidate's own border without ever being
        fully inside it (safer.pdf p4's "Cell Phone #" box overlaps an
        unrelated left-column caption two rows down this way) -- and that
        overhang is exactly the shape R9's own _runs_past test below
        already treats as "ink running past the edge", so it must be
        trimmed here on the same (overlap, not containment) terms or R9
        drops the box a second time regardless of what this rule decides."""
        x0, top, x1, bot = cand["x0"], cand["top"], cand["x1"], cand["bottom"]
        inside = [w for w in words if w["x1"] > x0 and w["x0"] < x1
                  and w["bottom"] > top and w["top"] < bot]
        top_b, bot_b = top, bot
        for w in inside:
            stripped = "".join(ch for ch in w["text"] if ch not in MASK_ONLY)
            if not HAS_LETTER.search(stripped):
                continue          # mask-only content -- never disqualifying
            top_clr, bot_clr = w["top"] - top, bot - w["bottom"]
            if top_clr <= R18_EDGE_TRIM_TOL and top_clr <= bot_clr:
                top_b = max(top_b, w["bottom"] + 1)
            elif bot_clr <= R18_EDGE_TRIM_TOL:
                bot_b = min(bot_b, w["top"] - 1)
            else:
                return None        # well clear of both edges -- real content
        return top_b, bot_b

    def _r18_valid_label(label):
        return bool(label) and 2 <= len(label) <= 60 and HAS_LETTER.search(label) \
            and not OFFICE_USE.search(label) and label.rstrip()[-1:] not in ".?"

    def _r18_already_claimed(cand):
        cx0, cx1 = cand["x0"], cand["x1"]
        cy_lo, cy_hi = H - cand["bottom"], H - cand["top"]
        area = (cx1 - cx0) * (cy_hi - cy_lo)
        if area <= 0:
            return False
        for f in out:
            fx0, fy0, fx1, fy1 = f["rect"]
            ix0, iy0 = max(cx0, fx0), max(cy_lo, fy0)
            ix1, iy1 = min(cx1, fx1), min(cy_hi, fy1)
            if ix1 > ix0 and iy1 > iy0 and (ix1 - ix0) * (iy1 - iy0) / area >= R18_CLAIMED_OVERLAP:
                return True
        return False

    def _r18_checkbox_label(cand, all_chk_cands):
        """A checkbox option's caption, adapted for a checkbox drawn as a
        full-size filled square (safer.pdf p2's "Option 1"/"Option 2"
        consent boxes, 25x25.9pt) rather than R1's small glyph (~10pt).
        R1's own _checkbox_label reads the option text starting from the
        glyph's own right edge with an ~11pt word-spacing tolerance
        (CHECK_LABEL_GAP_MAX) -- right for a glyph that size, but this
        box's caption sits a genuine 15pt clear of its own right edge, a
        normal gap for a box 2.5x wider, already past that tolerance on
        the very first word. Reusing CHECK_LABEL_LINE_TOL for "same line"
        and CHECK_LABEL_GAP_MAX for "still the same phrase" (both already
        measured and gated for R1), this reader skips straight to the
        nearest word on the line, bounded on the right by the NEXT
        checkbox sharing that line -- two options side by side must not
        merge into one label the way a bare gap threshold would risk if
        the line between them ever printed nothing at all."""
        cx1, top, bot = cand["x1"], cand["top"], cand["bottom"]
        cmid = (top + bot) / 2
        line = sorted((w for w in words if abs(((w["top"] + w["bottom"]) / 2) - cmid)
                       < CHECK_LABEL_LINE_TOL and w["x0"] >= cx1), key=lambda w: w["x0"])
        next_x0 = min((o["x0"] for o in all_chk_cands if o is not cand and o["x0"] > cx1
                       and abs(((o["top"] + o["bottom"]) / 2) - cmid) < CHECK_LABEL_LINE_TOL),
                      default=None)
        if next_x0 is not None:
            line = [w for w in line if w["x1"] <= next_x0 + 1]
        if not line:
            return ""
        picked, prev_edge = [line[0]], line[0]["x1"]
        for w in line[1:]:
            if w["x0"] - prev_edge > CHECK_LABEL_GAP_MAX:
                break
            picked.append(w)
            prev_edge = w["x1"]
        return " ".join(w["text"] for w in picked).rstrip(":").strip()

    raw_shaded = [r for r in page.rects if r["fill"] and not r["stroke"]
                  and _r18_is_chk_band(r)]
    curve_chk_cands = [r for r in _rect_like_curves(page) if r["fill"] and not r["stroke"]
                       and _r18_is_chk_band_curve(r)]
    # A real checkbox/radio button drawn this way is essentially always one
    # of a same-sized group sitting on the same or the next line (Yes/No,
    # Mr/Mrs/Ms/Miss, a checklist row wrapping onto a second line -- measured
    # up to 18pt apart on eval/holdout/b642646180f3.pdf's real "who helps
    # you" checklist). A lone curve candidate with no similarly-sized,
    # nearby sibling is far more likely a decorative icon badge next to
    # boilerplate text -- confirmed: a "Translating and Interpreting
    # Service" phone-icon badge is the only curve-square anywhere near it
    # and would otherwise be claimed as a checkbox (measured false positive,
    # scripts/verify.sh). R18_CURVE_SIBLING_Y_TOL is deliberately page-
    # local, not page-wide -- a same-sized icon in a different section of
    # the SAME page must not borrow siblinghood from an unrelated group.
    # The same icon is sometimes painted twice at IDENTICAL coordinates (a
    # duplicate fill pass); that duplicate must not count as a sibling
    # either, or the icon looks like a same-sized group of one.
    R18_CURVE_SIBLING_Y_TOL = 25   # points
    R18_CURVE_DUP_TOL = 3          # points; same spot -- a repeated paint, not a sibling

    def _r18_curve_siblings(r, others):
        return any(abs(r["width"] - o["width"]) <= 4 and abs(r["height"] - o["height"]) <= 4
                   and abs(r["top"] - o["top"]) <= R18_CURVE_SIBLING_Y_TOL
                   and (abs(r["x0"] - o["x0"]) > R18_CURVE_DUP_TOL
                        or abs(r["top"] - o["top"]) > R18_CURVE_DUP_TOL)
                   for o in others if o is not r)

    curve_chk_cands = [r for r in curve_chk_cands if _r18_curve_siblings(r, curve_chk_cands)]
    raw_shaded += curve_chk_cands
    r18_chk_cands = [r for r in raw_shaded
                     if not any(_r18_contains(r, o) for o in raw_shaded if o is not r)]

    for cand in r18_chk_cands:
        if _r18_already_claimed(cand):
            continue
        if _r18_trimmed_bounds(cand) is None:
            continue
        label = _r18_checkbox_label(cand, r18_chk_cands)
        if not _r18_valid_label(label):
            continue
        x0, top, x1, bot = cand["x0"], cand["top"], cand["x1"], cand["bottom"]
        out.append({"page": pno, "type": "checkbox", "label": label, "rule": "R18",
                    "confidence": 0.5,
                    "rect": [x0, H - bot, x1, H - top]})

    # R6 (small empty square cell drawn with rules, not a glyph) was removed:
    # measured on the 165-form real corpus it detected 53 boxes and matched
    # zero of them. Diagnosis: grid_cells() reconstructs "cells" from ruling
    # lines belonging to ordinary tables, and an empty small-square slice of
    # one of those tables is common and looks identical to a hand-drawn
    # checkbox square from geometry alone -- there is no signal in a blank
    # ruled cell's size that distinguishes the two. On safer.pdf, the
    # fixture that originally motivated this rule, its "Option 1 / Option 2"
    # consent boxes are drawn as single filled/stroked rects (not paired
    # ruling lines), so grid_cells() never turns them into a cell in the
    # first place -- R6 produced zero detections there even before removal,
    # confirming it does not, in fact, cover that fixture any more.

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
    return out, carry_out
