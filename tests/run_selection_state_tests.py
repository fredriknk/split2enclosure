import os
import sys
import unittest

import FreeCAD as App
import Part


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from split2enclosure._geometry_types import ContourInfo
from split2enclosure.selection_state import encode_contour_state, match_contour_state


def rectangle(index, x, y, width=10.0, height=6.0, kind="outer"):
    points = [
        App.Vector(x, y, 0),
        App.Vector(x + width, y, 0),
        App.Vector(x + width, y + height, 0),
        App.Vector(x, y + height, 0),
        App.Vector(x, y, 0),
    ]
    wire = Part.Wire(Part.makePolygon(points).Edges)
    return ContourInfo(index, kind, wire, Part.Face(wire).Area, wire.Length)


class ContourStateTests(unittest.TestCase):
    def test_choices_follow_geometry_when_indices_are_reordered(self):
        original = [rectangle(0, 0, 0), rectangle(1, 30, 0)]
        encoded = encode_contour_state(
            original,
            {0: "negative", 1: "positive"},
            {0: True, 1: False},
        )
        reordered = [rectangle(7, 30, 0), rectangle(4, 0, 0)]

        sides, snaps, report = match_contour_state(reordered, encoded)

        self.assertEqual(sides, {4: "negative", 7: "positive"})
        self.assertEqual(snaps, {4: True, 7: False})
        self.assertEqual(report, {"matched": 2, "missing": 0, "ambiguous": 0})

    def test_changed_contour_is_not_silently_assigned(self):
        encoded = encode_contour_state(
            [rectangle(0, 0, 0)], {0: "positive"}, {0: True}
        )

        sides, snaps, report = match_contour_state(
            [rectangle(2, 0, 0, width=15.0)], encoded
        )

        self.assertEqual(sides, {})
        self.assertEqual(snaps, {})
        self.assertEqual(report["missing"], 1)

    def test_equally_plausible_contours_are_left_unassigned(self):
        encoded = encode_contour_state(
            [rectangle(0, 0, 0)], {0: "negative"}, {0: True}
        )
        candidates = [
            rectangle(4, -0.01, 0),
            rectangle(9, 0.01, 0),
        ]

        sides, snaps, report = match_contour_state(candidates, encoded)

        self.assertEqual(sides, {})
        self.assertEqual(snaps, {})
        self.assertEqual(report["ambiguous"], 1)

    def test_corrupt_state_fails_closed(self):
        sides, snaps, report = match_contour_state(
            [rectangle(0, 0, 0)], "not json"
        )
        self.assertEqual(sides, {})
        self.assertEqual(snaps, {})
        self.assertEqual(report["missing"], 1)

    def test_two_saved_contours_cannot_claim_one_current_contour(self):
        original = [rectangle(0, -0.01, 0), rectangle(1, 0.01, 0)]
        encoded = encode_contour_state(
            original,
            {0: "negative", 1: "positive"},
            {0: True, 1: False},
        )

        sides, snaps, report = match_contour_state(
            [rectangle(8, 0, 0)], encoded
        )

        self.assertEqual(sides, {})
        self.assertEqual(snaps, {})
        self.assertEqual(report["ambiguous"], 2)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContourStateTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Contour-state test suite failed")
