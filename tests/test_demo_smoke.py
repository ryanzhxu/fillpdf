"""Smoke test for demo/demo.py -- the one artifact a user actually opens.

demo/demo.py has no test coverage of its own: eval/ and engine/ are tested
thoroughly, but the demo entry point is not, which is exactly how it rotted
silently (a refactor moved engine/detect/rules.py out from under the demo's
old import and nothing here would have noticed). This file exists so that
class of failure gets caught by `pytest` instead of by a user.

Run with:
    .venv/bin/python -m pytest tests/test_demo_smoke.py -v
"""
import importlib.util
import json
import socketserver
import webbrowser
from pathlib import Path

import pytest
from jsonschema import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "fixtures" / "safer.pdf"
SCHEMA = json.loads((REPO_ROOT / "eval" / "contracts" / "fields.schema.json").read_text())


def _load_demo_module():
    """Import demo/demo.py by file path.

    demo/ is not a package (no __init__.py), and demo.py is not meant to be
    run as a script here -- loading it this way executes only module-level
    code (imports, constant/function definitions), never the
    `if __name__ == "__main__":` block that calls build() and serve().
    """
    spec = importlib.util.spec_from_file_location("demo_demo", REPO_ROOT / "demo" / "demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def built(tmp_path_factory, monkeypatch_module):
    """Import demo.py, point its output at a scratch dir, and run build() once.

    build() writes to the module-level OUT constant (normally a fixed
    demo/out/ next to demo.py). Redirecting that constant to a pytest tmp
    dir is what makes this test possible without touching the repo or
    leaving files behind -- build() itself takes no output-directory
    argument.

    A guard on webbrowser.open and socketserver.TCPServer.__init__ makes the
    "no server, no browser" requirement an assertion instead of an accident
    of "we just didn't call serve()": if build() ever grew a code path that
    touched either, this fixture would fail loudly instead of popping a
    browser tab in CI.
    """
    def _blocked(*a, **k):
        raise AssertionError("demo.build() must not open a server or a browser")

    monkeypatch_module.setattr(webbrowser, "open", _blocked)
    monkeypatch_module.setattr(socketserver.TCPServer, "__init__", _blocked)

    mod = _load_demo_module()
    monkeypatch_module.setattr(mod, "OUT", tmp_path_factory.mktemp("demo_out"))
    mod.build(FIXTURE)
    doc = json.loads((mod.OUT / "fields.json").read_text())
    return mod, doc


@pytest.fixture(scope="module")
def monkeypatch_module():
    # pytest's built-in `monkeypatch` fixture is function-scoped; this test
    # only needs one build() per module, so provide a module-scoped variant
    # rather than re-running the (cheap, but not free) detection per test.
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_build_writes_expected_files(built):
    mod, _ = built
    out = mod.OUT
    assert (out / "source.pdf").exists()
    assert (out / "index.html").exists()
    assert (out / "fields.json").exists()


def test_fields_json_matches_contract_schema(built):
    _, doc = built
    validate(instance=doc, schema=SCHEMA)


def test_plausible_field_count_and_every_field_labelled(built):
    _, doc = built
    fields = doc["fields"]

    # Not zero (the import-breakage this test guards against would crash
    # before producing anything at all) and not absurd (a detector gone
    # wrong -- e.g. one field per character -- would produce thousands).
    assert 50 <= len(fields) <= 500

    # 81 of safer.pdf's fields are checkboxes. Before a real fix, checkboxes
    # could carry no label at all -- shipping unusable, unidentifiable boxes
    # a user has no way to tell apart. Every field, text or checkbox, must
    # have a real label.
    unlabelled = [f["id"] for f in fields if not f.get("label", "").strip()]
    assert not unlabelled, f"{len(unlabelled)} field(s) with no label: {unlabelled[:10]}"

    n_checkbox = sum(1 for f in fields if f["type"] == "checkbox")
    assert n_checkbox > 0


def test_demo_calls_the_same_detector_the_eval_harness_scores(built):
    """demo.py must call engine.detect.detect, not its own re-implementation.

    It used to re-implement the per-page loop and id assignment, which could
    silently drift from what eval/ measures. Compare field for field against
    a direct call, rather than only checking that the demo produced
    *something* plausible.
    """
    from engine.detect import detect

    _, demo_doc = built
    direct_doc = detect(str(FIXTURE))
    assert demo_doc == direct_doc


def test_served_index_html_surfaces_a_detector_notice(built):
    """The demo must show detect()'s notice, not swallow it.

    detect() flags a scanned / image-only PDF with a `notice` the UI can show.
    If index.html never reads it, a scan renders as a blank page with no boxes
    and no reason why -- the one 'app silently does nothing' case value item #5
    forbids. Assert the wiring is present so it cannot rot away unnoticed.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "d.notice" in html, "index.html never reads the detector notice"
    assert "showNotice" in html, "index.html has no notice renderer"
    assert "n.message" in html or "notice.message" in html, \
        "index.html renders no notice message text"


def test_served_index_html_reports_a_failed_load(built):
    """A failed fields.json load must surface, not leave #main stuck on 'rendering…'.

    boot() runs on page load and depends on fields.json for every page and box.
    If the fetch fails (missing file, 404, non-JSON body), an un-caught
    rejection leaves #main showing 'rendering…' forever with no reason why --
    the load-path twin of the build() catch, and the 'app silently does
    nothing' case item #5 forbids. Assert the catch and its user-visible
    message are wired up so this cannot rot back into a silent failure.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "[boot] could not load fields.json" in html, \
        "boot() no longer catches a failed fields.json load -- it is silent again"
    assert "Could not load the form" in html, \
        "boot() has no user-visible message when fields.json fails to load"


def test_served_index_html_reports_a_failed_build(built):
    """A failed inject/import/corrupt PDF must surface, not leave Download silent.

    build() runs inside a click handler. If injectFields rejects -- a failed
    dynamic import, a corrupt or encrypted source.pdf, a pdf-lib load error --
    an un-caught rejection produces no download and no message: the button
    looks dead. Assert the catch and its user-visible message are wired up so
    this cannot rot into the 'app silently does nothing' case item #5 forbids.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "async function build(flatten){\n  try {" in html, \
        "build() no longer wraps its work in a try -- a failed build is silent"
    assert "Could not build the PDF" in html, \
        "build() has no user-visible failure message"


def test_served_index_html_navigates_in_reading_order(built):
    """Tab and "Next field" must follow the page, not the detector's rule order.

    detect() emits fields grouped by rule, so raw FIELDS order sends the
    keyboard jumping up and down the sheet (safer.pdf page 2 tops go
    636 -> 534 -> 670 -> 568 ...). The demo sorts a navOrder in reading order
    and both the DOM (native Tab) and the "Next field" button walk it. Assert
    the wiring is present so a refactor cannot silently drop it and bring the
    jumping back.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "readingOrder" in html, "index.html has no reading-order comparator"
    assert "navOrder" in html, "index.html builds no reading-order navigation"
    # draw() must append boxes by navOrder (native Tab = DOM order), and
    # "Next field" must walk navOrder rather than a raw FIELDS index.
    assert "navOrder.forEach" in html, "draw() no longer appends in reading order"
    assert "navOrder.indexOf(cursor)" in html, "Next field no longer walks reading order"


def test_served_index_html_renders_multiline_as_a_textarea(built):
    """A multiline field must get a textarea, not a one-line input.

    detect() emits `type: "multiline"` for comment / description blocks (R12,
    common: 4 of the first 40 tuning forms, one with 16 of them), and
    tools/inject.mjs wraps their value in the downloaded PDF. If the demo drew
    them as a single-line <input>, the user could type only one line that
    scrolls off the box -- the editing experience would not match the file it
    produces. Assert the textarea path is wired, and that the two spots that
    would break with a textarea child are guarded: "Next field" must focus
    input,textarea (a multiline box has no <input>), and the keydown guard must
    treat a focused TEXTAREA as typing so Backspace does not delete the field.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "f.type === 'multiline'" in html, "draw() has no multiline branch"
    assert "createElement('textarea')" in html, "multiline field is not a textarea"
    assert "querySelector('input,textarea')" in html, \
        "Next field would throw on a multiline box (no <input> child)"
    assert "ae.tagName === 'TEXTAREA'" in html, \
        "keydown guard would delete the field on Backspace inside a textarea"


def test_demo_pipeline_carries_a_scanned_notice_to_fields_json(tmp_path, monkeypatch):
    """End to end: a scanned PDF through demo.build() lands the notice in fields.json.

    The demo dumps detect()'s output verbatim, and the browser reads notice
    from fields.json, so this proves the notice reaches the file the page loads.
    """
    import io
    import socketserver as _ss
    import webbrowser as _wb
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    def _blocked(*a, **k):
        raise AssertionError("demo.build() must not open a server or a browser")

    monkeypatch.setattr(_wb, "open", _blocked)
    monkeypatch.setattr(_ss.TCPServer, "__init__", _blocked)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    img = ImageReader(Image.new("RGB", (600, 800), (235, 235, 235)))
    c.drawImage(img, 0, 0, width=w, height=h)
    c.showPage()
    c.save()
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(buf.getvalue())

    mod = _load_demo_module()
    monkeypatch.setattr(mod, "OUT", tmp_path / "demo_out")
    mod.build(scan)

    doc = json.loads((mod.OUT / "fields.json").read_text())
    assert doc.get("notice", {}).get("code") == "scanned"
    assert doc["notice"]["message"].strip()


def test_served_index_html_surfaces_deferred_appearances(built):
    """The demo must tell the user when values can't be baked into the file.

    tools/inject.mjs falls back to NeedAppearances (and an un-flattened save)
    when a value has characters the standard PDF font cannot draw, returning
    `deferredAppearances`. If index.html ignores that flag, a person who typed
    non-Latin text clicks "Download filled", gets a fillable (not flattened)
    file whose text their browser may not render, and is told nothing -- the
    silent-degradation case value item #5 forbids. Assert the wiring is present.
    """
    mod, _ = built
    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "deferredAppearances" in html, "index.html never reads deferredAppearances"
    assert "!deferredAppearances" in html, \
        "index.html does not correct the filename when appearances are deferred"


def test_served_index_html_renders_a_group_as_a_radio(built):
    """A grouped checkbox (a Yes/No answer) must be a radio, not a checkbox.

    detect() tags a Yes/No question's two options with a shared `group`, and
    tools/inject.mjs injects them as one AcroForm radio group so a person
    cannot tick both answers. If the demo drew them as independent checkboxes,
    the on-screen form would let a person tick both -- not matching the file it
    produces, and wrong in law. Assert the radio branch is wired: a shared
    `name` makes the browser enforce exclusivity, and picking one clears the
    others in FIELDS so exactly one value is true when the PDF is built.
    """
    mod, _ = built
    doc = json.loads((mod.OUT / "fields.json").read_text())
    assert any(f.get("group") for f in doc["fields"]), \
        "the fixture should produce at least one Yes/No radio group"

    html = (mod.OUT / "index.html").read_text(encoding="utf-8")
    assert "if (f.group){" in html, "draw() has no grouped-checkbox branch"
    assert "inp.type='radio'; inp.name=f.group" in html, \
        "a grouped checkbox is not rendered as a radio sharing the group name"
    assert "g.group===f.group" in html, \
        "picking a radio does not clear its group siblings in FIELDS"
