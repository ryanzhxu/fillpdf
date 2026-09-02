"""Tests for eval/score.py. Run with:
    .venv/bin/python eval/test_score.py
"""
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import jsonschema

from eval.score import score_corpus, score_one

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORES_SCHEMA = json.loads((REPO_ROOT / "eval/contracts/scores.schema.json").read_text())


def _truth_doc(source_pdf="form.pdf", family="fam1", widgets=None):
    return {
        "version": 1, "source_pdf": source_pdf, "origin": "synthetic",
        "family": family,
        "pages": [{"page": 1, "width": 612, "height": 792}],
        "widgets": widgets or [
            {"page": 1, "type": "text", "rect": [10, 10, 110, 30], "expects_rule": "R1"},
            {"page": 1, "type": "checkbox", "rect": [200, 200, 220, 220], "expects_rule": "R2"},
        ],
    }


class _TmpForm:
    """A throwaway (pdf_path, truth_path) pair. The pdf file's content is
    irrelevant here since tests inject a detect_fn that ignores it.
    """

    def __init__(self, tmpdir, name="form", truth_doc=None):
        self.dir = Path(tmpdir)
        self.pdf_path = self.dir / f"{name}.pdf"
        self.truth_path = self.dir / f"{name}.json"
        self.pdf_path.write_bytes(b"%PDF-1.4 fake\n")
        doc = truth_doc if truth_doc is not None else _truth_doc(source_pdf=f"{name}.pdf")
        self.truth_path.write_text(json.dumps(doc))
        self.truth_doc = doc


def _perfect_detect_fn(truth_doc):
    """A detect_fn that returns the truth widgets verbatim as detections."""
    def _fn(pdf_path):
        fields = []
        for i, w in enumerate(truth_doc["widgets"]):
            fields.append({
                "id": f"f{i}", "page": w["page"], "type": w["type"],
                "rect": list(w["rect"]), "rule": w.get("expects_rule", "R1"),
                "label": w.get("label", ""),
            })
        return {"version": 1, "pages": truth_doc["pages"], "fields": fields}
    return _fn


def _empty_detect_fn(pdf_path):
    return {"version": 1, "pages": [{"page": 1, "width": 612, "height": 792}], "fields": []}


class TestScoreOne(unittest.TestCase):
    def test_perfect_detector_scores_1_1_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            form = _TmpForm(tmp)
            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=_perfect_detect_fn(form.truth_doc))
            self.assertEqual(metrics["precision"], 1.0)
            self.assertEqual(metrics["recall"], 1.0)
            self.assertEqual(metrics["f1"], 1.0)
            self.assertEqual(metrics["matched"], 2)
            self.assertEqual(metrics["placement"], 1.0)

    def test_empty_detector_scores_zero_no_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            form = _TmpForm(tmp)
            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=_empty_detect_fn)
            self.assertEqual(metrics["precision"], 0.0)
            self.assertEqual(metrics["recall"], 0.0)
            self.assertEqual(metrics["f1"], 0.0)
            self.assertEqual(metrics["matched"], 0)
            self.assertEqual(metrics["placement"], 0.0)


class TestLabelAccuracy(unittest.TestCase):
    """label_accuracy/label_pairs: the metric that catches a matched pair
    whose rect and type are right but whose NAME is wrong -- the blind spot
    rect-IoU-only matching cannot see.
    """

    def test_no_truth_labels_reports_unavailable_not_1_or_0(self):
        # A "stripped" (real-form) truth doc: widgets carry no "label" key at
        # all. This must never read as a perfect (1.0) or failing (0.0) score.
        with tempfile.TemporaryDirectory() as tmp:
            doc = _truth_doc(widgets=[
                {"page": 1, "type": "text", "rect": [10, 10, 110, 30]},
                {"page": 1, "type": "checkbox", "rect": [200, 200, 220, 220]},
            ])
            form = _TmpForm(tmp, truth_doc=doc)
            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=_perfect_detect_fn(form.truth_doc))
            self.assertEqual(metrics["matched"], 2)  # rects still match fine
            self.assertEqual(metrics["label_accuracy"], "UNAVAILABLE")
            self.assertEqual(metrics["label_pairs"], 0)

    def test_matched_labels_agree_counts_as_1_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _truth_doc(widgets=[
                {"page": 1, "type": "text", "rect": [10, 10, 110, 30], "label": "Quantity"},
            ])
            form = _TmpForm(tmp, truth_doc=doc)
            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=_perfect_detect_fn(form.truth_doc))
            self.assertEqual(metrics["label_pairs"], 1)
            self.assertEqual(metrics["label_accuracy"], 1.0)

    def test_matched_pair_with_wrong_label_counts_as_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _truth_doc(widgets=[
                {"page": 1, "type": "text", "rect": [10, 10, 110, 30], "label": "Quantity"},
            ])
            form = _TmpForm(tmp, truth_doc=doc)

            def wrong_label_detect(pdf_path):
                return {"version": 1, "pages": doc["pages"], "fields": [
                    {"id": "f0", "page": 1, "type": "text", "rect": [10, 10, 110, 30],
                     "label": "Amount", "rule": "R1"},
                ]}

            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=wrong_label_detect)
            self.assertEqual(metrics["matched"], 1)  # rect/type still matched
            self.assertEqual(metrics["label_pairs"], 1)
            self.assertEqual(metrics["label_accuracy"], 0.0)

    def test_label_normalisation_noise_does_not_count_as_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _truth_doc(widgets=[
                {"page": 1, "type": "text", "rect": [10, 10, 110, 30], "label": "Relationship"},
            ])
            form = _TmpForm(tmp, truth_doc=doc)

            def noisy_label_detect(pdf_path):
                return {"version": 1, "pages": doc["pages"], "fields": [
                    {"id": "f0", "page": 1, "type": "text", "rect": [10, 10, 110, 30],
                     "label": "Relationship:", "rule": "R2"},
                ]}

            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=noisy_label_detect)
            self.assertEqual(metrics["label_pairs"], 1)
            self.assertEqual(metrics["label_accuracy"], 1.0)

    def test_mixed_matched_pairs_average_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _truth_doc(widgets=[
                {"page": 1, "type": "text", "rect": [10, 10, 110, 30], "label": "Quantity"},
                {"page": 1, "type": "text", "rect": [200, 10, 300, 30], "label": "Amount"},
            ])
            form = _TmpForm(tmp, truth_doc=doc)

            def half_wrong_detect(pdf_path):
                return {"version": 1, "pages": doc["pages"], "fields": [
                    {"id": "f0", "page": 1, "type": "text", "rect": [10, 10, 110, 30],
                     "label": "Quantity", "rule": "R2"},
                    {"id": "f1", "page": 1, "type": "text", "rect": [200, 10, 300, 30],
                     "label": "Quantity", "rule": "R3"},  # column-merge style mislabel
                ]}

            metrics = score_one(form.pdf_path, form.truth_path, detect_fn=half_wrong_detect)
            self.assertEqual(metrics["label_pairs"], 2)
            self.assertAlmostEqual(metrics["label_accuracy"], 0.5)


class TestScoreCorpusSubprocess(unittest.TestCase):
    """These drive the real subprocess path in score_corpus, injecting a
    custom detect_spec (module:function) written to a temp module on disk
    so the child process can import it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write_detect_module(self, body):
        mod_dir = Path(self.tmp) / "detect_mods"
        mod_dir.mkdir(exist_ok=True)
        mod_path = mod_dir / "fake_detect.py"
        mod_path.write_text(textwrap.dedent(body))
        # score_corpus's worker is a separate subprocess -- it only sees
        # mod_dir if it is on PYTHONPATH, not just this process's sys.path.
        old = os.environ.get("PYTHONPATH", "")
        new = str(mod_dir) if not old else str(mod_dir) + os.pathsep + old
        os.environ["PYTHONPATH"] = new
        if old:
            self.addCleanup(os.environ.__setitem__, "PYTHONPATH", old)
        else:
            self.addCleanup(os.environ.pop, "PYTHONPATH", None)
        return str(mod_dir), "fake_detect:detect"

    def test_perfect_detector_end_to_end(self):
        mod_dir, spec = self._write_detect_module("""
            def detect(pdf_path):
                return {
                    "version": 1,
                    "pages": [{"page": 1, "width": 612, "height": 792}],
                    "fields": [
                        {"id": "f0", "page": 1, "type": "text", "rect": [10, 10, 110, 30], "rule": "R1"},
                        {"id": "f1", "page": 1, "type": "checkbox", "rect": [200, 200, 220, 220], "rule": "R2"},
                    ],
                }
        """)
        form = _TmpForm(self.tmp, name="perfect")

        out = score_corpus(
            [(form.pdf_path, form.truth_path)], out_path=None,
            detect_spec=spec, timeout=10,
        )
        self.assertEqual(out["overall"]["precision"], 1.0)
        self.assertEqual(out["overall"]["recall"], 1.0)
        self.assertEqual(out["overall"]["f1"], 1.0)
        self.assertEqual(out["failures"], [])

    def test_validates_against_scores_schema(self):
        mod_dir, spec = self._write_detect_module("""
            def detect(pdf_path):
                return {"version": 1, "pages": [{"page": 1, "width": 612, "height": 792}], "fields": []}
        """)
        form = _TmpForm(self.tmp, name="empty")
        out_dir = Path(self.tmp) / "scores"
        out = score_corpus(
            [(form.pdf_path, form.truth_path)], out_path=str(out_dir),
            detect_spec=spec, timeout=10, git_sha="deadbeef",
        )
        jsonschema.validate(out, SCORES_SCHEMA)

        written = json.loads((out_dir / "deadbeef.json").read_text())
        jsonschema.validate(written, SCORES_SCHEMA)
        self.assertFalse((out_dir / "deadbeef.partial.json").exists())

    def test_hanging_pdf_recorded_as_timeout_and_run_continues(self):
        # detect() hangs only for the form named "hang", so the same spec
        # covers both the pathological form and a normal one in one run.
        mod_dir, spec = self._write_detect_module("""
            import time
            def detect(pdf_path):
                if "hang" in str(pdf_path):
                    time.sleep(5)
                return {"version": 1, "pages": [{"page": 1, "width": 612, "height": 792}], "fields": []}
        """)
        hang_form = _TmpForm(self.tmp, name="hang")
        ok_form = _TmpForm(self.tmp, name="ok")

        out = score_corpus(
            [(hang_form.pdf_path, hang_form.truth_path), (ok_form.pdf_path, ok_form.truth_path)],
            out_path=None, detect_spec=spec, timeout=1,
        )
        self.assertEqual(len(out["failures"]), 1)
        self.assertEqual(out["failures"][0]["reason"], "timeout")
        self.assertIn("hang", out["failures"][0]["form"])
        # the run continued: the second (non-hanging) form was still scored
        self.assertIn("ok.pdf", out["per_form"])

    def test_resumable_skips_already_done_forms(self):
        mod_dir, spec = self._write_detect_module("""
            def detect(pdf_path):
                return {"version": 1, "pages": [{"page": 1, "width": 612, "height": 792}], "fields": []}
        """)
        form = _TmpForm(self.tmp, name="resume")
        out_dir = Path(self.tmp) / "scores"

        score_corpus([(form.pdf_path, form.truth_path)], out_path=str(out_dir),
                      detect_spec=spec, timeout=10, git_sha="abc123")
        # Manually recreate a partial file marking the form done with a
        # sentinel value that would fail schema validation if re-scored,
        # then confirm score_corpus does not re-invoke the detector.
        partial = out_dir / "abc123.partial.json"
        done = {f"tuning:{form.pdf_path}": {
            "ok": True, "source_pdf": "resume.pdf", "family": "fam1",
            "metrics": {"truth": 2, "detected": 2, "matched": 2, "precision": 1.0,
                        "recall": 1.0, "f1": 1.0, "placement": 1.0, "near_miss": 0},
            "raw": {"truth": 2, "detected": 2, "matched": 2, "iou_sum": 2.0, "near_miss": 0},
            "rule_buckets": {}, "_tag": "tuning", "_pdf_path": str(form.pdf_path),
        }}
        partial.write_text(json.dumps(done))

        out = score_corpus([(form.pdf_path, form.truth_path)], out_path=str(out_dir),
                            detect_spec=spec, timeout=10, git_sha="abc123")
        # sentinel values from the partial file made it into the final output
        self.assertEqual(out["overall"]["matched"], 2)
        self.assertEqual(out["per_form"]["resume.pdf"]["matched"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
