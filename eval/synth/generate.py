"""Synthetic Word-exported flat form generator.

Produces PDFs that mimic the structural fingerprints of real government forms
exported from Microsoft Word (thin filled-rect table borders, Webdings/Wingdings
checkbox glyphs, underscore write-on lines) together with a perfect answer key
in the shape of eval/contracts/truth.schema.json.

Deterministic: generate(seed, ...) always produces the same bytes for the same
seed. All randomness comes from a local random.Random(seed) instance; nothing
reads the wall clock, environment, or global random state.
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

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, PDF points

# Webdings/Wingdings render checkbox glyphs when text using char code 0xf063 /
# 0xf06f is drawn with these fonts (the PUA codepoints a Word-exported PDF
# also uses). Measured empirically at font size 10:
#   Webdings  'c' (): width == size,          baseline-0.2*size .. +0.8*size
#   Wingdings 'o' (): width == 0.8911*size,   baseline-0.205*size .. +0.795*size
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
    "Name", "Date of Birth", "Social Security Number", "Mailing Address",
    "City", "State", "ZIP Code", "Phone Number", "Email Address",
    "Employer Name", "Occupation", "Marital Status", "Number of Dependents",
    "Annual Income", "Date", "Account Number", "Reference Number",
    "Case Number", "County", "Country of Citizenship",
    "Relationship to Applicant", "Emergency Contact", "Policy Number",
    "Middle Initial", "Apartment Number", "Daytime Telephone",
]

QUESTION_POOL = [
    "Are you a U.S. citizen?", "Have you filed a return this year?",
    "Do you have dependents?", "Are you currently employed?",
    "Is this your primary residence?", "Have you received benefits before?",
    "Are you self-employed?", "Do you own real property?",
    "Is the applicant deceased?", "Are you married?",
    "Do you receive Social Security benefits?", "Has your address changed?",
]

COLUMN_HEADER_SETS = [
    ["Item", "Quantity", "Amount"],
    ["Date", "Description", "Amount"],
    ["Name", "Relationship", "Age"],
    ["Employer", "Position", "Dates of Employment"],
    ["Source", "Amount", "Frequency"],
    ["Dependent Name", "SSN", "Relationship", "Months in Home"],
    ["Account", "Institution"],
]

HEADING_POOL = [
    "SECTION A - PERSONAL INFORMATION", "SECTION B - EMPLOYMENT HISTORY",
    "PART 1: ELIGIBILITY", "PART 2: CERTIFICATION", "GENERAL INSTRUCTIONS",
    "APPLICANT INFORMATION", "HOUSEHOLD COMPOSITION", "INCOME AND RESOURCES",
]

PROSE_WORDS = (
    "the applicant must complete every section of this form in ink and "
    "return it to the office within thirty days of the date shown above "
    "failure to provide the requested information may delay or result in "
    "denial of the benefits described in this application all information "
    "provided is subject to verification by the agency and any person who "
    "knowingly makes a false statement may be subject to penalties under "
    "applicable law please read the instructions carefully before signing "
    "retain a copy of this document for your records and contact the "
    "helpline if you have questions about eligibility or required proof"
).split()

BODY_FONTS = ["Helvetica", "Times-Roman"]
BOLD_OF = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold"}


class Style:
    def __init__(self, body_font):
        self.body_font = body_font
        self.bold_font = BOLD_OF[body_font]


def _hrect(c, x0, x1, y, thickness=0.75):
    """Thin filled horizontal rect: a Word table border, not a drawn line."""
    c.setFillColorRGB(0, 0, 0)
    c.rect(x0, y - thickness / 2, x1 - x0, thickness, stroke=0, fill=1)


def _vrect(c, x, y0, y1, thickness=0.75):
    c.setFillColorRGB(0, 0, 0)
    c.rect(x - thickness / 2, y0, thickness, y1 - y0, stroke=0, fill=1)


def _draw_heading(c, rng, x0, x1, y_top, style):
    """Section heading with a decorative underline. NOT a field: no truth widget."""
    heading = rng.choice(HEADING_POOL)
    size = 12
    baseline = y_top - size
    c.setFont(style.bold_font, size)
    c.drawString(x0, baseline, heading)
    w = min(stringWidth(heading, style.bold_font, size), x1 - x0)
    _hrect(c, x0, x0 + w, baseline - 3)
    return baseline - 3 - 14


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


# ---------------------------------------------------------------------------
# Section generators. Each returns (new_y_top, widgets) or None if it does not
# fit in `avail` points of vertical space. `widgets` entries omit "page" -
# the caller fills that in. Coordinates are PDF points, origin bottom-left,
# matching truth.schema.json directly (reportlab is already bottom-up).
# ---------------------------------------------------------------------------

def sec_labelled_grid(c, rng, x0, x1, y_top, avail, style):
    ncols = rng.randint(2, 4)
    nrows = rng.randint(1, 3)
    row_h = rng.uniform(28, 42)
    if row_h * nrows > avail:
        nrows = max(1, int(avail // row_h))
    if nrows < 1 or row_h * nrows > avail:
        return None
    table_h = row_h * nrows
    col_edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bottom = y_top - table_h

    for r in range(nrows + 1):
        _hrect(c, x0, x1, y_top - r * row_h)
    for x in col_edges:
        _vrect(c, x, y_bottom, y_top)

    widgets = []
    size = 9
    for r in range(nrows):
        row_top = y_top - r * row_h
        row_bot = row_top - row_h
        for ci in range(ncols):
            cx0, cx1 = col_edges[ci], col_edges[ci + 1]
            label = rng.choice(LABEL_POOL)
            c.setFont(style.body_font, size)
            label_baseline = row_top - 3 - size * 0.8
            c.drawString(cx0 + 3, label_baseline, label)
            entry_top = label_baseline - size * 0.3
            if entry_top - row_bot < 11:
                continue
            widgets.append({
                "type": "text", "label": label, "expects_rule": "R2",
                "rect": [cx0 + 2, row_bot + 2, cx1 - 2, entry_top - 1.5],
            })
    return y_bottom, widgets


def sec_column_header_table(c, rng, x0, x1, y_top, avail, style):
    ncols = rng.randint(2, 4)
    headers = next((h for h in COLUMN_HEADER_SETS if len(h) == ncols), None)
    if headers is None:
        headers = [f"Field {i + 1}" for i in range(ncols)]
    header_h = 22.0
    row_h = rng.uniform(18, 26)
    nrows = rng.randint(2, 6)
    table_h = header_h + row_h * nrows
    if table_h > avail:
        nrows = int((avail - header_h) // row_h)
        if nrows < 2:
            return None
        table_h = header_h + row_h * nrows
    col_edges = [x0 + (x1 - x0) * i / ncols for i in range(ncols + 1)]
    y_bottom = y_top - table_h

    _hrect(c, x0, x1, y_top)
    _hrect(c, x0, x1, y_top - header_h)
    for r in range(1, nrows + 1):
        _hrect(c, x0, x1, y_top - header_h - r * row_h)
    for x in col_edges:
        _vrect(c, x, y_bottom, y_top)

    c.setFont(style.bold_font, 9)
    for ci, htext in enumerate(headers):
        c.drawString(col_edges[ci] + 3, y_top - header_h + 7, htext)

    widgets = []
    for ci, htext in enumerate(headers):
        cx0, cx1 = col_edges[ci], col_edges[ci + 1]
        for r in range(nrows):
            row_top = y_top - header_h - r * row_h
            row_bot = row_top - row_h
            widgets.append({
                "type": "text", "label": htext, "expects_rule": "R3",
                "rect": [cx0 + 2, row_bot + 2, cx1 - 2, row_top - 2],
            })
    return y_bottom, widgets


def sec_checkbox_row(c, rng, x0, x1, y_top, avail, style):
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
            rect = [cursor_x, baseline - 2.0, cursor_x + size, baseline + 8.0]
        else:
            rect = [cursor_x, baseline - 2.05, cursor_x + size * 0.8911, baseline + 7.95]
        widgets.append({"type": "checkbox", "label": f"{q} ({opt})",
                         "expects_rule": "R1", "rect": rect})
        cursor_x += size + 4
        c.setFont(style.body_font, size)
        c.drawString(cursor_x, baseline, opt)
        cursor_x += stringWidth(opt, style.body_font, size) + 16
    return baseline - 10, widgets


def sec_underscore_line(c, rng, x0, x1, y_top, avail, style):
    row_h = 24.0
    if avail < row_h:
        return None
    size = 10
    signature = rng.random() < 0.2
    label = "Signature" if signature else rng.choice(LABEL_POOL)
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
    widgets = []
    if not signature:
        widgets.append({"type": "text", "label": label, "expects_rule": "R5",
                         "rect": [line_x0 + 1, baseline, line_x1 - 1, baseline + 12]})
    return baseline - 10, widgets


def sec_rect_line(c, rng, x0, x1, y_top, avail, style):
    row_h = 24.0
    if avail < row_h:
        return None
    size = 10
    signature = rng.random() < 0.2
    label = "Signature" if signature else rng.choice(LABEL_POOL)
    baseline = y_top - 14
    c.setFont(style.body_font, size)
    prefix = f"{label}: "
    c.drawString(x0, baseline, prefix)
    pw = stringWidth(prefix, style.body_font, size)
    line_x0 = x0 + pw + 4
    max_w = (x1 - x0) - pw - 8
    if max_w < 40:
        return baseline - 10, []
    w = max(40, min(rng.uniform(0.4, 0.85) * max_w, PAGE_W * 0.77))
    line_y = baseline - 2
    _hrect(c, line_x0, line_x0 + w, line_y)
    widgets = []
    if not signature:
        widgets.append({"type": "text", "label": label, "expects_rule": "R5b",
                         "rect": [line_x0 + 1, line_y, line_x0 + w - 1, line_y + 13]})
    return baseline - 10, widgets


def sec_mask_cell(c, rng, x0, x1, y_top, avail, style):
    row_h = 26.0
    if avail < row_h:
        return None
    cell_w = min(rng.uniform(90, 140), x1 - x0)
    cx0, cx1 = x0, x0 + cell_w
    cy_top, cy_bot = y_top, y_top - row_h
    _hrect(c, cx0, cx1, cy_top)
    _hrect(c, cx0, cx1, cy_bot)
    _vrect(c, cx0, cy_bot, cy_top)
    _vrect(c, cx1, cy_bot, cy_top)
    c.setFont(style.body_font, 11)
    ty = cy_top - 16
    if rng.random() < 0.5:
        label_txt = "( ) -"
        c.drawString(cx0 + 8, ty, "(")
        c.drawString(cx0 + 8 + 18, ty, ")")
        c.drawString(cx0 + 8 + 50, ty, "-")
    else:
        label_txt = "$"
        c.drawString(cx0 + 8, ty, "$")
    widget = {"type": "text", "label": label_txt, "expects_rule": "R4",
              "rect": [cx0 + 2, cy_bot + 2, cx1 - 2, cy_top - 2]}
    return cy_bot, [widget]


def sec_prose(c, rng, x0, x1, y_top, avail, style):
    line_h = 13.0
    max_lines = int(avail // line_h)
    if max_lines < 2:
        return None
    nlines = rng.randint(2, min(6, max_lines))
    c.setFont(style.body_font, 10)
    y = y_top - 12
    for _ in range(nlines):
        c.drawString(x0, y, _make_prose_line(rng, x1 - x0, style.body_font, 10))
        y -= line_h
    return y, []


SECTION_FUNCS = [
    sec_labelled_grid, sec_column_header_table, sec_checkbox_row,
    sec_underscore_line, sec_rect_line, sec_mask_cell, sec_prose,
]


def _fill_column(c, rng, x0, x1, y_top, y_floor, style, widgets_out, tags):
    y = y_top
    if y - y_floor > 60 and rng.random() < 0.3:
        y = _draw_heading(c, rng, x0, x1, y, style)
        tags.add("heading")
    attempts = 0
    while y - y_floor > 30 and attempts < 40:
        attempts += 1
        fn = rng.choice(SECTION_FUNCS)
        result = fn(c, rng, x0, x1, y, y - y_floor, style)
        if result is None:
            continue
        new_y, widgets = result
        if new_y >= y - 1:
            continue
        widgets_out.extend(widgets)
        tags.add(fn.__name__[len("sec_"):])
        y = new_y - rng.uniform(8, 18)


def _draw_page(c, rng, widgets_out, tags):
    margin = rng.choice([36, 45, 54, 72])
    body_font = rng.choice(BODY_FONTS)
    style = Style(body_font)
    y_top = PAGE_H - margin
    y_floor = margin
    if rng.random() < 0.35:
        gutter = 18
        col_w = (PAGE_W - 2 * margin - gutter) / 2
        _fill_column(c, rng, margin, margin + col_w, y_top, y_floor, style, widgets_out, tags)
        _fill_column(c, rng, margin + col_w + gutter, PAGE_W - margin, y_top, y_floor,
                     style, widgets_out, tags)
        tags.add("twocol")
    else:
        _fill_column(c, rng, margin, PAGE_W - margin, y_top, y_floor, style, widgets_out, tags)


def generate(seed: int, out_dir) -> tuple:
    """Generate one synthetic form. Returns (pdf_path, truth_json_path).

    Deterministic: the same seed always produces byte-identical output.
    """
    _register_fonts()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    num_pages = rng.randint(1, 6)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), invariant=1, pageCompression=1)

    all_widgets = []
    tags = set()
    for pno in range(1, num_pages + 1):
        page_widgets = []
        _draw_page(c, rng, page_widgets, tags)
        for w in page_widgets:
            w["page"] = pno
        all_widgets.extend(page_widgets)
        if pno < num_pages:
            c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()

    stem = f"synth_{seed:05d}"
    pdf_path = out_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    truth = {
        "version": 1,
        "source_pdf": pdf_path.name,
        "origin": "synthetic",
        "family": "synthetic/" + "-".join(sorted(tags)) if tags else "synthetic/blank",
        "pages": [{"page": i, "width": PAGE_W, "height": PAGE_H}
                  for i in range(1, num_pages + 1)],
        "widgets": all_widgets,
    }
    truth_path = out_dir / f"{stem}.json"
    truth_path.write_text(json.dumps(truth, indent=2))
    return pdf_path, truth_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--out", default="eval/corpus/synth")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    for i in range(args.count):
        pdf_path, truth_path = generate(args.seed + i, args.out)
        print(f"{pdf_path.name}  {truth_path.name}")


if __name__ == "__main__":
    main()
