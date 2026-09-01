"""Geometry engine for splitting a solid and adding a mating lip and groove.

The implementation deliberately depends only on FreeCAD's App and Part modules,
so it can be exercised with FreeCADCmd and reused by either a macro or a GUI
workbench command.
"""

from dataclasses import dataclass

import FreeCAD as App
import Part
from BOPTools import SplitAPI


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


def _unit(vector):
    result = App.Vector(vector)
    if result.Length <= DEFAULT_TOLERANCE:
        raise ValueError("The split-plane normal must not be zero.")
    result.normalize()
    return result


def plane_from_axes(name, offset=0.0):
    """Return ``(origin, normal)`` for a global principal plane.

    Positive offsets follow the returned positive normal.
    """

    key = str(name).upper().replace(" ", "")
    normals = {
        "XY": App.Vector(0, 0, 1),
        "XZ": App.Vector(0, 1, 0),
        "YZ": App.Vector(1, 0, 0),
    }
    if key not in normals:
        raise ValueError("Plane must be XY, XZ, or YZ.")
    normal = normals[key]
    return normal * float(offset), normal


def _box_corners(bound_box):
    for x in (bound_box.XMin, bound_box.XMax):
        for y in (bound_box.YMin, bound_box.YMax):
            for z in (bound_box.ZMin, bound_box.ZMax):
                yield App.Vector(x, y, z)


def _make_plane_face(shape, origin, normal):
    """Make a finite planar face guaranteed to cover ``shape``."""

    normal = _unit(normal)
    helper = App.Vector(0, 0, 1)
    if abs(normal.dot(helper)) > 0.9:
        helper = App.Vector(1, 0, 0)
    axis_u = normal.cross(helper)
    axis_u.normalize()
    axis_v = normal.cross(axis_u)
    axis_v.normalize()

    projected = []
    for corner in _box_corners(shape.BoundBox):
        relative = corner - origin
        projected.append((relative.dot(axis_u), relative.dot(axis_v)))
    half_u = max(abs(value[0]) for value in projected)
    half_v = max(abs(value[1]) for value in projected)
    margin = max(shape.BoundBox.DiagonalLength * 0.1, 1.0)
    half_u += margin
    half_v += margin

    points = [
        origin - axis_u * half_u - axis_v * half_v,
        origin + axis_u * half_u - axis_v * half_v,
        origin + axis_u * half_u + axis_v * half_v,
        origin - axis_u * half_u + axis_v * half_v,
    ]
    points.append(points[0])
    return Part.Face(Part.makePolygon(points))


def _combine(shapes):
    shapes = [shape for shape in shapes if not shape.isNull()]
    if not shapes:
        return Part.Shape()
    if len(shapes) == 1:
        return shapes[0].copy()
    return Part.makeCompound([shape.copy() for shape in shapes])


def _safe_refine(shape):
    """Remove redundant split edges when OCC can do so safely.

    ``removeSplitter`` invokes OpenCASCADE's FuseEdges refinement. Refinement is
    cosmetic/topological cleanup, not part of constructing the Boolean result,
    and it is known to reject some valid shapes containing tangent or very
    short edges. In that case, retain the valid unrefined result.
    """

    if shape is None or shape.isNull():
        return shape
    try:
        refined = shape.removeSplitter()
        if not refined.isNull() and refined.isValid():
            return refined
    except Exception as exc:
        App.Console.PrintWarning(
            "Split2Enclosure: optional edge refinement skipped ({})\n".format(exc)
        )
    return shape


def _combine_faces(faces):
    if not faces:
        return Part.Shape()
    result = faces[0].copy()
    for face in faces[1:]:
        result = result.fuse(face)
    return _safe_refine(result)


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


def _plane_basis_for_wire(wire):
    face = Part.Face(wire)
    u_min, u_max, v_min, v_max = face.ParameterRange
    normal = face.normalAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
    normal.normalize()
    origin = wire.Vertexes[0].Point
    helper = App.Vector(0, 0, 1)
    if abs(normal.dot(helper)) > 0.9:
        helper = App.Vector(1, 0, 0)
    axis_u = normal.cross(helper)
    axis_u.normalize()
    axis_v = normal.cross(axis_u)
    axis_v.normalize()
    return origin, axis_u, axis_v


def _polygon_face_from_shapely(polygon, origin, axis_u, axis_v, tolerance):
    def ring_wire(coordinates):
        points = [
            origin + axis_u * float(x) + axis_v * float(y)
            for x, y in coordinates
        ]
        cleaned = []
        for point in points:
            if not cleaned or (point - cleaned[-1]).Length > tolerance:
                cleaned.append(point)
        if len(cleaned) > 1 and (cleaned[0] - cleaned[-1]).Length > tolerance:
            cleaned.append(cleaned[0])
        if len(cleaned) < 4:
            return None
        return Part.makePolygon(cleaned)

    outer_wire = ring_wire(polygon.exterior.coords)
    if outer_wire is None:
        return Part.Shape()
    result = Part.Face(outer_wire)
    for interior in polygon.interiors:
        inner_wire = ring_wire(interior.coords)
        if inner_wire is not None:
            result = result.cut(Part.Face(inner_wire))
    return result


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
    section_material = _combine_faces(section.Faces)
    lip_parts = []
    groove_parts = []
    lip_near = clearance * 0.5
    lip_far = lip_near + lip_width
    groove_far = lip_width + clearance

    for wire in wires:
        lip_zone = _zone_around_wire(wire, lip_far, tolerance)
        if lip_near > tolerance:
            near_zone = _zone_around_wire(wire, lip_near, tolerance)
            lip_zone = lip_zone.cut(near_zone)
        lip_part = lip_zone.common(section_material)
        groove_part = _zone_around_wire(
            wire, groove_far, tolerance
        ).common(section_material)
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


def make_enclosure(
    shape,
    plane_origin,
    plane_normal,
    lip_width=1.0,
    lip_height=2.0,
    clearance=0.2,
    vertical_clearance=0.2,
    lip_on="negative",
    contour_mode="internal",
    tolerance=DEFAULT_TOLERANCE,
):
    """Split ``shape`` and add a centered-clearance lip/groove joint.

    ``plane_normal`` defines the positive half.  If ``lip_on`` is
    ``"negative"``, the lip belongs to the negative half and projects in the
    positive-normal direction; ``"positive"`` reverses that arrangement.

    With ``contour_mode="internal"``, the lip follows every nested closed
    contour in the section. With ``contour_mode="outer"``, it follows each
    disconnected section region's outermost perimeter and ignores holes. The
    groove is wider than the lip by ``clearance`` and deeper by
    ``vertical_clearance``.
    """

    if shape is None or shape.isNull() or not shape.Solids:
        raise ValueError("Select a non-empty solid BRep shape.")
    if not shape.isValid():
        raise ValueError("The selected shape is not a valid BRep.")
    lip_width = float(lip_width)
    lip_height = float(lip_height)
    clearance = float(clearance)
    vertical_clearance = float(vertical_clearance)
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    if lip_width <= 0 or lip_height <= 0:
        raise ValueError("Lip width and height must be greater than zero.")
    if clearance < 0 or vertical_clearance < 0:
        raise ValueError("Clearances must not be negative.")
    if lip_on not in ("negative", "positive"):
        raise ValueError("lip_on must be 'negative' or 'positive'.")
    if contour_mode not in ("outer", "internal"):
        raise ValueError("contour_mode must be 'outer' or 'internal'.")

    origin = App.Vector(plane_origin)
    normal = _unit(plane_normal)
    plane_face = _make_plane_face(shape, origin, normal)
    section = shape.common(plane_face)
    if not section.Faces or section.Area <= tolerance * tolerance:
        raise ValueError("The split plane does not cross a solid wall section.")

    wires = _joint_wires(section, tolerance, contour_mode)
    if not wires:
        if contour_mode == "internal":
            raise ValueError(
                "No internal closed section contours were found. "
                "Try the outermost-perimeter contour mode."
            )
        raise ValueError("No outer closed section perimeters were found.")

    negative, positive = _split_sides(
        shape, plane_face, origin, normal, tolerance
    )
    lip_footprint, groove_footprint = _joint_footprints(
        section, wires, lip_width, clearance, tolerance
    )

    direction = normal if lip_on == "negative" else -normal
    lip_volume = _extrude_faces(lip_footprint, direction * lip_height)

    # Start the cutting tool just behind the split plane to avoid a Boolean
    # failure caused by a merely coincident starting face.
    groove_depth = lip_height + vertical_clearance
    groove_volume = _extrude_faces(
        groove_footprint, direction * (groove_depth + tolerance * 2)
    )
    groove_volume.translate(-direction * tolerance)

    if lip_on == "negative":
        negative = _safe_refine(negative.fuse(lip_volume))
        positive = _safe_refine(positive.cut(groove_volume))
    else:
        positive = _safe_refine(positive.fuse(lip_volume))
        negative = _safe_refine(negative.cut(groove_volume))

    if not negative.isValid() or not positive.isValid():
        raise RuntimeError(
            "OpenCASCADE produced an invalid result. Try a wider wall, smaller lip, "
            "or a slightly different split offset."
        )

    return EnclosureResult(
        negative=negative,
        positive=positive,
        lip=lip_volume,
        groove=groove_volume,
        section=section,
        plane=plane_face,
        internal_wires=wires,
    )
