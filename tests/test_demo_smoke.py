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
