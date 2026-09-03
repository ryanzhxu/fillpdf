"""Renders a PDF page (with form widgets) to a PIL image via pdf.js.

pdf.js is best-effort here: it needs pdfjs-dist plus a canvas implementation
in Node, which is more likely to be a rough environment to get working than
pypdfium2 is in Python. It DID work cleanly in this environment (see
tools/render_pdfjs_cli.cjs for the one real wrinkle: pdfjs-dist 6.x renders
blank under node-canvas, so this is pinned to 3.11.174, the same version
demo/index.html already loads from cdnjs). If a future environment can't
install `canvas` (it ships prebuilt binaries for common platforms but is not
guaranteed everywhere), tests/render/test_render.py skips the pdf.js tests
rather than failing the suite -- pdfium coverage stays green regardless.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RENDER_CLI = REPO_ROOT / "tools" / "render_pdfjs_cli.cjs"


def render_page(pdf_path: Path, out_png: Path, page_index: int = 0, scale: float = 3.0) -> Image.Image:
    argv = ["node", str(RENDER_CLI), str(pdf_path), str(out_png), str(page_index), str(scale)]
    result = subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, (
        f"tools/render_pdfjs_cli.cjs failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return Image.open(out_png).convert("RGB")
