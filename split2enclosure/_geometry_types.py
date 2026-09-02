"""Data structures shared by the enclosure geometry modules."""

from dataclasses import dataclass

import FreeCAD as App
import Part


DEFAULT_TOLERANCE = 1e-6


@dataclass
class EnclosureResult:
    """Shapes and diagnostics produced by :func:`make_enclosure`."""

    negative: Part.Shape
    positive: Part.Shape
    lip: Part.Shape
    groove: Part.Shape
    section: Part.Shape
    plane: Part.Shape
    internal_wires: list
    contour_sides: dict = None
    root_clearance: Part.Shape = None
    snap_features: Part.Shape = None
    contour_snaps: dict = None


@dataclass
class ContourInfo:
    """A selectable closed contour in a split-plane section."""

    index: int
    kind: str
    wire: Part.Shape
    area: float
    length: float


@dataclass
class SketchSplitResult:
    """Two solids split by a ruled surface extruded from an open sketch."""

    negative: Part.Shape
    positive: Part.Shape
    section: Part.Shape
    surface: Part.Shape
    sketch_wire: Part.Shape
    extrusion_normal: App.Vector


@dataclass(frozen=True)
class _JointParameters:
    """Normalized parameters shared by plane and sketch joint builders."""

    lip_width: float
    lip_height: float
    clearance: float
    vertical_clearance: float
    draft_angle: float
    lip_on: str
    contour_mode: str
    snap_radius: float
    snap_clearance: float
    snap_position: float
    tolerance: float


@dataclass
class _JointPlan:
    """Analyzed split state consumed by the shared joint-building pipeline."""

    negative: Part.Shape
    positive: Part.Shape
    section: Part.Shape
    plane: Part.Shape
    contours: list
    groups: dict
    assignments: dict
    snap_assignments: dict
    positive_direction: App.Vector
