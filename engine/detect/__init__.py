"""Field detection. The single entry point every consumer calls."""
import re
from pathlib import Path
from typing import Union

import pdfplumber

from .rules import detect as _detect_page, slug

__all__ = ["detect", "slug"]

# A scanned or image-only PDF carries no real text layer: each page is a raster
# image, so pdfplumber extracts almost no characters. The detector reads a
# form's printed text to place fields, so on a scan it finds nothing and would
# otherwise return an empty result with no explanation. A public app cannot do
# that, so detect() attaches an honest `notice` the UI can show. The check is
# deliberately conservative -- a real flat form carries hundreds of label
# characters per page, far above SCANNED_MAX_CHARS_PER_PAGE -- so it fires only
# on genuine scans, never on the 165-form corpus.
SCANNED_MAX_CHARS_PER_PAGE = 20   # a real flat form carries far more label text
SCANNED_IMAGE_COVERAGE = 0.5      # fraction of page area one image must cover
SCANNED_MESSAGE = (
    "This looks like a scanned or image-only PDF: its pages are images with no "
    "text layer, so there is nothing for FormFill to read and no fields could "
    "be found. Run OCR on it first (for example, export a searchable / "
    "text-layer PDF) and try again."
)

# A text-layer PDF that is not a scan can still yield zero fields: it may
# genuinely not be a form (an instructions sheet, a cover letter, a notice),
# or it may be a form whose layout none of the rules in rules.py reach. Either
# way the result today is an empty fields list with no explanation -- the same
# "silently does nothing" gap the scanned guard closes, just on the other side
# of that check. Say so plainly rather than let a blank page stand for both
# "there was nothing here" and "the detector missed it".
NO_FIELDS_MESSAGE = (
    "FormFill could not find any fillable fields in this document. It may not "
    "be a form (for example, an instructions sheet or a notice), or it may use "
    "a layout the detector does not recognize yet. You can still add fields by "
    "hand where you need to write."
)


# A page a backend successfully OCR'd is real, but structurally
# lower-confidence than a page read from an actual text layer: word-level
# OCR boxes and approximate CV box-finding, not exact vector geometry. Say
# so, the same way SCANNED_MESSAGE and NO_FIELDS_MESSAGE already do, rather
# than let an OCR-derived field look identical in confidence to one read
# from a real flat form.
OCR_ASSISTED_MESSAGE = (
    "Some pages in this document had no text layer, so FormFill used OCR to "
    "read them instead. OCR-derived fields are less reliable than fields "
    "read from a real text layer -- check labels and positions carefully "
    "before using them."
)


def _page_is_scanned(pg) -> bool:
    """True when a page is a page-filling image with almost no text."""
    non_ws = sum(1 for c in pg.chars if c["text"].strip())
    if non_ws >= SCANNED_MAX_CHARS_PER_PAGE:
        return False
    area = float(pg.width) * float(pg.height)
    if area <= 0:
        return False
    return any(
        (im.get("width", 0) or 0) * (im.get("height", 0) or 0) >= SCANNED_IMAGE_COVERAGE * area
        for im in pg.images
    )


# A checkbox question whose two answers are "Yes" and "No" is mutually
# exclusive: a person must never tick both, and a form that lets them is wrong
# in law, not just in software (AUTOPILOT value item #1). R1 already writes the
# question into each option's label as "<question> (Yes)" / "<question> (No)".
# When one "Yes" option and one "No" option sit under an identical question on a
# page, tag both with a shared `group` id so a consumer can inject one radio
# group instead of two independent checkboxes. Only this unambiguous shape is
# grouped: a multi-select list (a "cc:" recipient list, a "check all that apply"
# block) does not have one Yes plus one No under a shared prompt, so it stays
# independent. This adds only a `group` key -- geometry, type, label and id are
# untouched, so detection scoring is unaffected.
_YESNO_OPTION = re.compile(r"^(.*?)\s*\(([^()]+)\)\s*$")


def _group_yes_no(fields: list) -> None:
    buckets: dict = {}
    for f in fields:
        if f["type"] != "checkbox":
            continue
        m = _YESNO_OPTION.match(f.get("label", "") or "")
        if not m:
            continue
        prefix, option = m.group(1).strip(), m.group(2).strip().lower()
        if not prefix or prefix[-1] not in "?:" or option not in ("yes", "no"):
            continue
        buckets.setdefault((f["page"], prefix), []).append((option, f))
    n = 0
    for members in buckets.values():
        if sorted(o for o, _ in members) != ["no", "yes"]:   # exactly one Yes, one No
            continue
        n += 1
        gid = f"grp_{n}"
        for _option, f in members:
            f["group"] = gid


# A form often prints a fill-in blank as a row of dots -- a "dot leader"
# (". . . . . .", "......", "…"). Label extraction sometimes picks one up at the
# start or end of a caption, leaving a field named ". . . . . ." or
# ". . . . day of" that tells a user nothing (seen on real leader-line forms in
# the blind corpus). Strip a genuine leader run -- two or more dots, optionally
# single-spaced, or an ellipsis -- from each end of a label. A single dot is
# never treated as a leader, so a caption that opens with an abbreviation
# ("U.S.", ".NET") or ends one ("320 W.") is left untouched.
_DOT_LEADER = r"(?:…|[.·](?:[ \t]*[.·])+)"
_LEAD_LEADER = re.compile(r"^\s*" + _DOT_LEADER + r"[ \t,;:]*")
_TRAIL_LEADER = re.compile(r"[ \t,;:]*" + _DOT_LEADER + r"\s*$")


def _strip_dot_leaders(label: str) -> str:
    s = label or ""
    stripped = _TRAIL_LEADER.sub("", _LEAD_LEADER.sub("", s))
    return stripped.strip() if stripped != s else s


def detect(pdf_path: Union[str, Path], page_backend=None) -> dict:
    """Detect fillable regions in a flat PDF.

    Pure function. No network, no mutation of the input, no global state.
    Returns the shape defined in eval/contracts/fields.schema.json.

    `page_backend`, when given, is called as `page_backend(pdfplumber_page,
    page_number)` on every page `_page_is_scanned()` flags. Returning a
    `page_protocol.DetectablePage`-shaped object runs that page through the
    same rules as a real text-layer page, and its fields are tagged
    `origin: "ocr"`. Returning `None` leaves that page's current scanned
    behavior unchanged. Passing no `page_backend` at all reproduces today's
    behavior exactly.
    A backend that raises propagates the exception out of detect() --
    returning None is the only sanctioned way to decline a page.
    See docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.
    """
    fields, pages = [], []
    carry, prev_width = None, None
    scanned_pages = 0
    ocr_pages: set = set()
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            pages.append({"page": i, "width": float(pg.width), "height": float(pg.height)})
            if prev_width is not None and abs(pg.width - prev_width) > 1:
                carry = None      # a page-size/orientation change breaks column geometry
            page_source = pg
            if _page_is_scanned(pg):
                synthetic = page_backend(pg, i) if page_backend else None
                if synthetic is not None:
                    page_source = synthetic
                    ocr_pages.add(i)
                else:
                    scanned_pages += 1
            page_fields, carry = _detect_page(page_source, i, carry_in=carry)
            fields += page_fields
            prev_width = pg.width

    for f in fields:
        f["label"] = _strip_dot_leaders(f.get("label", "") or "")
        if f["type"] == "text" and not f["label"]:
            f["label"] = "value"     # a leader-only blank has no caption of its own

    seen: dict = {}
    for f in fields:
        base = f"p{f['page']}_" + (slug(f["label"]) if f["type"] == "text" else "chk")
        seen[base] = seen.get(base, 0) + 1
        f["id"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
        f["origin"] = "ocr" if f["page"] in ocr_pages else "detected"

    _group_yes_no(fields)

    out = {"version": 1, "source": {"pages": len(pages)}, "pages": pages, "fields": fields}
    # Flag the whole document when most content pages are scanned images. One
    # image page in an otherwise text PDF is not a scan, so require a majority.
    if pages and scanned_pages / len(pages) >= 0.5:
        out["notice"] = {"code": "scanned", "message": SCANNED_MESSAGE}
    elif ocr_pages:
        out["notice"] = {"code": "ocr_assisted", "message": OCR_ASSISTED_MESSAGE}
    elif pages and not fields:
        out["notice"] = {"code": "no_fields", "message": NO_FIELDS_MESSAGE}
    return out
