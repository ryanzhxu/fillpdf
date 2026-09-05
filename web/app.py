"""The deployed FormFill app.

demo/demo.py bakes ONE pdf into demo/out/ and serves it. That is a local
feel-test and cannot be deployed: a visitor has no way to supply their own
form. This serves the same UI with an upload path instead.

The split of work is deliberate and is the reason this stays small:

  * DETECTION is Python (engine.detect reads the page with pdfplumber), so it
    has to run here.
  * EVERYTHING ELSE is already client-side. pdf.js paints the pages and
    tools/inject.mjs writes the filled PDF with pdf-lib, both in the browser.

So the server takes PDF bytes, returns the fields JSON, and keeps nothing. The
uploaded file is written to a temp file only because pdfplumber opens a path,
and it is deleted in a finally before the response is sent. Nothing is written
to a database, a bucket, or a log. A person filling a benefits form is handing
over their address and their income; the honest default is to hold it for the
length of one request and never again.

Run locally:  ./.venv/bin/python -m web.app
Production:   gunicorn web.app:app
"""
import json
import os
import sys
import tempfile
from pathlib import Path

from flask import Flask, Response, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.detect import detect  # noqa: E402

# Caps. MAX_UPLOAD_BYTES matches eval/fetch.py's own 20MB ceiling and
# MAX_PAGES matches the demo's, so a file that works here works there.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30

# demo/index.html is the ONE UI. It ships with the flag false so the local
# demo keeps working unchanged; this server flips it to true. The marker is
# asserted at import time rather than trusted, because a silent miss would
# serve the upload build with no upload screen -- the same failure demo.py
# guards against when it rewrites the inject.mjs specifier.
UPLOAD_FLAG_SRC = "window.FILLPDF_UPLOAD = false;"
UPLOAD_FLAG_DST = "window.FILLPDF_UPLOAD = true;"

INDEX_SRC = ROOT / "demo" / "index.html"
_html = INDEX_SRC.read_text(encoding="utf-8")
if UPLOAD_FLAG_SRC not in _html:
    sys.exit(f"web.app: expected {UPLOAD_FLAG_SRC!r} in {INDEX_SRC}; "
             "the upload-mode marker moved and this rewrite is now wrong")
# The demo serves tools/ next to index.html and rewrites the specifier to
# match. Here tools/ is mounted at /tools/, so the '../tools/' the repo layout
# needs is right already -- assert it rather than assume.
if "'../tools/inject.mjs'" not in _html:
    sys.exit(f"web.app: expected \"'../tools/inject.mjs'\" in {INDEX_SRC}")
INDEX_HTML = _html.replace(UPLOAD_FLAG_SRC, UPLOAD_FLAG_DST)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/tools/<path:name>")
def tools(name):
    # index.html imports '../tools/inject.mjs'. Served from '/', that resolves
    # to '/tools/inject.mjs'. .mjs needs an explicit JS mimetype or the browser
    # refuses the module import.
    return send_from_directory(ROOT / "tools", name, mimetype="text/javascript")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/detect")
def api_detect():
    """PDF bytes in, fields JSON out. Stores nothing."""
    data = request.get_data(cache=False)
    if not data:
        return {"error": "No PDF was uploaded."}, 400
    if not data.startswith(b"%PDF"):
        return {"error": "That file is not a PDF."}, 400

    # pdfplumber opens a path, so the bytes touch disk for the length of this
    # call and no longer. delete=False + finally, because Windows and some
    # filesystems will not let pdfplumber reopen a still-open NamedTemporaryFile.
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            doc = detect(tmp)
        except Exception as err:            # noqa: BLE001 -- report, never 500 blank
            app.logger.exception("detect failed")
            return {"error": f"That PDF could not be read ({type(err).__name__}). "
                             "It may be corrupt, encrypted, or password-protected."}, 400
        if len(doc["pages"]) > MAX_PAGES:
            return {"error": f"That PDF has {len(doc['pages'])} pages; "
                             f"the limit is {MAX_PAGES}."}, 413
        return Response(json.dumps(doc), mimetype="application/json")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@app.errorhandler(413)
def too_large(_err):
    mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    return {"error": f"That file is larger than the {mb}MB limit."}, 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=True)
