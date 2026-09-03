"""Smoke tests for eval/blind.py. Self-contained -- builds its own tiny
manifest + fixture directory rather than depending on the 245MB fetched
corpus, so this stays fast and does not change behavior when the corpus grows.
"""
import json
import shutil
from pathlib import Path

from eval.blind import _flat_real_pdfs, main, probe_one

REPO_ROOT = Path(__file__).resolve().parent.parent
SAFER_PDF = REPO_ROOT / "fixtures" / "safer.pdf"


def _tiny_corpus(tmp_path):
    (tmp_path / "flat.pdf").write_bytes(SAFER_PDF.read_bytes())
    (tmp_path / "fillable.pdf").write_bytes(SAFER_PDF.read_bytes())
    manifest = {
        "records": [
            {"file": "flat.pdf", "verdict": "flat-wordlike",
             "checkbox_glyphs": 81, "underscore_chars": 9,
             "thin_h_rects": 279, "thin_v_rects": 285},
            {"file": "fillable.pdf", "verdict": "fillable-other"},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_selects_only_flat_verdicts(tmp_path):
    _tiny_corpus(tmp_path)
    picked = _flat_real_pdfs(str(tmp_path))
    assert [Path(p).name for p in picked] == ["flat.pdf"]


def test_probe_one_runs_the_live_detector():
    record = probe_one(str(SAFER_PDF))
    assert record["crashed"] is False
    assert record["fields"] > 0
    assert record["pages"] == 8
    assert "label_plausibility" in record  # SAFER has text fields
    assert "glyph_coverage" in record       # SAFER has checkboxes too


def test_probe_one_reports_a_crash_without_raising():
    bad = probe_one("/no/such/file.pdf")
    assert bad["crashed"] is True
    assert "error" in bad


def test_structured_vs_prose_split(tmp_path, capsys):
    _tiny_corpus(tmp_path)
    rc = main(["--dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PDFs probed" in out


def test_no_pdfs_found_is_a_clean_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--dir", str(empty)]) == 1
