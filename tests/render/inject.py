"""Python-side wrapper that shells out to tools/inject_cli.mjs.

Field injection (tools/inject.mjs) is JS-only -- it is a thin layer over
pdf-lib, and there is deliberately no Python reimplementation of it, so the
tests exercise the exact code demo/index.html calls, not a lookalike. This
module is just the subprocess plumbing so the pytest tests can call it like
a normal Python function.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INJECT_CLI = REPO_ROOT / "tools" / "inject_cli.mjs"


def inject(src_pdf: Path, fields: dict, out_pdf: Path, *, flatten: bool = False) -> None:
    """Run tools/inject_cli.mjs and write the resulting PDF to out_pdf.

    Raises AssertionError (with the CLI's stderr) if injection reported any
    failed fields or the subprocess exited non-zero.
    """
    fields_path = out_pdf.with_suffix(".fields.json")
    fields_path.write_text(json.dumps(fields))

    argv = ["node", str(INJECT_CLI), str(src_pdf), str(fields_path), str(out_pdf)]
    if flatten:
        argv.append("--flatten")

    result = subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, (
        f"tools/inject_cli.mjs failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert out_pdf.exists(), f"tools/inject_cli.mjs reported success but wrote no file: {out_pdf}"
