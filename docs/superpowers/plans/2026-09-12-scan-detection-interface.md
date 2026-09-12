# Scan Detection Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `_detect_page()` (the existing R1-R18 rule engine in `engine/detect/rules.py`) run unmodified against either a real pdfplumber page or a synthetic one assembled from future OCR/CV output, so sub-projects 2 (local backend) and 3 (browser backend) have a frozen contract to build against.

**Architecture:** A structural `typing.Protocol` (`DetectablePage`) names the five things `_detect_page()` actually reads off a page (`width`, `height`, `chars`, `rects`, `curves`, `extract_words()`). `detect()` gains an optional `page_backend` callback; on a page it already classifies as scanned, it calls the backend to get a `DetectablePage`-shaped object and runs the same rules against it, tagging the resulting fields `origin: "ocr"` and the whole document with an `ocr_assisted` notice. Default behavior (`page_backend=None`) is untouched.

**Tech Stack:** Python (stdlib `typing.Protocol`), pdfplumber (existing dependency), pytest/unittest (existing test style).

**Spec:** `docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md`

## Global Constraints

- Zero behavior change when `page_backend` is not passed — `eval/holdout`, `eval/corpus/tuning`, and every existing test must pass unmodified (protected paths, per `AUTOPILOT.md`).
- No changes to `engine/detect/rules.py`. The whole point of this interface is that the rules do not need to know their input is synthetic.
- `fixtures/` stays reserved for `safer.pdf` only (per `AUTOPILOT.md`) — new tests must not add files there or depend on the untracked `fixtures/AGM Proxy Form.pdf`. Generate synthetic scanned PDFs with reportlab instead, the same way `tests/test_scanned.py` already does.
- `engine/detect/__init__.py` and `engine/detect/rules.py` are copied verbatim into the Pyodide browser build (`scripts/build_site.py`) — anything added to these two files must stay pure-Python stdlib only (no new third-party imports).

---

### Task 1: The `DetectablePage` protocol

**Files:**
- Create: `engine/detect/page_protocol.py`
- Test: `tests/test_page_protocol.py`

**Interfaces:**
- Produces: `DetectablePage`, a `runtime_checkable` `typing.Protocol` with attributes `width: float`, `height: float`, `chars: list`, `rects: list`, `curves: list` and method `extract_words() -> list`. Later tasks import this from `engine.detect.page_protocol`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_page_protocol.py
"""Tests that a real pdfplumber page satisfies DetectablePage for free.

engine/detect/page_protocol.py is a structural (duck-typed) contract: any
object exposing these five things can be scored by rules.py's unmodified
rules. This is the cheapest possible proof that today's real pdfplumber
pages already qualify, before any synthetic page exists.

Run standalone with:  .venv/bin/python -m pytest tests/test_page_protocol.py
"""
import unittest

import pdfplumber

from engine.detect.page_protocol import DetectablePage


class TestPageProtocol(unittest.TestCase):
    def test_real_pdfplumber_page_satisfies_the_protocol(self):
        with pdfplumber.open("fixtures/safer.pdf") as pdf:
            page = pdf.pages[0]
            self.assertIsInstance(page, DetectablePage)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_page_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.detect.page_protocol'`

- [ ] **Step 3: Write the implementation**

```python
# engine/detect/page_protocol.py
"""Structural contract for a page `_detect_page()` (rules.py) can run against.

Anything satisfying this -- a real pdfplumber page, or a synthetic one built
from OCR + image-detection output -- can be scored by the exact same rules
in rules.py, with no changes to rules.py itself.

Confirmed by reading every `page.`/`pg.` access across rules.py and
__init__.py: these five things are the entire surface the detector uses.
Nothing font-specific beyond literal character text (CHECK_GLYPHS is a text
check, not a font lookup), nothing else PDF-internal.

See docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class DetectablePage(Protocol):
    width: float
    height: float
    chars: list       # each: {"x0", "x1", "top", "bottom", "text"}
    rects: list        # each: {"x0", "x1", "top", "bottom", "width", "height", "fill", "stroke"}
    curves: list       # same shape as rects; [] is valid -- see the spec's
                       # "curves may legitimately be empty" note

    def extract_words(self) -> list: ...  # each: {"x0", "x1", "top", "bottom", "text"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_page_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/detect/page_protocol.py tests/test_page_protocol.py
git commit -m "feat: add DetectablePage protocol for scan detection interface"
```

---

### Task 2: Prove the rules run unmodified against a synthetic page

**Files:**
- Test: `tests/test_synthetic_page.py`

**Interfaces:**
- Consumes: `engine.detect.page_protocol.DetectablePage` (Task 1); `engine.detect.rules.detect` (existing, the per-page rule function — note this is a *different* function from `engine.detect.detect`, the document-level one Task 3 modifies).
- Produces: `FakeSyntheticPage`, a hand-built `DetectablePage`-shaped test double other tests in this task file reuse.

This fixture's geometry was verified by running it against the real, unmodified `rules.py` before writing this task (not guessed): a "Name" label with a 10-character underscore write-on line (exercises R5, which reads only `chars` + `extract_words()`), and a 20x20 filled square with an "Agree" caption (exercises R18, which reads only `rects` + `extract_words()`). Both rules only ever read the five `DetectablePage` members — this is what proves the contract.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthetic_page.py
"""Proves _detect_page() (rules.py) runs unmodified against a synthetic
DetectablePage -- the whole point of the interface in
docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.

No OCR or image-processing library is used here. FakeSyntheticPage is
hand-built with geometry chosen to exercise two rules that read different
parts of the DetectablePage surface:
  - R5 (engine/detect/rules.py, "runs of underscores are write-on lines")
    reads only `chars` and `extract_words()`.
  - R18 ("a checkbox drawn as a filled square, not a glyph") reads only
    `rects` and `extract_words()`.
Together they cover every DetectablePage member except `curves`, which the
spec says may legitimately stay empty for a v1 backend.

Run standalone with:  .venv/bin/python -m pytest tests/test_synthetic_page.py
"""
import unittest

from engine.detect.page_protocol import DetectablePage
from engine.detect.rules import detect as detect_page


class FakeSyntheticPage:
    """A hand-built DetectablePage, standing in for a future OCR/CV backend."""

    def __init__(self, width, height, chars, rects, curves, words):
        self.width = width
        self.height = height
        self.chars = chars
        self.rects = rects
        self.curves = curves
        self._words = words

    def extract_words(self):
        return self._words


def _make_fixture():
    """One page: a "Name" write-on line (R5) and an "Agree" checkbox (R18).

    Coordinates use pdfplumber's convention: `top`/`bottom` measured down
    from the page's top edge; page height H = 100, width W = 200.
    """
    H, W = 100.0, 200.0

    # "Name" label immediately followed by a 30pt-wide run of 10 underscore
    # characters -- long enough (>=25pt) to skip R5's short-run gates, so
    # the label comes straight from the word sitting on the same baseline
    # ending at (or just before) the run's own start.
    name_word = {"text": "Name", "x0": 10, "x1": 40, "top": 10, "bottom": 20}
    underscore_chars = []
    x = 42
    for _ in range(10):
        underscore_chars.append(
            {"text": "_", "x0": x, "x1": x + 3, "top": 10, "bottom": 20})
        x += 3

    # A 20x20 filled, unstroked square (inside R18's 18-32pt band, square
    # within its 8pt tolerance) with an "Agree" caption on the same
    # vertical midline (within R18's 2pt line tolerance) and a 5pt gap
    # (within its 11pt max caption gap).
    chk_rect = {"x0": 10, "x1": 30, "top": 50, "bottom": 70,
                "width": 20, "height": 20, "fill": True, "stroke": False}
    agree_word = {"text": "Agree", "x0": 35, "x1": 65, "top": 55, "bottom": 65}

    return FakeSyntheticPage(
        width=W, height=H,
        chars=underscore_chars,
        rects=[chk_rect],
        curves=[],
        words=[name_word, agree_word],
    )


class TestSyntheticPage(unittest.TestCase):
    def test_fixture_satisfies_the_protocol(self):
        self.assertIsInstance(_make_fixture(), DetectablePage)

    def test_r5_write_on_line_from_synthetic_chars(self):
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        r5 = [f for f in fields if f["rule"] == "R5"]
        self.assertEqual(len(r5), 1)
        self.assertEqual(r5[0]["type"], "text")
        self.assertEqual(r5[0]["label"], "Name")
        self.assertEqual(r5[0]["page"], 1)

    def test_r18_checkbox_from_synthetic_rects(self):
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        r18 = [f for f in fields if f["rule"] == "R18"]
        self.assertEqual(len(r18), 1)
        self.assertEqual(r18[0]["type"], "checkbox")
        self.assertEqual(r18[0]["label"], "Agree")
        self.assertEqual(r18[0]["page"], 1)

    def test_exactly_two_fields_total(self):
        # Guards against the fixture accidentally tripping an unrelated rule.
        fields, _carry = detect_page(_make_fixture(), pno=1, carry_in=None)
        self.assertEqual(len(fields), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_synthetic_page.py -v`
Expected: FAIL — at this point `engine.detect.page_protocol` exists (Task 1 landed) but nothing here has been implemented yet, so this file itself IS the implementation; there's no separate "make it pass" step. Skip to Step 3.

- [ ] **Step 3: Confirm it already passes (no separate implementation needed)**

`FakeSyntheticPage` and its geometry are the deliverable — there is no
production code change in this task, only the test double and the proof it
provides.

Run: `.venv/bin/python -m pytest tests/test_synthetic_page.py -v`
Expected: PASS, 4 tests

- [ ] **Step 4: Commit**

```bash
git add tests/test_synthetic_page.py
git commit -m "test: prove rules.py runs unmodified against a synthetic page"
```

---

### Task 3: Wire `page_backend` into `detect()` and extend the output contract

**Files:**
- Modify: `engine/detect/__init__.py:113-154` (the `detect()` function)
- Modify: `eval/contracts/fields.schema.json`
- Test: `tests/test_ocr_backend.py`

**Interfaces:**
- Consumes: `FakeSyntheticPage` (Task 2, `tests/test_synthetic_page.py`) as the stub backend's return value in this task's own tests.
- Produces: `detect(pdf_path, page_backend=None)` — `page_backend`, when given, is `Callable[[pdfplumber.page.Page, int], DetectablePage | None]`, called once per page `_page_is_scanned()` flags. A `DetectablePage` return value is scored by the same rules and its fields get `origin: "ocr"`; `None` preserves today's per-page scanned behavior. Document-level `notice` precedence: `scanned` (today's unchanged majority-scanned check) outranks `ocr_assisted` (at least one page's backend call succeeded) outranks `no_fields` (unchanged).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ocr_backend.py
"""Tests for detect()'s optional page_backend hook
(docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md).

Reuses the exact FakeSyntheticPage fixture from tests/test_synthetic_page.py
as a stub backend's return value -- this test file is about detect()'s own
orchestration (notice precedence, origin tagging, ID assignment), not about
re-proving the rules work on synthetic geometry (that's test_synthetic_page.py).

Run standalone with:  .venv/bin/python -m pytest tests/test_ocr_backend.py
"""
import io
import unittest

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from engine.detect import detect
from tests.test_synthetic_page import _make_fixture


def _image_only_pdf(n_pages=1):
    """A PDF whose pages are each a single page-filling image, no text.

    Identical to tests/test_scanned.py's own helper of the same name --
    duplicated rather than imported, matching that file's existing pattern
    of each test file owning its small PDF-building helpers.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    img = ImageReader(Image.new("RGB", (600, 800), (235, 235, 235)))
    for _ in range(n_pages):
        c.drawImage(img, 0, 0, width=w, height=h)
        c.showPage()
    c.save()
    return buf.getvalue()


def _write(tmpdir, name, data):
    p = tmpdir / name
    p.write_bytes(data)
    return str(p)


class TestOcrBackend(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_no_backend_preserves_todays_scanned_notice(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path)
        self.assertEqual(out["notice"]["code"], "scanned")
        self.assertEqual(out["fields"], [])

    def test_backend_produces_ocr_tagged_fields(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        self.assertEqual(len(out["fields"]), 2)
        for f in out["fields"]:
            self.assertEqual(f["origin"], "ocr")

    def test_backend_sets_ocr_assisted_notice(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        self.assertEqual(out["notice"]["code"], "ocr_assisted")

    def test_backend_declining_falls_back_to_scanned(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: None)
        self.assertEqual(out["notice"]["code"], "scanned")
        self.assertEqual(out["fields"], [])

    def test_backend_ids_follow_the_normal_scheme(self):
        path = _write(self.tmp, "scan.pdf", _image_only_pdf())
        out = detect(path, page_backend=lambda pg, i: _make_fixture())
        ids = {f["id"] for f in out["fields"]}
        self.assertEqual(ids, {"p1_name", "p1_chk"})

    def test_real_text_pdf_is_never_sent_to_the_backend(self):
        # A backend that always raises must never be called on a real,
        # non-scanned page -- _page_is_scanned() gates every call.
        def _boom(pg, i):
            raise AssertionError("backend called on a non-scanned page")
        out = detect("fixtures/safer.pdf", page_backend=_boom)
        self.assertNotIn("notice", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ocr_backend.py -v`
Expected: FAIL — `TypeError: detect() got an unexpected keyword argument 'page_backend'`

- [ ] **Step 3: Modify `eval/contracts/fields.schema.json`**

Change the `origin` line inside the `fields` item schema from:

```json
          "origin": {"enum": ["detected", "user_added", "user_moved"]}
```

to:

```json
          "origin": {"enum": ["detected", "user_added", "user_moved", "ocr"]}
```

- [ ] **Step 4: Modify `engine/detect/__init__.py`**

Replace the `detect()` function (currently lines 113-154) with:

```python
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
    behavior exactly -- see
    docs/superpowers/specs/2026-09-12-scan-detection-interface-design.md.
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
```

Also add the new message constant next to `NO_FIELDS_MESSAGE` (after line 41, before the `_page_is_scanned` function):

```python
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
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ocr_backend.py -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add engine/detect/__init__.py eval/contracts/fields.schema.json tests/test_ocr_backend.py
git commit -m "feat: add optional page_backend hook to detect() for OCR-assisted pages"
```

---

### Task 4: Confirm zero regression on everything that does not opt in

**Files:**
- None modified — this task only runs and reads existing suites.

**Interfaces:**
- Consumes: nothing new: `tests/test_scanned.py`, the full `pytest` suite, and `./scripts/verify.sh` (the project's real merge gate — runs the test suite, then scores `eval/corpus/tuning` + `eval/holdout` and checks the result against `scores/HEAD_BASELINE.json`), all as they exist today.

- [ ] **Step 1: Run `tests/test_scanned.py` specifically**

Run: `.venv/bin/python -m pytest tests/test_scanned.py -v`
Expected: PASS, all 5 tests, identical to their behavior before this plan (this file was not modified)

- [ ] **Step 2: Run the project's full verify gate**

Run: `./scripts/verify.sh`
Expected: `VERIFY OK` — the test suite passes, and the tuning/holdout f1/precision/recall numbers printed at the end are identical to a run from before this plan's changes. `page_backend` defaults to `None` everywhere in `eval/`, so no scored file's detection should change at all; any score movement here means something in Task 3 leaked into the default (no-backend) path and must be fixed before proceeding.

- [ ] **Step 3: If everything is green, this plan is complete — no commit needed for this task**

This task is verification-only. If any of the above fails, stop and fix the
regression before considering sub-project 1 done — do not proceed to
sub-projects 2/3 on a broken interface.
