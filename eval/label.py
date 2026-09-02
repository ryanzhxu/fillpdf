"""Turn a fillable PDF into (flat PDF + answer key).

The premise this rests on, verified 2026-09-02: Acrobat's "Prepare Form" pass
ADDS widget annotations on top of the page. It does not rewrite the page
content. So a government form drafted in Word and then fielded in Acrobat still
carries the Word vector structure our rules read -- thin-rect table borders,
underscore runs -- underneath its widgets.

Strip the widgets and you have exactly the input the detector expects, plus a
perfect answer key, with no human labelling.

This does NOT work on Adobe LiveCycle / Designer forms. Those are rebuilt in a
different tool and carry no vector structure at all, so stripping them yields a
page no detector could read. eval/fetch.py's classifier separates the two.
"""
import hashlib
import json
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject

FT_TO_TYPE = {"/Tx": "text", "/Btn": "checkbox", "/Ch": "choice"}
CHECK_GLYPHS = {"\uf063", "\uf06f"}   # Webdings, Wingdings checkbox glyphs
MARK_CHARS = CHECK_GLYPHS | {"_", ".", "\u00b7", "\u2024"}   # underscores and dot leaders
MIN_SIDE = 4.0          # a widget smaller than this is not something a person fills
MIN_WIDGETS = 5         # a form with fewer is not worth a corpus slot


def extract_widgets(reader):
    """Widget annotations, not form fields.

    A field may own several widgets across pages, and a field tree carries
    parent nodes that are not rectangles at all -- 289 of the CRA T2201's 585
    entries are such parents. Only leaf widget annotations are answers.
    """
    widgets = []
    for i, page in enumerate(reader.pages, 1):
        for annot in (page.get("/Annots") or []):
            o = annot.get_object()
            if o.get("/Subtype") != "/Widget" or not o.get("/Rect"):
                continue
            if int(o.get("/Ff", 0) or 0) & 1:            # read-only
                continue
            if o.get("/F") and int(o.get("/F")) & 2:     # hidden
                continue
            r = [float(v) for v in o["/Rect"]]
            x0, y0 = min(r[0], r[2]), min(r[1], r[3])
            x1, y1 = max(r[0], r[2]), max(r[1], r[3])
            if (x1 - x0) < MIN_SIDE or (y1 - y0) < MIN_SIDE:
                continue
            parent = o.get("/Parent").get_object() if o.get("/Parent") else {}
            ft = str(o.get("/FT") or parent.get("/FT") or "")
            widgets.append({"page": i, "type": FT_TO_TYPE.get(ft, "text"),
                            "rect": [x0, y0, x1, y1]})
    return widgets


def strip(reader, dest):
    """Remove the interactive layer, leave every page content stream untouched.

    Deliberately does NOT flatten appearance streams. Flattening would paint the
    field borders onto the page and hand the detector a visual cue a genuinely
    flat form never has, inflating every score.
    """
    writer = PdfWriter()
    for page in reader.pages:
        annots = page.get("/Annots")
        if annots is not None:
            page[NameObject("/Annots")] = ArrayObject(
                [a for a in annots if a.get_object().get("/Subtype") != "/Widget"])
        writer.add_page(page)
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]
    with open(dest, "wb") as fh:
        writer.write(fh)


def keep_reachable(pdf_path, widgets, pad=6):
    """Drop widgets that sit over nothing a rule could read.

    Filtering at the WIDGET level rather than the form level. Some fillable
    PDFs place a widget where the page has no rule, no checkbox glyph and no
    underscore -- strip it and the page is blank there. No detector could find
    that field and no human could fill the printed form, so scoring against it
    punishes the detector for a defect in the source.

    Rejecting the whole FORM for containing some such widgets threw away 15 of
    21 usable forms. Rejecting only the unreachable widgets keeps the rest.

    Supporting structure means something a rule actually reads: a thin vector
    rule, a checkbox glyph, an underscore, or a dot leader. Counting any nearby
    CHARACTER was the original test and was useless -- a form with 50,000
    characters and zero vector rules passed at 100%, then scored 0.000 forever.

    KNOWN LIMITATION: this defines "reachable" as "carries a signal some current
    rule reads". A future rule that reads a NEW signal would find its evidence
    already filtered out of the corpus. Whenever a rule learns a new signal, add
    it here -- dot leaders were added when R11 landed.
    """
    kept = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        by_page = {}
        for i, page in enumerate(pdf.pages, 1):
            rules = [r for r in page.rects
                     if (r["height"] < 3 and r["width"] >= 5)
                     or (r["width"] < 3 and r["height"] >= 5)]
            marks = [c for c in page.chars if c["text"] in MARK_CHARS]
            by_page[i] = (page.height, rules, marks)
        for w in widgets:
            if w["page"] not in by_page:
                continue
            h, rules, marks = by_page[w["page"]]
            x0, y0, x1, y1 = w["rect"]
            top, bot = h - y1, h - y0

            def hit(o):
                return not (o["x1"] < x0 - pad or o["x0"] > x1 + pad
                            or o["bottom"] < top - pad or o["top"] > bot + pad)

            if any(hit(o) for o in rules) or any(hit(o) for o in marks):
                kept.append(w)
    return kept


def label(src_pdf, out_dir, family="real"):
    """(flat pdf, truth json) for one fillable PDF, or None if not admitted."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(src_pdf))
    widgets = extract_widgets(reader)
    if len(widgets) < MIN_WIDGETS:
        return None
    stem = hashlib.sha256(Path(src_pdf).read_bytes()).hexdigest()[:12]
    pdf_out = out_dir / f"{stem}.pdf"
    strip(reader, pdf_out)
    widgets = keep_reachable(pdf_out, widgets)
    if len(widgets) < MIN_WIDGETS:
        pdf_out.unlink(missing_ok=True)
        return None
    pages = [{"page": i, "width": float(p.mediabox.width), "height": float(p.mediabox.height)}
             for i, p in enumerate(reader.pages, 1)]
    truth = {"version": 1, "source_pdf": pdf_out.name, "origin": "stripped",
             "family": family, "pages": pages, "widgets": widgets}
    truth_out = out_dir / f"{stem}.json"
    truth_out.write_text(json.dumps(truth))
    return pdf_out, truth_out
