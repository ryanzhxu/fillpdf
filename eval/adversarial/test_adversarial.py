"""Acceptance tests for the adversarial corpus and runner.

Run with:
    .venv/bin/python -m pytest eval/adversarial/test_adversarial.py -v -s
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

import generate  # noqa: E402
import run as runner  # noqa: E402

CORPUS_DIR = generate.CORPUS_DIR
CPU_SECONDS = runner.CPU_SECONDS
KNOWN_STATUSES = {"ok", "timeout", "memory", "crash", "rejected"}


@pytest.fixture(scope="module")
def corpus():
    """Build the corpus once for the whole test module. Deterministic."""
    generate.main()
    return CORPUS_DIR


@pytest.fixture(scope="module")
def manifest(corpus):
    return {e["file"]: e for e in json.loads((corpus / "manifest.json").read_text())}


@pytest.fixture(scope="module")
def results(corpus):
    """Runs run.py as a real subprocess (not an in-process call) so that a
    hard crash of the harness itself would fail this fixture rather than take
    the test process down with it."""
    proc = subprocess.run(
        [sys.executable, str(HERE / "run.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    data = json.loads((HERE / "results.json").read_text())
    return data, proc.returncode


# --------------------------------------------------------------------------
# 1. Every generated file exists and is the size/shape intended.
# --------------------------------------------------------------------------

def test_all_manifest_files_exist(corpus, manifest):
    for name in manifest:
        path = corpus / name
        assert path.exists(), f"{name} missing from corpus"


def test_decompression_bomb_small_on_disk_huge_decompressed(corpus):
    import zlib
    path = corpus / "decompression_bomb.pdf"
    size = path.stat().st_size
    assert size < 1_000_000, f"bomb should be small on disk, got {size} bytes"
    raw = path.read_bytes()
    start = raw.index(b"stream\n") + len(b"stream\n")
    end = raw.index(b"\nendstream", start)
    decompressed = zlib.decompress(raw[start:end])
    assert len(decompressed) > 100_000_000, (
        f"expected a huge decompressed payload, got {len(decompressed)} bytes")


def test_page_flood_has_5000_pages(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "page_flood.pdf"), strict=False)
    assert len(reader.pages) == 5000


def test_rect_flood_has_one_page_100000_rects(corpus):
    import pdfplumber
    with pdfplumber.open(str(corpus / "rect_flood.pdf")) as pdf:
        assert len(pdf.pages) == 1
        assert len(pdf.pages[0].rects) == 100_000


def test_char_flood_has_500000_characters(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "char_flood.pdf"), strict=False)
    assert len(reader.pages) == 1
    content = reader.pages[0].get_contents().get_data()
    assert content.count(b"A") == 500_000


def test_deep_nesting_has_2000_chained_xobjects(corpus):
    raw = (corpus / "deep_nesting.pdf").read_bytes()
    assert raw.count(b"/Subtype /Form") == 2000


def test_encrypted_is_actually_encrypted(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "encrypted.pdf"))
    assert reader.is_encrypted


def test_truncated_has_no_trailer(corpus):
    raw = (corpus / "truncated.pdf").read_bytes()
    assert len(raw) > 0
    assert b"%%EOF" not in raw, "truncated file should be cut before the trailer"


def test_not_a_pdf_has_no_pdf_header(corpus):
    raw = (corpus / "not_a_pdf.pdf").read_bytes()
    assert not raw.startswith(b"%PDF")
    assert b"<html" in raw.lower()


def test_empty_file_is_zero_bytes(corpus):
    assert (corpus / "empty_file.pdf").stat().st_size == 0


def test_zero_size_page_mediabox(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "zero_size_page.pdf"))
    box = reader.pages[0].mediabox
    assert (float(box.width), float(box.height)) == (0.0, 0.0)


def test_huge_page_mediabox(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "huge_page.pdf"))
    box = reader.pages[0].mediabox
    assert (float(box.width), float(box.height)) == (14400.0, 14400.0)


def test_inverted_mediabox_is_inverted(corpus):
    import pypdf
    reader = pypdf.PdfReader(str(corpus / "inverted_mediabox.pdf"))
    box = reader.pages[0].mediabox
    assert float(box.left) > float(box.right)
    assert float(box.bottom) > float(box.top)


def test_cyclic_references_call_each_other(corpus):
    raw = (corpus / "cyclic_references.pdf").read_bytes()
    assert b"/XB Do" in raw and b"/XA Do" in raw


def test_javascript_openaction_present(corpus):
    raw = (corpus / "javascript_openaction.pdf").read_bytes()
    assert b"/OpenAction" in raw
    assert b"/JavaScript" in raw


# --------------------------------------------------------------------------
# 2 & 3. run.py classifies every file with no unclassified entries, and the
# parent process (run.py itself) survives every one of them.
# --------------------------------------------------------------------------

def test_run_classifies_every_file_with_no_gaps(results, manifest):
    data, returncode = results
    assert set(data.keys()) == set(manifest.keys()), (
        "run.py did not produce a result for every corpus file")
    for name, record in data.items():
        assert record.get("status") in KNOWN_STATUSES, (
            f"{name}: unclassified status {record.get('status')!r}")
        assert not record.get("harness_error"), f"{name}: {record.get('detail')}"

    # The single outcome the harness must never allow silently through:
    # a result that returned successfully but does not match the detector's
    # own output contract (fields.schema.json).
    violations = [n for n, r in data.items() if r.get("contract_violation")]
    assert not violations, f"schema-violating 'successful' results: {violations}"


def test_run_py_parent_process_survives(results):
    """run.py is itself the parent of every worker subprocess it spawns. If a
    hostile file could take the parent down, run.py would not be the one
    exiting cleanly here -- the OS would report it killed by a signal."""
    _, returncode = results
    assert returncode is not None
    assert returncode >= 0, (
        f"run.py (the parent) appears to have been killed by a signal "
        f"(returncode={returncode}); a hostile file must never do this")
    assert returncode in (0, 1), f"unexpected run.py exit code {returncode}"


# --------------------------------------------------------------------------
# 4. rect-flood specifically: guarded, or killed by the CPU cap, but never
# left hanging or unclassified. Report which one actually happened.
# --------------------------------------------------------------------------

def test_rect_flood_does_not_hang(results):
    data, _ = results
    record = data["rect_flood.pdf"]
    assert record["status"] in {"ok", "timeout"}, (
        f"rect_flood.pdf must be guarded or CPU-capped, not {record['status']}: {record}")

    if record["status"] == "ok":
        outcome = (f"GUARDED: the detector's 2000-rects/page guard stopped the "
                   f"quadratic cell-recovery loop; detect() returned normally in "
                   f"{record['elapsed_s']}s (fields={record.get('fields')})")
        assert record["elapsed_s"] < CPU_SECONDS, (
            "status is 'ok' but took as long as the CPU cap -- suspicious")
    else:
        outcome = (f"CPU-CAPPED: the guard did not stop it; the detector was killed "
                   f"after {record['elapsed_s']}s by the {CPU_SECONDS}s CPU cap")
    print(f"\n[rect_flood.pdf] {outcome}")
