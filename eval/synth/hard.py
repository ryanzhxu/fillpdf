"""Hard-mode synthetic form generator.

eval/synth/generate.py produces clean, single-purpose constructs that read
exactly like the current detector rules (R1-R6) expect. This module produces
forms with the kind of messiness real government forms actually have:
multi-line wrapped labels, labels to the left of the entry box instead of
above it, ragged tables whose columns drift row to row, ungrid-aligned
spacer/gutter columns, a "FOR OFFICE USE ONLY" block that looks fillable but
is not, decorative heading underlines placed to bait the R5b write-on-line
rule, checkboxes at sizes the detector's R6 window does not cover, a
nested sub-table inside a cell, a caption governing a GROUP of separate
blank strips rather than one, a rotated caption sitting outside a box's
own border in an unbordered margin gutter, a continuation table whose
columns reflow to different widths across the page break, a blank ruled
column separating a row's label from its own entry box, a field whose
only cue is the blank space after a printed label -- no rule, box, or dot
of any kind, a checkbox drawn as literal ASCII "[ ]" text instead of a
glyph or a drawn shape, and an underscore write-on line only a few
characters wide -- short enough to fall under the detector's own
minimum-run-width floor.

Every truth widget still corresponds to something a human would obviously
read as "write here": a bordered blank cell, a blank cell under a printed
column header, or a checkbox glyph/empty box. Nothing is invisible,
zero-area, or placed with no supporting page structure -- see
test_hard.py::test_every_widget_has_supporting_structure.

Deterministic: generate_hard(seed, ...) always produces the same bytes for
the same seed. All randomness comes from a local random.Random(seed)
instance; nothing reads the wall clock, environment, or global random state.
"""
import argparse
import io
import json
import random
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, PDF points, portrait

WEBDINGS_CHAR = ""
WINGDINGS_CHAR = ""
_WEBDINGS_TTF = "/System/Library/Fonts/Supplemental/Webdings.ttf"
_WINGDINGS_TTF = "/System/Library/Fonts/Supplemental/Wingdings.ttf"

_FONTS_REGISTERED = False


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("Webdings", _WEBDINGS_TTF))
    pdfmetrics.registerFont(TTFont("Wingdings", _WINGDINGS_TTF))
    _FONTS_REGISTERED = True


LABEL_POOL = [
    "Name", "Date of Birth", "Social Security Number", "City", "State",
    "ZIP Code", "Phone Number", "Email Address", "Occupation",
    "Marital Status", "Annual Income", "Date", "Account Number",
    "Case Number", "County", "Apartment Number", "Daytime Telephone",
    "Middle Initial", "Relationship",
]

LONG_LABELS = [
    "Name of Employer or Business (If Self-Employed, Write Self-Employed)",
    "Complete Mailing Address Including Apartment or Unit Number",
    "List All Household Members Currently Residing At This Address",
    "Relationship of This Person to the Applicant Named Above",
    "Explain Any Changes in Income Since Your Last Certification",
    "Name and Address of Nearest Relative Not Living With You",
    "Type of Income Received (Wages, Self-Employment, Benefits, Other)",
    "Reason for Requesting a Change to Your Case Record",
    "Name of School or Training Program Currently Attending",
    "Describe the Nature of Your Disability or Medical Condition",
]

NESTED_SUBFIELDS = ["Street Number and Name", "City or Town", "State and ZIP Code"]

# English caption + Spanish translation on one line, as many real benefits
# and government forms print bilingual captions. Combined with " / " this
# reliably clears R2's 60-char label sanity cap (a guard against mistaking
# a paragraph for a label), even though a person reads it as one short
# caption in two languages.
BILINGUAL_PAIRS = [
    ("Name of Employer or Business If Self-Employed", "Nombre del Empleador o Negocio Propio"),
    ("Complete Mailing Address Including Apartment", "Direccion Postal Completa Incluyendo Apartamento"),
    ("Reason for Requesting This Change", "Motivo para Solicitar Este Cambio"),
    ("Type of Income Received This Month", "Tipo de Ingreso Recibido Este Mes"),
    ("Relationship of This Person to the Applicant", "Parentesco de Esta Persona con el Solicitante"),
    ("Date of Your Last Certification Renewal", "Fecha de su Ultima Renovacion de Certificacion"),
]

QUESTION_POOL = [
    "Are you a U.S. citizen?", "Have you filed a return this year?",
    "Do you have dependents?", "Are you currently employed?",
    "Is this your primary residence?", "Are you self-employed?",
    "Do you own real property?", "Are you married?",
    "Has your address changed?", "Do you receive benefits?",
]

GROUP_CAPTIONS = [
    "List each additional household member below (one per line):",
    "Describe any other income received this month:",
    "Enter each vehicle owned by you or a household member:",
    "List all bank accounts held by any household member:",
    "Enter each address where you lived during the past year:",
]

COLUMN_HEADER_SETS = [
    ["Item", "Quantity", "Amount"],
    ["Date", "Description", "Amount"],
    ["Name", "Relationship", "Age"],
    ["Source", "Amount", "Frequency"],
    ["Account", "Institution"],
]

OFFICE_LABELS = ["Reviewed By", "Approval Date", "Case Worker"]

HEADING_POOL = [
    "SECTION A - PERSONAL INFORMATION", "SECTION B - EMPLOYMENT HISTORY",
    "PART 1: ELIGIBILITY", "PART 2: CERTIFICATION",
    "APPLICANT INFORMATION", "HOUSEHOLD COMPOSITION",
]

PROSE_WORDS = (
    "the applicant must complete every section of this form in ink and "
    "return it to the office within thirty days of the date shown above "
    "failure to provide the requested information may delay or result in "
    "denial of the benefits described in this application all information "
    "provided is subject to verification by the agency and any person who "
    "knowingly makes a false statement may be subject to penalties under "
    "applicable law please read the instructions carefully before signing"
).split()

BODY_FONTS = ["Helvetica", "Times-Roman"]
BOLD_OF = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold"}


class Style:
    def __init__(self, body_font):
        self.body_font = body_font
        self.bold_font = BOLD_OF[body_font]


def _hrect(c, x0, x1, y, thickness=0.75):
    c.setFillColorRGB(0, 0, 0)
    c.rect(x0, y - thickness / 2, x1 - x0, thickness, stroke=0, fill=1)


def _vrect(c, x, y0, y1, thickness=0.75):
    c.setFillColorRGB(0, 0, 0)
    c.rect(x - thickness / 2, y0, thickness, y1 - y0, stroke=0, fill=1)


def _wrap_text(text, font, size, max_w):
    """Greedy word-wrap. Returns a list of lines fitting max_w."""
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _make_prose_line(rng, max_width, font, size):
    words, w = [], 0.0
    while True:
        word = rng.choice(PROSE_WORDS)
        piece = word if not words else " " + word
        pw = stringWidth(piece, font, size)
        if w + pw > max_width and words:
            break
        words.append(word)
        w += pw
        if len(words) > 20:
            break
    return " ".join(words)


def _draw_heading(c, rng, x0, x1, y_top, style, tight_gap=False):
    """Section heading with a decorative underline. NOT a field: no truth
    widget. tight_gap=True leaves less than 14pt before the next section,
    which is the R5b false-positive bait: the detector's rule for
    associating a label with a rect-drawn write-on line looks for text
    within 14pt below the rect, and a decorative underline has none of its
    own -- unless something else is sitting close enough beneath it.
    """
    heading = rng.choice(HEADING_POOL)
    size = 12
    baseline = y_top - size
    c.setFont(style.bold_font, size)
    c.drawString(x0, baseline, heading)
    w = min(stringWidth(heading, style.bold_font, size), x1 - x0)
    _hrect(c, x0, x0 + w, baseline - 3)
    gap = 4 if tight_gap else 14
    return baseline - 3 - gap


# ---------------------------------------------------------------------------
# Difficulty sections. Each returns (new_y_top, widgets) or None if it does
# not fit in `avail`. widgets omit "page" (caller fills it in).
# ---------------------------------------------------------------------------

def sec_wrapped_label(c, rng, x0, x1, y_top, avail, style):
    """A multi-line label at the top of a bordered cell, blank entry below,
    all one cell. Defeats R2: that rule only accepts a header confined to
    the cell's first ~6pt text band, so a 2-3 line label makes it bail."""
    cell_w = min(rng.uniform(110, 170), x1 - x0)
    size = 8
    label = rng.choice(LONG_LABELS)
    lines = _wrap_text(label, style.body_font, size, cell_w - 6)[:3]
    if len(lines) < 2:
        return None
    line_h = 9.5
    label_h = len(lines) * line_h
    row_h = label_h + 16
    if row_h > avail:
        return None
    cy_top, cy_bot = y_top, y_top - row_h
    cx0, cx1 = x0, x0 + cell_w
    _hrect(c, cx0, cx1, cy_top)
    _hrect(c, cx0, cx1, cy_bot)
    _vrect(c, cx0, cy_bot, cy_top)
    _vrect(c, cx1, cy_bot, cy_top)
    c.setFont(style.body_font, size)
    y = cy_top - 3 - size * 0.8
    for line in lines:
        c.drawString(cx0 + 3, y, line)
        y -= line_h
    entry_top = y + line_h - 2
    if entry_top - cy_bot < 8:
        return None
    widget = {"type": "text", "label": label,
              "rect": [cx0 + 2, cy_bot + 2, cx1 - 2, entry_top - 1]}
    return cy_bot, [widget]


def sec_left_label(c, rng, x0, x1, y_top, avail, style, tight=False):
    """Label in a cell to the LEFT of the entry cell, one row, standalone
    (no header row above it in either column). R2 skips the entry cell
    because it has no text of its own; R3 never fires because the entry
    column's own top cell is blank, so it never acquires a header."""
    row_h = rng.uniform(11, 13) if tight else rng.uniform(16, 30)
    if row_h > avail:
        return None
    label = rng.choice(LABEL_POOL)
    label_w = min(rng.uniform(70, 130), (x1 - x0) * 0.42)
    mid = x0 + label_w
    if x1 - mid < 40:
        return None
    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - row_h)
    _vrect(c, x0, y_top - row_h, y_top)
    _vrect(c, mid, y_top - row_h, y_top)
    _vrect(c, x1, y_top - row_h, y_top)
    size = 7 if tight else 9
    c.setFont(style.body_font, size)
    baseline = y_top - row_h / 2 - size * 0.35
    label_txt = label
    while stringWidth(label_txt, style.body_font, size) > label_w - 6 and len(label_txt) > 3:
        label_txt = label_txt[:-1]
    c.drawString(x0 + 3, baseline, label_txt)
    entry_top, entry_bot = y_top - 1, y_top - row_h + 1
    if entry_top - entry_bot < 8 or x1 - mid - 4 < 15.5:
        return None
    widget = {"type": "text", "label": label,
              "rect": [mid + 2, entry_bot, x1 - 2, entry_top]}
    return y_top - row_h, [widget]


def sec_ragged_table(c, rng, x0, x1, y_top, avail, style):
    """Column-headed table whose interior vertical edges jitter row to
    row (a hand-adjusted table, or one assembled from mismatched rows).
    R3 groups blank cells under a header by rounding each cell's left edge
    to the nearest point; jittered edges scatter each row into its own
    one-cell group, so the header is never inherited."""
    ncols = rng.randint(2, 3)
    headers = next((h for h in COLUMN_HEADER_SETS if len(h) == ncols), COLUMN_HEADER_SETS[0])
    header_h = 20.0
    row_h = rng.uniform(17, 23)
    nrows = rng.randint(3, 5)
    table_h = header_h + row_h * nrows
    if table_h > avail:
        nrows = int((avail - header_h) // row_h)
        if nrows < 3:
            return None
        table_h = header_h + row_h * nrows
    base_edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bottom = y_top - table_h

    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - header_h)
    for x in base_edges:
        _vrect(c, x, y_top - header_h, y_top)
    c.setFont(style.bold_font, 9)
    for ci, htext in enumerate(headers):
        c.drawString(base_edges[ci] + 3, y_top - header_h + 7, htext)

    widgets = []
    row_top = y_top - header_h
    for r in range(nrows):
        row_bot = row_top - row_h
        edges = [x0]
        for i in range(1, ncols):
            jitter = rng.uniform(-20, 20)
            edges.append(min(max(base_edges[i] + jitter, edges[-1] + 30), x1 - 30 * (ncols - i)))
        edges.append(x1)
        _hrect(c, x0, x1, row_top)
        if r == nrows - 1:
            _hrect(c, x0, x1, row_bot)
        for xx in edges:
            _vrect(c, xx, row_bot, row_top)
        for ci in range(ncols):
            cx0, cx1 = edges[ci], edges[ci + 1]
            if cx1 - cx0 < 20:
                continue
            widgets.append({"type": "text", "label": headers[ci],
                             "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2]})
        row_top = row_bot
    return y_bottom, widgets


def sec_spacer_column(c, rng, x0, x1, y_top, avail, style):
    """A normal column-headed table plus one extra narrow blank column
    labelled for office use. R3 has no notion of 'not a field' -- any
    blank cell under a header is claimed -- so this column is a guaranteed
    false-positive source, matching the real corpus's worst failure mode."""
    ncols = rng.randint(2, 3)
    headers = next((h for h in COLUMN_HEADER_SETS if len(h) == ncols), COLUMN_HEADER_SETS[0])
    spacer_w = rng.uniform(50, 70)
    header_h, row_h = 20.0, rng.uniform(18, 24)
    nrows = rng.randint(3, 5)
    table_h = header_h + row_h * nrows
    if table_h > avail:
        nrows = int((avail - header_h) // row_h)
        if nrows < 3:
            return None
        table_h = header_h + row_h * nrows
    data_w = (x1 - x0) - spacer_w
    edges = [x0 + data_w * i / ncols for i in range(ncols + 1)] + [x1]
    y_bottom = y_top - table_h

    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - header_h)
    for r in range(1, nrows + 1):
        _hrect(c, x0, x1, y_top - header_h - r * row_h)
    for xx in edges:
        _vrect(c, xx, y_bottom, y_top)

    c.setFont(style.bold_font, 9)
    for ci, htext in enumerate(headers):
        c.drawString(edges[ci] + 3, y_top - header_h + 7, htext)
    c.setFont(style.bold_font, 7)
    c.drawString(edges[ncols] + 3, y_top - header_h + 7, "Office Use")

    widgets = []
    for ci, htext in enumerate(headers):
        cx0, cx1 = edges[ci], edges[ci + 1]
        for r in range(nrows):
            row_top = y_top - header_h - r * row_h
            row_bot = row_top - row_h
            widgets.append({"type": "text", "label": htext,
                             "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2]})
    # spacer column cells deliberately get no widgets.
    return y_bottom, widgets


def sec_office_use_box(c, rng, x0, x1, y_top, avail, style):
    """A shaded, bordered, clearly-labelled 'FOR OFFICE USE ONLY' block.
    Its interior cells look exactly like an ordinary R2 labelled grid, so
    the detector will happily fill it in -- but the applicant must not,
    so truth has zero widgets here. Pure false-positive bait."""
    box_h = rng.uniform(64, 84)
    if box_h > avail:
        return None
    oy_top, oy_bot = y_top, y_top - box_h
    c.setFillColorRGB(0.90, 0.90, 0.90)
    c.rect(x0, oy_top - 14, x1 - x0, 14, stroke=0, fill=1)
    _hrect(c, x0, x1, oy_top)
    _hrect(c, x0, x1, oy_bot)
    _vrect(c, x0, oy_bot, oy_top)
    _vrect(c, x1, oy_bot, oy_top)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(style.bold_font, 9)
    c.drawCentredString((x0 + x1) / 2, oy_top - 10, "FOR OFFICE USE ONLY")

    ncols = 3
    edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    row_top = oy_top - 14
    for xx in edges:
        _vrect(c, xx, oy_bot, row_top)
    c.setFont(style.body_font, 8)
    for ci, htext in enumerate(OFFICE_LABELS):
        c.drawString(edges[ci] + 3, row_top - 10, htext)
    return oy_bot, []


def sec_small_checkbox_row(c, rng, x0, x1, y_top, avail, style):
    """Checkbox glyphs at a spread of sizes (biased large, per the brief's
    8-14pt range) plus small bordered squares below R6's 20-34pt window.
    R1 always fires on a glyph but with a *fixed* 10x10 detected rect, so
    larger glyphs place badly; the small bordered squares (15-19pt) are
    entirely below R6's floor and are missed outright."""
    row_h = 22.0
    if avail < row_h:
        return None
    q = rng.choice(QUESTION_POOL)
    size_txt = 10
    baseline = y_top - 14
    c.setFont(style.body_font, size_txt)
    c.drawString(x0, baseline, q)
    cursor_x = x0 + stringWidth(q, style.body_font, size_txt) + 18
    widgets = []
    use_glyph = rng.random() < 0.7
    for opt in ("Yes", "No"):
        if cursor_x > x1 - 60:
            break
        if use_glyph:
            size = rng.choice([11, 12, 13, 14, 14, 13, 9])
            font, glyph = rng.choice([("Webdings", WEBDINGS_CHAR), ("Wingdings", WINGDINGS_CHAR)])
            c.setFont(font, size)
            c.drawString(cursor_x, baseline, glyph)
            if font == "Webdings":
                gx0, gx1 = cursor_x, cursor_x + size
            else:
                gx0, gx1 = cursor_x, cursor_x + size * 0.8911
            gy0, gy1 = baseline - 0.205 * size, baseline + 0.8 * size
            if gx1 - gx0 < 15.5:
                pad = (15.5 - (gx1 - gx0)) / 2
                gx0, gx1 = gx0 - pad, gx1 + pad
            rect = [gx0, gy0, gx1, gy1]
            adv = size + 4
        else:
            side = rng.uniform(17.5, 19)
            cy0 = baseline - 3
            _hrect(c, cursor_x, cursor_x + side, cy0)
            _hrect(c, cursor_x, cursor_x + side, cy0 + side)
            _vrect(c, cursor_x, cy0, cy0 + side)
            _vrect(c, cursor_x + side, cy0, cy0 + side)
            rect = [cursor_x + 1, cy0 + 1, cursor_x + side - 1, cy0 + side - 1]
            adv = side + 4
        widgets.append({"type": "checkbox", "label": f"{q} ({opt})", "rect": rect})
        cursor_x += adv
        c.setFont(style.body_font, size_txt)
        c.drawString(cursor_x, baseline, opt)
        cursor_x += stringWidth(opt, style.body_font, size_txt) + 16
    return baseline - 10, widgets


def sec_prose_box(c, rng, x0, x1, y_top, avail, style):
    """Prose paragraph inside a bordered box. No fields; exists to add the
    load real forms have (instructional text boxed right next to fields)
    without itself being a trap."""
    nlines = rng.randint(2, 4)
    line_h = 12.0
    box_h = nlines * line_h + 12
    if box_h > avail:
        return None
    oy_top, oy_bot = y_top, y_top - box_h
    _hrect(c, x0, x1, oy_top)
    _hrect(c, x0, x1, oy_bot)
    _vrect(c, x0, oy_bot, oy_top)
    _vrect(c, x1, oy_bot, oy_top)
    italic_font = "Helvetica-Oblique" if style.body_font == "Helvetica" else "Times-Italic"
    c.setFont(italic_font, 9)
    y = oy_top - 12
    for _ in range(nlines):
        c.drawString(x0 + 6, y, _make_prose_line(rng, x1 - x0 - 12, style.body_font, 9))
        y -= line_h
    return oy_bot, []


def sec_underline_trap(c, rng, x0, x1, y_top, avail, style):
    """Heading whose decorative underline sits tight (4pt) above the next
    line of text, which starts at the same x -- exactly the situation
    R5b's 'unlabelled rules are decorative' guard is supposed to catch,
    except here the follow-on text gives it a label to misuse."""
    if avail < 40:
        return None
    y = _draw_heading(c, rng, x0, x1, y_top, style, tight_gap=True)
    label = rng.choice(LABEL_POOL)
    size = 9
    c.setFont(style.body_font, size)
    c.drawString(x0, y - 9, f"{label} of applicant, printed")
    return y - 20, []


def sec_merged_header_table(c, rng, x0, x1, y_top, avail, style):
    """A group header row with no internal divider (one wide cell), with a
    per-column caption typeset inside it at each data column's own x
    position -- exactly how real forms print a merged header band, tabbed
    or centred captions and no ruled sub-columns until the data rows begin.
    grid_cells needs an internal vertical rule to split a row into cells;
    there is none in the header band, so it reconstructs as a single wide
    cell. R3's column clustering assigns that cell (by its own left edge)
    to the leftmost data column only -- every other column never sees a
    header cell in its own group and its blank cells go unclaimed, even
    though a person reads the aligned caption above each column exactly as
    intended."""
    ncols = rng.choice([2, 3, 3])
    headers = next((h for h in COLUMN_HEADER_SETS if len(h) == ncols), COLUMN_HEADER_SETS[0])
    header_h = 20.0
    row_h = rng.uniform(18, 24)
    nrows = rng.randint(3, 5)
    table_h = header_h + row_h * nrows
    if table_h > avail:
        nrows = int((avail - header_h) // row_h)
        if nrows < 3:
            return None
        table_h = header_h + row_h * nrows
    edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bottom = y_top - table_h

    _hrect(c, x0, x1, y_top)
    header_bot = y_top - header_h
    _hrect(c, x0, x1, header_bot)
    _vrect(c, x0, y_bottom, y_top)   # outer verticals span the whole table,
    _vrect(c, x1, y_bottom, y_top)   # including the undivided header band
    c.setFont(style.bold_font, 9)
    for ci, htext in enumerate(headers):
        c.drawString(edges[ci] + 3, y_top - header_h + 7, htext)

    widgets = []
    row_top = header_bot
    for r in range(nrows):
        row_bot = row_top - row_h
        _hrect(c, x0, x1, row_bot)
        for xx in edges[1:-1]:
            _vrect(c, xx, row_bot, row_top)
        for ci in range(ncols):
            cx0, cx1 = edges[ci], edges[ci + 1]
            widgets.append({"type": "text", "label": headers[ci],
                             "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2]})
        row_top = row_bot
    return y_bottom, widgets


def sec_dotted_line(c, rng, x0, x1, y_top, avail, style):
    """A write-on line drawn as a dot leader (". . . . .") instead of
    underscores -- a common real-forms convention. R5 only recognises runs
    of literal underscore characters and R4 only fires inside a bordered
    grid cell; a free-floating dot leader with a label to its left matches
    neither rule, so nothing is ever proposed here even though the blank
    space after the label is exactly as obvious to a human as an
    underscore line would be."""
    row_h = 24.0
    if avail < row_h:
        return None
    size = 10
    label = rng.choice(LABEL_POOL)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    prefix = f"{label}: "
    c.drawString(x0, baseline, prefix)
    pw = stringWidth(prefix, style.body_font, size)
    line_x0 = x0 + pw
    max_w = (x1 - x0) - pw - 4
    if max_w < 30:
        return baseline - 10, []
    dot = ". "
    dw = stringWidth(dot, style.body_font, size)
    n = max(8, int((rng.uniform(0.4, 0.85) * max_w) / dw))
    leader = dot * n
    c.drawString(line_x0, baseline, leader)
    line_x1 = line_x0 + stringWidth(leader, style.body_font, size)
    if line_x1 - line_x0 < 15.5:
        return baseline - 10, []
    widget = {"type": "text", "label": label,
              "rect": [line_x0 + 1, baseline, line_x1 - 1, baseline + 12]}
    return baseline - 10, [widget]


def sec_bilingual_grid(c, rng, x0, x1, y_top, avail, style):
    """A labelled cell whose header prints an English caption followed by
    its Spanish translation on the same line -- e.g. "Reason for This
    Request / Motivo de Esta Solicitud" -- exactly the standard-practice
    look of a real bilingual benefits form. R2's label-length sanity cap
    (which exists to reject a whole paragraph mistaken for a caption)
    rejects the combined bilingual caption too, even though it reads as
    one short label in two languages sitting on a single line."""
    row_h = rng.uniform(28, 38)
    if row_h > avail:
        return None
    y_bot = y_top - row_h
    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_bot)
    _vrect(c, x0, y_bot, y_top)
    _vrect(c, x1, y_bot, y_top)
    size = 8
    en, es = rng.choice(BILINGUAL_PAIRS)
    label = f"{en} / {es}"
    c.setFont(style.body_font, size)
    label_baseline = y_top - 3 - size * 0.8
    fitted = label
    while stringWidth(fitted, style.body_font, size) > (x1 - x0 - 6) and len(fitted) > 3:
        fitted = fitted[:-1]
    c.drawString(x0 + 3, label_baseline, fitted)
    entry_top = label_baseline - size * 0.3
    if entry_top - y_bot < 11 or (x1 - x0 - 4) < 15.5:
        return y_bot, []
    widget = {"type": "text", "label": label,
              "rect": [x0 + 2, y_bot + 2, x1 - 2, entry_top - 1.5]}
    return y_bot, [widget]


def sec_nested_table(c, rng, x0, x1, y_top, avail, style):
    """An address block: one outer bordered cell containing a 1x3 nested
    table of narrow sub-fields (street / city / state+zip) whose own
    labels wrap to two lines at that width -- same defeat as
    sec_wrapped_label, but inside a nested grid instead of a flat one."""
    outer_h = rng.uniform(58, 70)
    if outer_h > avail:
        return None
    oy_top, oy_bot = y_top, y_top - outer_h
    _hrect(c, x0, x1, oy_top)
    _hrect(c, x0, x1, oy_bot)
    _vrect(c, x0, oy_bot, oy_top)
    _vrect(c, x1, oy_bot, oy_top)
    c.setFont(style.bold_font, 9)
    c.drawString(x0 + 4, oy_top - 10, "Current Address")
    nested_top = oy_top - 18
    _hrect(c, x0, x1, nested_top)
    n = 3
    edges = [x0 + (x1 - x0) * i / n for i in range(n + 1)]
    for xx in edges:
        _vrect(c, xx, oy_bot, nested_top)

    size = 7
    widgets = []
    for i, sub in enumerate(NESTED_SUBFIELDS):
        cx0, cx1 = edges[i], edges[i + 1]
        lines = _wrap_text(sub, style.body_font, size, cx1 - cx0 - 6)[:2]
        if len(lines) < 2:
            lines = [sub[:len(sub) // 2], sub[len(sub) // 2:]]
        c.setFont(style.body_font, size)
        yy = nested_top - 3 - size * 0.8
        for line in lines:
            c.drawString(cx0 + 3, yy, line)
            yy -= 8.5
        entry_top = yy + 8.5 - 1
        if entry_top - oy_bot < 8 or cx1 - cx0 - 4 < 15.5:
            continue
        widgets.append({"type": "text", "label": sub,
                         "rect": [cx0 + 2, oy_bot + 2, cx1 - 2, entry_top]})
    return oy_bot, widgets


def sec_label_below(c, rng, x0, x1, y_top, avail, style):
    """A bordered cell whose caption sits BELOW the blank writing area
    instead of above it -- a blank line captioned 'Print Name' or 'Date'
    along its own bottom edge, the way a signature block is laid out (minus
    the word 'signature', which the detector's own SIGNATURE guard drops
    outright). R2 and R12 both expect a label confined to the cell's TOP
    band with the blank space following it down to the cell's bottom edge;
    here the caption sits flush against the bottom border, so the space
    R2/R12 look for below the label is a sliver or nothing, and both
    reject -- even though the blank area above the caption is exactly as
    obvious to a person as one printed above would be."""
    row_h = rng.uniform(30, 46)
    if row_h > avail:
        return None
    label = rng.choice(LABEL_POOL)
    cell_w = min(rng.uniform(140, 220), x1 - x0)
    cx0, cx1 = x0, x0 + cell_w
    cy_top, cy_bot = y_top, y_top - row_h
    _hrect(c, cx0, cx1, cy_top)
    _hrect(c, cx0, cx1, cy_bot)
    _vrect(c, cx0, cy_bot, cy_top)
    _vrect(c, cx1, cy_bot, cy_top)
    size = 8
    c.setFont(style.body_font, size)
    baseline = cy_bot + 3
    c.drawString(cx0 + 3, baseline, label)
    entry_bot = baseline + size + 2
    if cy_top - 2 - entry_bot < 8 or cell_w - 4 < 15.5:
        return None
    widget = {"type": "text", "label": label,
              "rect": [cx0 + 2, entry_bot, cx1 - 2, cy_top - 2]}
    return cy_bot, [widget]


def sec_shaded_field(c, rng, x0, x1, y_top, avail, style):
    """A writing area that is a plain shaded rectangle with no ruled border
    at all -- fill only, no stroke, under a normal printed caption. A
    common look on forms designed to photocopy or fax cleanly, where a
    ruled box would reproduce as a smudge. grid_cells() only ever treats a
    rect as part of a table when it is THIN: height < 3 for a horizontal
    ruling line, width < 3 for a vertical one. A filled box with real
    height and width satisfies neither test, so it never becomes a cell
    and no cell-walking rule (R2, R3, R4, R6, R10, R12) can reach it --
    even though the tinted rectangle is genuinely drawn on the page and a
    person sees exactly where to write."""
    row_h = rng.uniform(20, 26)
    label_h = 12
    if row_h + label_h > avail:
        return None
    label = rng.choice(LABEL_POOL)
    cell_w = min(rng.uniform(120, 200), x1 - x0)
    size = 9
    c.setFont(style.body_font, size)
    label_baseline = y_top - size
    c.drawString(x0, label_baseline, label)
    box_top = label_baseline - 4
    box_bot = box_top - row_h
    if row_h - 4 < 8 or cell_w - 4 < 15.5:
        return None
    c.setFillColorRGB(0.85, 0.85, 0.85)
    c.rect(x0, box_bot, cell_w, row_h, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    widget = {"type": "text", "label": label,
              "rect": [x0 + 2, box_bot + 2, x0 + cell_w - 2, box_top - 2]}
    return box_bot, [widget]


def sec_circle_checkbox(c, rng, x0, x1, y_top, avail, style):
    """A checkbox drawn as a hollow circle instead of a square glyph or a
    ruled square cell. R1 only fires on the two specific Webdings/Wingdings
    checkbox glyphs; R6 only fires on a small near-square cell rebuilt from
    grid_cells() rects. reportlab draws a circle as a set of bezier curves,
    which pdfplumber reports under page.curves, not page.rects -- outside
    what either rule looks at, even though a hollow circle beside a
    caption reads exactly like a checkbox to a person filling out the
    form."""
    row_h = 22.0
    if avail < row_h:
        return None
    q = rng.choice(QUESTION_POOL)
    size_txt = 10
    baseline = y_top - 14
    c.setFont(style.body_font, size_txt)
    c.drawString(x0, baseline, q)
    cursor_x = x0 + stringWidth(q, style.body_font, size_txt) + 18
    widgets = []
    r = 9.0
    for opt in ("Yes", "No"):
        if cursor_x > x1 - 60:
            break
        cy = baseline + 3
        cx = cursor_x + r
        c.setLineWidth(0.9)
        c.circle(cx, cy, r, stroke=1, fill=0)
        rect = [cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1]
        widgets.append({"type": "checkbox", "label": f"{q} ({opt})", "rect": rect})
        cursor_x += 2 * r + 4
        c.setFont(style.body_font, size_txt)
        c.drawString(cursor_x, baseline, opt)
        cursor_x += stringWidth(opt, style.body_font, size_txt) + 16
    return baseline - 10, widgets


COMB_BOX_W = 18.0
COMB_MIN_N, COMB_MAX_N = 6, 10


def sec_comb_field(c, rng, x0, x1, y_top, avail, style):
    """A comb field: one box per character, divided by full-height tick
    marks spaced under grid_cells()'s own 20pt minimum-cell-width floor.
    Real forms use this for SSNs, phone numbers, ZIP codes -- a fixed
    number of ruled boxes under one caption above them. grid_cells() only
    calls the gap between two adjacent edges a cell when it exceeds 20pt;
    every gap here is 18pt, so the whole row yields zero cells and no
    cell-based rule (R2/R3/R4/R6/R10/R12) ever sees it, even though the
    boxes are fully ruled and the caption sits in the ordinary place,
    directly above."""
    row_h = 22.0
    label_h = 12
    if row_h + label_h > avail:
        return None
    n = rng.randint(COMB_MIN_N, COMB_MAX_N)
    field_w = n * COMB_BOX_W
    if field_w > x1 - x0:
        n = int((x1 - x0) // COMB_BOX_W)
        if n < COMB_MIN_N:
            return None
        field_w = n * COMB_BOX_W
    label = rng.choice(LABEL_POOL)
    size = 9
    c.setFont(style.body_font, size)
    c.drawString(x0, y_top - size, label)
    box_top = y_top - size - 4
    box_bot = box_top - row_h
    _hrect(c, x0, x0 + field_w, box_top)
    _hrect(c, x0, x0 + field_w, box_bot)
    for i in range(n + 1):
        _vrect(c, x0 + i * COMB_BOX_W, box_bot, box_top)
    widget = {"type": "text", "label": label,
              "rect": [x0 + 2, box_bot + 2, x0 + field_w - 2, box_top - 2]}
    return box_bot, [widget]


def sec_group_caption(c, rng, x0, x1, y_top, avail, style):
    """One instructional caption governs several separate blank tinted
    strips below it -- "List each additional household member below (one
    per line):" followed by three to five bare strips, none carrying its
    own label. Each strip is a plain shaded fill with no stroke at all,
    the same shape sec_shaded_field uses and for the same reason:
    grid_cells() only ever treats a rect as part of a table when it is
    THIN (height < 3 for a horizontal ruling line, width < 3 for a
    vertical one), so a strip with real height and width never becomes a
    cell and no cell-walking rule (R2, R3, R4, R10, R12, R14, R16) can
    reach it.

    Earlier drafts drew these as ordinary bordered boxes instead, on the
    theory that a floating caption above a column of blank ruled cells
    would defeat R2 (label must be in the SAME cell) and R10 (label must
    be in the cell to the LEFT). It did not: grid_cells() pairs each
    horizontal ruling line with the CLOSEST one below it with no cap on
    the gap between them, so the ruling line ending whatever construct
    drew immediately above paired straight down past the caption to the
    first box's own top edge, reconstructing one phantom cell that
    happened to contain the caption text -- and R3 then read it as a
    completely ordinary column header, inheriting it into every bordered
    box below at full accuracy (measured 81% recall in isolation). Shaded
    strips have no ruling line at all for anything to pair down to, which
    is what actually keeps the caption unclaimed."""
    caption = rng.choice(GROUP_CAPTIONS)
    size = 9
    lines = _wrap_text(caption, style.body_font, size, x1 - x0)[:2]
    cap_h = len(lines) * 11 + 6
    row_h, gap = 20.0, 6.0
    n = rng.randint(3, 5)
    table_h = n * row_h + (n - 1) * gap
    if cap_h + table_h > avail:
        n = int((avail - cap_h + gap) // (row_h + gap))
        if n < 3:
            return None
        table_h = n * row_h + (n - 1) * gap
    c.setFont(style.body_font, size)
    y = y_top - size
    for line in lines:
        c.drawString(x0, y, line)
        y -= 11
    strip_w = min(x1 - x0, 260)
    if strip_w - 4 < 15.5:
        return None
    row_top = y - 6
    widgets = []
    for _ in range(n):
        row_bot = row_top - row_h
        c.setFillColorRGB(0.85, 0.85, 0.85)
        c.rect(x0, row_bot, strip_w, row_h, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        widgets.append({"type": "text", "label": caption,
                         "rect": [x0 + 2, row_bot + 2, x0 + strip_w - 2, row_top - 2]})
        row_top = row_bot - gap
    y_bottom = row_top + gap
    return y_bottom, widgets


def sec_margin_caption(c, rng, x0, x1, y_top, avail, style):
    """A caption printed in the margin OUTSIDE a bordered writing box --
    rotated 90 degrees to fit a narrow gutter, the way a printed sidebar
    caption or a rotated column head is set on real forms to fit a tight
    space. The box carries no text of its own; the label sits well clear of
    its border, running bottom-to-top. R2 needs a label inside the SAME
    cell; R10 needs a labelled CELL immediately to the left, sharing the
    box's row bounds -- there is no rule drawn between the gutter and the
    box, so grid_cells() never reconstructs a left cell there for R10 to
    find, even though a person reads the sideways caption exactly as the
    box's name."""
    gutter_w = 16.0
    row_h = rng.uniform(30, 46)
    if row_h > avail or (x1 - x0) < gutter_w + 20:
        return None
    label = rng.choice(LABEL_POOL)
    size = 8
    cy_top, cy_bot = y_top, y_top - row_h
    box_x0 = x0 + gutter_w
    if x1 - box_x0 - 4 < 15.5 or row_h - 4 < 8:
        return None
    _hrect(c, box_x0, x1, cy_top)
    _hrect(c, box_x0, x1, cy_bot)
    _vrect(c, box_x0, cy_bot, cy_top)
    _vrect(c, x1, cy_bot, cy_top)
    fitted = label
    while stringWidth(fitted, style.body_font, size) > row_h - 6 and len(fitted) > 3:
        fitted = fitted[:-1]
    c.saveState()
    c.translate(x0 + size + 2, cy_bot + 3)
    c.rotate(90)
    c.setFont(style.body_font, size)
    c.drawString(0, 0, fitted)
    c.restoreState()
    widget = {"type": "text", "label": label,
              "rect": [box_x0 + 2, cy_bot + 2, x1 - 2, cy_top - 2]}
    return cy_bot, [widget]


def _shifted_edges(x0, x1, base_edges, rng):
    """Column edges spanning the same (x0, x1) with the same column COUNT
    as base_edges, but every internal boundary displaced by 30-55pt in a
    random direction -- comfortably past the detector's 16pt column-
    alignment tolerance for matching a continuation table's columns back to
    the header page's. Mirrors a real thing that happens across a page
    break: a table whose column widths are derived from its own content
    (not a fixed template) often reflows with different proportions on a
    later page even though it is unmistakably the same list to a reader."""
    n_internal = len(base_edges) - 2
    if n_internal <= 0:
        return list(base_edges)
    edges = [x0]
    prev = x0
    for i, e in enumerate(base_edges[1:-1]):
        direction = 1 if rng.random() < 0.5 else -1
        shifted = e + direction * rng.uniform(30, 55)
        lo = prev + 40
        hi = x1 - 40 * (n_internal - i)
        shifted = min(max(shifted, lo), hi)
        edges.append(shifted)
        prev = shifted
    edges.append(x1)
    return edges


# ---------------------------------------------------------------------------
# Continuation tables: a table's header prints once on the page where it
# starts, and rows that do not fit continue at the top of the next page
# with no header repeated -- a standard real multi-page-form convention
# ("continued" list, no re-printed column names). The detector runs one
# page at a time (engine/detect/rules.py:detect(page, pno)), so the header
# on page N is simply not visible while page N+1 is scored: R3 has no
# header cell to inherit for the continuation rows. This is dispatched
# directly from _draw_page/generate_hard (not through SECTION_POOL)
# because it must hand a "carry" spec across the c.showPage() boundary.
# ---------------------------------------------------------------------------

def _start_continuation_table(c, rng, x0, x1, y_top, avail, style):
    """Draw a normal, cleanly-divided header and a few data rows -- deliberately
    not the whole table -- and return a carry spec so the caller can continue
    the same columns, unheaded, at the top of the next page."""
    ncols = rng.randint(2, 3)
    headers = next((h for h in COLUMN_HEADER_SETS if len(h) == ncols), COLUMN_HEADER_SETS[0])
    header_h = 20.0
    row_h = rng.uniform(18, 24)
    max_rows = int((avail - header_h) // row_h)
    if max_rows < 2:
        return None
    nrows = min(max_rows, rng.randint(2, 4))
    edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bottom = y_top - header_h - nrows * row_h

    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - header_h)
    for xx in edges:
        _vrect(c, xx, y_bottom, y_top)
    c.setFont(style.bold_font, 9)
    for ci, htext in enumerate(headers):
        c.drawString(edges[ci] + 3, y_top - header_h + 7, htext)

    widgets = []
    row_top = y_top - header_h
    for r in range(nrows):
        row_bot = row_top - row_h
        _hrect(c, x0, x1, row_bot)
        for ci in range(ncols):
            cx0, cx1 = edges[ci], edges[ci + 1]
            widgets.append({"type": "text", "label": headers[ci],
                             "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2]})
        row_top = row_bot

    italic_font = "Helvetica-Oblique" if style.body_font == "Helvetica" else "Times-Italic"
    c.setFont(italic_font, 8)
    c.drawString(x0, y_bottom - 10, "(list continues on next page)")

    # Half the time, the continuation page reflows the same columns at
    # different widths -- see _shifted_edges above. That is a genuine real-
    # world layout behaviour, not a trick played on the header page itself:
    # the rows drawn here on THIS page use the original `edges` unchanged.
    shifted = rng.random() < 0.65
    next_edges = _shifted_edges(x0, x1, edges, rng) if shifted else edges
    carry = {"edges": next_edges, "row_h": row_h,
             "nrows": rng.randint(3, 6), "headers": headers, "shifted": shifted}
    return y_bottom - 18, widgets, carry


def _draw_carried_rows(c, x0, x1, y_top, style, widgets_out, carry):
    """Top-of-page continuation of a table whose header was on the previous
    page: same column edges, no header text, a small italic '(continued)'
    note above the first row -- exactly what a person sees leafing from one
    page of a real multi-page list to the next."""
    edges, row_h = carry["edges"], carry["row_h"]
    nrows, headers = carry["nrows"], carry["headers"]
    italic_font = "Helvetica-Oblique" if style.body_font == "Helvetica" else "Times-Italic"
    c.setFont(italic_font, 8)
    c.drawString(x0, y_top - 8, "(continued)")
    row_top = y_top - 14
    _hrect(c, x0, x1, row_top)
    for r in range(nrows):
        row_bot = row_top - row_h
        _hrect(c, x0, x1, row_bot)
        for xx in edges:
            _vrect(c, xx, row_bot, row_top)
        for ci in range(len(headers)):
            cx0, cx1 = edges[ci], edges[ci + 1]
            widgets_out.append({"type": "text", "label": headers[ci],
                                 "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2]})
        row_top = row_bot
    return row_top


# ---------------------------------------------------------------------------
# A modest fraction of ordinary, easily-detected constructs so the corpus
# still reads as a form a person fills out, not a pure adversarial puzzle.
# ---------------------------------------------------------------------------

def sec_legit_grid(c, rng, x0, x1, y_top, avail, style):
    row_h = rng.uniform(28, 38)
    ncols = rng.randint(2, 3)
    if row_h > avail:
        return None
    col_edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bot = y_top - row_h
    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_bot)
    for xx in col_edges:
        _vrect(c, xx, y_bot, y_top)
    widgets = []
    size = 9
    for ci in range(ncols):
        cx0, cx1 = col_edges[ci], col_edges[ci + 1]
        label = rng.choice(LABEL_POOL)
        c.setFont(style.body_font, size)
        label_baseline = y_top - 3 - size * 0.8
        c.drawString(cx0 + 3, label_baseline, label)
        entry_top = label_baseline - size * 0.3
        if entry_top - y_bot < 11:
            continue
        widgets.append({"type": "text", "label": label, "expects_rule": "R2",
                         "rect": [cx0 + 2, y_bot + 2, cx1 - 2, entry_top - 1.5]})
    return y_bot, widgets


def sec_legit_checkbox(c, rng, x0, x1, y_top, avail, style):
    row_h = 22.0
    if avail < row_h:
        return None
    size = 10
    q = rng.choice(QUESTION_POOL)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    c.drawString(x0, baseline, q)
    cursor_x = x0 + stringWidth(q, style.body_font, size) + 18
    widgets = []
    for opt in ("Yes", "No"):
        if cursor_x > x1 - 60:
            break
        font, glyph = rng.choice([("Webdings", WEBDINGS_CHAR), ("Wingdings", WINGDINGS_CHAR)])
        c.setFont(font, size)
        c.drawString(cursor_x, baseline, glyph)
        if font == "Webdings":
            gx0, gx1 = cursor_x, cursor_x + size
        else:
            gx0, gx1 = cursor_x, cursor_x + size * 0.8911
        if gx1 - gx0 < 15.5:
            pad = (15.5 - (gx1 - gx0)) / 2
            gx0, gx1 = gx0 - pad, gx1 + pad
        rect = [gx0, baseline - 2.05, gx1, baseline + 7.95]
        widgets.append({"type": "checkbox", "label": f"{q} ({opt})",
                         "expects_rule": "R1", "rect": rect})
        cursor_x += size + 4
        c.setFont(style.body_font, size)
        c.drawString(cursor_x, baseline, opt)
        cursor_x += stringWidth(opt, style.body_font, size) + 16
    return baseline - 10, widgets


def sec_legit_line(c, rng, x0, x1, y_top, avail, style):
    row_h = 24.0
    if avail < row_h:
        return None
    size = 10
    label = rng.choice(LABEL_POOL)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    prefix = f"{label}: "
    c.drawString(x0, baseline, prefix)
    pw = stringWidth(prefix, style.body_font, size)
    line_x0 = x0 + pw
    max_w = (x1 - x0) - pw - 4
    if max_w < 30:
        return baseline - 10, []
    uw = stringWidth("_", style.body_font, size)
    n = max(6, int((rng.uniform(0.4, 0.85) * max_w) / uw))
    underscores = "_" * n
    c.drawString(line_x0, baseline, underscores)
    line_x1 = line_x0 + stringWidth(underscores, style.body_font, size)
    widget = {"type": "text", "label": label, "expects_rule": "R5",
              "rect": [line_x0 + 1, baseline, line_x1 - 1, baseline + 12]}
    return baseline - 10, [widget]


def sec_gutter_left_label(c, rng, x0, x1, y_top, avail, style):
    """Like sec_left_label, but with a third, wholly blank ruled cell
    inserted BETWEEN the label cell and the entry cell -- a narrow, purely
    decorative gutter column with no text of its own at all, drawn so that
    every row's entry box starts at the same x regardless of how long that
    row's own label is (a fixed-width template column rather than one sized
    to fit the label).

    R10 reads a blank cell's label from "the cell to the left, sharing this
    row's exact top/bottom" -- but it takes the CLOSEST such cell, not the
    nearest one with actual text, and it places NO upper bound on how far
    away that cell may sit. An early version of this construct picked the
    gutter's width from the same range grid_cells() itself uses as its own
    cell-width floor (20pt) and measured only 30% recall on the intent --
    because three-quarters of the time the gutter fell narrower than 20pt,
    grid_cells() never turned it into a cell of its own at all, and R10's
    "closest cell to the left" then resolved straight past the empty gap to
    the real label cell, exactly as if the gutter were not there. The gutter
    width here (21-24pt) is chosen to sit ABOVE grid_cells()'s 20pt cell
    floor (so it reliably becomes its own real, blank cell) but BELOW R10's
    own 25pt floor for how wide a candidate field may be (so R10 never tries
    to treat the gutter as a field in its own right) -- guaranteeing the
    gutter is always the nearest real cell to the entry box's left, and it
    always carries no text, so R10 gives up right there
    (`if not linside: continue`) without ever looking one cell further left
    to the real label. A person reading the row does not stop at the
    gutter -- they read the label two cells over and write in the box at the
    end of the row exactly the same as if the gutter were not there.

    Real form family: forms built from a rigid multi-column grid template
    where a fixed blank column keeps every entry box's left edge aligned
    down the page no matter how long each row's own label runs -- a common
    layout choice on typeset (not hand-drawn) government and utility forms,
    where "Name" and "Complete Mailing Address" sit in the same-width label
    column and the entry boxes still all start at the same x.
    """
    row_h = rng.uniform(16, 26)
    if row_h > avail:
        return None
    label = rng.choice(LABEL_POOL)
    label_w = min(rng.uniform(70, 120), (x1 - x0) * 0.35)
    gutter_w = rng.uniform(21, 24)
    entry_x0 = x0 + label_w + gutter_w
    if x1 - entry_x0 < 40:
        return None
    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - row_h)
    _vrect(c, x0, y_top - row_h, y_top)
    _vrect(c, x0 + label_w, y_top - row_h, y_top)
    _vrect(c, entry_x0, y_top - row_h, y_top)
    _vrect(c, x1, y_top - row_h, y_top)
    size = 9
    c.setFont(style.body_font, size)
    baseline = y_top - row_h / 2 - size * 0.35
    label_txt = label
    while stringWidth(label_txt, style.body_font, size) > label_w - 6 and len(label_txt) > 3:
        label_txt = label_txt[:-1]
    c.drawString(x0 + 3, baseline, label_txt)
    entry_top, entry_bot = y_top - 1, y_top - row_h + 1
    if entry_top - entry_bot < 8 or x1 - entry_x0 - 4 < 15.5:
        return None
    widget = {"type": "text", "label": label,
              "rect": [entry_x0 + 2, entry_bot, x1 - 2, entry_top]}
    return y_top - row_h, [widget]


def sec_bracket_checkbox(c, rng, x0, x1, y_top, avail, style):
    """A checkbox drawn as a literal ASCII bracket pair "[ ]" immediately
    followed by its option text -- "[ ] Yes   [ ] No" -- the standard
    plain-text/typewriter checkbox convention used wherever no special
    box-drawing glyph or vector graphic is available. R1 only fires on the
    two specific Webdings/Wingdings checkbox glyph codepoints in
    CHECK_GLYPHS; "[" and "]" are ordinary ASCII characters and are not
    members of that set. No rect is drawn at all -- this is plain text, the
    same three characters a "the" or a "and" is made of -- so no cell-
    walking rule (R2/R3/R4/R10/R12/R14/R16/R17) can reach it either, even
    though a person reads the bracket pair as a checkbox exactly as readily
    as a glyph or a drawn square.

    Real form family: plain-text and typewriter-style intake forms, faxed
    checklists, and forms transcribed to accessible plain text for screen
    readers, all of which render "[ ] Option" as their checkbox in place of
    a special glyph or a drawn box.
    """
    row_h = 22.0
    if avail < row_h:
        return None
    q = rng.choice(QUESTION_POOL)
    size_txt = 10
    baseline = y_top - 14
    c.setFont(style.body_font, size_txt)
    c.drawString(x0, baseline, q)
    cursor_x = x0 + stringWidth(q, style.body_font, size_txt) + 18
    widgets = []
    for opt in ("Yes", "No"):
        if cursor_x > x1 - 60:
            break
        box_txt = "[ ]"
        c.setFont(style.body_font, size_txt)
        c.drawString(cursor_x, baseline, box_txt)
        box_w = stringWidth(box_txt, style.body_font, size_txt)
        gx0, gx1 = cursor_x, cursor_x + box_w
        if gx1 - gx0 < 15.5:
            pad = (15.5 - (gx1 - gx0)) / 2
            gx0, gx1 = gx0 - pad, gx1 + pad
        rect = [gx0, baseline - 2.05, gx1, baseline + 7.95]
        widgets.append({"type": "checkbox", "label": f"{q} ({opt})", "rect": rect})
        cursor_x += box_w + 6
        c.setFont(style.body_font, size_txt)
        c.drawString(cursor_x, baseline, opt)
        cursor_x += stringWidth(opt, style.body_font, size_txt) + 16
    return baseline - 10, widgets


SHORT_FIELD_LABELS = ["Middle Initial", "Suffix", "Apartment Number", "Unit"]


def sec_short_underscore_field(c, rng, x0, x1, y_top, avail, style):
    """A write-on line only 4 underscore characters wide -- enough for a
    single letter, a check digit, or a short suffix, as real forms draw for
    "M.I. ____" (middle initial), an apartment/unit letter, or a name
    suffix. R5's own underscore-run scan requires the drawn run's total
    width to be at least 25pt (a guard against catching decorative
    underscore use, e.g. mid-sentence emphasis in a running-text false-
    positive it was built to reject); a genuine one-character field draws a
    run of about 20-22pt and is silently skipped by that same floor, even
    though a person reads the short blank after the label exactly as
    obviously as a longer line.

    Real form family: "Middle Initial" and suffix fields on government ID,
    tax, and voter-registration forms, almost always drawn as a single
    short underscore blank rather than a full-width line.
    """
    row_h = 24.0
    if avail < row_h:
        return None
    size = 10
    label = rng.choice(SHORT_FIELD_LABELS)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    prefix = f"{label}: "
    c.drawString(x0, baseline, prefix)
    pw = stringWidth(prefix, style.body_font, size)
    line_x0 = x0 + pw
    if line_x0 + 25 > x1:
        return None
    underscores = "____"   # 4 chars: well under R5's own 25pt run-width floor
    uw = stringWidth(underscores, style.body_font, size)
    if uw >= 24:
        return None
    c.drawString(line_x0, baseline, underscores)
    line_x1 = line_x0 + uw
    if line_x1 - line_x0 - 2 < 15.5:
        return baseline - 10, []
    widget = {"type": "text", "label": label,
              "rect": [line_x0 + 1, baseline, line_x1 - 1, baseline + 12]}
    return baseline - 10, [widget]


def sec_whitespace_field(c, rng, x0, x1, y_top, avail, style):
    """A label followed by nothing but printed blank space -- no underscore
    run, no dot leader, no ruled line, no box of any kind -- before either
    the next inline label or the row's own right margin. The oldest,
    plainest write-on convention there is: "Name:                City:",
    where the applicant writes directly in the gap after the colon, the way
    a typewriter-era form or a simple carbon-copy intake sheet is laid out.

    Every current write-on-line rule needs something DRAWN to key off: R5
    scans literal underscore characters, R5b scans thin drawn rects, R11
    scans dot-leader characters, and R2/R3/R4/R10/R12/R14/R16/R17 all walk
    grid_cells(), which is built entirely from rects. This construct draws
    no rect at all and no fill-character of any kind -- only two ordinary
    strings on one line with a gap between them -- so none of those signals
    exists anywhere near the field, even though a person reads the blank
    stretch after "Name:" exactly as obviously as an underscore line.

    Real form family: plain text-only forms common on older or low-budget
    government and clinic intake sheets, and on forms transcribed to plain
    text/typewriter layout, where two or more short fields share one line
    as "Label:  <blank>   Label:  <blank>" with no rule drawn anywhere.
    """
    row_h = 22.0
    if avail < row_h:
        return None
    size = 10
    label = rng.choice(LABEL_POOL)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    prefix = f"{label}: "
    c.drawString(x0, baseline, prefix)
    pw = stringWidth(prefix, style.body_font, size)
    gap_start = x0 + pw
    max_total = x1 - x0 - pw
    if max_total < 30:
        return baseline - 10, []
    widgets = []
    if rng.random() < 0.5 and max_total > 220:
        label2 = rng.choice(LABEL_POOL)
        gap1_w = rng.uniform(60, 120)
        gap1_end = gap_start + gap1_w
        suffix2 = f"{label2}: "
        c.setFont(style.body_font, size)
        c.drawString(gap1_end, baseline, suffix2)
        pw2 = stringWidth(suffix2, style.body_font, size)
        gap2_start = gap1_end + pw2
        if gap1_w - 4 >= 15.5:
            widgets.append({"type": "text", "label": label,
                             "rect": [gap_start + 1, baseline - 1, gap1_end - 3, baseline + 11]})
        if x1 - gap2_start - 4 >= 15.5:
            widgets.append({"type": "text", "label": label2,
                             "rect": [gap2_start + 1, baseline - 1, x1 - 3, baseline + 11]})
    elif max_total - 4 >= 15.5:
        widgets.append({"type": "text", "label": label,
                         "rect": [gap_start + 1, baseline - 1, x1 - 3, baseline + 11]})
    return baseline - 10, widgets


# Weighted pool: hard constructs dominate, a legit minority keeps the form
# readable as a form rather than a pure adversarial exercise.
#
# sec_left_label, sec_dotted_line and sec_merged_header_table were the
# highest-yield misses in the previous round (R10, R11 and the group-header
# split were added to the detector specifically because this corpus exposed
# them). The detector now reads all three, so they no longer carry the
# difficulty their comments describe -- kept in the pool at base weight for
# page variety and because sec_spacer_column's "office use" bait and
# sec_left_label's tight-row variant are still occasionally missed, but no
# longer boosted.
HARD_FUNCS = [
    sec_wrapped_label, sec_left_label, sec_ragged_table, sec_spacer_column,
    sec_office_use_box, sec_small_checkbox_row, sec_prose_box,
    sec_underline_trap, sec_nested_table, sec_merged_header_table,
    sec_dotted_line, sec_bilingual_grid,
    sec_label_below, sec_shaded_field, sec_circle_checkbox, sec_comb_field,
    sec_group_caption, sec_margin_caption,
    sec_gutter_left_label, sec_whitespace_field,
    sec_bracket_checkbox, sec_short_underscore_field,
]
# A modest boost for the sources that measure as the current blind spots:
# the four constructs (label below its box, an unruled shaded entry area, a
# circle checkbox, a comb field -- see each function's own docstring for
# which detector assumption it falls outside of), three older constructs
# still missed (wrapped_label, nested_table, bilingual_grid all defeat R2's
# label-shape and length guards), and the two newest additions -- a caption
# governing a GROUP of separate blank boxes (sec_group_caption) and a
# rotated caption sitting outside a box's own border in an unbordered
# margin gutter (sec_margin_caption). Both new ones were added because the
# corpus's own tripwire fired: prior difficulty had been largely absorbed
# by R3's column clustering, R11's dot leaders and the group-header split
# (see below), so the gradient needed fresh constructs the detector's
# cell-, column- and left-neighbour-based rules do not reach at all.
#
# Unlike the previous round, this weighting no longer needs to be timid
# about presence: _active_pool already controls, per form, which features
# can appear at all, so a weight here only shapes how many TIMES an
# already-eligible feature repeats within a form that has it active -- it
# cannot push a feature into a form that excluded it, and so cannot by
# itself push presence over the variety cap. Measured per-feature in
# isolation (an active pool of just that one function), sec_label_below,
# sec_shaded_field, sec_circle_checkbox, sec_comb_field, sec_wrapped_label,
# sec_nested_table, sec_bilingual_grid, sec_group_caption and
# sec_margin_caption all sit at 20% recall or under -- the detector
# essentially never reconstructs them -- while sec_dotted_line (96%),
# sec_ragged_table (85%), sec_small_checkbox_row (67%) and
# sec_merged_header_table (70%) are now largely or partly read by R11, the
# 16pt column clustering, and the group-header split respectively. The boost
# below is concentrated on the still-effective nine and left off the
# now-mostly-handled ones, so they carry the extra density.
#
# Two more constructs were added because those nine, plus the already-solved
# four (dotted_line, ragged_table, merged_header_table, sec_label_below --
# see R11, the 16pt column clustering, the group-header split and R17), left
# the corpus's difficulty resting on an ever-narrower set of R2-label-shape
# tricks. sec_gutter_left_label and sec_whitespace_field instead target R10's
# closest-left-neighbour assumption and the write-on-line rules' shared
# assumption that SOMETHING is drawn (a rect, an underscore, a dot)
# respectively -- two different structural assumptions, not more variations
# on label shape. A third candidate, a single-column ruled list with its
# caption floating above it (meant to target R3's floating-header
# corroboration requirement), was designed, measured, and DROPPED: see
# docs/tuning/log.md's account of this round for why it does not survive
# contact with grid_cells()'s own unbounded rule-pairing.
#
# sec_gutter_left_label is now ALSO spent: a later fix taught R10 to read
# past a blank left-neighbour cell to the real label beyond it (isolated
# recall 5.8% -> 90.8%), the same way dotted_line/ragged_table/
# merged_header_table/sec_label_below were solved before it. Its boost is
# removed here for the same reason theirs was removed above -- kept in
# HARD_FUNCS at base weight for page variety, no longer boosted.
#
# Two new constructs added this round, both measured (see
# eval/synth/test_hard.py's isolation harness / docs/tuning/log.md) at
# well under 10% isolated recall, so both keep the boost alongside
# sec_whitespace_field:
#
# sec_bracket_checkbox -- a checkbox drawn as the literal ASCII text
# "[ ]" instead of a glyph or a drawn shape. R1 only matches the two
# specific Webdings/Wingdings codepoints; "[" and "]" are ordinary text
# characters and draw no rect at all, so no cell-walking rule reaches it
# either. Isolated recall 5.0%.
#
# sec_short_underscore_field -- an underscore write-on line only 4
# characters wide (~20-22pt), as real forms draw for a "Middle Initial"
# or suffix blank. R5's own underscore-run scan requires a run at least
# 25pt wide; a genuine single-character field falls under that floor.
# Isolated recall 8.4%.
EXTRA_WEIGHT = ([sec_label_below] * 12 + [sec_shaded_field] * 12
                + [sec_circle_checkbox] * 12 + [sec_comb_field] * 12
                + [sec_wrapped_label] * 10 + [sec_nested_table] * 10
                + [sec_bilingual_grid] * 10
                + [sec_group_caption] * 22 + [sec_margin_caption] * 22
                + [sec_whitespace_field] * 22
                + [sec_bracket_checkbox] * 22 + [sec_short_underscore_field] * 22)
LEGIT_FUNCS = [sec_legit_grid, sec_legit_checkbox, sec_legit_line]

# How many of the 40-ish placement attempts a column gets, and how many
# distinct hard constructs are even eligible in one form, together decide
# how "presence" (does this form contain the feature at all?) behaves. With
# every one of the ~20 hard functions offered on every attempt across up to
# 3 pages, nearly all of them get drawn at least once per form purely from
# volume -- measured, this saturated every feature above 70% of forms, no
# matter how the weights were balanced (weight only shapes how often an
# ALREADY-eligible function fires, not whether it ever gets a look-in).
#
# The fix is to restrict which functions are even eligible, per form: each
# form draws a random subset of HARD_FUNCS (see _active_pool below) instead
# of offering the whole roster to every attempt. A feature missing from a
# form's subset cannot appear in it at all, so a feature's share of the
# 25-form corpus is controlled directly by ACTIVE_FRACTION rather than by
# how many draws a column happens to make.
ACTIVE_MIN, ACTIVE_MAX = 9, 12   # of 20 HARD_FUNCS made eligible per form


def _active_pool(rng):
    """One form's eligible section pool: a random subset of HARD_FUNCS (kept
    whole for LEGIT_FUNCS, which are not difficulty and stay available to
    every form) plus the weighted extra turns for whichever of the boosted
    functions happen to be in this form's subset."""
    k = rng.randint(ACTIVE_MIN, ACTIVE_MAX)
    active = set(rng.sample(HARD_FUNCS, k))
    weighted = [f for f in HARD_FUNCS if f in active] * 3
    weighted += [f for f in EXTRA_WEIGHT if f in active]
    return weighted + LEGIT_FUNCS


def _run_section(fn, c, rng, x0, x1, y, avail, style):
    if fn is sec_left_label:
        return fn(c, rng, x0, x1, y, avail, style, tight=rng.random() < 0.4)
    return fn(c, rng, x0, x1, y, avail, style)


def _tag_for(fn):
    return fn.__name__[len("sec_"):]


def _fill_column(c, rng, x0, x1, y_top, y_floor, style, widgets_out, tags, pool):
    y = y_top
    if y - y_floor > 60 and rng.random() < 0.3:
        y = _draw_heading(c, rng, x0, x1, y, style)
        tags.add("heading")
    attempts = 0
    while y - y_floor > 30 and attempts < 40:
        attempts += 1
        fn = rng.choice(pool)
        result = _run_section(fn, c, rng, x0, x1, y, y - y_floor, style)
        if result is None:
            continue
        new_y, widgets = result
        if new_y >= y - 1:
            continue
        widgets_out.extend(widgets)
        tags.add(_tag_for(fn))
        y = new_y - rng.uniform(6, 14)


def _draw_page(c, rng, widgets_out, tags, page_w, page_h, pool,
               carry_in=None, forced_margin=None, allow_continuation_start=False):
    """Returns (new_carry, next_page_forced_margin). new_carry is not None
    only when this page starts a continuation table that must resume,
    unheaded, at the top of the next page -- see the continuation-table
    block above sec_legit_grid. Carries are single-hop: a page consuming
    carry_in never itself starts a new one, so there is at most one
    outstanding carry at a time."""
    margin = forced_margin if forced_margin is not None else rng.choice([36, 45, 54])
    body_font = rng.choice(BODY_FONTS)
    style = Style(body_font)
    y_top = page_h - margin
    y_floor = margin

    if carry_in is not None:
        y_top = _draw_carried_rows(c, margin, page_w - margin, y_top, style, widgets_out, carry_in)
        tags.add("continuation")
        if carry_in.get("shifted"):
            tags.add("continuation_shift")

    new_carry = None
    if (carry_in is None and allow_continuation_start
            and (y_top - y_floor) > 90 and rng.random() < 0.65):
        result = _start_continuation_table(c, rng, margin, page_w - margin, y_top,
                                            y_top - y_floor, style)
        if result is not None:
            new_y, widgets, spec = result
            widgets_out.extend(widgets)
            tags.add("continuation_start")
            y_top = new_y - rng.uniform(6, 14)
            new_carry = spec

    if carry_in is None and new_carry is None and rng.random() < 0.3:
        gutter = 18
        col_w = (page_w - 2 * margin - gutter) / 2
        _fill_column(c, rng, margin, margin + col_w, y_top, y_floor, style, widgets_out, tags, pool)
        _fill_column(c, rng, margin + col_w + gutter, page_w - margin, y_top, y_floor,
                     style, widgets_out, tags, pool)
        tags.add("twocol")
    else:
        _fill_column(c, rng, margin, page_w - margin, y_top, y_floor, style, widgets_out, tags, pool)

    next_forced_margin = margin if new_carry is not None else None
    return new_carry, next_forced_margin


def generate_hard(seed: int, out_dir) -> tuple:
    """Generate one hard-mode synthetic form. Returns (pdf_path, truth_json_path).

    Deterministic: the same seed always produces byte-identical output.
    """
    _register_fonts()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    num_pages = rng.randint(1, 3)
    pool = _active_pool(rng)      # this form's eligible subset -- see _active_pool

    # Decide every page's orientation up front (rng draws stay in page order)
    # so a continuation table can check whether the next page matches the
    # current one before committing to carry column edges across the
    # showPage() boundary.
    page_specs = []
    for pno in range(1, num_pages + 1):
        landscape = num_pages > 1 and rng.random() < 0.15
        page_w, page_h = (PAGE_H, PAGE_W) if landscape else (PAGE_W, PAGE_H)
        page_specs.append((page_w, page_h, landscape))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), invariant=1, pageCompression=1)

    all_widgets = []
    tags = set()
    page_dims = []
    carry = None
    forced_margin = None
    for pno in range(1, num_pages + 1):
        page_w, page_h, landscape = page_specs[pno - 1]
        c.setPageSize((page_w, page_h))
        page_dims.append((pno, page_w, page_h))
        if landscape:
            tags.add("landscape")
        has_next = pno < num_pages
        next_same_orientation = has_next and page_specs[pno][:2] == (page_w, page_h)
        page_widgets = []
        carry, forced_margin = _draw_page(
            c, rng, page_widgets, tags, page_w, page_h, pool,
            carry_in=carry, forced_margin=forced_margin,
            allow_continuation_start=has_next and next_same_orientation,
        )
        for w in page_widgets:
            w["page"] = pno
        all_widgets.extend(page_widgets)
        if pno < num_pages:
            c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()

    stem = f"hard_{seed:05d}"
    pdf_path = out_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    truth = {
        "version": 1,
        "source_pdf": pdf_path.name,
        "origin": "synthetic",
        "family": "synthetic-hard/" + ("-".join(sorted(tags)) if tags else "blank"),
        "pages": [{"page": pno, "width": w, "height": h} for pno, w, h in page_dims],
        "widgets": all_widgets,
    }
    truth_path = out_dir / f"{stem}.json"
    truth_path.write_text(json.dumps(truth, indent=2))
    return pdf_path, truth_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--out", default="eval/corpus/hard")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    for i in range(args.count):
        pdf_path, truth_path = generate_hard(args.seed + i, args.out)
        print(f"{pdf_path.name}  {truth_path.name}")


if __name__ == "__main__":
    main()
