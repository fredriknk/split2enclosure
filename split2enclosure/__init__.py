"""Split2Enclosure FreeCAD add-on."""

from .geometry import (
    ContourInfo,
    EnclosureResult,
    SketchSplitResult,
    analyze_section_contours,
    make_enclosure,
    plane_from_axes,
    split_with_sketch,
)

__all__ = [
    "ContourInfo",
    "EnclosureResult",
    "SketchSplitResult",
    "analyze_section_contours",
    "make_enclosure",
    "plane_from_axes",
    "split_with_sketch",
]
