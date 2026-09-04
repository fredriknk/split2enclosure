"""Open-sketch splitting, contour analysis, and ruled joint volumes."""

import math

import FreeCAD as App
import Part
from BOPTools import SplitAPI

from ._geometry_types import ContourInfo, DEFAULT_TOLERANCE, SketchSplitResult
from ._geometry_common import (
    _combine,
    _combine_faces,
    _fuse_shapes,
    _polygon_face_from_shapely,
    _safe_refine,
    _unit,
)
from ._geometry_joint import _drafted_extrusion, _drafted_groove, _extrude_faces


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
    """Return one signed principal axis that crosses the whole ruled surface.

    A sketch seam used to extrude every ruled panel along its own normal. At a
    polyline corner those directions diverge, producing mitre wedges and gaps.
    The joint instead uses one global X, Y, or Z direction so both halves have
    a straight principal-axis assembly motion. Every lip, groove, shoulder
    relief, snap channel, and preview arrow shares that direction.
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

    candidates = []
    center_delta = split.positive.CenterOfMass - split.negative.CenterOfMass
    for axis in (
        App.Vector(1, 0, 0),
        App.Vector(0, 1, 0),
        App.Vector(0, 0, 1),
    ):
        dots = [normal.dot(axis) for normal in positive_normals]
        if not dots or min(abs(value) for value in dots) <= 1e-4:
            continue
        if min(dots) < 0.0 < max(dots):
            continue
        directed = axis if sum(dots) > 0.0 else -axis
        score = min(abs(value) for value in dots)
        score += abs(center_delta.dot(axis)) * 1e-9
        candidates.append((score, directed))
    if not candidates:
        raise ValueError(
            "The sketch seam cannot use one principal-axis assembly direction. "
            "Make the split path monotonic across global X, Y, or Z."
        )
    return max(candidates, key=lambda item: item[0])[1]


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
        top_lip_width = lip_width - draft_slope * lip_height
        top_groove_width = top_lip_width + clearance
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
        groove_piece = _drafted_groove(
            groove_band,
            top_groove_band,
            direction,
            lip_height,
            vertical_clearance,
            draft_angle,
            tolerance,
        )
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
