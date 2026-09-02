# FormFill demo — THROWAWAY

A feel-test, not the foundation.

## Run

```bash
cd /Users/ryan.xu/Developer/formfill
./.venv/bin/python demo/demo.py fixtures/safer.pdf     # or any PDF
```

Detects fields, serves the result, opens your browser. Ctrl-C to stop.
Pages are rendered by pdf.js **in the browser**, so the original PDF text stays
selectable. There is no server-side image pipeline.

## What you can do

- **Type** in any box.
- **Hover** a box to reveal its handles. Drag the circle to move, the square to resize.
- **Alt-drag** anywhere on a box also moves it.
- **Arrow keys** nudge the selected box by 1pt, or 10pt with Shift.
- **+ Add box**, then click the page.
- Rename, switch text/checkbox, or delete from the bar at the bottom.
  The bar shows which rule produced the box.
- **Download filled** (flattened) or **Download fillable** (still editable).

## Coverage on fixtures/safer.pdf

```
234 fields (153 text, 81 checkbox) across 8 pages
R1=81  R2=42  R3=90  R4=12  R5=9
```

Up from 123 with R1+R2 alone. R3 (column-header inheritance for empty grid
cells) is the single largest win, as the spec predicted.

The true field count is roughly 200, so **234 means some over-detection**.
Deleting a spurious box is cheaper than hunting a missing one, but R3 needs a
tighter spacer-cell test in track T1.

## Known gaps, seen while testing

- **R5b is missing.** Some write-on lines are drawn as thin vector rects rather
  than underscore characters, so R5 misses them. Visible on 4a of the SAFER form:
  "If no, when did you move to B.C.? ____" gets no box. Added to the spec.
- **R6 finds nothing.** The two large Option 1 / Option 2 consent boxes on page 2
  are cell-drawn and fall outside the current size window.
- **A few R3 false positives** float near the page 2 section headings, where an
  upper table's columns bleed into the rows below.

## Fixes made after the first play-through

1. **Original text was not selectable.** Pages were rendered server-side to PNG,
   so the text was a picture. Now pdf.js renders in the browser with a real text
   layer — 1,979 selectable spans on this file.
2. **Boxes could not be moved.** `draw()` appends boxes into per-page layers, so
   DOM order is grouped by page and does not match the `FIELDS` array order. But
   `paintSel()` indexed `querySelectorAll('.fld')[sel]`, which put the handles on
   a different box. Every lookup now goes through `elOf(i)`, which matches on a
   `data-i` attribute. Handles are also bigger (12px) and appear on hover.
3. **Too many missing boxes.** Implemented R3, R4 and R5.

## Spike result — PASSED, 2026-09-01

`pdf-lib` created 123/123 fields with zero failures. Output round-tripped through
`pypdf` with values intact and rendered correctly in pdfium.

**Conclusion:** browser-writes-the-PDF holds. The `drawText`-and-flatten fallback
is not needed. `pdf-lib` auto-sizes text to fill its box, which looks oversized,
so set the font size explicitly — carried into the demo and noted for T4.

## Testing note

`pdf.js` paints the canvas through `requestAnimationFrame`, which does not fire
in a hidden tab. Automated checks against a background tab will see blank
canvases and hanging render promises. Assert on the box overlay and text layer
instead, or force the tab visible.
