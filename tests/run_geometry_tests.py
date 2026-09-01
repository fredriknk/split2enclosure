import os
import sys
import unittest

import FreeCAD as App
import Part

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from split2enclosure.geometry import make_enclosure, plane_from_axes


def hollow_box(open_top=True):
    outer = Part.makeBox(40, 30, 20)
    cavity_height = 18 if open_top else 16
    cavity = Part.makeBox(36, 26, cavity_height, App.Vector(2, 2, 2))
    return outer.cut(cavity)


class GeometryTests(unittest.TestCase):
    def assert_valid_pair(self, source, result):
        self.assertTrue(result.negative.isValid())
        self.assertTrue(result.positive.isValid())
        self.assertGreater(result.lip.Volume, 0.0)
        self.assertGreater(result.groove.Volume, result.lip.Volume)
        self.assertLess(
            result.negative.common(result.positive).Volume,
            1e-5,
            "The assembled halves must not interfere",
        )
        self.assertGreater(result.negative.Volume + result.positive.Volume, 0.0)
        self.assertLess(
            result.negative.Volume + result.positive.Volume,
            source.Volume + result.lip.Volume + 1e-4,
        )

    def test_xy_split_hollow_box(self):
        source = hollow_box(open_top=False)
        origin, normal = plane_from_axes("XY", 10)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.5,
            clearance=0.2,
            vertical_clearance=0.2,
        )
        self.assertEqual(len(result.internal_wires), 1)
        self.assertAlmostEqual(result.section.Area, 264.0, places=5)
        self.assert_valid_pair(source, result)

    def test_xz_split_and_reversed_lip(self):
        source = hollow_box(open_top=False)
        origin, normal = plane_from_axes("XZ", 15)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.25,
            clearance=0.15,
            vertical_clearance=0.1,
            lip_on="positive",
        )
        self.assertEqual(len(result.internal_wires), 1)
        self.assert_valid_pair(source, result)

    def test_internal_divider_creates_two_internal_contours(self):
        source = hollow_box()
        divider = Part.makeBox(2, 26, 18, App.Vector(19, 2, 2))
        source = source.fuse(divider).removeSplitter()
        origin, normal = plane_from_axes("XY", 10)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.6,
            lip_height=1.0,
            clearance=0.15,
            vertical_clearance=0.15,
        )
        self.assertEqual(len(result.internal_wires), 2)
        self.assert_valid_pair(source, result)

    def test_arbitrary_datum_style_plane(self):
        source = hollow_box(open_top=False)
        origin = App.Vector(20, 15, 10)
        normal = App.Vector(1, 1, 0.35)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.55,
            lip_height=1.0,
            clearance=0.15,
            vertical_clearance=0.1,
        )
        self.assertEqual(len(result.internal_wires), 1)
        self.assert_valid_pair(source, result)

    def test_solid_block_has_no_internal_contour(self):
        origin, normal = plane_from_axes("XY", 5)
        with self.assertRaisesRegex(ValueError, "No internal closed"):
            make_enclosure(Part.makeBox(10, 10, 10), origin, normal)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(GeometryTests)
outcome = unittest.TextTestRunner(verbosity=2).run(suite)
if not outcome.wasSuccessful():
    raise RuntimeError("Geometry test suite failed")
