"""Low-level vector, face, and robust Boolean helpers."""

import FreeCAD as App
import Part

from ._geometry_types import DEFAULT_TOLERANCE


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


def _fuse_shapes(shapes):
    shapes = [shape for shape in shapes if not shape.isNull()]
    if not shapes:
        return Part.Shape()
    result = shapes[0].copy()
    for shape in shapes[1:]:
        result = result.fuse(shape)
    return _safe_refine(result)


def _discard_boolean_slivers(shape, tolerance, keep_largest=False):
    """Drop zero-volume OCC artifacts while retaining real result solids."""

    if shape is None or shape.isNull():
        return shape
    # A groove Boolean at a sharp ruled-surface mitre can leave a detached
    # microscopic wedge. Enclosure halves are required to stay connected, so
    # discard components below one thousandth of the result volume.
    threshold = max(abs(shape.Volume) * 1e-3, tolerance ** 3 * 1000, 1e-9)
    if keep_largest and shape.Solids:
        return max(shape.Solids, key=lambda solid: solid.Volume).copy()
    solids = [solid for solid in shape.Solids if solid.Volume > threshold]
    if not solids:
        return shape
    return _combine(solids)


def _cut_tool_solids(shape, tool):
    """Apply compound Boolean tools one solid at a time for OCC stability."""

    if tool is None or tool.isNull():
        return shape
    result = shape
    solids = sorted(tool.Solids, key=lambda solid: solid.Volume, reverse=True)
    if not solids:
        return result.cut(tool)
    for solid in solids:
        result = result.cut(solid)
    return _safe_refine(result)


def _fuse_tool_solids(shape, tool):
    """Fuse compound tools one solid at a time and retain solid topology."""

    if tool is None or tool.isNull():
        return shape
    result = shape
    solids = sorted(tool.Solids, key=lambda solid: solid.Volume, reverse=True)
    if not solids:
        raise ValueError("The feature to fuse does not contain a solid.")
    for solid in solids:
        fused = result.fuse(solid)
        if fused.isNull() or not fused.Solids:
            raise RuntimeError("OpenCASCADE could not fuse a snap seam into the lip.")
        result = fused
    return _safe_refine(result)


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
