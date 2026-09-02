# FormFill — Design

Date: 2026-09-01
Status: approved for planning

## 1. Problem

Government and institutional PDF forms usually arrive flat. They have no
interactive fields, so a person cannot type into them. The common workaround is
to float text boxes over the page image. That work is slow, it does not survive
a re-open, and it produces documents that look wrong.

FormFill accepts a flat PDF, finds the places where a person must write, gives
them real boxes to type into, and returns a completed PDF.

### Evidence this is tractable

Measured on `fixtures/safer.pdf` (BC Housing SAFER application, 8 pages, exported
from Microsoft Word):

| Property | Value |
|---|---|
| `/AcroForm` in catalog | absent — zero form fields |
| `/XFA` | absent |
| Annotations | 3 hyperlinks only |
| Text layer | present, about 21,000 characters with exact coordinates |
| Table rules (thin filled rects) | 285 vertical, 279 horizontal |
| Checkbox glyphs (Webdings, Wingdings) | 81 |
| Underscore write-on runs | 9 |

A prototype detected 124 fields, injected real AcroForm widgets, filled them,
and rendered correctly in Preview and pdfium. All 81 checkboxes landed exactly
on their glyphs. Roughly 200 fields exist in total, so unaided detection reached
about 62 percent.

That 62 percent is the central design constraint. Sections 6 and 10 exist to
close the gap.

## 2. Scope

**In scope for v1**

- Public web application. No account, no login, no administrator role.
- Anyone uploads a PDF. The service detects fields and returns them.
- The person fills the form in the browser and downloads the result.
- The person can add a box, move a box, resize a box, and delete a box.
- Corrections improve the result for the next person who uploads the same form.
- Upload limits and a sandboxed converter.

**Out of scope for v1**

- Images and scanned pages. They carry no text layer, so they need OCR plus
  OpenCV line detection. That is a second engine and roughly doubles the work.
  Deferred to v2.
- XFA forms.
- Digital signatures.
- Any machine-learning model. See section 10, Level 2.
- Integration with MapleBenefits. Deferred until FormFill works on its own.

## 3. Architecture

Three pieces, with one boundary that carries the whole privacy claim.

```
  Browser (static, no backend needed to fill)
  ┌──────────────────────────────────────────────┐
  │  Upload → Detect → Edit boxes → Fill → Save  │
  │  pdf.js renders · pdf-lib writes             │
  │  IndexedDB holds the profile                 │
  └───────┬──────────────────────────────────────┘
          │  blank PDF only  ▲  field geometry only
          ▼                  │
  ┌──────────────────────────────────────────────┐
  │  Converter service (Python, sandboxed)       │
  │  PDF in → fields.json out. Stateless.        │
  │  No network egress. Uploads deleted at exit. │
  └───────┬──────────────────────────────────────┘
          │  fingerprint + geometry
          ▼
  ┌──────────────────────────────────────────────┐
  │  Template store — the only durable state     │
  │  fingerprint → corrected field sets          │
  │  Never holds a value a person typed.         │
  └──────────────────────────────────────────────┘
```

**The boundary that matters:** the converter receives only blank forms and
returns only geometry. Values a person types never leave the browser. The
privacy claim is therefore structural, not a policy.

### Why the browser writes the PDF

The browser, not the server, produces the finished document. Two consequences
follow, and both are wanted:

1. Filled values cannot reach the server, because the server is never asked to
   write them.
2. Adding or dragging a box is instant, because it needs no round trip. A
   user-added box and a detected box follow the same code path.

**Named risk.** `pdf-lib` is MIT-licensed and heavily used (47 million downloads
per month) but its last commit was July 2024. The design depends on its ability
to create form fields and set values. Track T4 opens with a spike that proves
this on `fixtures/safer.pdf` before any other T4 work begins. If the spike
fails, the fallback is `drawText` at the detected coordinates followed by
flattening, which loses the editable output but keeps the product.

## 4. Contracts

These two contracts are the keystone of parallel execution. They are written
before any track starts, and no track may change them alone.

### 4.1 `fields.json`

```json
{
  "version": 1,
  "fingerprint": "sha256:9f2c…",
  "source": { "pages": 8, "width": 612, "height": 792 },
  "fields": [
    {
      "id": "p2_last_name",
      "page": 2,
      "type": "text",
      "rect": [92.0, 63.0, 290.0, 79.0],
      "label": "Last Name",
      "origin": "detected",
      "rule": "R2",
      "confidence": 0.82
    },
    {
      "id": "p3_chk_4a_yes",
      "page": 3,
      "type": "checkbox",
      "rect": [618.0, 140.0, 628.0, 150.0],
      "label": "Yes",
      "origin": "detected",
      "rule": "R1",
      "confidence": 0.99
    }
  ]
}
```

Rules that bind every track:

- `rect` is `[x0, y0, x1, y1]` in **PDF points, origin bottom-left**. One
  coordinate system, everywhere. Conversion happens only at the rendering edge.
- `type` is one of `text`, `checkbox`, `multiline`.
- `origin` is `detected`, `user_added`, or `user_moved`.
- `id` is unique within the document and safe as an AcroForm field name:
  `[A-Za-z0-9_]{1,64}`.
- `label` is untrusted text taken from the uploaded file. Every consumer escapes
  it before rendering. See section 9.
- `confidence` is advisory. The UI may sort or highlight by it. No logic depends
  on it in v1.

### 4.2 HTTP API

```
POST /v1/detect
  multipart/form-data: file=<pdf>
  200 → fields.json (section 4.1), plus "cached": true|false
  400   not a PDF / encrypted / malformed
  413   over 20 MB or over 30 pages
  422   PDF parsed but no page structure could be read
  429   rate limit
  503   converter timed out or was killed

GET  /v1/templates/{fingerprint}
  200 → fields.json of the best-ranked correction set
  404   no corrections recorded

POST /v1/templates/{fingerprint}/corrections
  body: { "fields": [...] }        # geometry only, values rejected
  200 → { "version": 3, "confirmations": 12 }
  409   payload contained anything that looks like a filled value
```

`POST /v1/detect` returns cached geometry when the fingerprint is known, and
skips conversion entirely. That is the fast path and it will be the common one.

## 5. Detection engine

Pure function: PDF bytes in, `fields.json` out. No network, no state, no I/O
beyond the input file. Fully testable offline.

Rules, each derived from measured structure in `fixtures/safer.pdf`:

| Rule | Signal | Emits | SAFER count |
|---|---|---|---|
| R1 | Character in the Webdings/Wingdings checkbox glyph set | checkbox at the glyph rect | 81 |
| R2 | Table cell with label text in its top band and blank below | text field below the label | 43 |
| R3 | Empty cell in a grid column, name inherited from the column header above | text field | 95 candidates |
| R4 | Cell containing only mask characters `( ) - $` | text field, mask preserved | 12 |
| R5 | Run of `_` at least 25 points wide | text field on the line | 9 |
| R6 | Small empty square cell drawn with rules, not a glyph | checkbox | 2 |
| R7 | Reject: full-width cell whose only text is a label ending in `:` | nothing | removes 1 false positive |

R1 and R2 are implemented and proven. R3 through R7 are the work.

The R3 figure counts candidate cells, not confirmed fields. Some are spacers and
must be rejected, which is why the rule needs a header-inheritance test rather
than emitting every empty cell. The rule counts do not sum to the ~200 total.

Word draws table borders as thin filled rectangles rather than lines. The cell
grid is recovered from rects narrower than 3 points (vertical rules) and shorter
than 3 points (horizontal rules). `pdfplumber.lines` is empty on this file and
must not be relied on.

**Coverage target:** at least 90 percent of true fields on `fixtures/safer.pdf`
with at most 3 false positives. Current baseline is 62 percent with 1 false
positive.

**Complexity guard.** The prototype cell-recovery loop is quadratic in the number
of horizontal rules per page. A crafted PDF with 50,000 rules would hang it.
Cap rules per page at 2,000 and return a partial result above that.

## 6. Front end

Three screens.

**Upload.** One drop zone. Client-side checks for size and MIME type before
sending, so the obvious rejects never cost a request. Progress and a plain
error message for each status code in section 4.2.

**Fill.** The page renders through `pdf.js`. Detected boxes are drawn as an
overlay of real HTML inputs positioned from `rect`. This screen carries the
whole correction affordance:

- Click empty space to add a box.
- Drag a box to move it, drag an edge to resize.
- Delete a box.
- Toggle a box between text and checkbox.

Every edit updates the in-memory `fields.json` and re-renders instantly. There
is no server round trip and no distinction in code between a detected box and a
user-added one.

**Mobile.** Eight pages of pinch-zooming is the known weakness of typing on the
page. Because every field has a known rect, a **Next field** control zooms to
the next box and focuses a normally sized input. Built in from the start, not
deferred.

**Save.** `pdf-lib` writes the values into the original PDF and triggers a
download. Two outputs: *filled and flattened* for submission, and *fillable* so
the person can revise it later.

## 7. Profile store

`IndexedDB`, in the person's own browser. Holds ordinary identity values —
name, date of birth, address, phone, and similar — under stable semantic keys.

On opening a form, the app proposes matches between profile keys and detected
field labels using normalization plus a small alias table (`sin`,
`social insurance number`, `s.i.n.` all map to one key). Proposals are shown,
never applied silently. The person confirms.

The store is never uploaded, never synced, and never included in a correction
payload. Clearing site data clears it, and the UI says so plainly.

## 8. Template memory

The mechanism that makes the app improve with use.

**Fingerprint.** Computed from the blank form's structure: page count, page
dimensions, and a hash over the first 200 text runs of each page as
`(round(x0), round(top), text)` tuples, sorted. Identical blank forms collide by
design. Different forms do not. Any pre-existing AcroForm data is excluded so
that a fillable and flat copy of the same form still match.

**Flow.** Person 1 uploads SAFER, receives 62 percent, corrects it, and the
corrected geometry is stored against the fingerprint. Person 2 uploads the same
file, hits the cache, and receives a complete form immediately.

**Poisoning guard.** Shared templates are writable by strangers, so they can be
poisoned. Mitigation:

- Store correction sets per fingerprint as versions, never overwrite.
- Track a confirmation count: increment when a person accepts a set and
  downloads without further edits.
- Serve the highest-confirmation set, with ties broken by recency.
- Reject any correction payload containing a field value. Geometry only.

**Retention.** Geometry only, indefinitely. No IP address, no upload, no value.

## 9. Security

The threat is a hostile PDF, because anyone may upload one.

| Risk | Mitigation |
|---|---|
| Parser exploit (`pikepdf`/QPDF, Pillow are C) | Converter runs in its own container: no network egress, read-only filesystem, dropped capabilities, seccomp profile, non-root user |
| Decompression bomb | 512 MB memory cap; process killed on breach |
| Resource exhaustion | 30 s CPU cap; 30-page cap; 2,000-rules-per-page cap |
| SSRF via remote references | No network egress from the converter container. This is the complete fix |
| Stored XSS through field labels | Labels are untrusted. Escaped at every render site. No `innerHTML` on any extracted string |
| Malicious passthrough | Strip `/JavaScript`, `/OpenAction`, `/Launch`, `/EmbeddedFile`, `/AA` from anything returned |
| Compute abuse | 10 uploads per hour per IP |
| Template poisoning | Section 8 |

Uploads are processed in a temporary directory and deleted when the process
exits, on both the success and the failure path.

Limits, in one place:

| Limit | Value |
|---|---|
| File size | 20 MB |
| Pages | 30 |
| CPU | 30 s, then killed |
| Memory | 512 MB, then killed |
| Rules per page | 2,000 |
| Uploads | 10 per hour per IP |

## 10. Learning, stated honestly

**Level 0 — template memory.** Section 8. Delivers most of the perceived
"learning" and needs no model. Build it in v1.

**Level 1 — rule tuning from aggregate corrections.** Log anonymized correction
events, for example "a box was added 12 points right of a label ending in `#`".
Read them periodically and improve the rules in section 5. Cheap and effective.
The developer learns, not the app. Build the logging in v1, act on it later.

**Level 2 — a trained layout model.** Explicitly **not** in scope. It needs
thousands of labeled forms, training infrastructure, and above all a held-out
evaluation set. Without an evaluation set, continuous self-training cannot
distinguish an improvement from a regression, so quality would decay silently.
Revisit only if the corpus grows large, and only after building the evaluation
set first.

## 11. Error handling

| Condition | Behavior |
|---|---|
| Encrypted PDF | 400 with a plain message; offer the manual box-drawing path |
| No text layer (a scan) | 422 with "this looks like a scan, which we cannot read yet"; offer the manual path |
| Converter timeout or kill | 503; the person may retry once, then is offered the manual path |
| Zero fields detected | Not an error. Open the fill screen empty and invite the person to draw boxes |
| Cache serves a bad template | "Fields look wrong?" resets to fresh detection and records a negative signal |

Zero detected fields is deliberately not a failure. A form the detector cannot
read is still fillable by hand, which is strictly better than the status quo.

## 12. Testing

- **Detector.** Golden-file tests per rule against `fixtures/safer.pdf`, with
  expected counts from section 5. Add a fixture per new form shape. Assert
  coverage and false-positive counts, so a rule change that regresses either
  fails the build.
- **Adversarial corpus.** Decompression bomb, 5,000-page file, 100,000-rect
  page, encrypted file, truncated file, a PDF with `/JavaScript`, an HTML file
  renamed to `.pdf`. Each must produce the correct status code, never a hang and
  never a crash.
- **Service.** Contract tests against section 4.2, including every error code.
- **Front end.** Unit tests for the box model. One Playwright run of the full
  path: upload `safer.pdf`, add a box, fill three fields, download, then reopen
  the output and assert the values are present.
- **Round trip.** The strongest test: fill through the browser, re-read the
  output with `pypdf`, assert every value matches what was entered.

## 13. Parallel execution plan

The contracts in section 4 exist so that tracks can proceed without talking to
each other. Each track owns a disjoint set of files. No track edits another
track's files. Every track is given its acceptance test up front.

**T0 — contracts and fixtures. Blocking. Not delegated.**
Write `contracts/fields.schema.json`, `contracts/openapi.yaml`, the shared
fixture set, and a stub server that returns a recorded `fields.json`. Every
other track builds against these. Nothing starts until T0 lands.

| Track | Owns | Depends on | Acceptance |
|---|---|---|---|
| **T1 Detector** | `engine/detect/**` | T0 | Rules R3–R7 implemented; at least 90% coverage and at most 3 false positives on `safer.pdf` |
| **T2 Service** | `service/**` | T0 | Every status code in 4.2 reachable; adversarial corpus passes; detector imported behind an interface, mocked in tests |
| **T3 Viewer/Editor** | `web/src/viewer/**` | T0 | Renders `safer.pdf`, draws boxes from a fixture `fields.json`, supports add/move/resize/delete and Next-field; no network calls |
| **T4 Fill/Export** | `web/src/engine/**` | T0 | **Opens with the `pdf-lib` spike.** Then: values in, filled PDF out, both flattened and fillable; round-trip test passes |
| **T5 Template memory** | `store/**` | T0 | Fingerprint is stable across re-saves of the same blank form and distinct across different forms; ranking and value-rejection tested |
| **T6 Security/Deploy** | `infra/**` | T0 | Container has no egress, is non-root and read-only; limits enforced by kill; rate limiter tested; deploys to Render |

Notes on running these:

- T3 and T4 both live under `web/` and must not share files. T3 owns components
  and interaction. T4 owns pure modules with no DOM. The split is by directory
  and is enforced in review.
- Give each track its own git worktree so the trees never collide.
- Each is a Sonnet subagent. Each brief states: the goal, the exact contract to
  honor, the files it owns, the files it must not touch, and the acceptance test
  it must make pass.
- T4's spike gates the rest of T4. If `pdf-lib` cannot create fields reliably,
  T4 stops and reports before building on it.
- Integration is a separate step after T1–T6 land, done in one place, not by the
  tracks.

## 14. Deferred

- Images and scans: OCR plus OpenCV. The largest single follow-up.
- XFA forms.
- MapleBenefits integration.
- A trained layout model, and the evaluation set it would require first.
- Multi-language form labels.
