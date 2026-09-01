"""Split2Enclosure FreeCAD add-on."""

from .geometry import (
    ContourInfo,
    EnclosureResult,
    analyze_section_contours,
    make_enclosure,
    plane_from_axes,
)

__all__ = [
    "ContourInfo",
    "EnclosureResult",
    "analyze_section_contours",
    "make_enclosure",
    "plane_from_axes",
]
