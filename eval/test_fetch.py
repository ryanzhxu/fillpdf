"""Acceptance tests for eval/fetch.py. No network access required -- every
network call the fetcher makes is mocked. Run with:
    .venv/bin/python -m pytest eval/test_fetch.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.fetch import (
    FetchError,
    classify,
    fetch,
    _robots_allowed,
)
from eval.synth.generate import generate as synth_generate

REPO_ROOT = Path(__file__).resolve().parent.parent
SAFER_PDF = REPO_ROOT / "fixtures" / "safer.pdf"
ADVERSARIAL_CORPUS = REPO_ROOT / "eval" / "adversarial" / "corpus"


def _ensure_adversarial_corpus():
    if not (ADVERSARIAL_CORPUS / "truncated.pdf").exists():
        subprocess.run(
            [sys.executable, "-m", "eval.adversarial.generate"],
            cwd=str(REPO_ROOT), check=True,
        )


# --------------------------------------------------------------------------
# 1. classify() on the one real, hand-verified form
# --------------------------------------------------------------------------

def test_classify_safer_pdf_is_flat_wordlike():
    record = classify(SAFER_PDF)
    assert record["verdict"] == "flat-wordlike"
    assert record["has_acroform"] is False
    assert abs(record["thin_h_rects"] - 279) <= 5
    assert abs(record["thin_v_rects"] - 285) <= 5
    assert abs(record["checkbox_glyphs"] - 81) <= 3
    assert abs(record["underscore_chars"] - 209) <= 5
    assert record["producer"] and "word" in record["producer"].lower()


# --------------------------------------------------------------------------
# 2. classify() on a generated synthetic form
# --------------------------------------------------------------------------

def test_classify_synthetic_form_is_flat_wordlike(tmp_path):
    pdf_path, _truth_path = synth_generate(1, tmp_path)
    record = classify(pdf_path)
    assert record["verdict"] == "flat-wordlike"
    assert record["has_acroform"] is False
    assert record["thin_h_rects"] > 20
    assert record["thin_v_rects"] > 20


# --------------------------------------------------------------------------
# 3. classify() on adversarial files never raises
# --------------------------------------------------------------------------

def test_classify_adversarial_never_raises():
    _ensure_adversarial_corpus()
    names = [
        "truncated.pdf", "not_a_pdf.pdf", "empty_file.pdf",
        "encrypted.pdf", "zero_size_page.pdf", "inverted_mediabox.pdf",
        "javascript_openaction.pdf",
    ]
    for name in names:
        path = ADVERSARIAL_CORPUS / name
        if not path.exists():
            continue
        record = classify(path)  # must not raise
        assert record["verdict"] in ("unusable", "scan"), (name, record["verdict"])


def test_classify_truncated_file_is_unusable():
    _ensure_adversarial_corpus()
    record = classify(ADVERSARIAL_CORPUS / "truncated.pdf")
    assert record["verdict"] == "unusable"
    assert record["reason"]


def test_classify_encrypted_file_is_unusable():
    _ensure_adversarial_corpus()
    record = classify(ADVERSARIAL_CORPUS / "encrypted.pdf")
    assert record["verdict"] == "unusable"
    assert "encrypt" in record["reason"].lower()


# --------------------------------------------------------------------------
# 4. robots.txt handling, via a local stub -- no live request
# --------------------------------------------------------------------------

def test_robots_disallowed_path_is_refused(monkeypatch):
    stub = "User-agent: *\nDisallow: /private/\n"
    monkeypatch.setattr("eval.fetch._fetch_robots_txt", lambda url: stub)
    cache = {}
    assert _robots_allowed(cache, "https://example.gov/private/form.pdf") is False
    assert _robots_allowed(cache, "https://example.gov/public/form.pdf") is True


def test_robots_unreachable_defaults_to_allow(monkeypatch):
    def _boom(url):
        raise OSError("connection refused")

    monkeypatch.setattr("eval.fetch._fetch_robots_txt", _boom)
    cache = {}
    assert _robots_allowed(cache, "https://example.gov/anything.pdf") is True


# --------------------------------------------------------------------------
# 5. cache prevents a second fetch of the same URL (mocked network layer)
# --------------------------------------------------------------------------

def test_fetch_does_not_refetch_cached_url(monkeypatch, tmp_path):
    monkeypatch.setattr("eval.fetch._robots_allowed", lambda cache, url: True)
    monkeypatch.setattr("eval.fetch._throttle", lambda *a, **k: None)

    calls = []

    def fake_download(url):
        calls.append(url)
        return SAFER_PDF.read_bytes()

    monkeypatch.setattr("eval.fetch._download_with_retries", fake_download)

    url = "https://example.gov/forms/one.pdf"
    manifest1 = fetch([url], tmp_path)
    assert len(calls) == 1
    assert len(manifest1["records"]) == 1
    assert manifest1["records"][0]["verdict"] == "flat-wordlike"

    # second call, same URL: must not hit the network again
    manifest2 = fetch([url], tmp_path)
    assert len(calls) == 1, "fetch() re-requested a URL already in the manifest"
    assert len(manifest2["records"]) == 1


def test_fetch_skips_duplicate_content_under_new_url(monkeypatch, tmp_path):
    monkeypatch.setattr("eval.fetch._robots_allowed", lambda cache, url: True)
    monkeypatch.setattr("eval.fetch._throttle", lambda *a, **k: None)
    monkeypatch.setattr(
        "eval.fetch._download_with_retries",
        lambda url: SAFER_PDF.read_bytes(),
    )

    manifest = fetch(
        ["https://example.gov/a.pdf", "https://example.gov/mirror/a.pdf"], tmp_path,
    )
    assert len(manifest["records"]) == 1
    assert any(s["reason"].startswith("duplicate content") for s in manifest["skipped"])


def test_fetch_blocks_host_after_403(monkeypatch, tmp_path):
    monkeypatch.setattr("eval.fetch._robots_allowed", lambda cache, url: True)
    monkeypatch.setattr("eval.fetch._throttle", lambda *a, **k: None)

    def fake_download(url):
        raise FetchError("HTTP 403", status=403)

    monkeypatch.setattr("eval.fetch._download_with_retries", fake_download)

    manifest = fetch(
        ["https://blocked.gov/a.pdf", "https://blocked.gov/b.pdf"], tmp_path,
    )
    assert manifest["blocked_hosts"] == ["blocked.gov"]
    assert len(manifest["records"]) == 0
    assert len(manifest["skipped"]) == 2


# --------------------------------------------------------------------------
# 6. classify() never raises -- proven on a truncated file
# --------------------------------------------------------------------------

def test_classify_truncated_bytes_never_raises(tmp_path):
    data = SAFER_PDF.read_bytes()
    truncated = tmp_path / "chopped.pdf"
    truncated.write_bytes(data[: len(data) // 3])
    record = classify(truncated)  # must not raise
    assert record["verdict"] == "unusable"
    assert record["reason"]


def test_classify_empty_and_garbage_never_raise(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    record = classify(empty)
    assert record["verdict"] == "unusable"

    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"not a pdf at all" * 100)
    record = classify(garbage)
    assert record["verdict"] == "unusable"

    missing = tmp_path / "does_not_exist.pdf"
    record = classify(missing)
    assert record["verdict"] == "unusable"
