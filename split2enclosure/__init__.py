"""Split2Enclosure FreeCAD add-on."""

from .geometry import (
    ContourInfo,
    EnclosureResult,
    SketchSplitResult,
    analyze_section_contours,
    analyze_sketch_contours,
    make_enclosure,
    make_enclosure_with_sketch,
    plane_from_axes,
    ruled_contour_positive_direction,
    split_with_sketch,
)

__all__ = [
    "ContourInfo",
    "EnclosureResult",
    "SketchSplitResult",
    "analyze_section_contours",
    "analyze_sketch_contours",
    "make_enclosure",
    "make_enclosure_with_sketch",
    "plane_from_axes",
    "ruled_contour_positive_direction",
    "split_with_sketch",
]
