"""Split2Enclosure FreeCAD add-on."""

__version__ = "0.5.2"

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
from .settings import (
    find_source_settings,
    load_source_defaults,
    save_source_defaults,
    settings_owner,
)

__all__ = [
    "__version__",
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
    "find_source_settings",
    "load_source_defaults",
    "save_source_defaults",
    "settings_owner",
]
