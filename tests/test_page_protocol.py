"""Tests that a real pdfplumber page satisfies DetectablePage for free.

engine/detect/page_protocol.py is a structural (duck-typed) contract: any
object exposing these five things can be scored by rules.py's unmodified
rules. This is the cheapest possible proof that today's real pdfplumber
pages already qualify, before any synthetic page exists.

Run standalone with:  .venv/bin/python -m pytest tests/test_page_protocol.py
"""
import unittest

import pdfplumber

from engine.detect.page_protocol import DetectablePage


class TestPageProtocol(unittest.TestCase):
    def test_real_pdfplumber_page_satisfies_the_protocol(self):
        with pdfplumber.open("fixtures/safer.pdf") as pdf:
            page = pdf.pages[0]
            self.assertIsInstance(page, DetectablePage)


if __name__ == "__main__":
    unittest.main()
