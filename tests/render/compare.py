"""Tolerant PNG comparison for the golden-image render tests.

Exact-byte (or exact-pixel) comparison is too brittle for this purpose: a
pdfium or pdf.js point release can shift anti-aliasing or font hinting by a
shade without any real rendering regression, and a test that breaks on every
such bump is worse than no test (it trains people to ignore failures).

The tolerance here was set from real measurements taken while building this
suite, not guessed:
  - Rendering the exact same PDF bytes through pypdfium2 twice in the same
    run produced a 0.0 mean pixel difference (fully deterministic, as
    expected for a fixed engine version on one machine).
  - Rendering the SAME filled fixture through pdfium vs. pdf.js (two
    different rasterizers, which these tests never compare against each
    other) differed by a mean of ~0.8-1.1 per channel, with ~1.3% of pixels
    differing by more than 10 and ~0.26% by more than 100 -- all normal
    anti-aliasing/hinting noise concentrated at glyph and line edges.
A real rendering defect (a missing appearance stream, a checkbox drawn off
its box, clipped text) flips a large solid region of pixels between
"content" and "blank", which reliably produces a much bigger mean diff and
a much bigger over-threshold fraction than either baseline above -- so the
limits below sit above the measured noise floor with headroom for a future
same-engine version bump, while still catching that shape of regression.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

MEAN_DIFF_LIMIT = 4.0  # mean per-channel diff, out of 255
HARD_DIFF_THRESHOLD = 40  # a per-pixel (per-channel-max) diff above this counts as "substantially different"
HARD_DIFF_FRACTION_LIMIT = 0.02  # fraction of pixels allowed to be substantially different


class ImageMismatch(AssertionError):
    pass


def compare_to_golden(golden_path: Path, actual: Image.Image, *, diff_dir: Path, name: str) -> None:
    """Compare `actual` to the PNG at `golden_path`.

    On any mismatch (missing golden, size mismatch, or over-tolerance pixel
    diff), writes `<name>.actual.png` and, where applicable, `<name>.diff.png`
    into diff_dir and raises ImageMismatch with the numbers, so a failure is
    diagnosable from the pytest output plus two images, not just "images
    differ".
    """
    actual_rgb = actual.convert("RGB")
    diff_dir.mkdir(parents=True, exist_ok=True)

    if not golden_path.exists():
        actual_rgb.save(diff_dir / f"{name}.actual.png")
        raise ImageMismatch(
            f"{golden_path.name}: no golden on disk. Wrote {name}.actual.png to {diff_dir} "
            f"for inspection. If this render is correct, copy it to {golden_path} to accept it "
            f"(or re-run with FORMFILL_REGEN_GOLDENS=1 to do that for every golden in the run)."
        )

    golden = Image.open(golden_path).convert("RGB")
    if golden.size != actual_rgb.size:
        actual_rgb.save(diff_dir / f"{name}.actual.png")
        raise ImageMismatch(
            f"{golden_path.name}: size mismatch, golden={golden.size} actual={actual_rgb.size}. "
            f"Wrote {name}.actual.png to {diff_dir}."
        )

    diff = ImageChops.difference(golden, actual_rgb)
    mean_per_channel = ImageStat.Stat(diff).mean
    mean_avg = sum(mean_per_channel) / len(mean_per_channel)

    gray = diff.convert("L")
    total_px = gray.size[0] * gray.size[1]
    hist = gray.histogram()
    over_threshold_px = sum(hist[HARD_DIFF_THRESHOLD + 1 :])
    over_fraction = over_threshold_px / total_px

    if mean_avg > MEAN_DIFF_LIMIT or over_fraction > HARD_DIFF_FRACTION_LIMIT:
        actual_rgb.save(diff_dir / f"{name}.actual.png")
        diff.save(diff_dir / f"{name}.diff.png")
        raise ImageMismatch(
            f"{golden_path.name}: mean per-channel diff {mean_avg:.2f} (limit {MEAN_DIFF_LIMIT}), "
            f"{over_fraction:.2%} of pixels differ by >{HARD_DIFF_THRESHOLD} "
            f"(limit {HARD_DIFF_FRACTION_LIMIT:.2%}). "
            f"Wrote {name}.actual.png and {name}.diff.png to {diff_dir} for inspection."
        )


def write_golden(golden_path: Path, actual: Image.Image) -> None:
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    actual.convert("RGB").save(golden_path)
