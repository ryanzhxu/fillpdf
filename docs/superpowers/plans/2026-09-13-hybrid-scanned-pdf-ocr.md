# Hybrid Scanned-PDF OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the deployed FormFill app recover fields from scanned/image-only PDFs by offering an explicit, opt-in server-side OCR path, while every PDF still defaults to today's fully on-device (Pyodide) detection.

**Architecture:** A new stateless Flask service (`server/`) wraps the existing, already-working `engine.detect.detect(path, page_backend=make_cv_ocr_backend(path))` behind `POST /detect` (PDF bytes in, the same fields.json shape out), deployed to Cloud Run. `demo/index.html` (the single source `scripts/build_site.py` copies into `site/index.html`) grows a "Try OCR" button on the existing "scanned" notice; nothing is sent to the server unless that button is clicked.

**Tech Stack:** Python 3.12, Flask, gunicorn, Docker, Google Cloud Run. No changes to `engine/detect/` or `engine/scan_cv/` — this plan only adds a new caller of code that already exists and is already tested.

**Spec:** `docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md`

## Global Constraints

- Client default is unchanged: every PDF is detected on-device first. The server is reachable only after an explicit "Try OCR" click — never automatically. (Spec §2, §6.)
- `POST /detect` request/response contract: raw PDF bytes in (`Content-Type: application/pdf`, capped at 20MB), the exact JSON shape `engine.detect.detect()` already returns, validated against `eval/contracts/fields.schema.json`. No new notice codes. (Spec §5.)
- CORS restricted to `https://fillpdf.ryanxu.dev` by default, overridable via the `ALLOWED_ORIGIN` env var for local testing. (Spec §5, corrected from the original localhost:8000 assumption — `demo.py`'s dev server binds an OS-assigned ephemeral port, so no fixed local origin exists to hardcode.)
- The temp file holding an uploaded PDF is deleted when the request finishes, success or failure — never persisted, never logged. (Spec §4.)
- No CI auto-deploy for the server in this pass, no auth/API-key gating `/detect` in this pass — both are explicit, documented v1 tradeoffs, not omissions. (Spec §3, §7.)
- Real cloud billing is involved once Task 4 runs `gcloud run deploy`. That task is a hard STOP: an executor must not run it unattended, and must get the user's explicit go-ahead first, every time. Likewise, do not `git push` or open a PR without the user explicitly asking — this repo's own memory says land changes via PR, never a direct push to `main`.
- Do the plan's work on a feature branch, not on `main` directly: this worktree currently has `main` checked out and clean, and committing here directly would put unreviewed work on `main`.

---

## Task 1: Create a feature branch

**Files:** none (git operation only).

- [ ] **Step 1: Create and switch to a feature branch**

```bash
git checkout -b feat/hybrid-scanned-pdf-ocr
```

- [ ] **Step 2: Verify**

```bash
git branch --show-current
```

Expected: `feat/hybrid-scanned-pdf-ocr`

---

## Task 2: The `/detect` Flask service

**Files:**
- Create: `server/requirements.txt`
- Create: `server/app.py`
- Create: `server/tests/test_app.py`

**Interfaces:**
- Consumes: `engine.detect.detect(pdf_path, page_backend=None)` (existing, `engine/detect/__init__.py:127`), `engine.scan_cv.backend.make_cv_ocr_backend(pdf_path, dpi=300)` (existing, `engine/scan_cv/backend.py:31`).
- Produces: a Flask app object `app` importable as `server.app:app`, exposing `POST /detect` and `GET /healthz`. Later tasks (Dockerfile, deploy script) run this via gunicorn using exactly that import path.

### Step 1: Write `server/requirements.txt`

```
-r ../requirements.txt
flask>=3.0
gunicorn>=22.0
```

(`../requirements.txt` already declares `opencv-python`, `pytesseract`, `pdfplumber`, `pypdfium2`, `pillow`, `jsonschema`, `pytest` — everything `engine.detect`/`engine.scan_cv` and this task's own tests need. This file adds only what's new: the web framework and the WSGI server that will run it in Task 3's Dockerfile.)

### Step 2: Write the failing tests (all of them — this task builds `app.py` to satisfy them one behavior at a time in Step 3)

Create `server/tests/test_app.py`:

```python
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
FIXTURE = REPO_ROOT / "fixtures" / "AGM Proxy Form.pdf"


@pytest.fixture
def client():
    app.testing = True
    return app.test_client()


def test_healthz(client):
    resp = client.get("/healthz")
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


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixtures/AGM Proxy Form.pdf not present")
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
    resp = client.get("/healthz")
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://example.test"
```

### Step 3: Run the tests to verify they fail

```bash
cd server && pip install -r requirements.txt -q && pytest tests/test_app.py -v
```

Expected: every test fails with `ModuleNotFoundError: No module named 'app'` (no `server/app.py` exists yet).

### Step 4: Write `server/app.py`

```python
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
# DPI constant); fixtures/AGM Proxy Form.pdf, the real motivating case, is
# under 1MB.
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


@app.route("/healthz", methods=["GET"])
def healthz():
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
```

### Step 5: Run the tests to verify they pass

```bash
cd server && pytest tests/test_app.py -v
```

Expected: all 8 tests PASS. `test_detects_scanned_form_via_ocr` is slow (real OpenCV + real Tesseract on a real scanned page — comparable to the ~1-2s/page timings already measured in `tests/scan_cv/test_backend.py`); that is expected, not a hang.

### Step 6: Commit

```bash
git add server/requirements.txt server/app.py server/tests/test_app.py
git commit -m "feat: add stateless /detect Flask service for opt-in server-side OCR"
```

---

## Task 3: Containerize the service

**Files:**
- Create: `server/Dockerfile`
- Create: `.dockerignore` (repo root)

**Interfaces:**
- Consumes: `server/app.py`'s `app` object from Task 2, importable as `server.app:app`.
- Produces: a Docker image, buildable from the repo root, that Task 4's deploy script pushes and deploys.

### Step 1: Write `.dockerignore` at the repo root

```
.git/
.claude/
node_modules/
.venv/
venv/
__pycache__/
*.pyc
eval/corpus/
eval/work/
site/
demo/out/
docs/
review/
scores/
tests/
.env
.DS_Store
```

(Build context is the repo root — see Step 2's comment on why — so without this, Docker would upload the gitignored 36MB `eval/corpus/`, `node_modules/`, etc. on every build.)

### Step 2: Write `server/Dockerfile`

```dockerfile
# Stateless server for the hybrid scanned-PDF OCR path (POST /detect).
# See docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md.
#
# Build from the REPO ROOT, not from inside server/ -- this needs engine/
# and the root requirements.txt, both of which live outside server/:
#   docker build -f server/Dockerfile -t <tag> .
# (server/deploy.sh, Task 4, does this for you.)
FROM python:3.12-slim

# Matches .github/workflows/ci.yml's system Tesseract install -- pytesseract
# (engine/scan_cv/ocr.py) shells out to the system binary; pip alone does
# not provide it.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY engine/ ./engine/
COPY server/ ./server/

ENV PYTHONPATH=/app
EXPOSE 8080

# Cloud Run sets $PORT at runtime; shell form so that substitution happens.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 180 --workers 1 server.app:app"]
```

(`server/requirements.txt` already contains `-r ../requirements.txt`, which pip resolves relative to `server/requirements.txt`'s own location — `/app/server/requirements.txt` → `/app/requirements.txt`, which the `COPY requirements.txt` line above already placed. One `pip install` line installs both.)

(`server/` has no `__init__.py`, matching `engine/`'s existing convention — `scripts/build_site.py`'s comment confirms `engine/` is an implicit Python namespace package with no `__init__.py`. The same works for `server/` given `/app` is on `PYTHONPATH` and gunicorn's working directory.)

### Step 3: Build the image locally and verify it runs

```bash
docker build -f server/Dockerfile -t fillpdf-ocr:local .
docker run --rm -p 8080:8080 fillpdf-ocr:local &
sleep 3
curl -s http://localhost:8080/healthz
kill %1
```

Expected: `{"status":"ok"}`, and no build errors (this is the mechanical proof that the Dockerfile's paths, the namespace-package import, and gunicorn's entry point are all correct — the same kind of "runs without crashing" bar `AUTOPILOT.md` part 4 already set for the detection pipeline itself).

### Step 4: Commit

```bash
git add server/Dockerfile .dockerignore
git commit -m "build: containerize the /detect service"
```

---

## Task 4: Deploy to Cloud Run — HARD CHECKPOINT, requires the user

**This task deploys real, billed cloud infrastructure and must not be run unattended.** An executing agent must stop here, show this task to the user, and get their explicit go-ahead before running anything in it — this is a standing instruction from this repo's CLAUDE.md and from the design spec (§8), not a one-time note.

**Files:**
- Create: `server/deploy.sh`

**Interfaces:**
- Consumes: `server/Dockerfile` from Task 3.
- Produces: a live Cloud Run service URL. Task 5 cannot be completed without this URL — it is a hard, sequential dependency, not something to stub or guess.

### Step 1: One-time setup (the user runs this once; not part of `deploy.sh`, since re-running these would error on already-existing resources)

```bash
# Install the gcloud CLI first if not already present (confirmed absent on
# this machine at plan-writing time): brew install --cask google-cloud-sdk
gcloud auth login
gcloud config set project <YOUR_GCP_PROJECT_ID>
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create fillpdf \
  --repository-format=docker --location=us-central1 \
  --description="FormFill OCR server images"
gcloud auth configure-docker us-central1-docker.pkg.dev
```

### Step 2: Write `server/deploy.sh`

```bash
#!/usr/bin/env bash
# Builds server/Dockerfile against the repo root (needed for engine/ and
# requirements.txt, both outside server/), pushes it to Artifact Registry,
# and deploys it to Cloud Run. Run from the repo root:
#
#   PROJECT_ID=<your-gcp-project> ./server/deploy.sh
#
# One-time setup before the first run: see Task 4, Step 1 of
# docs/superpowers/plans/2026-09-13-hybrid-scanned-pdf-ocr.md (gcloud auth
# login, project selection, enabling APIs, creating the Artifact Registry
# repo, `gcloud auth configure-docker`).
#
# This deploys real, billed cloud infrastructure. Run it only when you have
# decided to -- never as an unattended or automatic step.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
REGION=us-central1
REPO=fillpdf
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/ocr:$(git rev-parse --short HEAD)"

if [ ! -f requirements.txt ] || [ ! -d server ]; then
  echo "run this from the repo root, not from inside server/" >&2
  exit 1
fi

docker build -f server/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy fillpdf-ocr \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --max-instances 3 \
  --concurrency 1 \
  --timeout 180 \
  --memory 2Gi \
  --cpu 2 \
  --set-env-vars ALLOWED_ORIGIN=https://fillpdf.ryanxu.dev

echo "Deployed. Copy the Service URL printed above -- Task 5 needs it."
```

```bash
chmod +x server/deploy.sh
```

### Step 3: Commit the script (do not run it yet)

```bash
git add server/deploy.sh
git commit -m "build: add Cloud Run deploy script"
```

### Step 4: STOP and hand off to the user

Show the user Step 1 and `server/deploy.sh`. Ask them to either run Step 1 (if not already done) and then `PROJECT_ID=<their project> ./server/deploy.sh` themselves, or explicitly say go-ahead for it to be run in this session. **Do not proceed to Task 5 until they hand back the printed Cloud Run Service URL** (looks like `https://fillpdf-ocr-xxxxx-uc.a.run.app`).

---

## Task 5: Wire the client to the deployed service

**Files:**
- Modify: `demo/index.html:64-65` (CSS)
- Modify: `demo/index.html:253-296` (English i18n dictionary)
- Modify: `demo/index.html:297-341` (Traditional Chinese i18n dictionary)
- Modify: `demo/index.html:449-455` (`showNotice()`)
- Modify: `demo/index.html:869-872` (comment)
- Modify: `demo/index.html` (add `OCR_ENDPOINT_URL` constant and `tryServerOcr()` function, placed next to `detectLocally()`, currently ending around line 943)

**Interfaces:**
- Consumes: the real Cloud Run URL from Task 4, Step 4. `SOURCE` (existing top-level variable, the current file's `ArrayBuffer`, set in `onFile()`). `render(d)` (existing function, `demo/index.html`, renders any `detect()`-shaped result). `tr(key, vars)` (existing i18n lookup).
- Produces: `tryServerOcr()`, called from the button `showNotice()` now renders.

### Step 1: Add the two small CSS rules

In `demo/index.html`, immediately after the existing `.hint.notice{...}` rule (around line 65):

```css
.ocr-status{opacity:.75;margin-left:6px}
.ocr-error{color:var(--warn);margin-top:4px;font-size:13px}
```

### Step 2: Add new i18n keys to the `en` dictionary

In the `en` block (around line 295, right after the existing `notice_no_fields` line), add:

```js
    notice_ocr_try_button: 'Try OCR (uploads this file to a server)',
    notice_ocr_working: 'Sending this file to a server for OCR…',
    notice_ocr_network_error: 'Could not reach the OCR server. Check your connection and try again.',
    notice_ocr_no_text_found: 'The server ran OCR on this file but still could not find readable text. It may be too low-quality to process automatically.',
    notice_ocr_assisted: 'Some pages in this document had no text layer, so FormFill used OCR to read them instead. OCR-derived fields are less reliable than fields read from a real text layer -- check labels and positions carefully before using them.',
```

Also replace the existing `picker_fine2_html` line (line 283) — the old wording is an absolute claim this feature narrows:

```js
    picker_fine2_html: '<b>Your file is processed on this device by default.</b> The whole thing, reading the form, filling it, writing the finished PDF, runs inside your browser. If a page turns out to be a scanned image, you can choose to send just that file to a server for OCR — nothing is uploaded unless you ask. The first form takes a few seconds while the detector loads.',
```

### Step 3: Add the matching keys to the `zh-Hant-HK` dictionary

In the `'zh-Hant-HK'` block (around line 340, right after the existing `notice_no_fields` line), add:

```js
    notice_ocr_try_button: '嘗試 OCR（將此檔案上載至伺服器）',
    notice_ocr_working: '正在將檔案傳送至伺服器進行 OCR…',
    notice_ocr_network_error: '無法連線至 OCR 伺服器，請檢查網絡連線後再試一次。',
    notice_ocr_no_text_found: '伺服器已嘗試 OCR，但仍無法辨識出可讀取的文字。這份檔案的品質可能太低，無法自動處理。',
    notice_ocr_assisted: '這份文件中有部分頁面沒有文字圖層，因此 FormFill 改用 OCR 讀取。OCR 辨識出的欄位可靠程度低於'
      + '直接從文字圖層讀取的欄位 —— 使用前請仔細核對標籤與位置。',
```

Also replace the existing `picker_fine2_html` line (line 326):

```js
    picker_fine2_html: '<b>預設情況下，您的檔案會在此裝置上處理。</b>從讀取表單、填寫內容到產生完成的 PDF，整個流程都在您的瀏覽器中執行。'
      + '如果某一頁是掃描影像，您可以選擇將該檔案傳送至伺服器進行 OCR —— 除非您主動要求，否則不會上載任何內容。第一次使用時，偵測器需要幾秒鐘載入時間。',
```

### Step 4: Rewrite the comment at line 869-872

Replace:

```js
// ---- the detector, running in this tab ------------------------------------
// engine/detect is Python, so it runs under Pyodide (CPython on WebAssembly).
// The alternative was a server, which would mean uploading the form. A person
// filling a benefits form is handing over their address and their income, and
// the only way to promise that goes nowhere is for it to actually go nowhere.
```

with:

```js
// ---- the detector, running in this tab ------------------------------------
// engine/detect is Python, so it runs under Pyodide (CPython on WebAssembly).
// By default nothing is uploaded: a person filling a benefits form is handing
// over their address and their income, and the only way to promise that goes
// nowhere is for it to actually go nowhere. The one named exception is a
// scanned/image-only PDF, which this sandbox cannot OCR on its own (no cv2,
// no system Tesseract under WASM) -- see showNotice()/tryServerOcr() below.
// Even then, the file is sent only after an explicit "Try OCR" click; never
// automatically. See docs/superpowers/specs/
// 2026-09-13-hybrid-scanned-pdf-ocr-design.md.
```

### Step 5: Add the `OCR_ENDPOINT_URL` constant and `tryServerOcr()`

Immediately after `detectLocally()`'s closing brace (right before the `// ---- the picker` comment, around line 943), add:

```js
// The Cloud Run service from server/app.py (Task 4 of
// docs/superpowers/plans/2026-09-13-hybrid-scanned-pdf-ocr.md). Only ever
// called from an explicit "Try OCR" click -- see showNotice() below.
const OCR_ENDPOINT_URL = 'REPLACE_WITH_CLOUD_RUN_URL/detect';

async function tryServerOcr(){
  const h = $('#hint');
  const btn = h.querySelector('#tryOcrBtn');
  if (btn) btn.disabled = true;
  const status = document.createElement('span');
  status.className = 'ocr-status';
  status.textContent = tr('notice_ocr_working');
  h.appendChild(status);
  try {
    const resp = await fetch(OCR_ENDPOINT_URL, {
      method: 'POST',
      headers: {'Content-Type': 'application/pdf'},
      body: SOURCE.slice(0),
    });
    if (!resp.ok) throw new Error('server responded ' + resp.status);
    const d = await resp.json();
    status.remove();
    if (d.notice && d.notice.code === 'scanned'){
      if (btn) btn.remove();
      h.innerHTML = `<b>${tr('heads_up')}:</b> ${tr('notice_ocr_no_text_found')}`;
      return;
    }
    HINT_CLEARED = false;
    return render(d);
  } catch (err){
    console.error('[tryServerOcr]', err);
    status.remove();
    if (btn) btn.disabled = false;
    const errLine = document.createElement('div');
    errLine.className = 'ocr-error';
    errLine.textContent = tr('notice_ocr_network_error');
    h.appendChild(errLine);
  }
}
```

(`REPLACE_WITH_CLOUD_RUN_URL` is filled in with the real URL from Task 4, Step 4 in Step 7 below — this is the one line in this whole plan that cannot be written before that URL exists.)

### Step 6: Update `showNotice()` to add the button and the `ocr_assisted` key mapping

Replace the current function (lines 449-455):

```js
function showNotice(n){
  CURRENT_NOTICE = n;
  const h = $('#hint');
  h.classList.add('notice');
  const key = n.code === 'scanned' ? 'notice_scanned' : n.code === 'no_fields' ? 'notice_no_fields' : null;
  h.innerHTML = `<b>${tr('heads_up')}:</b> ${key ? tr(key) : escapeHtml(n.message)}`;
}
```

with:

```js
function showNotice(n){
  CURRENT_NOTICE = n;
  const h = $('#hint');
  h.classList.add('notice');
  const key = n.code === 'scanned' ? 'notice_scanned'
    : n.code === 'no_fields' ? 'notice_no_fields'
    : n.code === 'ocr_assisted' ? 'notice_ocr_assisted'
    : null;
  h.innerHTML = `<b>${tr('heads_up')}:</b> ${key ? tr(key) : escapeHtml(n.message)}`;
  if (n.code === 'scanned'){
    const btn = document.createElement('button');
    btn.id = 'tryOcrBtn';
    btn.className = 'p';
    btn.textContent = tr('notice_ocr_try_button');
    btn.onclick = tryServerOcr;
    h.appendChild(document.createTextNode(' '));
    h.appendChild(btn);
  }
}
```

### Step 7: Fill in the real Cloud Run URL

Replace `'REPLACE_WITH_CLOUD_RUN_URL/detect'` in `OCR_ENDPOINT_URL` (Step 5) with the actual URL handed back at the end of Task 4, e.g. `'https://fillpdf-ocr-xxxxx-uc.a.run.app/detect'`.

### Step 8: Rebuild the site and check it mechanically

```bash
./.venv/bin/python scripts/build_site.py
grep -c "OCR_ENDPOINT_URL" site/index.html
```

Expected: the build succeeds (no assertion errors from `build_site.py`'s marker checks) and the grep returns a nonzero count, confirming the new code made it into `site/index.html` unchanged, the same way every other piece of `demo/index.html` already does.

### Step 9: Manual browser verification (human task — no browser-automation harness exists in this repo, confirmed via `tests/test_demo_smoke.py`, which tests `demo.py`'s Python side only)

```bash
cd site && python3 -m http.server 8000
```

Then in a browser: open `http://localhost:8000`, drop in `fixtures/AGM Proxy Form.pdf`, confirm the "scanned" notice and the "Try OCR" button both appear, click it, confirm the status message shows, and confirm fields render afterward with the OCR-assisted notice. Also verify: dropping a normal text-layer PDF (`fixtures/safer.pdf`) never shows the button and never triggers a network request (check the browser's Network tab) — the on-device default must stay untouched.

### Step 10: Commit

```bash
git add demo/index.html
git commit -m "feat: add opt-in 'Try OCR' server path for scanned PDFs"
```

---

## Task 6: Push and open a pull request — checkpoint, only on explicit request

**Do not run this task's commands unprompted.** Per this repo's own convention (land changes via PR, never a direct push to `main`) and this repo's CLAUDE.md ("push means ship" — a push authorizes opening a PR and arming auto-merge in the same action, but only once the user actually says "push"), wait for the user to say the word before doing any of this.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/hybrid-scanned-pdf-ocr
```

- [ ] **Step 2: Open a pull request with auto-merge armed**

```bash
gh pr create --title "Add opt-in server-side OCR for scanned PDFs" --body "$(cat <<'EOF'
## Summary
- Adds a stateless Cloud Run service (server/) wrapping the existing engine.scan_cv OCR/CV pipeline behind POST /detect
- Adds an explicit, opt-in "Try OCR" path to the deployed app for scanned/image-only PDFs; every PDF still defaults to on-device (Pyodide) detection, unchanged
- See docs/superpowers/specs/2026-09-13-hybrid-scanned-pdf-ocr-design.md for the full design

## Test plan
- [ ] `cd server && pip install -r requirements.txt && pytest tests/ -v` — all pass
- [ ] `docker build -f server/Dockerfile -t fillpdf-ocr:local .` — builds clean
- [ ] Cloud Run service deployed and reachable (Task 4)
- [ ] Manual browser check (Task 5, Step 9): scanned PDF shows the button and OCR works end to end; a normal text-layer PDF never triggers a network request
EOF
)"
gh pr edit --add-label "" 2>/dev/null || true
gh pr merge --auto --squash
```

---

## Self-Review

**Spec coverage:**
- §2 (opt-in, not automatic), §6 (client changes) → Task 5.
- §4 (architecture: stateless server, temp file deleted at exit), §5 (contract: size cap, content-type, CORS, error codes) → Task 2.
- §7 (max-instances, concurrency, timeout as cost/abuse mitigation) → Task 4's `deploy.sh`.
- §8 (Cloud Run, Docker, manual deploy, no CI in this pass) → Tasks 3-4.
- §9 (server tests mirroring `test_demo_smoke.py`, manual client verification) → Task 2 Step 2, Task 5 Step 9.
- §3 non-goals (no engine/ changes, no auth, no polling API, no CI auto-deploy) — none of the tasks above touch `engine/`, add auth, add polling, or touch `.github/workflows/ci.yml`. Confirmed clean.

**Corrected from the original brief:** the CORS localhost assumption (`demo.py` uses an OS-assigned ephemeral port, not a fixed one) and the `gcloud run deploy --source --dockerfile` flag (verified via search not to reliably exist) were both wrong in the initial framing and are fixed in this plan's `ALLOWED_ORIGIN` env-var design and Task 4's build-locally-and-push-by-image approach.

**Placeholder scan:** the only literal placeholder text is `REPLACE_WITH_CLOUD_RUN_URL` in Task 5, Step 5 — intentional, since that URL cannot exist before Task 4 runs, and Step 7 of the same task closes it with the real value. No other TBD/TODO markers.

**Type/name consistency:** `OCR_ENDPOINT_URL`, `tryServerOcr`, `MAX_UPLOAD_BYTES`, `ALLOWED_ORIGIN`, `app` (server.app:app) are each defined once and referenced identically everywhere else they appear across Tasks 2-5.
