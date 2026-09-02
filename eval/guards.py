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
import math
import re

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


# ---------------------------------------------------------------------------
# label_plausibility: a truth-free label QUALITY guard.
#
# label_accuracy (eval/score.py) needs a truth "label" to compare against,
# and only synthetic corpora carry one -- on the 165 real stripped forms it
# is always UNAVAILABLE. That is a blind spot: a rule that raises recall by
# attaching fragments of a run-on sentence as "labels" (observed for real:
# "ADDRESS OF" / "BEING RENTED TO TENANT(s)", sliced out of one heading
# straddling a column boundary) looks like a pure win to box-IoU matching.
# This guard needs no truth. It asks three truth-free questions of each
# detected label, against the page it names:
#
#   1. Provenance ("not_found_on_page"). Do the label's words actually occur,
#      in that order, on the page near the box? A label built by joining
#      words out of their real page order (seen for real: "SERVICE street
#      number of the and street landlord name") will not.
#   2. Contiguity ("internal_gap"). Within the label's own matched words, is
#      there a gap far wider than normal word spacing? That is the signature
#      of a word straddling the physical gap BETWEEN two grid cells -- a
#      run-on sentence read as one label, not two column headers.
#   3. Sentence-fragment shape ("truncated_left"/"truncated_right"). Does a
#      normally-spaced word sit on the page immediately before/after the
#      label's matched span, excluded from the label? That is evidence the
#      label is a slice of a longer run, not a complete field name (this is
#      exactly what "ADDRESS OF" and "BEING RENTED TO TENANT(s)" show: real
#      contiguous words on the page, normal-width gaps throughout, but each
#      one sits mid-run with the rest of the sentence excluded on both
#      sides).
#
# Checkbox glyphs and blank-fill runs of underscores sit next to almost
# every real label by construction (a label immediately precedes its own
# input blank, or a Yes/No glyph); they are excluded from gap and
# neighbor checks the same way CHECK_GLYPHS is excluded from box_over_ink's
# "ink", or this guard flags every ordinary label in the corpus.
#
# Duplication (the same label on many boxes on one page) is measured, not
# judged -- a repeated table-column header is correct, a header inherited
# too widely is not, and telling them apart needs a human. See "duplication"
# in the return value.
#
# Tried and rejected: a hard "far from its matched text" flag
# (reject a match more than ~200pt from the box). It fired constantly on a
# single header governing a tall stack of boxes below it -- exactly the
# legitimate half of the duplication question above, not a defect. Dropped;
# nearest-match selection is still used to pick which occurrence of a
# repeated phrase to score, just not to judge it.
# ---------------------------------------------------------------------------

LABEL_GAP_MULTIPLIER = 3.0   # normal-gap multiplier for the fragment threshold
LABEL_GAP_FLOOR = 10.0       # points; never more sensitive than this
LABEL_GAP_CEIL = 30.0        # points; never less sensitive than this
LABEL_BAND_MARGIN = 30.0     # points either side of the box, for the
                             # multi-line column-band fallback match

_WORD_NORM_RE = re.compile(r"[^a-z0-9']")
_BLANK_RUN_RE = re.compile(r"_{3,}")
_LEADER_CHARS = set(".·․ ")  # period, middle dot, one-dot-leader -- see R11 in rules.py


def _lp_normalize(text):
    return _WORD_NORM_RE.sub("", text.lower())


def _lp_is_blank_or_glyph(text):
    """A checkbox glyph, an underscore fill-in blank, or a dot-leader
    write-on-line (R11's LEADER_CHARS) -- not prose, so a gap next to one
    says nothing about whether a label is a sentence fragment. Without this,
    EVERY R11 label ("Case Number:", immediately followed by "." as pdfplumber
    tokenizes a spaced-out leader dot as its own word) would flag
    truncated_right, a false positive discovered by measuring R11's rate on
    the real corpus (see module docstring)."""
    if text in CHECK_GLYPHS or _BLANK_RUN_RE.search(text):
        return True
    return bool(text) and all(ch in _LEADER_CHARS for ch in text)


def _lp_median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _lp_cluster_lines(words, line_tol=2.5):
    """Group words into lines (by "top" proximity) and return them
    flattened back out in reading order: line top-to-bottom, word
    left-to-right within a line. Each word gets a "line" index so callers
    can tell same-line neighbors from a line break."""
    lines = []
    for w in sorted(words, key=lambda w: w["top"]):
        for ln in lines:
            if abs(ln["top"] - w["top"]) <= line_tol:
                ln["words"].append(w)
                break
        else:
            lines.append({"top": w["top"], "words": [w]})
    ordered = []
    for li, ln in enumerate(sorted(lines, key=lambda l: l["top"])):
        for w in sorted(ln["words"], key=lambda w: w["x0"]):
            ordered.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"],
                             "top": w["top"], "bottom": w["bottom"], "line": li})
    return ordered


def _lp_page_reading_order(page):
    return _lp_cluster_lines(page.extract_words(use_text_flow=False, keep_blank_chars=False))


def _lp_gap_threshold(ordered_words):
    """A page-local "normal word gap", turned into a fragment-detection
    threshold. Real forms vary in font size and word spacing; a fixed
    threshold either misses cramped forms or fires on loosely-set ones, so
    this scales with the page's own typical gap -- floored and ceilinged so
    a page with almost no prose (nothing to measure) still gets a sane
    value.
    """
    gaps = []
    for prev, cur in zip(ordered_words, ordered_words[1:]):
        if prev["line"] == cur["line"]:
            g = cur["x0"] - prev["x1"]
            if g > 0.1:
                gaps.append(g)
    med = _lp_median(gaps) or 3.0
    return min(max(med * LABEL_GAP_MULTIPLIER, LABEL_GAP_FLOOR), LABEL_GAP_CEIL)


def _lp_tokenize(label):
    return [t for t in re.split(r"\s+", label.strip()) if t]


def _lp_all_spans(label_tokens, ordered_words, allow_prefix_last):
    """Every contiguous run of `ordered_words` whose normalized text equals
    `label_tokens`. `allow_prefix_last` lets the LAST token match as a
    prefix of the page word, because rules R5/R5b/R11 truncate a label to
    60 chars and can cut the final word mid-way -- without this, that
    truncation alone would look like a fragment.
    """
    norm_label = [_lp_normalize(t) for t in label_tokens]
    n = len(norm_label)
    if n == 0:
        return []
    page_norm = [_lp_normalize(w["text"]) for w in ordered_words]
    spans = []
    for i in range(len(ordered_words) - n + 1):
        window = page_norm[i:i + n]
        ok = True
        for j in range(n):
            if j == n - 1 and allow_prefix_last:
                if not (window[j] == norm_label[j] or (norm_label[j] and window[j].startswith(norm_label[j]))):
                    ok = False
                    break
            elif window[j] != norm_label[j]:
                ok = False
                break
        if ok:
            spans.append(ordered_words[i:i + n])
    return spans


def _lp_span_center(span):
    xs = [w["x0"] for w in span] + [w["x1"] for w in span]
    ys = [w["top"] for w in span] + [w["bottom"] for w in span]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _lp_box_center(rect, page_height):
    x0, y0, x1, y1 = rect
    top = page_height - y1
    bottom = page_height - y0
    return ((x0 + x1) / 2.0, (top + bottom) / 2.0)


def _lp_nearest_span(spans, box_center):
    best, best_d = None, None
    for span in spans:
        cx, cy = _lp_span_center(span)
        d = math.hypot(cx - box_center[0], cy - box_center[1])
        if best_d is None or d < best_d:
            best, best_d = span, d
    return best


def _lp_find_span(label_tokens, ordered_words, box_center, page, rect):
    spans = _lp_all_spans(label_tokens, ordered_words, allow_prefix_last=False)
    if not spans:
        spans = _lp_all_spans(label_tokens, ordered_words, allow_prefix_last=True)
    if spans:
        return _lp_nearest_span(spans, box_center)

    # Column-band fallback: a legitimate multi-line header (e.g. "From
    # Date" on one line, its "(dd/mm/yyyy)" format hint one line further
    # down) can have an unrelated sibling column's row sandwiched between
    # its own lines in full-page reading order. Re-cluster into lines using
    # only words near the box's own x-range, so a sibling column's words
    # (outside that range) cannot sit between this column's own lines.
    x0, y0, x1, y1 = rect
    band_words = [
        w for w in page.extract_words(use_text_flow=False, keep_blank_chars=False)
        if (x0 - LABEL_BAND_MARGIN) <= (w["x0"] + w["x1"]) / 2.0 <= (x1 + LABEL_BAND_MARGIN)
    ]
    band_ordered = _lp_cluster_lines(band_words)
    spans = _lp_all_spans(label_tokens, band_ordered, allow_prefix_last=False)
    if not spans:
        spans = _lp_all_spans(label_tokens, band_ordered, allow_prefix_last=True)
    return _lp_nearest_span(spans, box_center) if spans else None


def _lp_internal_gap(span, gap_threshold):
    for prev, cur in zip(span, span[1:]):
        if _lp_is_blank_or_glyph(prev["text"]) or _lp_is_blank_or_glyph(cur["text"]):
            continue
        if prev["line"] == cur["line"] and (cur["x0"] - prev["x1"]) > gap_threshold:
            return True
    return False


def _lp_truncation(span, ordered_words, gap_threshold):
    first, last = span[0], span[-1]
    idx_by_id = {id(w): i for i, w in enumerate(ordered_words)}
    fi, li = idx_by_id.get(id(first)), idx_by_id.get(id(last))
    trunc_left = trunc_right = False
    if fi is not None and fi > 0:
        prev = ordered_words[fi - 1]
        if prev["line"] == first["line"] and not _lp_is_blank_or_glyph(prev["text"]):
            gap = first["x0"] - prev["x1"]
            if 0 <= gap <= gap_threshold:
                trunc_left = True
    if li is not None and li < len(ordered_words) - 1:
        nxt = ordered_words[li + 1]
        if nxt["line"] == last["line"] and not _lp_is_blank_or_glyph(nxt["text"]):
            gap = nxt["x0"] - last["x1"]
            if 0 <= gap <= gap_threshold:
                trunc_right = True
    return trunc_left, trunc_right


def label_plausibility(pdf_path, fields):
    """Truth-free label quality guard. See the module-level comment above
    `label_plausibility` for the three signals and why each was kept.
    LOWER `fraction` IS BETTER (it is a defect rate, like box_over_ink).
    """
    text_fields = [f for f in fields
                   if f.get("type") != "checkbox" and (f.get("label") or "").strip()]
    empty = {
        "fraction": 0.0, "flagged": 0, "total": 0, "offenders": [],
        "signal_counts": {"not_found_on_page": 0, "internal_gap": 0,
                           "truncated_left": 0, "truncated_right": 0},
        "per_rule": {},
        "duplication": {"total": 0, "duplicated": 0, "max_repeat": 0, "top": []},
    }
    if not text_fields:
        return empty

    by_page = {}
    for f in text_fields:
        by_page.setdefault(f["page"], []).append(f)

    offenders = []
    flagged = 0
    signal_counts = {"not_found_on_page": 0, "internal_gap": 0,
                      "truncated_left": 0, "truncated_right": 0}
    rule_stats = {}
    dup_duplicated = 0
    dup_max_repeat = 0
    dup_top = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page_fields in by_page.items():
            if page_no - 1 >= len(pdf.pages):
                continue
            page = pdf.pages[page_no - 1]
            ordered = _lp_page_reading_order(page)
            gap_threshold = _lp_gap_threshold(ordered)

            norm_counts = {}
            for f in page_fields:
                norm_counts[_lp_normalize(f["label"])] = norm_counts.get(_lp_normalize(f["label"]), 0) + 1
            for norm_label, count in norm_counts.items():
                dup_max_repeat = max(dup_max_repeat, count)
                if count > 1:
                    dup_top.append({"page": page_no, "label": norm_label, "count": count})

            for f in page_fields:
                rule = f.get("rule", "unknown")
                rs = rule_stats.setdefault(rule, {"total": 0, "flagged": 0})
                rs["total"] += 1

                label = f["label"]
                tokens = _lp_tokenize(label)
                box_center = _lp_box_center(f["rect"], page.height)
                span = _lp_find_span(tokens, ordered, box_center, page, f["rect"])

                reasons = []
                if span is None:
                    reasons.append("not_found_on_page")
                else:
                    if _lp_internal_gap(span, gap_threshold):
                        reasons.append("internal_gap")
                    tl, tr = _lp_truncation(span, ordered, gap_threshold)
                    if tl:
                        reasons.append("truncated_left")
                    if tr:
                        reasons.append("truncated_right")

                if reasons:
                    flagged += 1
                    rs["flagged"] += 1
                    for r in reasons:
                        signal_counts[r] += 1
                    dup_count = norm_counts.get(_lp_normalize(label), 1)
                    if dup_count > 1:
                        dup_duplicated += 1
                    offenders.append({
                        "id": f.get("id"), "page": page_no, "rule": rule,
                        "label": label, "reasons": reasons,
                    })
                elif norm_counts.get(_lp_normalize(label), 1) > 1:
                    dup_duplicated += 1

    total = len(text_fields)
    offenders.sort(key=lambda o: (-len(o["reasons"]), o["page"], o.get("id") or ""))
    dup_top.sort(key=lambda d: -d["count"])
    per_rule = {
        r: {"total": v["total"], "flagged": v["flagged"],
            "fraction": (v["flagged"] / v["total"]) if v["total"] else 0.0}
        for r, v in sorted(rule_stats.items())
    }
    return {
        "fraction": flagged / total if total else 0.0,
        "flagged": flagged, "total": total,
        "offenders": offenders[:40],
        "signal_counts": signal_counts,
        "per_rule": per_rule,
        "duplication": {
            "total": total, "duplicated": dup_duplicated,
            "max_repeat": dup_max_repeat, "top": dup_top[:10],
        },
    }


def guards(pdf_path, fields):
    """Run all label-free guards. Returns the "guards" object for scores.json."""
    boi = box_over_ink(pdf_path, fields)
    gc = glyph_coverage(pdf_path, fields)
    wf = whitespace_fit(pdf_path, fields)
    lp = label_plausibility(pdf_path, fields)
    return {
        "box_over_ink": boi["fraction"],
        "box_over_ink_offenders": boi["offenders"],
        "glyph_coverage": gc["fraction"],
        "whitespace_fit": wf,
        "label_plausibility": lp["fraction"],
        "label_plausibility_flagged": lp["flagged"],
        "label_plausibility_total": lp["total"],
        "label_plausibility_offenders": lp["offenders"],
        "label_plausibility_signal_counts": lp["signal_counts"],
        "label_plausibility_per_rule": lp["per_rule"],
        "label_duplication": lp["duplication"],
    }
