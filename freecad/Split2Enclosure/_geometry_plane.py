"""Planar splitting, contour offsets, and planar joint volumes."""

import math

import FreeCAD as App
import Part
from BOPTools import SplitAPI

from ._geometry_types import ContourInfo, DEFAULT_TOLERANCE
from ._geometry_common import (
    _combine,
    _combine_faces,
    _make_plane_face,
    _plane_basis_for_wire,
    _polygon_face_from_shapely,
    _safe_refine,
    _unit,
)
from ._geometry_joint import _drafted_extrusion, _drafted_groove, _extrude_faces


def _split_sides(shape, plane_face, origin, normal, tolerance):
    sliced = SplitAPI.slice(shape, [plane_face], "Split", tolerance)
    negative = []
    positive = []
    for solid in sliced.Solids:
        signed_distance = (solid.CenterOfMass - origin).dot(normal)
        if signed_distance < -tolerance:
            negative.append(solid)
        elif signed_distance > tolerance:
            positive.append(solid)
        else:
            vertex_distances = [
                (vertex.Point - origin).dot(normal) for vertex in solid.Vertexes
            ]
            if sum(vertex_distances) < 0:
                negative.append(solid)
            else:
                positive.append(solid)
    if not negative or not positive:
        raise ValueError("The plane does not split the selected solid into both sides.")
    return _combine(negative), _combine(positive)


def _unique_closed_wires(section):
    wires = []
    for face in section.Faces:
        for wire in face.Wires:
            if not wire.isClosed():
                continue
            if any(wire.isSame(existing) for existing in wires):
                continue
            wires.append(wire.copy())
    return wires


def _classified_wires(section, tolerance):
    """Return ``(outer, internal)`` closed contours from a planar section."""

    wire_faces = []
    for wire in _unique_closed_wires(section):
        try:
            face = Part.Face(wire)
        except Part.OCCError:
            continue
        if face.Area > tolerance * tolerance:
            wire_faces.append((wire, face))

    outer = []
    internal = []
    for wire, face in wire_faces:
        is_nested = False
        for other_wire, other_face in wire_faces:
            if wire.isSame(other_wire) or other_face.Area <= face.Area + tolerance:
                continue
            overlap = face.common(other_face)
            allowed_error = max(face.Area * 1e-7, tolerance * tolerance * 10)
            if abs(overlap.Area - face.Area) <= allowed_error:
                is_nested = True
                break
        if is_nested:
            internal.append(wire)
        else:
            outer.append(wire)
    return outer, internal


def _joint_wires(section, tolerance, contour_mode):
    outer, internal = _classified_wires(section, tolerance)
    if contour_mode == "outer":
        return outer
    if contour_mode == "internal":
        return internal
    raise ValueError("contour_mode must be 'outer' or 'internal'.")


def analyze_section_contours(
    shape,
    plane_origin,
    plane_normal,
    tolerance=DEFAULT_TOLERANCE,
):
    """Return the section, covering plane, and all selectable closed contours.

    Outermost contours are listed first, followed by nested/internal contours;
    each group is ordered by descending enclosed area for stable GUI indices.
    """

    if shape is None or shape.isNull() or not shape.Solids:
        raise ValueError("Select a non-empty solid BRep shape.")
    origin = App.Vector(plane_origin)
    normal = _unit(plane_normal)
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    plane_face = _make_plane_face(shape, origin, normal)
    section = shape.common(plane_face)
    if not section.Faces or section.Area <= tolerance * tolerance:
        raise ValueError("The split plane does not cross a solid wall section.")

    outer, internal = _classified_wires(section, tolerance)

    def by_area_descending(wires):
        return sorted(wires, key=lambda wire: Part.Face(wire).Area, reverse=True)

    classified = [
        ("outer", wire) for wire in by_area_descending(outer)
    ] + [
        ("internal", wire) for wire in by_area_descending(internal)
    ]
    contours = []
    for index, (kind, wire) in enumerate(classified):
        contours.append(
            ContourInfo(
                index=index,
                kind=kind,
                wire=wire,
                area=Part.Face(wire).Area,
                length=wire.Length,
            )
        )
    return section, plane_face, contours


def _offset_fill(wire, distance):
    # OpenCASCADE's 2D offset builder can return a null shape for a closed wire
    # containing one full-circle edge. Construct that elementary offset
    # analytically so round bores in an enclosure section remain exact circles.
    if len(wire.Edges) == 1 and isinstance(wire.Edges[0].Curve, Part.Circle):
        circle = wire.Edges[0].Curve
        radius = circle.Radius + distance
        base_edge = Part.makeCircle(circle.Radius, circle.Center, circle.Axis)
        base_face = Part.Face(Part.Wire([base_edge]))
        if radius <= DEFAULT_TOLERANCE:
            return base_face
        edge = Part.makeCircle(radius, circle.Center, circle.Axis)
        offset_face = Part.Face(Part.Wire([edge]))
        return (
            offset_face.cut(base_face)
            if distance > 0
            else base_face.cut(offset_face)
        )
    try:
        return wire.makeOffset2D(distance, join=2, fill=True)
    except (Part.OCCError, ValueError, RuntimeError):
        # Arc joins are more tolerant of tight concave corners.
        return wire.makeOffset2D(distance, join=0, fill=True)


def _zone_around_wire_fallback(wire, distance, tolerance):
    """Construct an offset band with GEOS when OpenCASCADE cannot offset it.

    Shapely is bundled with the targeted FreeCAD 1.1 distribution. Curves are
    discretized only on this exceptional path; the regular OCC path and the
    analytic full-circle path retain exact geometry.
    """

    from shapely.geometry import LineString

    deflection = max(min(distance * 0.025, 0.05), 0.005)
    points = wire.discretize(Deflection=deflection)
    if len(points) < 3:
        raise ValueError("The contour has too few points for fallback offsetting.")
    origin, axis_u, axis_v = _plane_basis_for_wire(wire)
    coordinates = []
    for point in points:
        relative = point - origin
        coordinate = (relative.dot(axis_u), relative.dot(axis_v))
        if not coordinates or (
            abs(coordinate[0] - coordinates[-1][0]) > tolerance
            or abs(coordinate[1] - coordinates[-1][1]) > tolerance
        ):
            coordinates.append(coordinate)
    if coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])

    buffered = LineString(coordinates).buffer(
        distance,
        quad_segs=12,
        cap_style=1,
        join_style=2,
        mitre_limit=5.0,
    )
    if buffered.is_empty:
        raise ValueError("Fallback offsetting produced an empty contour band.")

    polygons = (
        [buffered]
        if buffered.geom_type == "Polygon"
        else list(buffered.geoms)
    )
    faces = []
    for polygon in polygons:
        if polygon.geom_type != "Polygon" or polygon.area <= tolerance * tolerance:
            continue
        face = _polygon_face_from_shapely(
            polygon, origin, axis_u, axis_v, tolerance
        )
        if not face.isNull() and face.Faces:
            faces.extend(face.Faces)
    if not faces:
        raise ValueError("Fallback offsetting could not make a planar face.")
    return _combine_faces(faces)


def _zone_around_wire(wire, distance, tolerance):
    """Return a planar face within ``distance`` of both sides of a wire."""

    if distance <= tolerance:
        return Part.Shape()
    offset_shapes = []
    offset_errors = []
    for signed_distance in (distance, -distance):
        try:
            candidate = _offset_fill(wire, signed_distance)
            if not candidate.isNull() and candidate.Faces:
                offset_shapes.append(candidate)
        except (Part.OCCError, ValueError, RuntimeError) as exc:
            offset_errors.append(exc)

    # With fill=True OCC returns the strip swept from the source wire to the
    # offset wire. The two signs produce opposite sides (which sign is the
    # material side depends on wire orientation), so keep both as a compound.
    if len(offset_shapes) >= 2:
        return Part.makeCompound(offset_shapes)

    try:
        return _zone_around_wire_fallback(wire, distance, tolerance)
    except Exception as fallback_error:
        cause = offset_errors[0] if offset_errors else fallback_error
        raise ValueError(
            "Could not offset a closed contour with either OpenCASCADE or "
            "the polygon fallback: {}".format(fallback_error)
        ) from cause


def _joint_footprints(section, wires, lip_width, clearance, tolerance):
    lip_parts = []
    groove_parts = []
    groove_far = lip_width + clearance

    for wire in wires:
        # Keep the lip anchored to the selected wall perimeter. Clearance is
        # added only at its material-side mating face by widening the groove;
        # it must not move the lip away from the perimeter itself. Do not clip
        # these construction footprints to the section yet: a locally thin
        # wall changes their topology and can make one bad segment collapse the
        # drafted tool for the complete contour. The final receiver Boolean
        # clips the lip and groove to actual enclosure material instead.
        lip_zone = _zone_around_wire(wire, lip_width, tolerance)
        lip_part = lip_zone
        groove_part = _zone_around_wire(wire, groove_far, tolerance)
        if lip_part.Faces:
            lip_parts.extend(lip_part.Faces)
        if groove_part.Faces:
            groove_parts.extend(groove_part.Faces)

    lip_footprint = _combine_faces(lip_parts)
    groove_footprint = _combine_faces(groove_parts)
    if lip_footprint.isNull() or not lip_footprint.Faces:
        raise ValueError(
            "No lip footprint fits in the wall section. Reduce lip width or clearance."
        )
    if groove_footprint.isNull() or not groove_footprint.Faces:
        raise ValueError("No groove footprint could be constructed.")
    return lip_footprint, groove_footprint


def _thin_wall_contact_length(section, wires, lip_width, tolerance):
    """Return boundary length where the requested lip reaches another contour."""

    selected_edges = [edge for wire in wires for edge in wire.Edges]
    opposite_edges = []
    for edge in section.Edges:
        belongs_to_selection = False
        for selected in selected_edges:
            try:
                overlap = edge.common(selected)
                belongs_to_selection = (
                    not overlap.isNull()
                    and overlap.Length >= edge.Length - tolerance
                )
            except (Part.OCCError, RuntimeError):
                belongs_to_selection = edge.isSame(selected)
            if belongs_to_selection:
                break
        if not belongs_to_selection:
            opposite_edges.append(edge)
    if not opposite_edges:
        return 0.0
    section_material = _combine_faces(section.Faces)
    contact_length = 0.0
    opposite_boundary = Part.makeCompound(opposite_edges)
    for wire in wires:
        footprint = _zone_around_wire(wire, lip_width, tolerance)
        material_footprint = footprint.common(section_material)
        contact = material_footprint.common(opposite_boundary)
        if not contact.isNull():
            contact_length += contact.Length
    return contact_length


def _plane_joint_volumes(
    section,
    wires,
    negative_half,
    positive_half,
    normal,
    lip_width,
    lip_height,
    clearance,
    vertical_clearance,
    draft_angle,
    lip_on,
    tolerance,
    clip_lip=True,
):
    lip_footprint, groove_footprint = _joint_footprints(
        section, wires, lip_width, clearance, tolerance
    )
    direction = normal if lip_on == "negative" else -normal
    receiver = positive_half if lip_on == "negative" else negative_half
    draft_slope = math.tan(math.radians(draft_angle))
    top_lip_width = lip_width - draft_slope * lip_height
    if top_lip_width <= tolerance or top_lip_width + clearance <= tolerance:
        raise ValueError(
            "Draft angle is too large for the requested lip width and height."
        )
    top_lip_footprint, _unused = _joint_footprints(
        section, wires, top_lip_width, clearance, tolerance
    )
    _unused, top_groove_footprint = _joint_footprints(
        section, wires, top_lip_width, clearance, tolerance
    )
    raw_lip = _drafted_extrusion(
        lip_footprint,
        top_lip_footprint,
        direction * lip_height,
        draft_angle,
    )
    lip = _safe_refine(raw_lip.common(receiver)) if clip_lip else raw_lip
    if lip.isNull() or not lip.Solids:
        raise RuntimeError(
            "The receiving half contains no material inside the requested lip region."
        )

    groove = _drafted_groove(
        groove_footprint,
        top_groove_footprint,
        direction,
        lip_height,
        vertical_clearance,
        draft_angle,
        tolerance,
    )

    root_clearance = Part.Shape()
    if vertical_clearance > tolerance:
        section_material = _combine_faces(section.Faces)
        root_band = section_material.cut(lip_footprint)
        if not root_band.isNull() and root_band.Faces:
            root_clearance = _extrude_faces(
                root_band,
                -direction * (vertical_clearance + tolerance),
            )
            root_clearance.translate(direction * tolerance)
    return lip, groove, root_clearance
