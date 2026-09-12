# FormFill

Fill any flat, non-fillable PDF form right in your browser. FormFill detects
the form fields on a scanned or flattened PDF and turns them into a real,
typeable form — no upload, no server, nothing ever leaves your device.

**Live:** https://fillpdf.ryanxu.dev

## How it works

Most government and institutional PDFs ship as flat, non-interactive pages —
no AcroForm fields, just printed boxes and lines. FormFill renders the page
with pdf.js (so the original text stays selectable), detects where the fields
should be, and lets you type directly into them. Download the result as a
flattened PDF or a still-editable fillable one.

Detection runs against a corpus of real government forms; see `eval/` for the
scoring harness and `docs/` for how the detector rules work.

## Development

```bash
npm install
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python demo/demo.py fixtures/safer.pdf   # or any PDF
```

Run the test suite with `python -m pytest -q`.
