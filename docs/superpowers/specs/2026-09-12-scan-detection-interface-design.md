# Scan detection: the synthetic-page interface (sub-project 1 of 4)

Date: 2026-09-12
Status: approved, ready for implementation plan

## Background

`detect()` (`engine/detect/__init__.py`) already flags genuinely scanned /
image-only PDFs (`fixtures/AGM Proxy Form.pdf` is the motivating case: one
page, 0 characters, one full-page raster image) with an honest `scanned`
notice rather than silently returning zero fields. That check is correct and
stays as-is for anyone who does not opt into OCR.

The goal, at the user's direction, is full parity: scanned PDFs should
eventually get real fields, in every place FormFill runs, including the
public static site at fillpdf.ryanxu.dev — which has no server and runs the
Python detector inside Pyodide (Python compiled to WebAssembly) in the
visitor's browser (`scripts/build_site.py`, `wrangler.jsonc`).

Two facts make this bigger than "call an OCR library":

1. **No real OCR engine is a pure-Python wheel.** `scripts/build_site.py`
   hard-fails any dependency that is not pure-Python (or one of Pyodide's own
   prebuilt packages), because that is the only thing `micropip`/Pyodide can
   install in the browser sandbox. A local/CLI backend and a browser backend
   will necessarily use different underlying libraries.
2. **OCR alone cannot drive the existing rules.** `engine/detect/rules.py`
   (R1-R18) is built entirely on vector PDF primitives pdfplumber exposes
   from a real text layer: `page.rects` / `page.curves` (vector-drawn boxes,
   underlines, checkbox borders) and `page.chars` including specific
   Webdings/Wingdings codepoints (`CHECK_GLYPHS`) for checkbox glyphs. A
   raster scan has none of these — no vector rects, no curves, and no font
   codepoints, only pixels. OCR recovers label *text*; it cannot recover "this
   square is a checkbox" or "this is a drawn write-on line" — that has to
   come from image processing (CV) on the rendered bitmap.

## Decomposition

This project is split into four ordered sub-projects. Only sub-project 1 is
specified and planned here; 2-4 each get their own spec/plan cycle later.

1. **Synthetic-page interface** (this document) — a data contract that lets
   the existing rules run against either a real pdfplumber page or one
   assembled from OCR + CV output, proved with a hand-built fixture. No real
   OCR or CV yet.
2. **Local/CLI backend** — real OCR (pytesseract + system Tesseract) and
   real image-based line/box/checkbox detection (e.g. OpenCV-python),
   producing a page conforming to sub-project 1's contract, wired into
   `demo.py`/CLI/eval. No browser constraints; fastest to tune.
3. **Browser backend** — the same contract implemented in JS (tesseract.js
   for OCR, an in-browser CV approach for lines/boxes) against pages
   `pdf.js` already rasterizes, feeding results across the Pyodide bridge
   into the same Python rules. Adds a real download-size cost to the public
   site's first load (tens of MB of WASM/model data) — still free to host,
   just a UX tradeoff worth tracking, not blocking.
4. **Its own tuning/eval corpus** — scan detection precision will look
   nothing like the vector-based 165-form corpus and must not be folded into
   `eval/holdout`/`eval/corpus/tuning` (those stay human-labelled-only per
   the existing protected-paths rule). Corpus *fetching* can start in
   parallel with 2/3; actual *tuning* is an iterative loop best done once a
   backend exists.

Sub-projects 2 and 3 are independent of each other (different languages,
different libraries, no shared files) and can be built in parallel once this
interface is locked, most likely each in its own git worktree.

## Design: the synthetic-page interface

### What `_detect_page()` actually needs

Confirmed by reading every `page.`/`pg.` access in `rules.py` and
`__init__.py`: exactly `.width`, `.height`, `.chars`, `.rects`, `.curves`,
and `.extract_words()`. Nothing font-specific beyond literal character text
(`CHECK_GLYPHS` is a text-content check, not a font lookup), nothing else
PDF-internal. This is small enough to express as a structural
(`typing.Protocol`) contract — no inheritance required, so a real
pdfplumber page satisfies it automatically.

### `engine/detect/page_protocol.py` (new)

```python
class DetectablePage(Protocol):
    width: float
    height: float
    chars: list[dict]      # each: x0, x1, top, bottom, text
    rects: list[dict]      # each: x0, x1, top, bottom, width, height, fill, stroke
    curves: list[dict]     # same shape as rects; [] is valid
    def extract_words(self) -> list[dict]: ...  # each: x0, x1, top, bottom, text
```

Two granularity decisions, fixed here so sub-projects 2/3 both target the
same contract:

- **`chars` from OCR is word-level, split into even per-character
  sub-boxes.** OCR engines return word (or line) bounding boxes, not true
  per-glyph ones. A few rules need char-level granularity: underscore runs
  and dot-leaders (write-on lines), and `_ink_boxes` (box-emptiness). A
  backend must emit one `chars` entry per character, dividing the word's
  bbox width evenly by character count. This is a monospaced approximation;
  acceptable because these specific rules only care about horizontal extent
  and character identity, not per-glyph metrics.
- **`curves` may legitimately be `[]`.** Curve-drawn checkbox-like shapes
  (`_rect_like_curves`) are a refinement on top of `rects`, not a separate
  detection path. A v1 CV backend only needs to emit `rects`.

### Checkbox mapping

R1 (`CHECK_GLYPHS` in `page.chars`) can never fire on a scan, OCR or not —
there is no font codepoint in a raster image. CV-detected checkbox squares
instead become synthetic `rects` (small bordered/filled boxes), which feeds
the *existing* rect-based checkbox rules (R16/R18) that already handle
cell-drawn checkboxes on real forms. No new checkbox-detection logic is
needed in `rules.py`.

### `detect()` entry point

```python
def detect(pdf_path, page_backend=None):
```

`page_backend`, when given, is `(pdfplumber_page, page_number) ->
DetectablePage | None`.

- `_page_is_scanned()` still runs on every page exactly as today.
- On a scanned page, if `page_backend` is set, `detect()` calls it. A
  returned `DetectablePage` is run through the unchanged `_detect_page()`
  and its fields merge into the normal output. Returning `None` (backend
  declined, e.g. OCR failed) falls back to today's scanned-page behavior for
  that page.
- Default `page_backend=None` reproduces current behavior exactly — zero
  risk to the scored 165-form corpus, `eval/holdout`, or existing tests,
  which is why they stay a required check with no changes here.

### Output contract (honesty)

`AUTOPILOT.md` treats honest error handling as equal in importance to
detection quality. Fields recovered from a scan are real but structurally
lower-confidence than vector-derived ones (word-level OCR boxes, approximate
CV box-finding), so consumers must be able to tell them apart:

- `eval/contracts/fields.schema.json`: add `"ocr"` to the `origin` enum
  (alongside `detected`/`user_added`/`user_moved`).
- `detect()`'s single `notice` field stays document-level, so a mixed
  document (some real text pages, some OCR'd, maybe some still-undetected
  scans) needs one precedence order: `scanned` (a backend was available but
  declined on at least one page, or no backend was given and the existing
  majority-scanned threshold is met) outranks `ocr_assisted` (at least one
  page's fields came from a backend and no page hit the `scanned` case)
  outranks `no_fields` (unchanged from today). `ocr_assisted` fires whenever
  any page used a backend, regardless of whether other pages had a normal
  text layer — so a consumer always knows to review the document, not just
  when the whole thing was a scan.

## Testing / verification

No real OCR or CV in this sub-project — the goal is proving the contract,
not detection quality.

- `tests/test_synthetic_page.py`: a hand-built `FakeSyntheticPage` test
  double (not built via any backend) with one label + one write-on line (as
  synthetic `chars`/`rects`) and one rect-drawn checkbox, mirroring a shape
  already covered by real fixtures. Assert `_detect_page()` returns the
  expected fields (label text, type, rule, rect) — proving R2/R5b/R16-style
  rules run correctly against synthetic geometry with zero changes to
  `rules.py`.
- A `detect()`-level test using a trivial stub `page_backend` (always
  returns the same `FakeSyntheticPage`) against
  `fixtures/AGM Proxy Form.pdf`: assert fields appear, `origin == "ocr"`,
  notice code is `ocr_assisted`.
- A second `detect()`-level test with `page_backend=None` against the same
  file: assert the `scanned` notice is unchanged from today.
- `tests/test_scanned.py` and the full `eval`/holdout run must pass
  unmodified.

## Out of scope for this sub-project

- Any real OCR library or image-processing library.
- Any change to `rules.py`.
- Browser/Pyodide wiring, `demo.py`/CLI wiring.
- Confidence-score calibration for OCR/CV-derived fields (real numbers need
  a real backend to measure against).
