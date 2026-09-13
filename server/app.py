"""Stateless HTTP wrapper around engine.detect.detect() + make_cv_ocr_backend,
the server-side half of the hybrid scanned-PDF OCR path. See
docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md.

Receives a PDF only when a person explicitly clicked "Try OCR" in the app
(demo/index.html's showNotice()/tryServerOcr()) -- never automatically, and
never anything but a blank/unfilled form (filled values are written
client-side and never sent anywhere; see docs/superpowers/specs/
2026-09-01-formfill-design.md section 3). The upload lives only in a temp
file for the duration of one request; nothing is logged or persisted.
"""
import os
import tempfile

from flask import Flask, jsonify, request

from engine.detect import detect
from engine.scan_cv.backend import make_cv_ocr_backend

app = Flask(__name__)

# Generous for a scanned form rendered at 300 DPI (engine/scan_cv/backend.py's
# DPI constant); eval/scan_cv/samples/agm_proxy_form.pdf, the real motivating
# case, is under 1MB.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

# Overridable so a local/dev deployment (a different Cloud Run URL, or a
# locally-served copy of site/) is not locked out. Production default matches
# the one real deployed origin.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://fillpdf.ryanxu.dev")


@app.after_request
def _add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.errorhandler(413)
def _too_large(_e):
    limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    return jsonify(error=f"file too large; the limit is {limit_mb}MB"), 413


@app.route("/health", methods=["GET"])
def healthz():
    # Not /healthz -- Cloud Run's frontend reserves that exact path and
    # serves its own 404 before the request reaches this container.
    return jsonify(status="ok")


@app.route("/detect", methods=["OPTIONS"])
def detect_preflight():
    return "", 204


@app.route("/detect", methods=["POST"])
def detect_route():
    if request.content_type != "application/pdf":
        return jsonify(error="expected Content-Type: application/pdf"), 400

    data = request.get_data()
    if not data:
        return jsonify(error="empty request body"), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            result = detect(tmp.name, page_backend=make_cv_ocr_backend(tmp.name))
        except Exception:
            return jsonify(error="could not process this file as a PDF"), 500

    return jsonify(result)
