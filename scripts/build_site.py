#!/usr/bin/env python3
"""Build the static site deployed to Cloudflare.

There is no server. The detector is Python, so it runs in the visitor's
browser under Pyodide (CPython on WebAssembly), against the SAME
engine/detect sources the eval harness scores. This script just assembles
what that needs into one directory:

    site/index.html            demo/index.html, mode rewritten to 'pyodide'
    site/tools/inject.mjs      the one injector, shared with the demo + tests
    site/engine/detect/*.py    the detector itself, fetched and run in-browser
    site/wheels/*.whl          pdfminer.six + pdfplumber, so the app does not
                               depend on PyPI being reachable at runtime

Two rewrites are asserted rather than assumed, because a silent miss would
serve a page that looks fine until someone picks a file:

  * the mode marker, which turns on the picker
  * the '../tools/' import specifier, which is right in the repo layout and
    wrong once index.html sits at the site root

Usage:  ./.venv/bin/python scripts/build_site.py [--out site]
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODE_SRC = "window.FILLPDF_MODE = 'baked';"
MODE_DST = "window.FILLPDF_MODE = 'pyodide';"
TOOLS_SRC = "'../tools/inject.mjs'"
TOOLS_DST = "'./tools/inject.mjs'"

# Pure-Python wheels only. pdfplumber's other dependency, pypdfium2, has no
# pure-Python wheel and is stubbed in the browser -- it is an image-rendering
# path engine/detect never calls. Pillow and cryptography come from Pyodide's
# own package set, not from here.
#
# The real wheel filenames are kept, and written to wheels/index.json for the
# page to read. micropip parses name, version and tags back OUT of the
# filename, so a tidier "pdfplumber.whl" is rejected as
# InvalidWheelFilename -- measured, not guessed.
WHEELS = ("pdfminer.six", "pdfplumber")

# The detector. engine/ is a namespace package, so there is no __init__.py to
# copy; the browser adds the site root to sys.path and imports engine.detect.
ENGINE_FILES = ["engine/detect/__init__.py", "engine/detect/rules.py"]


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    for marker in (MODE_SRC, TOOLS_SRC):
        if marker not in html:
            sys.exit(f"build_site: expected {marker!r} in demo/index.html; "
                     "the marker moved and this rewrite is now wrong")
    (out / "index.html").write_text(
        html.replace(MODE_SRC, MODE_DST).replace(TOOLS_SRC, TOOLS_DST),
        encoding="utf-8")

    shutil.copytree(ROOT / "tools", out / "tools")

    for rel in ENGINE_FILES:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dst)

    wheels = out / "wheels"
    wheels.mkdir()
    manifest = {}
    for pkg in WHEELS:
        before = set(wheels.glob("*.whl"))
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
             "--no-deps", pkg, "-d", str(wheels), "-q"],
            check=True)
        got = sorted(set(wheels.glob("*.whl")) - before)
        if len(got) != 1:
            sys.exit(f"build_site: expected one new wheel for {pkg}, got "
                     f"{[p.name for p in got]}")
        # A pure-Python wheel, or it cannot install in the browser at all.
        if not got[0].name.endswith("-py3-none-any.whl"):
            sys.exit(f"build_site: {got[0].name} is not a pure-Python wheel; "
                     "it cannot be installed under Pyodide")
        manifest[pkg] = got[0].name
    (wheels / "index.json").write_text(json.dumps(manifest, indent=1),
                                       encoding="utf-8")

    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    out = build(ROOT / args.out)

    files = sorted(p for p in out.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print(f"built {out.relative_to(ROOT)}/  {len(files)} files, "
          f"{total / 1024 / 1024:.1f}MB")
    for p in files:
        print(f"  {p.relative_to(out)}  {p.stat().st_size / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
