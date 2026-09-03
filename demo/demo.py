#!/usr/bin/env python3
"""THROWAWAY DEMO. Feel-test for FormFill, not the foundation.

Usage:  python demo.py <any.pdf>
Detects fields, writes ./out/, serves it, opens a browser.
Pages are rendered in the browser by pdf.js, so the original text stays selectable.
"""
import collections
import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

import pdfplumber



HERE = Path(__file__).parent

# The detector lives in engine/detect now. The demo deliberately calls the SAME
# code the evaluation harness scores, so what you see is exactly what is
# measured -- if they drifted apart the demo would stop being evidence.
sys.path.insert(0, str(HERE.parent))
from engine.detect import detect as detect_pdf  # noqa: E402
OUT = HERE / "out"


def build(pdf_path: Path):
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copy(pdf_path, OUT / "source.pdf")
    # index.html imports tools/inject.mjs -- the ONE implementation of field
    # injection, shared with the render tests so the two cannot drift. Two
    # different layouts have to agree here: in the repo, index.html sits in
    # demo/ and tools/ is at the root, so the source says "../tools/". Served,
    # OUT is the document root and "../" escapes it, which the stdlib handler
    # answers with a 404 -- silently, since it is a dynamic import inside a
    # click handler, so the page would look fine until Download did nothing.
    # Copy the module in and rewrite the one specifier. This demo has been
    # broken by exactly this shape before: a move left an import pointing at
    # nothing and it went unnoticed while the whole test suite passed.
    shutil.copytree(HERE.parent / "tools", OUT / "tools")
    html = (HERE / "index.html").read_text(encoding="utf-8")
    served = html.replace("'../tools/inject.mjs'", "'./tools/inject.mjs'")
    if served == html:
        sys.exit("demo build: expected \"'../tools/inject.mjs'\" in index.html; "
                 "the import moved and this rewrite is now wrong")
    (OUT / "index.html").write_text(served, encoding="utf-8")

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) > 30:
            sys.exit("demo caps at 30 pages")

    # One call into the same entry point the evaluation harness scores. The
    # demo used to re-implement the per-page loop and the id assignment, which
    # meant it could silently drift from what was being measured.
    doc = detect_pdf(str(pdf_path))
    fields, pages = doc["fields"], doc["pages"]

    json.dump(doc, open(OUT / "fields.json", "w"), indent=1)

    by_rule = collections.Counter(f.get("rule", "?") for f in fields)
    n_txt = sum(1 for f in fields if f["type"] == "text")
    n_lab = sum(1 for f in fields if f.get("label", "").strip())
    print(f"detected {len(fields)} fields  ({n_txt} text, {len(fields)-n_txt} checkbox) "
          f"across {len(pages)} pages")
    print("  by rule: " + "  ".join(f"{k}={by_rule[k]}" for k in sorted(by_rule)))
    print(f"  labelled: {n_lab}/{len(fields)}")
    return len(fields)


def serve():
    os.chdir(OUT)
    with socketserver.TCPServer(("127.0.0.1", 0),
                                http.server.SimpleHTTPRequestHandler) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        print(f"\n  {url}\n  ctrl-c to stop\n")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("stopped")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python demo.py <any.pdf>")
    src = Path(sys.argv[1]).expanduser()
    if not src.exists():
        sys.exit(f"no such file: {src}")
    build(src)
    serve()
