"""Geometry engine for splitting a solid and adding a mating lip and groove.

The implementation deliberately depends only on FreeCAD's App and Part modules,
so it can be exercised with FreeCADCmd and reused by either a macro or a GUI
workbench command.
"""

from dataclasses import dataclass
import math

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


def _open_sketch_wire(sketch_shape, tolerance=DEFAULT_TOLERANCE):
    """Return the single connected open wire represented by a sketch shape."""

    if sketch_shape is None or sketch_shape.isNull() or not sketch_shape.Edges:
        raise ValueError("Select an open sketch containing at least one edge.")
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    wires = [wire for wire in sketch_shape.Wires if wire.Edges]
    if len(wires) != 1 or len(wires[0].Edges) != len(sketch_shape.Edges):
        raise ValueError("The split sketch must contain one connected edge chain.")
    wire = wires[0].copy()
    if wire.isClosed():
        raise ValueError("The split sketch must be open, not a closed profile.")
    if len(wire.Vertexes) < 2:
        raise ValueError("The split sketch does not define a usable path.")
    if any(not isinstance(edge.Curve, Part.Line) for edge in wire.Edges):
        raise ValueError(
            "Sketch splits currently support connected line segments only."
        )
    return wire


def _validate_simple_sketch_path(wire, sketch_normal, tolerance):
    from shapely.geometry import LineString

    points = wire.discretize(Deflection=0.05)
    origin = points[0]
    axis_u = points[1] - origin
    axis_u.normalize()
    axis_v = sketch_normal.cross(axis_u)
    axis_v.normalize()
    coordinates = []
    for point in points:
        relative = point - origin
        if abs(relative.dot(sketch_normal)) > tolerance * 100:
            raise ValueError("All split-sketch edges must lie on one plane.")
        coordinate = (relative.dot(axis_u), relative.dot(axis_v))
        if not coordinates or coordinate != coordinates[-1]:
            coordinates.append(coordinate)
    if len(coordinates) < 2 or not LineString(coordinates).is_simple:
        raise ValueError("The split sketch must not cross or overlap itself.")


def _surface_normal_at_face(face, point):
    try:
        u_value, v_value = face.Surface.parameter(point)
    except (AttributeError, Part.OCCError, RuntimeError, ValueError):
        u_min, u_max, v_min, v_max = face.ParameterRange
        u_value = (u_min + u_max) * 0.5
        v_value = (v_min + v_max) * 0.5
    normal = face.normalAt(u_value, v_value)
    normal.normalize()
    return normal


def _section_sample(section, surface, tolerance):
    """Return a material point on the section and its oriented surface normal."""

    for section_face in section.Faces:
        point = section_face.CenterOfMass
        vertex = Part.Vertex(point)
        for surface_face in surface.Faces:
            distance = surface_face.distToShape(vertex)[0]
            if distance <= tolerance * 100:
                return point, _surface_normal_at_face(surface_face, point)
    raise RuntimeError("Could not orient the two sketch-split halves.")


def split_with_sketch(
    shape,
    sketch_shape,
    sketch_normal,
    tolerance=DEFAULT_TOLERANCE,
):
    """Split ``shape`` with a surface made by extruding one open sketch chain.

    ``sketch_normal`` is the normal of the sketch support plane and therefore
    the extrusion direction. The wire must be connected and open, and the
    resulting ruled surface must divide the source into exactly two solids.
    Positive/negative are determined by the ruled surface's oriented normal.
    """

    if shape is None or shape.isNull() or not shape.Solids:
        raise ValueError("Select a non-empty solid BRep shape.")
    if not shape.isValid():
        raise ValueError("The selected shape is not a valid BRep.")
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    extrusion_normal = _unit(sketch_normal)
    sketch_wire = _open_sketch_wire(sketch_shape, tolerance)
    _validate_simple_sketch_path(sketch_wire, extrusion_normal, tolerance)

    span = max(shape.BoundBox.DiagonalLength * 2.0, 10.0)
    surface_wire = sketch_wire.copy()
    surface_wire.translate(-extrusion_normal * span)
    surface = surface_wire.extrude(extrusion_normal * (span * 2.0))
    if surface.isNull() or not surface.Faces or not surface.isValid():
        raise ValueError("The selected sketch could not form a valid cutting surface.")

    section = shape.common(surface)
    if section.isNull() or not section.Faces:
        raise ValueError("The extruded sketch surface does not cross the selected solid.")

    sliced = SplitAPI.slice(shape, surface.Faces, "Split", tolerance)
    solids = [solid.copy() for solid in sliced.Solids]
    if len(solids) != 2:
        raise ValueError(
            "The sketch split must produce exactly two solids; it produced {}.".format(
                len(solids)
            )
        )

    sample, surface_normal = _section_sample(section, surface, tolerance)
    epsilon = max(shape.BoundBox.DiagonalLength * 1e-5, tolerance * 100)
    positive_point = sample + surface_normal * epsilon
    negative_point = sample - surface_normal * epsilon
    positive = next(
        (solid for solid in solids if solid.isInside(positive_point, tolerance, True)),
        None,
    )
    negative = next(
        (solid for solid in solids if solid.isInside(negative_point, tolerance, True)),
        None,
    )
    if positive is None or negative is None or positive.isSame(negative):
        signed = [
            ((solid.CenterOfMass - sample).dot(surface_normal), solid)
            for solid in solids
        ]
        signed.sort(key=lambda item: item[0])
        negative, positive = signed[0][1], signed[-1][1]

    return SketchSplitResult(
        negative=_safe_refine(negative),
        positive=_safe_refine(positive),
        section=section,
        surface=surface,
        sketch_wire=sketch_wire,
        extrusion_normal=extrusion_normal,
    )


def _free_section_edges(section):
    return [
        edge
        for edge in section.Edges
        if len(section.ancestorsOfType(edge, Part.Face)) == 1
    ]


def _connected_closed_wires(edges, tolerance):
    """Join unordered boundary edges into non-branching closed wires."""

    tolerance = max(float(tolerance), DEFAULT_TOLERANCE) * 100
    nodes = []

    def node_for(point):
        for index, existing in enumerate(nodes):
            if (point - existing).Length <= tolerance:
                return index
        nodes.append(App.Vector(point))
        return len(nodes) - 1

    edge_nodes = []
    separate_loops = []
    graph_edges = []
    for edge in edges:
        vertices = edge.Vertexes
        if len(vertices) == 1:
            separate_loops.append(Part.Wire([edge.copy()]))
            continue
        if len(vertices) < 2:
            continue
        first = node_for(vertices[0].Point)
        second = node_for(vertices[-1].Point)
        edge_nodes.append((first, second))
        graph_edges.append(edge)

    adjacency = {index: [] for index in range(len(nodes))}
    for edge_index, (first, second) in enumerate(edge_nodes):
        adjacency[first].append(edge_index)
        adjacency[second].append(edge_index)

    unused = set(range(len(graph_edges)))
    wires = list(separate_loops)
    while unused:
        seed = next(iter(unused))
        component_edges = set()
        pending = [seed]
        while pending:
            edge_index = pending.pop()
            if edge_index in component_edges:
                continue
            component_edges.add(edge_index)
            first, second = edge_nodes[edge_index]
            pending.extend(adjacency[first])
            pending.extend(adjacency[second])
        unused.difference_update(component_edges)
        component_nodes = {
            node
            for edge_index in component_edges
            for node in edge_nodes[edge_index]
        }
        if any(
            len([edge for edge in adjacency[node] if edge in component_edges]) != 2
            for node in component_nodes
        ):
            continue

        ordered = []
        current_edge = seed
        current_node = edge_nodes[seed][0]
        remaining = set(component_edges)
        while remaining:
            if current_edge not in remaining:
                break
            edge = graph_edges[current_edge].copy()
            first, second = edge_nodes[current_edge]
            if first == current_node:
                next_node = second
            else:
                edge.reverse()
                next_node = first
            ordered.append(edge)
            remaining.remove(current_edge)
            candidates = [
                index
                for index in adjacency[next_node]
                if index in remaining
            ]
            current_node = next_node
            if not candidates:
                break
            current_edge = candidates[0]
        if not remaining and ordered:
            wire = Part.Wire(ordered)
            if wire.isClosed():
                wires.append(wire)
    return wires


def _wire_samples(wire, deflection=0.1):
    points = wire.discretize(Deflection=max(float(deflection), 0.01))
    cleaned = []
    for point in points:
        if not cleaned or (point - cleaned[-1]).Length > DEFAULT_TOLERANCE:
            cleaned.append(point)
    if cleaned and (cleaned[0] - cleaned[-1]).Length > DEFAULT_TOLERANCE:
        cleaned.append(cleaned[0])
    return cleaned


def _base_path_samples(wire):
    samples = []
    cumulative = []
    distance = 0.0
    for edge in wire.Edges:
        points = edge.discretize(Deflection=0.05)
        if samples and points:
            if (samples[-1] - points[-1]).Length < (samples[-1] - points[0]).Length:
                points.reverse()
        for point in points:
            if samples and (samples[-1] - point).Length <= DEFAULT_TOLERANCE:
                continue
            if samples:
                distance += (point - samples[-1]).Length
            samples.append(point)
            cumulative.append(distance)
    return samples, cumulative


def _unfold_point(point, base_samples, cumulative, extrusion_normal):
    height = (point - base_samples[0]).dot(extrusion_normal)
    projected = point - extrusion_normal * height
    best_distance = None
    best_s = 0.0
    for index in range(len(base_samples) - 1):
        start = base_samples[index]
        segment = base_samples[index + 1] - start
        length_squared = segment.dot(segment)
        if length_squared <= DEFAULT_TOLERANCE:
            continue
        fraction = max(0.0, min(1.0, (projected - start).dot(segment) / length_squared))
        nearest = start + segment * fraction
        distance = (projected - nearest).Length
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_s = cumulative[index] + segment.Length * fraction
    return best_s, height


def _classify_ruled_contours(wires, sketch_wire, extrusion_normal, tolerance):
    from shapely.geometry import Polygon

    base_samples, cumulative = _base_path_samples(sketch_wire)
    polygons = []
    valid_wires = []
    for wire in wires:
        coordinates = [
            _unfold_point(point, base_samples, cumulative, extrusion_normal)
            for point in _wire_samples(wire)
        ]
        polygon = Polygon(coordinates)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= tolerance * tolerance:
            continue
        valid_wires.append(wire)
        polygons.append(polygon)

    classified = []
    for index, (wire, polygon) in enumerate(zip(valid_wires, polygons)):
        representative = polygon.representative_point()
        depth = sum(
            1
            for other_index, other in enumerate(polygons)
            if other_index != index
            and other.area > polygon.area + tolerance
            and other.contains(representative)
        )
        classified.append(
            ("outer" if depth % 2 == 0 else "internal", wire, polygon.area)
        )
    classified.sort(key=lambda item: (item[0] != "outer", -item[2]))
    return [
        ContourInfo(
            index=index,
            kind=kind,
            wire=wire,
            area=area,
            length=wire.Length,
        )
        for index, (kind, wire, area) in enumerate(classified)
    ]


def analyze_sketch_contours(
    shape,
    sketch_shape,
    sketch_normal,
    tolerance=DEFAULT_TOLERANCE,
):
    """Return a sketch split plus selectable seam-boundary contours."""

    split = split_with_sketch(shape, sketch_shape, sketch_normal, tolerance)
    wires = _connected_closed_wires(
        _free_section_edges(split.section), tolerance
    )
    contours = _classify_ruled_contours(
        wires, split.sketch_wire, split.extrusion_normal, tolerance
    )
    if not contours:
        raise ValueError("No closed joint contours were found on the sketch seam.")
    return split, contours


def _planar_face_basis(face):
    if not isinstance(face.Surface, Part.Plane):
        raise ValueError("Sketch-seam joint panels must be planar.")
    u_min, u_max, v_min, v_max = face.ParameterRange
    point = face.valueAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
    normal = face.normalAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
    normal.normalize()
    helper = App.Vector(0, 0, 1)
    if abs(normal.dot(helper)) > 0.9:
        helper = App.Vector(1, 0, 0)
    axis_u = normal.cross(helper)
    axis_u.normalize()
    axis_v = normal.cross(axis_u)
    axis_v.normalize()
    return point, axis_u, axis_v


def _coplanar_face_groups(faces, tolerance):
    """Group section patches that share one geometric support plane."""

    groups = []
    plane_data = []
    distance_tolerance = max(float(tolerance), DEFAULT_TOLERANCE) * 100
    for face in faces:
        if not isinstance(face.Surface, Part.Plane):
            raise ValueError("Sketch-seam joint panels must be planar.")
        u_min, u_max, v_min, v_max = face.ParameterRange
        point = face.valueAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
        normal = face.normalAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
        normal.normalize()
        for index, (plane_point, plane_normal) in enumerate(plane_data):
            if (
                abs(abs(normal.dot(plane_normal)) - 1.0) <= 1e-7
                and abs((point - plane_point).dot(plane_normal)) <= distance_tolerance
            ):
                groups[index].append(face)
                break
        else:
            groups.append([face])
            plane_data.append((point, normal))
    return groups


def _edge_band_on_faces(section_faces, edges, distance, tolerance):
    """Buffer boundary edges once across all coplanar section patches.

    OCC commonly splits a complex section into adjacent faces even though they
    lie on exactly the same plane. Buffering and clipping each patch in
    isolation leaves tiny seams where those artificial face boundaries meet.
    Treating the coplanar collection as one material region keeps the joint
    continuous across those topology-only divisions.
    """

    from shapely.geometry import LineString
    from shapely.ops import unary_union

    if not section_faces or not edges:
        return Part.Shape()
    origin, axis_u, axis_v = _planar_face_basis(section_faces[0])
    lines = []
    for edge in edges:
        coordinates = []
        for point in edge.discretize(Deflection=max(min(distance * 0.05, 0.05), 0.01)):
            relative = point - origin
            coordinate = (relative.dot(axis_u), relative.dot(axis_v))
            if not coordinates or coordinate != coordinates[-1]:
                coordinates.append(coordinate)
        if len(coordinates) >= 2:
            lines.append(LineString(coordinates))
    if not lines:
        return Part.Shape()
    buffered = unary_union(lines).buffer(
        distance,
        quad_segs=10,
        cap_style=2,
        join_style=2,
        mitre_limit=5.0,
    )
    polygons = [buffered] if buffered.geom_type == "Polygon" else list(buffered.geoms)
    band_faces = []
    for polygon in polygons:
        if polygon.geom_type != "Polygon":
            continue
        converted = _polygon_face_from_shapely(
            polygon, origin, axis_u, axis_v, tolerance
        )
        if not converted.isNull() and converted.Faces:
            band_faces.extend(converted.Faces)
    if not band_faces:
        return Part.Shape()
    section_material = _combine_faces(section_faces)
    return _combine_faces(band_faces).common(section_material)


def _matching_surface_face(section_face, surface, tolerance):
    point = section_face.CenterOfMass
    vertex = Part.Vertex(point)
    candidates = sorted(
        surface.Faces,
        key=lambda face: face.distToShape(vertex)[0],
    )
    if not candidates or candidates[0].distToShape(vertex)[0] > tolerance * 100:
        raise RuntimeError("Could not match a seam section panel to the sketch surface.")
    return candidates[0]


def _same_geometric_edge(first, second, tolerance):
    length_tolerance = max(first.Length, second.Length, 1.0) * 1e-7
    return (
        abs(first.Length - second.Length) <= max(length_tolerance, tolerance * 10)
        and first.distToShape(second)[0] <= tolerance * 100
    )


def ruled_contour_positive_direction(
    split,
    wire,
    tolerance=DEFAULT_TOLERANCE,
):
    """Return one signed direction that crosses the whole ruled surface.

    A sketch seam used to extrude every ruled panel along its own normal. At a
    polyline corner those directions diverge, producing mitre wedges and gaps.
    The joint instead uses the direction that maximizes its least-direct
    crossing of all panels. A straight or coplanar seam therefore follows its
    exact surface normal, while a polyline uses the bisector of its limiting
    panel normals. Every panel and preview arrow shares that direction.
    """

    del wire  # Kept in the public signature for backwards compatibility.
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    positive_normals = []
    epsilon = max(split.section.BoundBox.DiagonalLength * 1e-6, tolerance * 10)
    for section_face in split.section.Faces:
        surface_face = _matching_surface_face(section_face, split.surface, tolerance)
        point = section_face.CenterOfMass
        normal = _surface_normal_at_face(surface_face, point)
        if split.positive.isInside(point + normal * epsilon, tolerance, True):
            positive_normals.append(normal)
            continue
        if split.positive.isInside(point - normal * epsilon, tolerance, True):
            positive_normals.append(-normal)
            continue
        if (split.positive.CenterOfMass - point).dot(normal) < 0.0:
            normal = -normal
        positive_normals.append(normal)

    center_delta = split.positive.CenterOfMass - split.negative.CenterOfMass
    raw_candidates = list(positive_normals)
    normal_sum = App.Vector()
    for normal in positive_normals:
        normal_sum += normal
    raw_candidates.append(normal_sum)
    raw_candidates.append(center_delta)
    for first_index, first in enumerate(positive_normals):
        for second in positive_normals[first_index + 1:]:
            raw_candidates.append(first + second)

    candidates = []
    for raw_candidate in raw_candidates:
        if raw_candidate.Length <= tolerance:
            continue
        candidate = _unit(raw_candidate)
        score = min(normal.dot(candidate) for normal in positive_normals)
        candidates.append((score, candidate))
    if not candidates or max(candidates, key=lambda item: item[0])[0] <= 1e-4:
        raise ValueError(
            "The sketch seam cannot use one consistent extrusion direction. "
            "Remove any reversal or overhang in the split path."
        )
    return max(candidates, key=lambda item: item[0])[1]


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


def _ruled_joint_volumes(
    split,
    selected_wires,
    lip_width,
    lip_height,
    clearance,
    vertical_clearance,
    draft_angle,
    lip_on,
    positive_direction,
    tolerance,
):
    selected_edges = [edge for wire in selected_wires for edge in wire.Edges]
    receiver = split.positive if lip_on == "negative" else split.negative
    lip_parts = []
    groove_parts = []
    root_parts = []
    direction = (
        _unit(positive_direction)
        if lip_on == "negative"
        else -_unit(positive_direction)
    )
    for section_faces in _coplanar_face_groups(split.section.Faces, tolerance):
        boundary_edges = [
            edge
            for section_face in section_faces
            for edge in section_face.Edges
            if any(
                edge.isSame(selected)
                or _same_geometric_edge(edge, selected, tolerance)
                for selected in selected_edges
            )
        ]
        if not boundary_edges:
            continue
        lip_band = _edge_band_on_faces(
            section_faces, boundary_edges, lip_width, tolerance
        )
        groove_band = _edge_band_on_faces(
            section_faces, boundary_edges, lip_width + clearance, tolerance
        )
        if lip_band.isNull() or not lip_band.Faces:
            continue
        draft_slope = math.tan(math.radians(draft_angle))
        groove_depth = lip_height + vertical_clearance
        top_lip_width = lip_width - draft_slope * lip_height
        top_groove_width = lip_width + clearance - draft_slope * groove_depth
        if top_lip_width <= tolerance or top_groove_width <= tolerance:
            raise ValueError(
                "Draft angle is too large for the requested lip width and height."
            )
        top_lip_band = _edge_band_on_faces(
            section_faces, boundary_edges, top_lip_width, tolerance
        )
        top_groove_band = _edge_band_on_faces(
            section_faces, boundary_edges, top_groove_width, tolerance
        )
        lip_piece = _drafted_extrusion(
            lip_band,
            top_lip_band,
            direction * lip_height,
            draft_angle,
        ).common(receiver)
        if lip_piece.isNull() or not lip_piece.Solids:
            continue
        groove_piece = _drafted_extrusion(
            groove_band,
            top_groove_band,
            direction * (groove_depth + tolerance * 2),
            draft_angle,
        )
        groove_piece.translate(-direction * tolerance)
        if vertical_clearance > tolerance:
            section_material = _combine_faces(section_faces)
            root_band = section_material.cut(lip_band)
            if not root_band.isNull() and root_band.Faces:
                root_piece = _extrude_faces(
                    root_band,
                    -direction * (vertical_clearance + tolerance),
                )
                root_piece.translate(direction * tolerance)
                if not root_piece.isNull():
                    root_parts.extend(root_piece.Solids)
        if not lip_piece.isNull():
            lip_parts.extend(lip_piece.Solids)
        if not groove_piece.isNull():
            groove_parts.extend(groove_piece.Solids)

    if not lip_parts or not groove_parts:
        raise ValueError("No lip/groove volume fits along the selected sketch seam.")
    raw_lip = _fuse_shapes(lip_parts)
    raw_groove = _combine(groove_parts)
    lip = _safe_refine(raw_lip)
    if lip.isNull() or not lip.Solids:
        raise ValueError("The receiving half contains no material for the sketch-seam lip.")
    groove = raw_groove
    return lip, groove, _combine(root_parts)


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

    lip_width = float(lip_width)
    lip_height = float(lip_height)
    clearance = float(clearance)
    vertical_clearance = float(vertical_clearance)
    draft_angle = float(draft_angle)
    snap_radius = float(snap_radius)
    snap_clearance = float(snap_clearance)
    snap_position = float(snap_position)
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    if lip_width <= 0 or lip_height <= 0:
        raise ValueError("Lip width and height must be greater than zero.")
    if clearance < 0 or vertical_clearance < 0:
        raise ValueError("Clearances must not be negative.")
    if draft_angle < 0 or draft_angle >= 45:
        raise ValueError("Draft angle must be at least 0 and less than 45 degrees.")
    if snap_radius <= 0 or snap_clearance < 0:
        raise ValueError("Snap radius must be positive and snap clearance non-negative.")
    if not 0.1 <= snap_position <= 0.9:
        raise ValueError("Snap position must be between 0.1 and 0.9 of lip height.")
    if lip_on not in ("negative", "positive"):
        raise ValueError("lip_on must be 'negative' or 'positive'.")
    if contour_mode not in ("outer", "internal"):
        raise ValueError("contour_mode must be 'outer' or 'internal'.")

    split, contours = analyze_sketch_contours(
        shape, sketch_shape, sketch_normal, tolerance
    )
    groups, assignments = _contour_groups(
        contours, contour_sides, contour_indices, lip_on, contour_mode
    )
    snap_assignments = _normalized_snap_assignments(contours, contour_snaps)
    if any(snap_assignments.values()) and lip_width - snap_radius <= tolerance:
        raise ValueError("Snap seam half-size must be smaller than the lip width.")
    joint_positive_direction = ruled_contour_positive_direction(
        split, contours[0].wire, tolerance
    )
    negative = split.negative
    positive = split.positive
    lips = []
    grooves = []
    root_clearance_groups = {"negative": [], "positive": []}
    for side in ("negative", "positive"):
        for snapped in (False, True):
            wires = [
                contour.wire
                for contour in contours
                if assignments[contour.index] == side
                and snap_assignments[contour.index] == snapped
            ]
            if not wires:
                continue
            effective_width = lip_width - snap_radius if snapped else lip_width
            lip, groove, root_clearance = _ruled_joint_volumes(
                split,
                wires,
                effective_width,
                lip_height,
                clearance,
                vertical_clearance,
                draft_angle,
                side,
                joint_positive_direction,
                tolerance,
            )
            lips.append(lip)
            grooves.append(groove)
            if root_clearance is not None and not root_clearance.isNull():
                root_clearance_groups[side].append(root_clearance)
            if side == "negative":
                negative = _safe_refine(negative.fuse(lip))
                positive = _cut_tool_solids(positive, groove)
                positive = _safe_refine(positive.cut(lip))
            else:
                positive = _safe_refine(positive.fuse(lip))
                negative = _cut_tool_solids(negative, groove)
                negative = _safe_refine(negative.cut(lip))

    root_clearances = []
    for side, tools in root_clearance_groups.items():
        if not tools:
            continue
        root_clearance = tools[0]
        for tool in tools[1:]:
            root_clearance = root_clearance.common(tool)
        if root_clearance.isNull() or not root_clearance.Solids:
            continue
        root_clearances.append(root_clearance)
        if side == "negative":
            negative = _cut_tool_solids(negative, root_clearance)
        else:
            positive = _cut_tool_solids(positive, root_clearance)

    snap_features = []
    for contour in contours:
        side = assignments[contour.index]
        if side == "none" or not snap_assignments[contour.index]:
            continue
        contour_lip, contour_groove, _unused_root = _ruled_joint_volumes(
            split,
            [contour.wire],
            lip_width - snap_radius,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            joint_positive_direction,
            tolerance,
        )
        wide_lip, _unused_groove, _unused_root = _ruled_joint_volumes(
            split,
            [contour.wire],
            lip_width,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            joint_positive_direction,
            tolerance,
        )
        _unused_lip, wide_groove, _unused_root = _ruled_joint_volumes(
            split,
            [contour.wire],
            lip_width + snap_clearance,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            joint_positive_direction,
            tolerance,
        )
        extrusion_direction = (
            joint_positive_direction
            if side == "negative"
            else -joint_positive_direction
        )
        bump, pocket, added = _snap_for_contour(
            split.section,
            contour_lip,
            contour_groove,
            wide_lip,
            wide_groove,
            extrusion_direction,
            lip_height,
            snap_radius,
            snap_clearance,
            snap_position,
            tolerance,
        )
        snap_features.append(added)
        if side == "negative":
            negative = _fuse_tool_solids(negative, bump)
            positive = _safe_refine(positive.cut(pocket).cut(bump))
        else:
            positive = _fuse_tool_solids(positive, bump)
            negative = _safe_refine(negative.cut(pocket).cut(bump))
    negative = _discard_boolean_slivers(negative, tolerance, keep_largest=True)
    positive = _discard_boolean_slivers(positive, tolerance, keep_largest=True)
    if not negative.isValid() or not positive.isValid():
        raise RuntimeError(
            "OpenCASCADE produced an invalid sketch-seam joint. Try smaller joint dimensions."
        )
    return EnclosureResult(
        negative=negative,
        positive=positive,
        lip=_combine(lips),
        groove=_combine(grooves),
        section=split.section,
        plane=split.surface,
        internal_wires=groups["negative"] + groups["positive"],
        contour_sides=assignments,
        root_clearance=_combine(root_clearances),
        snap_features=_combine(snap_features),
        contour_snaps=snap_assignments,
    )


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
    groove_far = lip_width + clearance

    for wire in wires:
        # Keep the lip anchored to the selected wall perimeter. Clearance is
        # added only at its material-side mating face by widening the groove;
        # it must not move the lip away from the perimeter itself.
        lip_zone = _zone_around_wire(wire, lip_width, tolerance)
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
):
    lip_footprint, groove_footprint = _joint_footprints(
        section, wires, lip_width, clearance, tolerance
    )
    direction = normal if lip_on == "negative" else -normal
    receiver = positive_half if lip_on == "negative" else negative_half
    draft_slope = math.tan(math.radians(draft_angle))
    groove_depth = lip_height + vertical_clearance
    top_lip_width = lip_width - draft_slope * lip_height
    top_groove_lip_width = lip_width - draft_slope * groove_depth
    if top_lip_width <= tolerance or top_groove_lip_width + clearance <= tolerance:
        raise ValueError(
            "Draft angle is too large for the requested lip width and height."
        )
    top_lip_footprint, _unused = _joint_footprints(
        section, wires, top_lip_width, clearance, tolerance
    )
    _unused, top_groove_footprint = _joint_footprints(
        section, wires, top_groove_lip_width, clearance, tolerance
    )
    raw_lip = _drafted_extrusion(
        lip_footprint,
        top_lip_footprint,
        direction * lip_height,
        draft_angle,
    )
    lip = _safe_refine(raw_lip.common(receiver))
    if lip.isNull() or not lip.Solids:
        raise RuntimeError(
            "The receiving half contains no material inside the requested lip region."
        )

    groove = _drafted_extrusion(
        groove_footprint,
        top_groove_footprint,
        direction * (lip_height + vertical_clearance + tolerance * 2),
        draft_angle,
    )
    groove.translate(-direction * tolerance)

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
    lip_width = float(lip_width)
    lip_height = float(lip_height)
    clearance = float(clearance)
    vertical_clearance = float(vertical_clearance)
    draft_angle = float(draft_angle)
    snap_radius = float(snap_radius)
    snap_clearance = float(snap_clearance)
    snap_position = float(snap_position)
    tolerance = max(float(tolerance), DEFAULT_TOLERANCE)
    if lip_width <= 0 or lip_height <= 0:
        raise ValueError("Lip width and height must be greater than zero.")
    if clearance < 0 or vertical_clearance < 0:
        raise ValueError("Clearances must not be negative.")
    if draft_angle < 0 or draft_angle >= 45:
        raise ValueError("Draft angle must be at least 0 and less than 45 degrees.")
    if snap_radius <= 0 or snap_clearance < 0:
        raise ValueError("Snap radius must be positive and snap clearance non-negative.")
    if not 0.1 <= snap_position <= 0.9:
        raise ValueError("Snap position must be between 0.1 and 0.9 of lip height.")
    if lip_on not in ("negative", "positive"):
        raise ValueError("lip_on must be 'negative' or 'positive'.")
    if contour_mode not in ("outer", "internal"):
        raise ValueError("contour_mode must be 'outer' or 'internal'.")

    origin = App.Vector(plane_origin)
    normal = _unit(plane_normal)
    section, plane_face, contours = analyze_section_contours(
        shape, origin, normal, tolerance
    )
    groups, assignments = _contour_groups(
        contours, contour_sides, contour_indices, lip_on, contour_mode
    )
    snap_assignments = _normalized_snap_assignments(contours, contour_snaps)
    if any(snap_assignments.values()) and lip_width - snap_radius <= tolerance:
        raise ValueError("Snap seam half-size must be smaller than the lip width.")

    negative, positive = _split_sides(
        shape, plane_face, origin, normal, tolerance
    )
    base_negative = negative
    base_positive = positive
    lips = []
    grooves = []
    root_clearance_groups = {"negative": [], "positive": []}
    for side in ("negative", "positive"):
        for snapped in (False, True):
            wires = [
                contour.wire
                for contour in contours
                if assignments[contour.index] == side
                and snap_assignments[contour.index] == snapped
            ]
            if not wires:
                continue
            effective_width = lip_width - snap_radius if snapped else lip_width
            lip, groove, root_clearance = _plane_joint_volumes(
                section,
                wires,
                base_negative,
                base_positive,
                normal,
                effective_width,
                lip_height,
                clearance,
                vertical_clearance,
                draft_angle,
                side,
                tolerance,
            )
            lips.append(lip)
            grooves.append(groove)
            if not root_clearance.isNull():
                root_clearance_groups[side].append(root_clearance)
            if side == "negative":
                negative = _safe_refine(negative.fuse(lip))
                positive = _cut_tool_solids(positive, groove)
                positive = _safe_refine(positive.cut(lip))
            else:
                positive = _safe_refine(positive.fuse(lip))
                negative = _cut_tool_solids(negative, groove)
                negative = _safe_refine(negative.cut(lip))

    root_clearances = []
    for side, tools in root_clearance_groups.items():
        if not tools:
            continue
        root_clearance = tools[0]
        for tool in tools[1:]:
            root_clearance = root_clearance.common(tool)
        if root_clearance.isNull() or not root_clearance.Solids:
            continue
        root_clearances.append(root_clearance)
        if side == "negative":
            negative = _cut_tool_solids(negative, root_clearance)
        else:
            positive = _cut_tool_solids(positive, root_clearance)

    snap_features = []
    for contour in contours:
        side = assignments[contour.index]
        if side == "none" or not snap_assignments[contour.index]:
            continue
        contour_lip, contour_groove, _unused_root = _plane_joint_volumes(
            section,
            [contour.wire],
            base_negative,
            base_positive,
            normal,
            lip_width - snap_radius,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            tolerance,
        )
        wide_lip, _unused_groove, _unused_root = _plane_joint_volumes(
            section,
            [contour.wire],
            base_negative,
            base_positive,
            normal,
            lip_width,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            tolerance,
        )
        _unused_lip, wide_groove, _unused_root = _plane_joint_volumes(
            section,
            [contour.wire],
            base_negative,
            base_positive,
            normal,
            lip_width + snap_clearance,
            lip_height,
            clearance,
            vertical_clearance,
            draft_angle,
            side,
            tolerance,
        )
        extrusion_direction = normal if side == "negative" else -normal
        bump, pocket, added = _snap_for_contour(
            section,
            contour_lip,
            contour_groove,
            wide_lip,
            wide_groove,
            extrusion_direction,
            lip_height,
            snap_radius,
            snap_clearance,
            snap_position,
            tolerance,
        )
        snap_features.append(added)
        if side == "negative":
            negative = _fuse_tool_solids(negative, bump)
            positive = _safe_refine(positive.cut(pocket).cut(bump))
        else:
            positive = _fuse_tool_solids(positive, bump)
            negative = _safe_refine(negative.cut(pocket).cut(bump))

    if not negative.isValid() or not positive.isValid():
        raise RuntimeError(
            "OpenCASCADE produced an invalid result. Try a wider wall, smaller lip, "
            "or a slightly different split offset."
        )

    return EnclosureResult(
        negative=negative,
        positive=positive,
        lip=_combine(lips),
        groove=_combine(grooves),
        section=section,
        plane=plane_face,
        internal_wires=groups["negative"] + groups["positive"],
        contour_sides=assignments,
        root_clearance=_combine(root_clearances),
        snap_features=_combine(snap_features),
        contour_snaps=snap_assignments,
    )
