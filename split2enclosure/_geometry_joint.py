"""Shared lip, groove, draft, clearance, and snap construction."""

import math

import FreeCAD as App
import Part

from ._geometry_types import DEFAULT_TOLERANCE, EnclosureResult, _JointParameters
from ._geometry_common import (
    _combine,
    _cut_tool_solids,
    _discard_boolean_slivers,
    _fuse_shapes,
    _fuse_tool_solids,
    _safe_refine,
    _unit,
)


def _contour_groups(
    contours,
    contour_sides,
    contour_indices,
    lip_on,
    contour_mode,
):
    """Resolve backwards-compatible selection into per-side contour groups."""

    assignments = {}
    if contour_sides is not None:
        items = (
            contour_sides.items()
            if hasattr(contour_sides, "items")
            else enumerate(contour_sides)
        )
        for raw_index, raw_side in items:
            index = int(raw_index)
            side = str(raw_side).lower()
            if side not in ("negative", "positive", "none"):
                raise ValueError(
                    "Each contour side must be 'negative', 'positive', or 'none'."
                )
            if index < 0 or index >= len(contours):
                raise ValueError("A selected contour index is no longer available.")
            assignments[index] = side
    elif contour_indices is not None:
        for raw_index in contour_indices:
            index = int(raw_index)
            if index < 0 or index >= len(contours):
                raise ValueError("A selected contour index is no longer available.")
            assignments[index] = lip_on
    else:
        for contour in contours:
            if contour.kind == contour_mode:
                assignments[contour.index] = lip_on

    groups = {"negative": [], "positive": []}
    normalized = {}
    for contour in contours:
        side = assignments.get(contour.index, "none")
        normalized[contour.index] = side
        if side in groups:
            groups[side].append(contour.wire)
    if not groups["negative"] and not groups["positive"]:
        if contour_sides is None and contour_indices is None:
            if contour_mode == "internal":
                raise ValueError(
                    "No internal closed section contours were found. "
                    "Try the outermost-perimeter contour mode."
                )
            raise ValueError("No outer closed section perimeters were found.")
        raise ValueError("Assign at least one contour to the negative or positive half.")
    return groups, normalized


def _normalized_snap_assignments(contours, contour_snaps):
    if contour_snaps is None:
        return {contour.index: False for contour in contours}
    items = (
        contour_snaps.items()
        if hasattr(contour_snaps, "items")
        else enumerate(contour_snaps)
    )
    requested = {int(index): bool(enabled) for index, enabled in items}
    if any(index < 0 or index >= len(contours) for index in requested):
        raise ValueError("A snap contour index is no longer available.")
    return {
        contour.index: requested.get(contour.index, False)
        for contour in contours
    }


def _joint_section(section, shape, direction, position):
    """Return the actual joint cross-section at one axial position."""

    surface = section.copy()
    surface.translate(_unit(direction) * position)
    return shape.common(surface)


def _loft_joint_sections(bottom, top, direction, distance, tolerance):
    """Loft matching section faces with straight, printable side walls."""

    if bottom.isNull() or top.isNull() or not bottom.Faces or not top.Faces:
        raise ValueError("A snap transition has an empty cross-section.")
    if len(bottom.Faces) != len(top.Faces):
        raise ValueError(
            "A snap transition changes topology. Reduce the snap size or move it."
        )

    available_top_faces = list(top.Faces)
    solids = []
    expected_offset = _unit(direction) * distance
    for bottom_face in bottom.Faces:
        expected_center = bottom_face.CenterOfMass + expected_offset
        top_face = _nearest_shape(expected_center, available_top_faces)
        available_top_faces.remove(top_face)
        try:
            solid = Part.makeLoft(
                [bottom_face.OuterWire, top_face.OuterWire],
                True,
                True,
            )
            bottom_holes = _inner_wires(bottom_face)
            top_holes = _inner_wires(top_face)
            if len(bottom_holes) != len(top_holes):
                raise ValueError("A snap transition changes its hole topology.")
            available_top_holes = list(top_holes)
            for bottom_hole in bottom_holes:
                expected_hole_center = bottom_hole.CenterOfMass + expected_offset
                top_hole = _nearest_shape(expected_hole_center, available_top_holes)
                available_top_holes.remove(top_hole)
                hole = Part.makeLoft([bottom_hole, top_hole], True, True)
                solid = solid.cut(hole)
        except (Part.OCCError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "Could not construct a printable snap transition: {}".format(exc)
            ) from exc
        if not solid.isNull() and solid.Solids:
            solids.extend(solid.Solids)
    if not solids:
        raise ValueError("Could not construct a printable snap transition.")
    result = _fuse_shapes(solids)
    if (
        result.isNull()
        or not result.Solids
        or result.Volume <= max(tolerance ** 3 * 1000, 1e-9)
        or not result.isValid()
    ):
        raise ValueError("The printable snap transition is not a valid solid.")
    return result


def _snap_for_contour(
    section,
    stem_lip,
    stem_groove,
    wide_lip,
    wide_groove,
    extrusion_direction,
    lip_height,
    snap_radius,
    snap_clearance,
    snap_position,
    tolerance,
):
    direction = _unit(extrusion_direction)
    available_height = lip_height * min(snap_position, 1.0 - snap_position)
    channel_extent = snap_radius + snap_clearance
    if channel_extent >= available_height - tolerance:
        raise ValueError(
            "Snap size plus channel clearance is too large for its height on "
            "the lip. Reduce it or move the snap height fraction toward 0.5."
        )

    center = lip_height * snap_position
    start = center - snap_radius
    end = center + snap_radius
    stem_start = _joint_section(section, stem_lip, direction, start)
    wide_center = _joint_section(section, wide_lip, direction, center)
    stem_end = _joint_section(section, stem_lip, direction, end)
    lower_rib = _loft_joint_sections(
        stem_start, wide_center, direction, snap_radius, tolerance
    )
    upper_rib = _loft_joint_sections(
        wide_center, stem_end, direction, snap_radius, tolerance
    )
    rib = _fuse_shapes([lower_rib, upper_rib])
    if rib.isNull() or not rib.Solids:
        raise ValueError("Could not construct a printable snap seam on the lip.")
    added = rib.cut(stem_lip)
    if added.isNull() or added.Volume <= max(tolerance ** 3 * 1000, 1e-9):
        raise ValueError(
            "No continuous snap seam fits this lip perimeter. Reduce snap size."
        )

    channel_start = center - channel_extent
    channel_end = center + channel_extent
    narrow_channel_start = _joint_section(
        section, stem_groove, direction, channel_start
    )
    wide_channel_center = _joint_section(
        section, wide_groove, direction, center
    )
    narrow_channel_end = _joint_section(
        section, stem_groove, direction, channel_end
    )
    lower_channel = _loft_joint_sections(
        narrow_channel_start,
        wide_channel_center,
        direction,
        channel_extent,
        tolerance,
    )
    upper_channel = _loft_joint_sections(
        wide_channel_center,
        narrow_channel_end,
        direction,
        channel_extent,
        tolerance,
    )
    channel = _fuse_shapes([lower_channel, upper_channel])
    if channel.isNull() or not channel.Solids:
        raise ValueError("Could not construct the matching snap channel.")
    return rib, channel, added


def _joint_parameters(
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
):
    """Normalize and validate parameters used by both joint workflows."""

    parameters = _JointParameters(
        lip_width=float(lip_width),
        lip_height=float(lip_height),
        clearance=float(clearance),
        vertical_clearance=float(vertical_clearance),
        draft_angle=float(draft_angle),
        lip_on=lip_on,
        contour_mode=contour_mode,
        snap_radius=float(snap_radius),
        snap_clearance=float(snap_clearance),
        snap_position=float(snap_position),
        tolerance=max(float(tolerance), DEFAULT_TOLERANCE),
    )
    if parameters.lip_width <= 0 or parameters.lip_height <= 0:
        raise ValueError("Lip width and height must be greater than zero.")
    if parameters.clearance < 0 or parameters.vertical_clearance < 0:
        raise ValueError("Clearances must not be negative.")
    if parameters.draft_angle < 0 or parameters.draft_angle >= 45:
        raise ValueError("Draft angle must be at least 0 and less than 45 degrees.")
    if parameters.snap_radius <= 0 or parameters.snap_clearance < 0:
        raise ValueError(
            "Snap radius must be positive and snap clearance non-negative."
        )
    if not 0.1 <= parameters.snap_position <= 0.9:
        raise ValueError("Snap position must be between 0.1 and 0.9 of lip height.")
    if parameters.lip_on not in ("negative", "positive"):
        raise ValueError("lip_on must be 'negative' or 'positive'.")
    if parameters.contour_mode not in ("outer", "internal"):
        raise ValueError("contour_mode must be 'outer' or 'internal'.")
    return parameters


def _wire_batches(contours, assignments, snap_assignments):
    """Yield contours grouped by owner and whether they need a snap seam."""

    for side in ("negative", "positive"):
        for snapped in (False, True):
            wires = [
                contour.wire
                for contour in contours
                if assignments[contour.index] == side
                and snap_assignments[contour.index] == snapped
            ]
            if wires:
                yield side, snapped, wires


def _apply_joint_boolean(negative, positive, side, lip, groove):
    """Transfer one lip to its owner and cut its matching receiver groove."""

    if side == "negative":
        negative = _safe_refine(negative.fuse(lip))
        positive = _cut_tool_solids(positive, groove)
        positive = _safe_refine(positive.cut(lip))
    else:
        positive = _safe_refine(positive.fuse(lip))
        negative = _cut_tool_solids(negative, groove)
        negative = _safe_refine(negative.cut(lip))
    return negative, positive


def _add_joint_batches(
    negative,
    positive,
    contours,
    assignments,
    snap_assignments,
    parameters,
    volume_builder,
):
    """Build and apply ordinary/narrowed joint batches for both owners."""

    lips = []
    grooves = []
    root_tools = {"negative": [], "positive": []}
    for side, snapped, wires in _wire_batches(
        contours, assignments, snap_assignments
    ):
        width = (
            parameters.lip_width - parameters.snap_radius
            if snapped
            else parameters.lip_width
        )
        lip, groove, root_clearance = volume_builder(wires, width, side)
        lips.append(lip)
        grooves.append(groove)
        if root_clearance is not None and not root_clearance.isNull():
            root_tools[side].append(root_clearance)
        negative, positive = _apply_joint_boolean(
            negative, positive, side, lip, groove
        )
    return negative, positive, lips, grooves, root_tools


def _apply_root_clearances(negative, positive, grouped_tools):
    """Intersect each owner's root tools, then relieve that owner's shoulder."""

    applied = []
    for side, tools in grouped_tools.items():
        if not tools:
            continue
        root_clearance = tools[0]
        for tool in tools[1:]:
            root_clearance = root_clearance.common(tool)
        if root_clearance.isNull() or not root_clearance.Solids:
            continue
        applied.append(root_clearance)
        if side == "negative":
            negative = _cut_tool_solids(negative, root_clearance)
        else:
            positive = _cut_tool_solids(positive, root_clearance)
    return negative, positive, applied


def _apply_snap_boolean(negative, positive, side, bump, pocket):
    """Add a snap rib to its owner and cut the matching receiver channel."""

    if side == "negative":
        negative = _fuse_tool_solids(negative, bump)
        positive = _safe_refine(positive.cut(pocket).cut(bump))
    else:
        positive = _fuse_tool_solids(positive, bump)
        negative = _safe_refine(negative.cut(pocket).cut(bump))
    return negative, positive


def _add_snap_features(
    negative,
    positive,
    contours,
    assignments,
    snap_assignments,
    section,
    positive_direction,
    parameters,
    volume_builder,
):
    """Construct and apply continuous snap ribs for enabled contours."""

    features = []
    for contour in contours:
        side = assignments[contour.index]
        if side == "none" or not snap_assignments[contour.index]:
            continue
        contour_lip, contour_groove, _root = volume_builder(
            [contour.wire], parameters.lip_width - parameters.snap_radius, side
        )
        wide_lip, _groove, _root = volume_builder(
            [contour.wire], parameters.lip_width, side
        )
        _lip, wide_groove, _root = volume_builder(
            [contour.wire],
            parameters.lip_width + parameters.snap_clearance,
            side,
        )
        direction = (
            positive_direction if side == "negative" else -positive_direction
        )
        bump, pocket, added = _snap_for_contour(
            section,
            contour_lip,
            contour_groove,
            wide_lip,
            wide_groove,
            direction,
            parameters.lip_height,
            parameters.snap_radius,
            parameters.snap_clearance,
            parameters.snap_position,
            parameters.tolerance,
        )
        features.append(added)
        negative, positive = _apply_snap_boolean(
            negative, positive, side, bump, pocket
        )
    return negative, positive, features


def _enclosure_result(
    plan,
    negative,
    positive,
    lips,
    grooves,
    root_clearances,
    snap_features,
):
    """Package final shapes and selection diagnostics for the public API."""

    return EnclosureResult(
        negative=negative,
        positive=positive,
        lip=_combine(lips),
        groove=_combine(grooves),
        section=plan.section,
        plane=plan.plane,
        internal_wires=plan.groups["negative"] + plan.groups["positive"],
        contour_sides=plan.assignments,
        root_clearance=_combine(root_clearances),
        snap_features=_combine(snap_features),
        contour_snaps=plan.snap_assignments,
    )


def _build_joint_result(
    plan,
    parameters,
    volume_builder,
    invalid_message,
    discard_slivers=False,
):
    """Run the common Boolean pipeline for an already analyzed split."""

    negative, positive, lips, grooves, root_tools = _add_joint_batches(
        plan.negative,
        plan.positive,
        plan.contours,
        plan.assignments,
        plan.snap_assignments,
        parameters,
        volume_builder,
    )
    negative, positive, root_clearances = _apply_root_clearances(
        negative, positive, root_tools
    )
    negative, positive, snap_features = _add_snap_features(
        negative,
        positive,
        plan.contours,
        plan.assignments,
        plan.snap_assignments,
        plan.section,
        plan.positive_direction,
        parameters,
        volume_builder,
    )
    if discard_slivers:
        negative = _discard_boolean_slivers(
            negative, parameters.tolerance, keep_largest=True
        )
        positive = _discard_boolean_slivers(
            positive, parameters.tolerance, keep_largest=True
        )
    if not negative.isValid() or not positive.isValid():
        raise RuntimeError(invalid_message)
    return _enclosure_result(
        plan,
        negative,
        positive,
        lips,
        grooves,
        root_clearances,
        snap_features,
    )


def _extrude_faces(shape, vector):
    solids = []
    for face in shape.Faces:
        extrusion = face.extrude(vector)
        if extrusion.Solids:
            solids.extend(extrusion.Solids)
        elif not extrusion.isNull():
            solids.append(extrusion)
    if not solids:
        return Part.Shape()
    result = solids[0]
    for solid in solids[1:]:
        result = result.fuse(solid)
    return _safe_refine(result)


def _inner_wires(face):
    return [wire for wire in face.Wires if not wire.isSame(face.OuterWire)]


def _nearest_shape(reference_point, shapes):
    return min(shapes, key=lambda shape: (shape.CenterOfMass - reference_point).Length)


def _drafted_extrusion(bottom, top, vector, draft_angle):
    """Make a true lofted solid, preserving matching footprint holes."""

    if draft_angle <= DEFAULT_TOLERANCE:
        return _extrude_faces(bottom, vector)
    translated_top = top.copy()
    translated_top.translate(vector)
    if len(bottom.Faces) != len(translated_top.Faces):
        raise ValueError("Draft changed the joint footprint topology.")

    available_top_faces = list(translated_top.Faces)
    solids = []
    for bottom_face in bottom.Faces:
        expected_center = bottom_face.CenterOfMass + vector
        top_face = _nearest_shape(expected_center, available_top_faces)
        available_top_faces.remove(top_face)
        try:
            solid = Part.makeLoft(
                [bottom_face.OuterWire, top_face.OuterWire],
                True,
                True,
            )
            bottom_holes = _inner_wires(bottom_face)
            top_holes = _inner_wires(top_face)
            if len(bottom_holes) != len(top_holes):
                raise ValueError("Draft changed the joint footprint hole topology.")
            available_top_holes = list(top_holes)
            for bottom_hole in bottom_holes:
                expected_hole_center = bottom_hole.CenterOfMass + vector
                top_hole = _nearest_shape(expected_hole_center, available_top_holes)
                available_top_holes.remove(top_hole)
                hole_solid = Part.makeLoft(
                    [bottom_hole, top_hole],
                    True,
                    True,
                )
                solid = solid.cut(hole_solid)
        except (Part.OCCError, RuntimeError, ValueError) as exc:
            # Some heavily trimmed BRep panels have incompatible edge splits
            # after offsetting even though their areas correspond. Preserve a
            # valid joint on that local panel and draft every compatible panel.
            App.Console.PrintWarning(
                "Split2Enclosure: draft skipped on one complex panel ({})\n".format(
                    exc
                )
            )
            solid = bottom_face.extrude(vector)
        if not solid.isNull() and solid.Solids:
            solids.extend(solid.Solids)
    if not solids:
        raise ValueError("Could not construct the drafted joint solid.")
    return _fuse_shapes(solids)


def _drafted_groove(
    bottom,
    lip_height_footprint,
    direction,
    lip_height,
    vertical_clearance,
    draft_angle,
    tolerance,
):
    """Draft beside the lip, then keep its tip clearance straight above it.

    Extending the taper through ``vertical_clearance`` narrows the groove above
    the lip. On an outer perimeter that can make the receiver touch the lip's
    drafted tip. The clearance portion must instead retain the cross-section
    reached at the actual lip height.
    """

    drafted = _drafted_extrusion(
        bottom,
        lip_height_footprint,
        direction * lip_height,
        draft_angle,
    )
    cap_footprint = lip_height_footprint.copy()
    cap_footprint.translate(direction * lip_height)
    cap = _extrude_faces(
        cap_footprint,
        direction * (vertical_clearance + tolerance * 2),
    )
    groove = _fuse_shapes([drafted, cap])
    groove.translate(-direction * tolerance)
    return groove
