"""Blind-testing report: run the live detector on real PDFs that carry no
ground truth, and rank them by how likely they are to be hiding a bug.

`eval/corpus/tuning` and `eval/holdout` only hold PDFs that WERE fillable and
got stripped by `label.py` -- that is where a truth "answer key" comes from.
Most of what `eval/fetch.py` pulls down was never fillable at all (verdict
`flat-wordlike` or `flat-sparse`): 172 of 419 fetched real forms, at last
count. Those carry no truth, so `eval.score` cannot touch them, and until now
nothing did -- they sat in `eval/corpus/real/` unused past the fetch manifest.

That is exactly the population product testing needs: forms nobody has ever
labelled, from producers and layouts the tuned corpus may not represent. This
module runs the SAME truth-free guards the eval harness already trusts
(`eval/guards.py`) against them and ranks the worst offenders, because without
an answer key those guards -- not precision/recall -- are the only signal.

    python -m eval.blind                          # rank eval/corpus/real
    python -m eval.blind --dir eval/corpus/real_v4 --limit 20
    python -m eval.blind --worst-only 8            # just the 8 to look at

A PDF a pass decides is worth hand-inspecting after reading this report is
still not ground truth. Do not add it to eval/corpus/tuning or eval/holdout --
that requires a human-verified label, per AUTOPILOT.md.
"""
import argparse
import glob
import json
import sys
from pathlib import Path

from engine.detect import detect
from eval.guards import box_over_ink, glyph_coverage, label_plausibility, whitespace_fit


def _fields_per_page(n_fields: int, n_pages: int) -> float:
    return n_fields / n_pages if n_pages else 0.0


def _is_structured(manifest_row: dict) -> bool:
    """True when fetch.py's own classify() already saw form-like structure --
    a ruled table, a checkbox glyph, or a write-on line. False for most of
    what a real crawl actually turns up alongside a form: an instructions
    sheet, a cover letter, a notice, none of which SHOULD get any fields.
    Checked by hand: three "zero fields" cases with none of these signals
    were a bilingual instructions page (0 rects), a court admonition notice
    (0 rects), and a program info sheet (20 rects but 0 checkbox/underscore
    marks and no ruled grid) -- all correctly empty, not detector bugs.
    Without this split, "zero fields" looks like 64 bugs when most of it is
    an artifact of what a forms page happens to link to."""
    if not manifest_row:
        return True  # no manifest data to rule it out -- don't hide it
    return (manifest_row.get("checkbox_glyphs", 0) >= 1
            or manifest_row.get("underscore_chars", 0) >= 3
            or manifest_row.get("thin_h_rects", 0) >= 10
            or manifest_row.get("thin_v_rects", 0) >= 10)


def probe_one(pdf_path: str, manifest_row: dict = None) -> dict:
    """One PDF's guard readings, or a `crashed` record if detect() itself
    raises -- a crash on a real, unremarkable PDF is itself the highest-value
    finding this module can surface."""
    try:
        doc = detect(pdf_path)
    except Exception as e:  # noqa: BLE001 -- report every failure, not just some
        return {"path": pdf_path, "crashed": True, "error": f"{type(e).__name__}: {e}"}

    fields = doc["fields"]
    n_pages = len(doc["pages"])
    record = {
        "path": pdf_path,
        "crashed": False,
        "pages": n_pages,
        "fields": len(fields),
        "fields_per_page": round(_fields_per_page(len(fields), n_pages), 2),
        "notice": doc.get("notice"),
        "structured": _is_structured(manifest_row),
    }
    text_fields = [f for f in fields if f["type"] != "checkbox"]
    if text_fields:
        record["label_plausibility"] = round(label_plausibility(pdf_path, fields)["fraction"], 4)
        record["box_over_ink"] = round(box_over_ink(pdf_path, fields)["fraction"], 4)
        ws = whitespace_fit(pdf_path, fields)
        record["too_small_fraction"] = round(ws["too_small_fraction"], 4)
        record["stacked_fraction"] = round(ws["stacked_fraction"], 4)
    if any(f["type"] == "checkbox" for f in fields):
        record["glyph_coverage"] = round(glyph_coverage(pdf_path, fields)["fraction"], 4)
    return record


def _flat_real_pdfs(corpus_dir: str) -> list:
    """PDFs in `corpus_dir` whose manifest verdict says "never had an
    AcroForm" -- the only ones with no ground truth anywhere else. Falls back
    to every .pdf in the directory if there is no manifest (e.g. a directory a
    pass populated by hand)."""
    manifest_path = Path(corpus_dir) / "manifest.json"
    all_pdfs = sorted(glob.glob(str(Path(corpus_dir) / "*.pdf")))
    if not manifest_path.exists():
        return all_pdfs
    manifest = json.loads(manifest_path.read_text())
    flat_files = {
        r["file"] for r in manifest.get("records", [])
        if r.get("verdict") in ("flat-wordlike", "flat-sparse") and r.get("file")
    }
    if not flat_files:
        return all_pdfs
    return [p for p in all_pdfs if Path(p).name in flat_files] or all_pdfs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="eval/corpus/real")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--worst-only", type=int, default=0,
                     help="print only the N most suspicious, 0 = print all")
    args = ap.parse_args(argv)

    pdfs = _flat_real_pdfs(args.dir)
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"no PDFs found under {args.dir}", file=sys.stderr)
        return 1

    manifest_path = Path(args.dir) / "manifest.json"
    by_file = {}
    if manifest_path.exists():
        for r in json.loads(manifest_path.read_text()).get("records", []):
            if r.get("file"):
                by_file[r["file"]] = r

    records = [probe_one(p, by_file.get(Path(p).name)) for p in pdfs]

    def is_real_miss(r: dict) -> bool:
        """Zero fields on a document that fetch.py's own classify() already
        saw ruled/marked structure in -- the shape actually worth chasing."""
        return not r["crashed"] and r["fields"] == 0 and r["structured"]

    def suspicion(r: dict) -> float:
        if r["crashed"]:
            return 1e9  # a crash always sorts first
        if is_real_miss(r):
            return 1e8  # a structured document with nothing found sorts second
        if r["fields"] == 0:
            return -1  # correctly-empty prose sorts LAST, not first
        return (
            r.get("label_plausibility", 0) * 100
            + r.get("box_over_ink", 0) * 50
            + r.get("stacked_fraction", 0) * 20
            + (1.0 / r["fields_per_page"] if r["fields_per_page"] else 5)
        )

    records.sort(key=suspicion, reverse=True)
    shown = records[: args.worst_only] if args.worst_only else records

    crashed = sum(1 for r in records if r["crashed"])
    real_miss = sum(1 for r in records if is_real_miss(r))
    prose_zero = sum(1 for r in records if not r["crashed"] and r["fields"] == 0 and not r["structured"])
    print(f"{len(records)} PDFs probed  |  {crashed} crashed  |  "
          f"{real_miss} structured docs with ZERO fields found  |  "
          f"{prose_zero} zero-field docs that look like prose, not forms (correctly empty)\n")
    for r in shown:
        if r["crashed"]:
            print(f"CRASH  {r['path']}\n       {r['error']}")
            continue
        flags = []
        if is_real_miss(r):
            flags.append("STRUCTURED BUT ZERO FIELDS -- likely a real gap")
        elif r["fields"] == 0:
            flags.append("zero fields, looks like prose -- probably correct")
        if r.get("notice"):
            flags.append(f"notice={r['notice'].get('code')}")
        if r.get("label_plausibility", 0) > 0.35:
            flags.append(f"label_plausibility={r['label_plausibility']}")
        if r.get("box_over_ink", 0) > 0.05:
            flags.append(f"box_over_ink={r['box_over_ink']}")
        if r.get("stacked_fraction", 0) > 0:
            flags.append(f"stacked_fraction={r['stacked_fraction']}")
        tail = ("  <- " + ", ".join(flags)) if flags else ""
        print(f"{r['fields']:4d} fields  {r['fields_per_page']:5.1f}/pg  "
              f"({r['pages']}pg)  {r['path']}{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
