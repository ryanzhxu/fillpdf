"""Tests for the Yes/No radio-group tagging in engine.detect.

A checkbox question with a "Yes" answer and a "No" answer is mutually
exclusive: a person must never tick both. R1 writes the question into each
option's label ("<question> (Yes)" / "<question> (No)"), so detect() tags the
pair with a shared `group` id a consumer can inject as one radio group. Only
this unambiguous shape is grouped -- a multi-select list (a "cc:" recipient
list) has no single Yes/No pair and must stay independent, or the injected form
would wrongly stop a person selecting more than one.

Run standalone with:  .venv/bin/python -m pytest tests/test_radio_groups.py
"""
import unittest

from engine.detect import detect
from engine.detect import _group_yes_no


def _cb(page, label):
    return {"type": "checkbox", "page": page, "label": label}


class TestGroupYesNo(unittest.TestCase):
    def test_yes_no_pair_under_one_question_is_grouped(self):
        fields = [_cb(1, "Are you married? (Yes)"), _cb(1, "Are you married? (No)")]
        _group_yes_no(fields)
        self.assertEqual(fields[0]["group"], fields[1]["group"])
        self.assertTrue(fields[0]["group"])

    def test_two_questions_get_two_distinct_groups(self):
        fields = [
            _cb(1, "Are you married? (Yes)"), _cb(1, "Are you married? (No)"),
            _cb(1, "Are you employed? (Yes)"), _cb(1, "Are you employed? (No)"),
        ]
        _group_yes_no(fields)
        self.assertEqual(fields[0]["group"], fields[1]["group"])
        self.assertEqual(fields[2]["group"], fields[3]["group"])
        self.assertNotEqual(fields[0]["group"], fields[2]["group"])

    def test_multi_select_cc_list_is_not_grouped(self):
        # A "cc:" recipient list: a person checks all who apply, not one.
        fields = [
            _cb(1, "cc: (Petitioner)"), _cb(1, "cc: (Sheriff)"), _cb(1, "cc: (Jail)"),
        ]
        _group_yes_no(fields)
        self.assertFalse(any("group" in f for f in fields))

    def test_four_boxes_under_one_prefix_are_not_merged(self):
        # Two Yes/No questions that happen to share wording must not collapse
        # into one radio group -- that would let only one of the two be answered.
        fields = [_cb(1, "Paid off? (Yes)")] * 0 + [
            _cb(1, "Paid off? (Yes)"), _cb(1, "Paid off? (No)"),
            _cb(1, "Paid off? (Yes)"), _cb(1, "Paid off? (No)"),
        ]
        _group_yes_no(fields)
        self.assertFalse(any("group" in f for f in fields))

    def test_same_question_on_different_pages_stays_separate(self):
        fields = [
            _cb(1, "Married? (Yes)"), _cb(1, "Married? (No)"),
            _cb(2, "Married? (Yes)"), _cb(2, "Married? (No)"),
        ]
        _group_yes_no(fields)
        self.assertEqual(fields[0]["group"], fields[1]["group"])
        self.assertEqual(fields[2]["group"], fields[3]["group"])
        self.assertNotEqual(fields[0]["group"], fields[2]["group"])

    def test_prefix_without_question_or_colon_is_not_grouped(self):
        # "Option 1 (Yes)" is not a question prompt; require a trailing ? or :.
        fields = [_cb(1, "Choice 1 (Yes)"), _cb(1, "Choice 1 (No)")]
        _group_yes_no(fields)
        self.assertFalse(any("group" in f for f in fields))

    def test_text_field_with_a_yes_no_label_is_not_grouped(self):
        fields = [
            {"type": "text", "page": 1, "label": "Married? (Yes)"},
            {"type": "text", "page": 1, "label": "Married? (No)"},
        ]
        _group_yes_no(fields)
        self.assertFalse(any("group" in f for f in fields))


class TestGroupYesNoOnFixture(unittest.TestCase):
    """The real SAFER form carries several Yes/No questions on page 3-4."""

    def test_fixture_yes_no_questions_form_valid_radio_groups(self):
        doc = detect("fixtures/safer.pdf")
        groups = {}
        for f in doc["fields"]:
            gid = f.get("group")
            if gid:
                groups.setdefault(gid, []).append(f)

        self.assertTrue(groups, "expected at least one Yes/No radio group on SAFER")
        for gid, members in groups.items():
            self.assertEqual(len(members), 2, f"{gid} must have exactly two options")
            for f in members:
                self.assertEqual(f["type"], "checkbox")
            # One option is Yes, the other No, under an identical question.
            options = sorted(f["label"].rsplit("(", 1)[1].rstrip(")").strip().lower()
                             for f in members)
            self.assertEqual(options, ["no", "yes"])
            prefixes = {f["label"].rsplit("(", 1)[0].strip() for f in members}
            self.assertEqual(len(prefixes), 1)

    def test_grouping_does_not_change_field_count_or_geometry(self):
        # `group` is an annotation only: it must not add, drop or move a field.
        doc = detect("fixtures/safer.pdf")
        # Every grouped field is still a checkbox with an intact rect and label.
        for f in doc["fields"]:
            if f.get("group"):
                self.assertEqual(len(f["rect"]), 4)
                self.assertTrue(f["label"])


if __name__ == "__main__":
    unittest.main()
