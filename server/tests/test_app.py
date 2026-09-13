"""Tests for server/app.py, the stateless HTTP wrapper around
engine.detect.detect() + make_cv_ocr_backend -- the server-side half of the
hybrid scanned-PDF OCR path. See
docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md.

Not part of the root repo's `pytest` run or .github/workflows/ci.yml in this
pass (see that spec's section 3) -- flask/gunicorn are server-only
dependencies, not declared in the root requirements.txt. Run with:

    cd server
    pip install -r requirements.txt
    pytest tests/ -v
"""
import json
import sys
from pathlib import Path

import pytest
from jsonschema import validate

SERVER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

from app import app, MAX_UPLOAD_BYTES  # noqa: E402

SCHEMA = json.loads((REPO_ROOT / "eval" / "contracts" / "fields.schema.json").read_text())
FIXTURE = REPO_ROOT / "eval" / "scan_cv" / "samples" / "agm_proxy_form.pdf"


@pytest.fixture
def client():
    app.testing = True
    return app.test_client()


def test_healthz(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_rejects_wrong_content_type(client):
    resp = client.post("/detect", data=b"not a pdf", content_type="text/plain")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_rejects_empty_body(client):
    resp = client.post("/detect", data=b"", content_type="application/pdf")
    assert resp.status_code == 400


def test_rejects_oversized_body(client):
    oversized = b"%PDF-1.4\n" + b"0" * (MAX_UPLOAD_BYTES + 1)
    resp = client.post("/detect", data=oversized, content_type="application/pdf")
    assert resp.status_code == 413


def test_returns_500_on_unreadable_pdf(client):
    resp = client.post("/detect", data=b"this is not a real pdf at all",
                        content_type="application/pdf")
    assert resp.status_code == 500
    assert "error" in resp.get_json()


@pytest.mark.skipif(not FIXTURE.exists(), reason="eval/scan_cv/samples/agm_proxy_form.pdf not present")
def test_detects_scanned_form_via_ocr(client):
    resp = client.post("/detect", data=FIXTURE.read_bytes(),
                        content_type="application/pdf")
    assert resp.status_code == 200
    body = resp.get_json()
    validate(instance=body, schema=SCHEMA)
    assert body["notice"]["code"] == "ocr_assisted"
    assert len(body["fields"]) > 0


def test_cors_header_present(client):
    resp = client.post("/detect", data=b"", content_type="application/pdf")
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://fillpdf.ryanxu.dev"


def test_cors_header_respects_env_override(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "ALLOWED_ORIGIN", "http://example.test")
    resp = client.get("/health")
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://example.test"
