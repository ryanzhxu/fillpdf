# FormFill demo — THROWAWAY

A feel-test, not the foundation. Built to answer two questions:

1. What is it actually like to fix the boxes the detector misses?
2. Can `pdf-lib` create AcroForm fields in the browser? (This is the T4 spike —
   the riskiest dependency in the spec, unmaintained since July 2024.)

## Run

```bash
cd /Users/ryan.xu/Developer/formfill
./.venv/bin/python demo/demo.py fixtures/safer.pdf     # or any PDF you like
```

It detects fields, renders the pages, serves the result, and opens your browser.
Ctrl-C to stop.

## What you can do

- **Type** in any blue box.
- **Click** a box to select it. Drag the circle to move, the square to resize.
- **+ Add box**, then click anywhere on a page to add one (shows green).
- **Next field →** jumps and focuses the next box. This is the answer to
  eight pages of pinch-zooming on a phone.
- Rename, switch text/checkbox, or delete from the bar at the bottom.
- **Download filled** (flattened) or **Download fillable** (still editable).

## What is deliberately missing

Detection runs rules R1 (checkbox glyphs) and R2 (labelled table cells) only.
It finds about 60% of fields on purpose. Rules R3–R7 are the work in track T1.
The gap is the point: it shows what correction feels like.

## Spike result — PASSED, 2026-09-01

Ran on `fixtures/safer.pdf`:

```
detected 123 fields (42 text, 81 checkbox) across 8 pages
[spike] created 123/123 fields; 0 failed
updateFieldAppearances(): no error
output: 396,139 bytes
```

Round-tripped the output through `pypdf`: 123 fields present, values intact
(`p2_last_name -> 'Xu'`, `p4_postal_code -> 'V5H 4V8'`), 3 checkboxes ticked.
Rendered through pdfium: correct, in the right places.

**Conclusion:** `pdf-lib` field creation works. The browser-writes-the-PDF
architecture in the spec is sound. The `drawText`-and-flatten fallback is not
needed.

One cosmetic finding: `pdf-lib` auto-sizes text to fill the box, which looks
oversized. Fixed here by setting the font size explicitly. Carry that into T4.
