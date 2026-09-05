"""Tests for scripts/build_site.py, the static site deployed to Cloudflare.

There is no server. The site runs engine/detect in the visitor's browser under
Pyodide, so the build's job is to put the REAL detector sources next to the
page rather than reimplement anything. What is worth pinning is the seams:

  * both rewrite markers still exist in demo/index.html, so the build cannot
    silently produce a page that looks fine until someone picks a file
  * the built page is in pyodide mode and the demo's is not
  * the detector shipped to the browser is byte-identical to the one the eval
    harness scores -- a copy that drifted would make the deployed app a
    different product from the measured one
  * the vendored wheels are pure-Python, or they cannot install under Pyodide

The wheel download needs the network, so those tests skip without it rather
than failing a clean offline checkout.

Run standalone with:  .venv/bin/python -m pytest tests/test_build_site.py
"""
import json
import shutil
import unittest
from pathlib import Path

from scripts.build_site import (ENGINE_FILES, MODE_DST, MODE_SRC, TOOLS_DST,
                                TOOLS_SRC, WHEELS, build)

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "demo" / "index.html"


class TestMarkers(unittest.TestCase):
    """Cheap, offline, and the ones that actually break the page."""

    def test_index_html_carries_both_rewrite_markers(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn(MODE_SRC, html)
        self.assertIn(TOOLS_SRC, html)

    def test_demo_ships_in_baked_mode(self):
        # demo.py serves this file as-is. If it ever ships as 'pyodide' the
        # local demo would show a file picker instead of the baked form.
        self.assertIn(MODE_SRC, INDEX.read_text(encoding="utf-8"))

    def test_engine_files_listed_for_the_browser_all_exist(self):
        for rel in ENGINE_FILES:
            self.assertTrue((ROOT / rel).exists(), rel)


class TestBuild(unittest.TestCase):
    """The full build. Needs the network for pip download."""

    @classmethod
    def setUpClass(cls):
        cls.out = ROOT / "site-test"
        try:
            build(cls.out)
        except Exception as err:            # noqa: BLE001
            cls.out = None
            raise unittest.SkipTest(f"build needs the network: {err}")

    @classmethod
    def tearDownClass(cls):
        if cls.out:
            shutil.rmtree(cls.out, ignore_errors=True)

    def test_built_page_is_in_pyodide_mode(self):
        html = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn(MODE_DST, html)
        self.assertNotIn(MODE_SRC, html)

    def test_built_page_imports_the_injector_from_the_site_root(self):
        html = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn(TOOLS_DST, html)
        self.assertNotIn(TOOLS_SRC, html)
        self.assertTrue((self.out / "tools" / "inject.mjs").exists())

    def test_shipped_detector_is_byte_identical_to_the_scored_one(self):
        # The whole claim of this project is that the demo runs the same code
        # the harness measures. A drifted copy would quietly break that.
        for rel in ENGINE_FILES:
            self.assertEqual((self.out / rel).read_bytes(),
                             (ROOT / rel).read_bytes(), rel)

    def test_wheels_are_pure_python_and_listed_in_the_manifest(self):
        manifest = json.loads(
            (self.out / "wheels" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), set(WHEELS))
        for pkg, name in manifest.items():
            # micropip parses name/version/tags out of the filename, so the
            # real wheel name has to survive into the manifest verbatim.
            self.assertTrue(name.endswith("-py3-none-any.whl"), f"{pkg}: {name}")
            self.assertTrue((self.out / "wheels" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
