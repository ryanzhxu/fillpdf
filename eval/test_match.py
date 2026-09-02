"""Tests for eval/match.py. Run with:
    .venv/bin/python eval/test_match.py
"""
import unittest

from eval.match import iou, match


class TestIou(unittest.TestCase):
    def test_identical_rects(self):
        r = [10, 10, 20, 20]
        self.assertEqual(iou(r, list(r)), 1.0)

    def test_disjoint_rects(self):
        a = [0, 0, 10, 10]
        b = [20, 20, 30, 30]
        self.assertEqual(iou(a, b), 0.0)

    def test_half_overlap_known_value(self):
        # a: 10x10 square [0,0,10,10], b: 10x10 square [5,0,15,10]
        # intersection = 5x10 = 50, union = 100+100-50 = 150 -> 1/3
        a = [0, 0, 10, 10]
        b = [5, 0, 15, 10]
        self.assertAlmostEqual(iou(a, b), 50 / 150)

    def test_swapped_corners_normalised(self):
        # same rect as test_half_overlap_known_value but with swapped corners
        a = [10, 10, 0, 0]
        b = [15, 10, 5, 0]
        self.assertAlmostEqual(iou(a, b), 50 / 150)

    def test_touching_edges_zero_area_intersection(self):
        a = [0, 0, 10, 10]
        b = [10, 0, 20, 10]
        self.assertEqual(iou(a, b), 0.0)


def _det(id_, page, type_, rect, rule="R1"):
    return {"id": id_, "page": page, "type": type_, "rect": rect, "rule": rule}


def _truth(page, type_, rect, expects_rule=None):
    d = {"page": page, "type": type_, "rect": rect}
    if expects_rule:
        d["expects_rule"] = expects_rule
    return d


class TestMatchGreedyOneToOne(unittest.TestCase):
    def test_two_detections_one_truth_higher_iou_wins(self):
        truth = [_truth(1, "text", [0, 0, 100, 20])]
        # det0 covers truth well (IoU high), det1 covers it poorly but still >=0.5
        det_high = _det("high", 1, "text", [0, 0, 100, 20])       # IoU 1.0
        det_low = _det("low", 1, "text", [0, 0, 100, 12])          # IoU 0.6
        detections = [det_low, det_high]

        result = match(detections, truth)
        self.assertEqual(len(result["matches"]), 1)
        di, ti, iou_val = result["matches"][0]
        self.assertEqual(di, 1)  # det_high, index 1
        self.assertEqual(ti, 0)
        self.assertAlmostEqual(iou_val, 1.0)
        self.assertEqual(result["unmatched_det"], [0])  # det_low is the false positive
        self.assertEqual(result["unmatched_truth"], [])

    def test_perfect_match_all_matched(self):
        truth = [_truth(1, "text", [0, 0, 10, 10]), _truth(1, "checkbox", [20, 20, 30, 30])]
        detections = [
            _det("a", 1, "text", [0, 0, 10, 10]),
            _det("b", 1, "checkbox", [20, 20, 30, 30]),
        ]
        result = match(detections, truth)
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual(result["unmatched_det"], [])
        self.assertEqual(result["unmatched_truth"], [])


class TestTypeMismatch(unittest.TestCase):
    def test_text_over_checkbox_is_miss_and_false_positive(self):
        truth = [_truth(1, "checkbox", [0, 0, 10, 10])]
        detections = [_det("a", 1, "text", [0, 0, 10, 10])]  # perfect geometric overlap
        result = match(detections, truth)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unmatched_det"], [0])
        self.assertEqual(result["unmatched_truth"], [0])

    def test_multiline_detection_matches_text_truth(self):
        truth = [_truth(1, "text", [0, 0, 10, 10])]
        detections = [_det("a", 1, "multiline", [0, 0, 10, 10])]
        result = match(detections, truth)
        self.assertEqual(len(result["matches"]), 1)

    def test_choice_truth_treated_as_text(self):
        truth = [_truth(1, "choice", [0, 0, 10, 10])]
        detections = [_det("a", 1, "text", [0, 0, 10, 10])]
        result = match(detections, truth)
        self.assertEqual(len(result["matches"]), 1)


class TestNearMiss(unittest.TestCase):
    def test_fires_at_iou_0_3(self):
        # truth [0,0,10,10] area 100; det [4,4,14,14] area 100
        # inter = [4,4]-[10,10] = 6x6 = 36; union = 100+100-36 = 164; iou = 36/164 ~= 0.2195
        truth = [_truth(1, "text", [0, 0, 10, 10])]
        det_rect = [4, 4, 14, 14]
        detections = [_det("a", 1, "text", det_rect)]
        iou_val = iou(truth[0]["rect"], det_rect)
        self.assertTrue(0.15 <= iou_val < 0.5, f"test setup invalid, iou={iou_val}")
        result = match(detections, truth)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["near_miss"], [0])

    def test_does_not_fire_at_iou_0_6(self):
        truth = [_truth(1, "text", [0, 0, 10, 10])]
        det_rect = [0, 0, 10, 6]  # inter=60, union=100+60-60=100, iou=0.6 -> real match, not near-miss
        detections = [_det("a", 1, "text", det_rect)]
        iou_val = iou(truth[0]["rect"], det_rect)
        self.assertGreaterEqual(iou_val, 0.5)
        result = match(detections, truth)
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["near_miss"], [])

    def test_does_not_fire_at_iou_0_05(self):
        truth = [_truth(1, "text", [0, 0, 10, 10])]
        det_rect = [9, 9, 20, 20]  # inter = 1x1=1, area_b=121, union=100+121-1=220, iou=1/220 ~0.0045
        detections = [_det("a", 1, "text", det_rect)]
        iou_val = iou(truth[0]["rect"], det_rect)
        self.assertLess(iou_val, 0.15)
        result = match(detections, truth)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["near_miss"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
