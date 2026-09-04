"""Public API and compatibility facade for enclosure geometry.

Implementation is organized by responsibility in the private geometry modules;
imports remain available here so existing macros and tests keep working.
"""

import FreeCAD as App

from ._geometry_types import (
    ContourInfo,
    DEFAULT_TOLERANCE,
    EnclosureResult,
    SketchSplitResult,
    _JointParameters,
    _JointPlan,
)
from ._geometry_common import (
    _box_corners,
    _combine,
    _combine_faces,
    _cut_tool_solids,
    _discard_boolean_slivers,
    _fuse_shapes,
    _fuse_tool_solids,
    _make_plane_face,
    _plane_basis_for_wire,
    _polygon_face_from_shapely,
    _safe_refine,
    _unit,
    plane_from_axes,
)
from ._geometry_joint import (
    _add_joint_batches,
    _add_snap_features,
    _apply_joint_boolean,
    _apply_root_clearances,
    _apply_snap_boolean,
    _build_joint_result,
    _contour_groups,
    _drafted_extrusion,
    _drafted_groove,
    _enclosure_result,
    _extrude_faces,
    _inner_wires,
    _joint_parameters,
    _joint_section,
    _loft_joint_sections,
    _nearest_shape,
    _normalized_snap_assignments,
    _snap_for_contour,
    _wire_batches,
)
from ._geometry_sketch import (
    _base_path_samples,
    _classify_ruled_contours,
    _connected_closed_wires,
    _coplanar_face_groups,
    _edge_band_on_faces,
    _free_section_edges,
    _matching_surface_face,
    _open_sketch_wire,
    _planar_face_basis,
    _ruled_joint_volumes,
    _same_geometric_edge,
    _section_sample,
    _surface_normal_at_face,
    _unfold_point,
    _validate_simple_sketch_path,
    _wire_samples,
    analyze_sketch_contours,
    ruled_contour_positive_direction,
    split_with_sketch,
)
from ._geometry_plane import (
    _classified_wires,
    _joint_footprints,
    _joint_wires,
    _offset_fill,
    _plane_joint_volumes,
    _split_sides,
    _thin_wall_contact_length,
    _unique_closed_wires,
    _zone_around_wire,
    _zone_around_wire_fallback,
    analyze_section_contours,
)


def make_enclosure_with_sketch(
    shape,
    sketch_shape,
    sketch_normal,
    lip_width=1.0,
    lip_height=2.0,
    clearance=0.2,
    vertical_clearance=0.2,
    draft_angle=0.0,
    lip_on="negative",
    contour_mode="outer",
    contour_indices=None,
    contour_sides=None,
    contour_snaps=None,
    snap_radius=0.2,
    snap_clearance=0.05,
    snap_position=0.7,
    tolerance=DEFAULT_TOLERANCE,
):
    """Split on an extruded open sketch and add a face-relative lip/groove."""

    parameters = _joint_parameters(
        lip_width,
        lip_height,
        clearance,
        vertical_clearance,
        draft_angle,
        lip_on,
        contour_mode,
        snap_radius,
        snap_clearance,
        snap_position,
        tolerance,
    )
    split, contours = analyze_sketch_contours(
        shape, sketch_shape, sketch_normal, parameters.tolerance
    )
    groups, assignments = _contour_groups(
        contours,
        contour_sides,
        contour_indices,
        parameters.lip_on,
        parameters.contour_mode,
    )
    snap_assignments = _normalized_snap_assignments(contours, contour_snaps)
    if (
        any(snap_assignments.values())
        and parameters.lip_width - parameters.snap_radius <= parameters.tolerance
    ):
        raise ValueError("Snap seam half-size must be smaller than the lip width.")
    joint_positive_direction = ruled_contour_positive_direction(
        split, contours[0].wire, parameters.tolerance
    )

    def volume_builder(wires, width, side):
        return _ruled_joint_volumes(
            split,
            wires,
            width,
            parameters.lip_height,
            parameters.clearance,
            parameters.vertical_clearance,
            parameters.draft_angle,
            side,
            joint_positive_direction,
            parameters.tolerance,
        )

    plan = _JointPlan(
        negative=split.negative,
        positive=split.positive,
        section=split.section,
        plane=split.surface,
        contours=contours,
        groups=groups,
        assignments=assignments,
        snap_assignments=snap_assignments,
        positive_direction=joint_positive_direction,
    )
    return _build_joint_result(
        plan,
        parameters,
        volume_builder,
        "OpenCASCADE produced an invalid sketch-seam joint. "
        "Try smaller joint dimensions.",
        discard_slivers=True,
    )


def make_enclosure(
    shape,
    plane_origin,
    plane_normal,
    lip_width=1.0,
    lip_height=2.0,
    clearance=0.2,
    vertical_clearance=0.2,
    draft_angle=0.0,
    lip_on="negative",
    contour_mode="internal",
    contour_indices=None,
    contour_sides=None,
    contour_snaps=None,
    snap_radius=0.2,
    snap_clearance=0.05,
    snap_position=0.7,
    tolerance=DEFAULT_TOLERANCE,
):
    """Split ``shape`` and add a matched lip/groove joint.

    ``plane_normal`` defines the positive half.  If ``lip_on`` is
    ``"negative"``, the lip belongs to the negative half and projects in the
    positive-normal direction; ``"positive"`` reverses that arrangement.

    When ``contour_indices`` is supplied, only those indices returned by
    :func:`analyze_section_contours` are used. Otherwise, with
    ``contour_mode="internal"``, the lip follows every nested closed
    contour in the section. With ``contour_mode="outer"``, it follows each
    disconnected section region's outermost perimeter and ignores holes. The
    groove is wider on the material side by ``clearance``. The same
    ``vertical_clearance`` is left both beyond the lip tip and between the
    opposing shoulder faces. The lip volume is intersected with ("stolen"
    from) the receiving half so existing holes, slopes, and local details are
    retained instead of being covered by a uniform prism.
    """

    if shape is None or shape.isNull() or not shape.Solids:
        raise ValueError("Select a non-empty solid BRep shape.")
    if not shape.isValid():
        raise ValueError("The selected shape is not a valid BRep.")
    parameters = _joint_parameters(
        lip_width,
        lip_height,
        clearance,
        vertical_clearance,
        draft_angle,
        lip_on,
        contour_mode,
        snap_radius,
        snap_clearance,
        snap_position,
        tolerance,
    )

    origin = App.Vector(plane_origin)
    normal = _unit(plane_normal)
    section, plane_face, contours = analyze_section_contours(
        shape, origin, normal, parameters.tolerance
    )
    groups, assignments = _contour_groups(
        contours,
        contour_sides,
        contour_indices,
        parameters.lip_on,
        parameters.contour_mode,
    )
    snap_assignments = _normalized_snap_assignments(contours, contour_snaps)
    if (
        any(snap_assignments.values())
        and parameters.lip_width - parameters.snap_radius <= parameters.tolerance
    ):
        raise ValueError("Snap seam half-size must be smaller than the lip width.")

    selected_wires = groups["negative"] + groups["positive"]
    if _thin_wall_contact_length(
        section,
        selected_wires,
        parameters.lip_width,
        parameters.tolerance,
    ) > parameters.tolerance:
        App.Console.PrintWarning(
            "Split2Enclosure: thin wall detected along the selected perimeter. "
            "Clearance will still be cut, and snap geometry will be retained "
            "where supporting material exists.\n"
        )

    negative, positive = _split_sides(
        shape, plane_face, origin, normal, parameters.tolerance
    )
    base_negative = negative
    base_positive = positive

    def volume_builder(wires, width, side):
        return _plane_joint_volumes(
            section,
            wires,
            base_negative,
            base_positive,
            normal,
            width,
            parameters.lip_height,
            parameters.clearance,
            parameters.vertical_clearance,
            parameters.draft_angle,
            side,
            parameters.tolerance,
        )

    def snap_volume_builder(wires, width, side):
        return _plane_joint_volumes(
            section,
            wires,
            base_negative,
            base_positive,
            normal,
            width,
            parameters.lip_height,
            parameters.clearance,
            parameters.vertical_clearance,
            parameters.draft_angle,
            side,
            parameters.tolerance,
            clip_lip=False,
        )

    plan = _JointPlan(
        negative=negative,
        positive=positive,
        section=section,
        plane=plane_face,
        contours=contours,
        groups=groups,
        assignments=assignments,
        snap_assignments=snap_assignments,
        positive_direction=normal,
    )
    return _build_joint_result(
        plan,
        parameters,
        volume_builder,
        "OpenCASCADE produced an invalid result. Try a wider wall, smaller lip, "
        "or a slightly different split offset.",
        snap_volume_builder=snap_volume_builder,
        snap_donors={"negative": base_positive, "positive": base_negative},
    )
