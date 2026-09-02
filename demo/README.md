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
246 fields (165 text, 81 checkbox) across 8 pages
R1=81  R2=39  R3=89  R4=12  R5=9  R5b=16
```

Up from 123 with R1+R2 alone. R3 (column-header inheritance) and R5b (rect-drawn
write-on lines) are the two big wins.

The true field count is roughly 200, so **coverage is no longer the problem —
precision is**. R3 emits some spacer cells, most visibly on page 3 where boxes
land on the 4c heading. Tightening R3 is now T1's main job.

## Known gaps, still open

- **R3 over-detects.** Spacer cells become boxes. Worst on page 3, where boxes
  overlay the "4c. If you or your spouse were not born in Canada" heading.
- **R6 finds nothing.** The two large Option 1 / Option 2 consent boxes on page 2
  are cell-drawn and fall outside the current size window.
- **Three R5b false positives** on headings that carry a decorative underline
  ("PLEASE:", "Avoid Processing Delays:", "Scan and Upload:").

## Fixes from the second play-through

1. **Panel would not dismiss.** The overlay layer carries `pointer-events: none`
   so the page text stays selectable, which means a background click never
   reached its handler. Now a document-level listener checks the event target.
2. **Checkboxes were misaligned.** Detection was already exact — a 10pt Webdings
   glyph measures 10.0 x 10.0 and overlays the printed box precisely. The cause
   was CSS: user agents apply `margin: 3px 3.5px` to a checkbox input, shifting
   an absolutely-placed one up and left. Set `margin: 0`.
3. **Missing lines in 4a, 9a, 9b, 9d, 9f.** These write-on lines are drawn as
   thin vector rects, not underscore characters. Added R5b, which separates them
   from table borders by a single clean test: a cell border has a vertical rule
   standing at one of its endpoints, a write-on line has none. All 16 found.
4. **Signature lines got input boxes.** A signature is signed, not typed. R8 now
   drops any field whose label matches `/signatur/i`.

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
