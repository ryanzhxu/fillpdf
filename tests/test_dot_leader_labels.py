"""Tests for dot-leader stripping in engine.detect (_strip_dot_leaders).

A form often prints a fill-in blank as a row of dots -- a "dot leader"
(". . . . . .", "......", "…"). Label extraction sometimes captures one at
the start or end of a caption, leaving a field named ". . . . . ." or
". . . . day of" that tells a user nothing. This was seen on real
leader-line forms in the blind corpus (Canadian bilingual court forms:
eval/corpus/real/1b5c872f5ff35ce0.pdf, d5a3f643b39a6fac.pdf). detect() now
strips a genuine leader run from each end of every label, and a text field
left with no caption of its own falls back to "value".

The strip is conservative: a single dot is never a leader, so an
abbreviation ("U.S.", ".NET", "320 W.") or a trailing colon ("Case
Number:") is left untouched.

Run standalone with:  .venv/bin/python -m pytest tests/test_dot_leader_labels.py
"""
import unittest
from pathlib import Path

from engine.detect import _strip_dot_leaders, detect

# The two real files named in the module docstring above. Not part of this
# worktree's tracked fixtures -- eval/corpus/real is gitignored -- so these
# tests skip if the files are not present rather than failing a clean
# checkout.
REAL_SHORT_FORM = "/Users/ryan.xu/Developer/formfill/eval/corpus/real/1b5c872f5ff35ce0.pdf"
REAL_LONG_FORM = "/Users/ryan.xu/Developer/formfill/eval/corpus/real/d5a3f643b39a6fac.pdf"


def _is_pure_leader(label):
    stripped = label.replace(" ", "")
    return bool(stripped) and set(stripped) <= set(".")


class StripDotLeaders(unittest.TestCase):
    def test_pure_leader_becomes_empty(self):
        for lab in (". . . . . .", "....................................", "…", "··· ·"):
            self.assertEqual(_strip_dot_leaders(lab), "", f"{lab!r} should empty out")

    def test_leading_leader_stripped_with_separator(self):
        self.assertEqual(_strip_dot_leaders(". . . . ., this"), "this")
        self.assertEqual(_strip_dot_leaders(". . . . . Adresse"), "Adresse")
        self.assertEqual(_strip_dot_leaders(". . . in the Province"), "in the Province")

    def test_trailing_leader_stripped(self):
        self.assertEqual(_strip_dot_leaders("The petitioner was born on the . . ."), "The petitioner was born on the")
        self.assertEqual(_strip_dot_leaders("day of . . . ."), "day of")

    def test_single_dot_is_not_a_leader(self):
        # abbreviations and trailing single dots must survive untouched
        for lab in ("U.S. Citizen", ".NET Framework", "320 W.", "e.g. small note",
                    ". Raison sociale (s’il y a"):
            self.assertEqual(_strip_dot_leaders(lab), lab, f"{lab!r} must be untouched")

    def test_ordinary_labels_untouched(self):
        for lab in ("Case Number:", "Name of Firm (if applicable):",
                    "Total cost of meals $ $", "Middle Initial", ""):
            self.assertEqual(_strip_dot_leaders(lab), lab)

    def test_original_whitespace_preserved_when_no_leader(self):
        # surgical: a label with no leader run is returned byte-for-byte,
        # trailing space and all, so this change cannot silently re-trim
        # every other rule's labels.
        self.assertEqual(_strip_dot_leaders("Proof of "), "Proof of ")


class DetectInvariants(unittest.TestCase):
    def test_fixture_has_no_leader_only_labels(self):
        d = detect("fixtures/safer.pdf")
        offenders = [f["label"] for f in d["fields"]
                     if (f.get("label") or "") and set(f["label"]) <= set(". ·…")]
        self.assertEqual(offenders, [])

    def test_fixture_field_count_unchanged(self):
        # the same 222-field count every prior pass asserts on this fixture:
        # leader stripping is label-only and must not add or drop a field.
        d = detect("fixtures/safer.pdf")
        self.assertEqual(len(d["fields"]), 222)

    def test_text_field_never_left_blank(self):
        # a text field emptied by leader stripping falls back to "value";
        # no detected text field may carry an empty label.
        d = detect("fixtures/safer.pdf")
        blank = [f for f in d["fields"] if f["type"] == "text" and not (f.get("label") or "")]
        self.assertEqual(blank, [])


class RealLeaderLineForms(unittest.TestCase):
    """The two bilingual EN/FR Canadian court forms that motivated this
    fix (see module docstring), each hand-verified against detect()'s
    current live output."""

    @unittest.skipUnless(Path(REAL_SHORT_FORM).exists(), "real corpus not present in this worktree")
    def test_short_form_leader_labels_are_readable(self):
        d = detect(REAL_SHORT_FORM)
        labels = [f["label"] for f in d["fields"]]
        self.assertEqual(len(labels), 11)
        self.assertFalse(any(_is_pure_leader(l) for l in labels))
        # Hand-verified: the field that used to be labelled ". . . . . ."
        # now falls back to "value"; "Adresse" is what remains of
        # ". . . . . Adresse" once its leader run is stripped.
        self.assertIn("value", labels)
        self.assertIn("Adresse", labels)
        # The strip is deliberately conservative about a single LEADING dot
        # (see _strip_dot_leaders' docstring: never a leader on its own), so
        # these two French captions keep their leading ". " untouched.
        self.assertIn(". Raison sociale (s’il y a", labels)
        self.assertIn(". Nom du demandeur (ou du", labels)

    @unittest.skipUnless(Path(REAL_LONG_FORM).exists(), "real corpus not present in this worktree")
    def test_long_bilingual_form_has_no_leader_only_labels(self):
        d = detect(REAL_LONG_FORM)
        labels = [f["label"] for f in d["fields"]]
        self.assertEqual(len(labels), 117)
        self.assertFalse(any(_is_pure_leader(l) for l in labels))
        # Hand-verified: page 8 carries two "day of . . . ." write-on lines
        # that fall back to "this" once their trailing leader is stripped.
        self.assertEqual(labels.count("this"), 2)


if __name__ == "__main__":
    unittest.main()
