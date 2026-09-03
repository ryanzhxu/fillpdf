"""Golden-image render tests for every field type FormFill emits: text,
multiline, checkbox -- filled and unfilled, plus an explicitly checked
checkbox (the "filled" checkbox variant below).

Before this file, there were zero render tests anywhere in the repo (no
reference to pdfium or pdf.js in tests/ or eval/): correctness rested on
someone eyeballing macOS Preview. This closes that gap the way a comparable
project (acroforge) does -- golden-image tests per field type, in both
pdfium and pdf.js.

Design, and why:

  - Fixture: a small, purpose-built page (tests/render/fixture.py), not the
    8-page SAFER form, so goldens stay a few KB each and a failure is easy
    to look at. Each field's rect is also where its guide box is printed on
    the flat page -- exactly like a real paper form -- so a crop at that
    rect shows directly whether the injected widget landed on its box.

  - Injection: tools/inject.mjs is the one real implementation (extracted
    from the demo/index.html "pdf-lib spike"); this suite calls it via
    tools/inject_cli.mjs as a subprocess (tests/render/inject.py), never a
    reimplementation.

  - Engines: pypdfium2 is the must-have and is fully golden-tested here.
    pdf.js is best-effort -- it worked cleanly in this environment (see
    tools/render_pdfjs_cli.cjs for the one real wrinkle found along the
    way), so it also gets full golden coverage, with its OWN goldens
    (pdfium and pdf.js are different rasterizers and are never compared to
    each other). If node/canvas is not usable in some future environment,
    the pdf.js tests skip rather than fail, so pdfium coverage stays green
    regardless of that.

  - Goldens are crops (field rect + 4pt padding), not full pages, so each
    committed PNG is small and a diff is about one field, not a whole page.

  - Comparison tolerance is explained in tests/render/compare.py, based on
    real same-run (0.0 diff) and cross-engine (~1% of pixels differ by >10)
    measurements taken while building this suite.

  - Un-flattened PDFs (flatten=False) are what get golden-tested: that is
    the higher-risk path (a widget with a missing/wrong appearance stream
    can be invisible in one engine and fine in another) and it is what the
    "fillable.pdf" download in demo/index.html actually produces.
    test_flatten_smoke below exercises flatten=True once, separately, to
    confirm that path also works without doubling the golden matrix.

Regenerating goldens: run with FORMFILL_REGEN_GOLDENS=1 to write every crop
below to tests/render/goldens/ instead of comparing. Review with `git diff`
before committing -- this bypasses the tolerance check, it does not double
as a "nothing changed" verification.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import fixture
from compare import compare_to_golden, write_golden
from geometry import crop_rect
from inject import inject
from pdfium_render import render_page as pdfium_render_page
from pdfjs_render import render_page as pdfjs_render_page

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent
GOLDENS = HERE / "goldens"
DIFF_DIR = HERE / "_diffs"
SCALE = 3.0
REGEN = os.environ.get("FORMFILL_REGEN_GOLDENS") == "1"

FIELD_TYPES = ["text", "checkbox", "multiline"]
VARIANTS = ["empty", "filled"]

_pdfjs_status: bool | None = None


def _pdfjs_available() -> bool:
    """Probe once per test session whether node + canvas + pdfjs-dist actually load.

    node_modules is committed to being present in this repo's own dev setup,
    but `canvas` is a native addon (prebuilt binaries, not guaranteed on
    every platform) -- so this is a real runtime probe, not just a
    `shutil.which("node")` check.
    """
    global _pdfjs_status
    if _pdfjs_status is None:
        try:
            result = subprocess.run(
                ["node", "-e", "require('canvas'); require('pdfjs-dist/legacy/build/pdf.js')"],
                capture_output=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            _pdfjs_status = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            _pdfjs_status = False
    return _pdfjs_status


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    """Build the flat fixture once, then inject each variant once each (3 subprocess calls total)."""
    tmp = tmp_path_factory.mktemp("render_fixture")
    flat = tmp / "flat.pdf"
    fixture.build_flat_pdf(flat)

    empty_pdf = tmp / "empty.pdf"
    filled_pdf = tmp / "filled.pdf"
    filled_flattened_pdf = tmp / "filled_flattened.pdf"

    inject(flat, fixture.fields_doc(filled=False), empty_pdf, flatten=False)
    inject(flat, fixture.fields_doc(filled=True), filled_pdf, flatten=False)
    inject(flat, fixture.fields_doc(filled=True), filled_flattened_pdf, flatten=True)

    return {"empty": empty_pdf, "filled": filled_pdf, "filled_flattened": filled_flattened_pdf}


def test_fixture_matches_contract_schema():
    """The fixture's fields.json must be shaped exactly like engine.detect.detect()'s
    real output, or the injector below would be tested against a lookalike
    instead of the real contract."""
    import json

    from jsonschema import validate

    schema = json.loads((REPO_ROOT / "eval" / "contracts" / "fields.schema.json").read_text())
    validate(instance=fixture.fields_doc(filled=False), schema=schema)
    validate(instance=fixture.fields_doc(filled=True), schema=schema)


@pytest.mark.parametrize("field_type", FIELD_TYPES)
@pytest.mark.parametrize("variant", VARIANTS)
def test_pdfium_golden(pdfs, field_type, variant):
    pdf_bytes = pdfs[variant].read_bytes()
    img, _width_pt, height_pt = pdfium_render_page(pdf_bytes, scale=SCALE)
    rect = fixture.FIELD_RECTS[field_type]
    crop = crop_rect(img, height_pt, rect, SCALE)

    name = f"pdfium_{variant}_{field_type}"
    golden = GOLDENS / f"{name}.png"
    if REGEN:
        write_golden(golden, crop)
        pytest.skip(f"FORMFILL_REGEN_GOLDENS=1: wrote {golden}")
    compare_to_golden(golden, crop, diff_dir=DIFF_DIR, name=name)


@pytest.mark.parametrize("field_type", FIELD_TYPES)
@pytest.mark.parametrize("variant", VARIANTS)
def test_pdfjs_golden(pdfs, tmp_path, field_type, variant):
    if not _pdfjs_available():
        pytest.skip("pdf.js render path unavailable (node/canvas not usable in this environment)")

    out_png = tmp_path / f"{variant}_full.png"
    img = pdfjs_render_page(pdfs[variant], out_png, scale=SCALE)
    _width_pt, height_pt = fixture.PAGE_SIZE
    rect = fixture.FIELD_RECTS[field_type]
    crop = crop_rect(img, height_pt, rect, SCALE)

    name = f"pdfjs_{variant}_{field_type}"
    golden = GOLDENS / f"{name}.png"
    if REGEN:
        write_golden(golden, crop)
        pytest.skip(f"FORMFILL_REGEN_GOLDENS=1: wrote {golden}")
    compare_to_golden(golden, crop, diff_dir=DIFF_DIR, name=name)


def test_flatten_smoke(pdfs):
    """flatten=True bakes appearances into the page content stream and drops
    the AcroForm -- a different code path from every test above. This does
    not golden-test it (that would double the matrix for one boolean); it
    just confirms pdfium still draws real content (not a blank page) for
    the filled text field and the checked checkbox after flattening."""
    pdf_bytes = pdfs["filled_flattened"].read_bytes()
    img, _width_pt, height_pt = pdfium_render_page(pdf_bytes, scale=SCALE)

    for field_type in ("text", "checkbox", "multiline"):
        rect = fixture.FIELD_RECTS[field_type]
        crop = crop_rect(img, height_pt, rect, SCALE).convert("L")
        darkest = crop.getextrema()[0]
        assert darkest < 200, (
            f"flattened {field_type} field looks blank (darkest pixel value {darkest}); "
            f"expected visible ink from the filled/checked value"
        )
