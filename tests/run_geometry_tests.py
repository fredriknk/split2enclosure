import os
import sys
import unittest
from unittest import mock

import FreeCAD as App
import Part

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.environ.get("SPLIT2ENCLOSURE_TEST_INSTALLED") and PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from split2enclosure.geometry import (
    _safe_refine,
    analyze_section_contours,
    make_enclosure,
    plane_from_axes,
    split_with_sketch,
)


def hollow_box(open_top=True):
    outer = Part.makeBox(40, 30, 20)
    cavity_height = 18 if open_top else 16
    cavity = Part.makeBox(36, 26, cavity_height, App.Vector(2, 2, 2))
    return outer.cut(cavity)


class GeometryTests(unittest.TestCase):
    def test_open_sketch_ruled_surface_splits_into_two_valid_solids(self):
        source = Part.makeBox(40, 30, 20, App.Vector(-20, -15, -10))
        path = Part.makePolygon(
            [
                App.Vector(-25, -2, 0),
                App.Vector(-4, -2, 0),
                App.Vector(3, 5, 0),
                App.Vector(9, 5, 0),
                App.Vector(13, -4, 0),
                App.Vector(25, -4, 0),
            ]
        )
        result = split_with_sketch(source, path, App.Vector(0, 0, 1))
        self.assertTrue(result.negative.isValid())
        self.assertTrue(result.positive.isValid())
        self.assertEqual(len(result.negative.Solids), 1)
        self.assertEqual(len(result.positive.Solids), 1)
        self.assertAlmostEqual(
            result.negative.Volume + result.positive.Volume,
            source.Volume,
            places=5,
        )
        self.assertEqual(len(result.surface.Faces), 5)

    def test_sketch_split_rejects_closed_and_disconnected_paths(self):
        source = Part.makeBox(10, 10, 10)
        closed = Part.makePolygon(
            [
                App.Vector(-1, -1, 0),
                App.Vector(11, -1, 0),
                App.Vector(11, 11, 0),
                App.Vector(-1, 11, 0),
                App.Vector(-1, -1, 0),
            ]
        )
        with self.assertRaisesRegex(ValueError, "must be open"):
            split_with_sketch(source, closed, App.Vector(0, 0, 1))

        disconnected = Part.makeCompound(
            [
                Part.makeLine(App.Vector(-1, 4, 0), App.Vector(4, 4, 0)),
                Part.makeLine(App.Vector(6, 4, 0), App.Vector(11, 4, 0)),
            ]
        )
        with self.assertRaisesRegex(ValueError, "one connected"):
            split_with_sketch(source, disconnected, App.Vector(0, 0, 1))

    def test_failed_optional_refinement_keeps_original_shape(self):
        class RefinementFailure:
            def isNull(self):
                return False

            def removeSplitter(self):
                raise RuntimeError("FuseEdges : Fusion failed")

        original = RefinementFailure()
        self.assertIs(_safe_refine(original), original)

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

    def test_section_with_small_circular_through_holes(self):
        source = hollow_box(open_top=False)
        holes = [
            Part.makeCylinder(0.55, 20, App.Vector(1, 8, 0)),
            Part.makeCylinder(0.55, 20, App.Vector(1, 22, 0)),
            Part.makeCylinder(0.55, 20, App.Vector(39, 8, 0)),
            Part.makeCylinder(0.55, 20, App.Vector(39, 22, 0)),
        ]
        for hole in holes:
            source = source.cut(hole)
        origin, normal = plane_from_axes("XY", 10)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.2,
            vertical_clearance=0.1,
        )
        self.assertEqual(len(result.internal_wires), 5)
        self.assert_valid_pair(source, result)

    def test_outer_mode_ignores_nested_holes(self):
        source = hollow_box(open_top=False)
        holes = []
        for x, y in ((1, 8), (1, 22), (39, 8), (39, 22)):
            hole = Part.makeCylinder(0.55, 20, App.Vector(x, y, 0))
            holes.append(hole)
            source = source.cut(hole)
        origin, normal = plane_from_axes("XY", 10)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.2,
            vertical_clearance=0.1,
            contour_mode="outer",
        )
        self.assertEqual(len(result.internal_wires), 1)
        for hole in holes:
            self.assertLess(result.lip.common(hole).Volume, 1e-6)
        self.assert_valid_pair(source, result)

    def test_side_clearance_does_not_shift_or_shrink_lip(self):
        source = hollow_box(open_top=False)
        origin, normal = plane_from_axes("XY", 10)
        no_clearance = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.0,
            vertical_clearance=0.1,
            contour_mode="outer",
        )
        with_clearance = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.4,
            vertical_clearance=0.1,
            contour_mode="outer",
        )
        self.assertAlmostEqual(
            no_clearance.lip.Volume, with_clearance.lip.Volume, places=6
        )
        self.assertGreater(with_clearance.groove.Volume, no_clearance.groove.Volume)

    def test_outer_mode_supports_a_solid_block(self):
        source = Part.makeBox(10, 10, 10)
        origin, normal = plane_from_axes("XY", 5)
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.2,
            vertical_clearance=0.1,
            contour_mode="outer",
        )
        self.assertEqual(len(result.internal_wires), 1)
        self.assert_valid_pair(source, result)

    def test_explicit_contour_selection_uses_preview_indices(self):
        source = hollow_box(open_top=False)
        source = source.cut(
            Part.makeCylinder(0.55, 20, App.Vector(1, 8, 0))
        )
        origin, normal = plane_from_axes("XY", 10)
        _section, _plane, contours = analyze_section_contours(
            source, origin, normal
        )
        self.assertEqual(contours[0].kind, "outer")
        self.assertTrue(any(contour.kind == "internal" for contour in contours))
        result = make_enclosure(
            source,
            origin,
            normal,
            lip_width=0.7,
            lip_height=1.0,
            clearance=0.2,
            vertical_clearance=0.1,
            contour_indices=[0],
        )
        self.assertEqual(len(result.internal_wires), 1)
        self.assert_valid_pair(source, result)

    def test_polygon_fallback_when_occ_offset_rejects_contour(self):
        source = hollow_box(open_top=False)
        origin, normal = plane_from_axes("XY", 10)
        with mock.patch(
            "split2enclosure.geometry._offset_fill",
            side_effect=ValueError("simulated OCC offset failure"),
        ):
            result = make_enclosure(
                source,
                origin,
                normal,
                lip_width=0.7,
                lip_height=1.0,
                clearance=0.2,
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
