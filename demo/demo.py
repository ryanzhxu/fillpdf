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

from detect_rules import detect, slug

HERE = Path(__file__).parent
OUT = HERE / "out"


def build(pdf_path: Path):
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copy(pdf_path, OUT / "source.pdf")
    shutil.copy(HERE / "index.html", OUT / "index.html")

    fields, pages = [], []
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) > 30:
            sys.exit("demo caps at 30 pages")
        for i, pg in enumerate(pdf.pages, 1):
            pages.append({"page": i, "width": pg.width, "height": pg.height})
            fields += detect(pg, i)

    seen = {}
    for f in fields:
        base = f"p{f['page']}_" + (slug(f["label"]) if f["type"] == "text" else "chk")
        seen[base] = seen.get(base, 0) + 1
        f["id"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
        f["origin"] = "detected"

    json.dump({"version": 1, "source": {"pages": len(pages)},
               "pages": pages, "fields": fields},
              open(OUT / "fields.json", "w"), indent=1)

    by_rule = collections.Counter(f["rule"] for f in fields)
    n_txt = sum(1 for f in fields if f["type"] == "text")
    print(f"detected {len(fields)} fields  ({n_txt} text, {len(fields)-n_txt} checkbox) "
          f"across {len(pages)} pages")
    print("  by rule: " + "  ".join(f"{k}={by_rule[k]}" for k in sorted(by_rule)))
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
