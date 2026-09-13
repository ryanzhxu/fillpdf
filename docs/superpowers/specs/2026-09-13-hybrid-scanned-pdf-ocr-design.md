# Hybrid scanned-PDF OCR: opt-in server path, design

## 1. Problem

`engine/detect/detect()` can now recover fields from a scanned/image-only PDF
when given a `page_backend` (`engine/scan_cv/backend.py#make_cv_ocr_backend`),
verified end to end against `fixtures/AGM Proxy Form.pdf` and the court-form
samples in `tests/scan_cv/test_backend.py`. That backend needs `cv2` (OpenCV)
and a system Tesseract binary — neither can run under Pyodide/WASM, which is
how the deployed app (`fillpdf.ryanxu.dev`, built by `scripts/build_site.py`)
runs the detector today. So the deployed app still shows the plain "scanned,
can't read it" notice (`SCANNED_MESSAGE`, `engine/detect/__init__.py`) no
matter how good `engine/scan_cv` gets, because it never has a `page_backend`
to pass in.

## 2. Decision

Add one new component: a small, stateless server that runs the exact same
`make_cv_ocr_backend` path, reachable over HTTP. The client stays on-device
by default for every PDF, exactly as today. Only when detection finds a
scanned page does the UI offer — never assume — sending that one file to the
server. This mirrors the original architecture in
`docs/superpowers/specs/2026-09-01-formfill-design.md` section 3 ("Converter
service (Python, sandboxed): PDF in → fields.json out. Stateless. No network
egress. Uploads deleted at exit."), which predates the Pyodide-only
simplification and already drew this exact boundary.

**Non-negotiable constraint, carried over from `demo/index.html:869-872`'s
existing comment:** nothing is ever uploaded without an explicit action from
the person using the app. That comment currently reads as an absolute
("the only way to promise that goes nowhere is for it to actually go
nowhere") and must be edited, not contradicted — the promise now reads "true
by default; the one exception is a scanned PDF, and only if you ask."

## 3. Non-goals

- No change to `engine/detect/`, `engine/scan_cv/`, or the `page_backend`
  contract. This is purely a new caller of code that already exists and is
  already tested.
- No auth system, no accounts, no per-user rate limiting. Traffic is low
  (personal side project); see §7 for the accepted v1 risk.
- No async job/polling API. Real OCR timings measured in this repo
  (`tests/scan_cv/test_backend.py`) are ~1-2s/page up to ~24s for a full
  multi-page court form — well inside one synchronous HTTP request.
- No CI auto-deploy for the server in this pass. `.github/workflows/ci.yml`
  already auto-deploys the static site on merge to `main`; wiring the same
  for the Cloud Run service needs GCP credentials as a repo secret, which is
  a separate, later decision once the manual deploy is proven out.

## 4. Architecture

```
Browser (Pyodide, unchanged default path)
  ┌────────────────────────────────────────────────────┐
  │ detect(pdf) → notice.code == 'scanned'              │
  │   → show notice + "Try OCR" button                  │
  │   → (only if clicked) POST raw PDF bytes            │
  └───────────────────────┬──────────────────────────────┘
                           │ PDF bytes, over HTTPS
                           ▼
  Cloud Run service (server/), stateless, no persistence
  ┌────────────────────────────────────────────────────┐
  │ POST /detect                                        │
  │   write body to a temp file                         │
  │   detect(tmp, page_backend=make_cv_ocr_backend(tmp)) │
  │   delete temp file (finally)                         │
  │   return the same fields.json shape                  │
  └────────────────────────────────────────────────────┘
```

The server never writes to any durable store and never logs PDF content or
extracted text — only structured request metadata (status code, timing,
byte size) for operational visibility.

## 5. Contract

`POST https://<cloud-run-url>/detect`

- Request: `Content-Type: application/pdf`, raw bytes, capped at 20MB
  (`MAX_UPLOAD_BYTES` in `server/app.py`). Oversized or wrong-content-type
  requests are rejected before any file I/O or detection work.
- Response: `200` with the exact JSON shape `engine.detect.detect()` returns
  today — validated in the server's own tests against
  `eval/contracts/fields.schema.json`, the same schema the rest of the repo
  already treats as the source of truth. No new fields, no new notice codes:
  a successful OCR run produces `notice.code == 'ocr_assisted'` (already
  defined, `OCR_ASSISTED_MESSAGE`); a page the backend still can't recover
  keeps `notice.code == 'scanned'` — both already handled by `detect()`
  itself, unchanged.
- Errors: `400` (bad content-type, empty body, over size cap), `500` (detect()
  raised — e.g. unreadable/corrupt PDF), with a small `{"error": "..."}` body.
  No PDF content or stack trace detail goes in the response body.
- CORS: `Access-Control-Allow-Origin` restricted to `https://fillpdf.ryanxu.dev`
  (and `http://localhost:8000` for local dev against `demo/demo.py`'s server).

## 6. Client changes (`demo/index.html`, copied verbatim into `site/index.html`
by `scripts/build_site.py` — one source, both places)

- `showNotice(n)` (currently ~line 449): when `n.code === 'scanned'`, append a
  button to the existing notice HTML and wire its `onclick` to a new
  `tryServerOcr()` function. The existing `no_fields` / unknown-code paths are
  unchanged.
- New `tryServerOcr()`: disables the button, shows a progress message reusing
  the existing `say()`-style pattern already used during Pyodide load
  (`starting_detector`, `loading_libraries`, …), `fetch()`s `OCR_ENDPOINT_URL`
  with `SOURCE` (the already-held `ArrayBuffer` from `onFile()`) as the body,
  and on success calls `render()` again with the parsed response — the exact
  same function that already renders any `detect()` result, so no new
  rendering logic is needed. `OCR_ENDPOINT_URL` is one new top-level `const`,
  set to the deployed Cloud Run URL once known (placeholder until deploy).
- Error handling: network failure / non-200 / bad JSON → re-show the original
  scanned notice plus a distinct error line and re-enable the button (retry).
  A `200` response whose `notice.code` is still `'scanned'` → distinct
  "OCR could not find readable text on this page" message, no retry button
  (already tried, retrying won't change the result).
- Update the comment at `demo/index.html:869-872` to state the narrowed
  promise (see §2) instead of the absolute one it currently states.
- Update `picker_fine2_html` (both locales) from "There is no upload and no
  server" to something that stays true: on-device by default, with the one
  named, explicit exception.
- New i18n keys, both `en` and `zh-Hant-HK` dictionaries: a button label, an
  "uploading…" status line, a network-error line, and an
  "OCR found nothing" line. Exact keys and copy are the implementation plan's
  job, not this spec's.

## 7. Security / cost posture (v1)

Accepted, revisitable risk: no API key or CAPTCHA gating `/detect`, so a
determined actor could script requests directly against the Cloud Run URL,
bypassing the browser and its CORS check, and run up compute cost. Mitigated,
not eliminated, by: `max-instances` capped low (e.g. 3), `concurrency=1` per
instance (CV+OCR is CPU-bound; sharing an instance would only slow both
requests down), a request timeout (e.g. 180s), and the 20MB body cap. If
abuse becomes real, the next step is Cloudflare Turnstile (free, and the site
is already behind Cloudflare) verified server-side before any detection work
starts — not built now, per YAGNI.

## 8. Deployment

Google Cloud Run, chosen for scale-to-zero (no idle cost, matching the
project's existing cost-consciousness — see `wrangler.jsonc`'s comment on why
there's no server today) and native Docker support (needed for the `apt-get
install tesseract-ocr` step; Cloud Run buildpacks alone cannot do this).
Deployment is `gcloud run deploy --source server/` from a Dockerfile — no
separate CI step in this pass (see §3). This is a real GCP project and real
(if likely free-tier) billing, so the actual `gcloud deploy` invocation is a
step the user runs or explicitly approves interactively, not something done
unilaterally.

## 9. Testing

- `server/`: a pytest suite mirroring `tests/test_demo_smoke.py`'s style —
  happy path against `fixtures/AGM Proxy Form.pdf` (expect `ocr_assisted` and
  a non-empty `fields` list, matching the mechanical bar `AUTOPILOT.md` part 4
  already set), oversized-body rejection, wrong-content-type rejection, CORS
  header presence, and response-schema validation against
  `eval/contracts/fields.schema.json`.
- Client: manual verification in a real browser (drop a scanned PDF, click
  "Try OCR", confirm fields render). No browser-automation harness exists in
  this repo today (confirmed: `tests/test_demo_smoke.py` tests `demo.py`'s
  Python side only), so this stays a human check, consistent with how every
  other `index.html` behavior in this repo is verified.
