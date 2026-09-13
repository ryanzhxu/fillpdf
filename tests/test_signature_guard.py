"""Tests engine.detect.rules.SIGNATURE, the regex R2/R3/R12/R14 and the
end-of-detect catch-all filter use to drop signature lines (see rules.py's
"A signature must be signed, not typed" comment).

Broadened from a bare "signatur" substring to also catch a standalone
"signed" -- fixes a real false positive on
eval/scan_cv/samples/agm_proxy_form.pdf, where a cell captioned "Signed;"
was being offered as a typeable field (see tests/scan_cv/test_backend.py's
test_agm_proxy_form_ignores_boxed_headings_and_the_signature_line).
"""
import unittest

from engine.detect.rules import SIGNATURE


class TestSignatureGuard(unittest.TestCase):
    def test_matches_the_word_signature(self):
        self.assertTrue(SIGNATURE.search("Signature of applicant"))

    def test_matches_a_bare_signed_caption(self):
        self.assertTrue(SIGNATURE.search("Signed;"))
        self.assertTrue(SIGNATURE.search("Signed by"))
        self.assertTrue(SIGNATURE.search("signed"))

    def test_does_not_match_unsigned_or_designed(self):
        # \bsigned\b must not fire on a word that merely CONTAINS "signed"
        # as a substring with no real word boundary in front of it.
        self.assertFalse(SIGNATURE.search("Unsigned or incomplete forms will be void"))
        self.assertFalse(SIGNATURE.search("Designed for accessibility"))


if __name__ == "__main__":
    unittest.main()
