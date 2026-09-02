# Contracts — do not change these without changing every consumer

Three shapes bind every part of the evaluation harness. They exist so that
independent tracks can be built in parallel without talking to each other.

## 1. Detector interface

```python
from engine.detect import detect
result = detect("some.pdf")     # -> dict matching fields.schema.json
```

Pure function. No network. No mutation of the input file. No global state.
Same bytes in, same dict out, every time.

## 2. `fields.schema.json` — what the detector emits

Coordinates are **PDF points, origin bottom-left**, as `[x0, y0, x1, y1]`.
One coordinate system everywhere. Convert only at a rendering edge.

## 3. `truth.schema.json` — the answer key

Same coordinate system as fields. Produced by the synthetic generator (which
knows exactly where it placed each field) or by extracting widget annotations
from a fillable PDF.

## 4. `scores.schema.json` — what the scorer writes

One file per run at `scores/<git-sha>.json`. Committed, so regressions are
visible in `git log` rather than in somebody's memory.

## Rules that bind everyone

- `label` is untrusted text taken from a PDF. Escape it at every render site.
- `type` is one of `text`, `checkbox`, `multiline`.
- `id` matches `[A-Za-z0-9_]{1,64}` and is unique within a document.
- Never write outside the directory your task owns.
