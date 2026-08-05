"""Planar face reconstruction for concave prism profiles."""

from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt, tessellate_polygon

from .convex_prism import EPSILON
from ...core.geometry import (
    compute_normal_from_verts,
    polygon_has_negligible_area,
)
from ...core.logging import debug_log


class _CanonicalCutNode:
    """One shared cutter/mesh intersection point."""

    def __init__(self, point, face, anchor_vertex):
        self.point = Vector(point).copy()
        self.faces = {face}
        self.anchor_vertex = anchor_vertex


class _CanonicalCutEdge:
    """One graph edge, possibly contributed by multiple source faces."""

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.faces = set()
        self.supporting_planes = set()
        self.active = True


class CanonicalCutGraph:
    """Canonical cutter intersection segments shared by all source faces."""

    def __init__(
            self, edges_by_face, edges, suppressed_caps_by_face, epsilon):
        self._edges_by_face = edges_by_face
        self._edges = edges
        self._suppressed_caps_by_face = suppressed_caps_by_face
        self.epsilon = epsilon
        self._bmesh_boundary_edges = set()

    def segments_for_face(self, face):
        """Return canonical 3D segment coordinates for one source face."""
        return [
            (edge.start.point.copy(), edge.end.point.copy())
            for edge in self._edges_by_face.get(face, [])
            if edge.active
        ]

    def edge_is_boundary(self, edge):
        """Return whether reconstruction registered this exact graph edge."""
        return edge in self._bmesh_boundary_edges

    def register_boundary_edges(self, edges):
        """Record exact BMesh edges emitted for graph constraints."""
        self._bmesh_boundary_edges.update(edges)

    def vertices_for_face(self, face):
        """Return the canonical BMesh vertices used by one source face."""
        return list({
            node.anchor_vertex
            for edge in self._edges_by_face.get(face, [])
            if edge.active
            for node in (edge.start, edge.end)
            if node.anchor_vertex is not None
            and node.anchor_vertex.is_valid
        })

    def suppressed_cap_indices_for_face(self, face):
        """Return cap planes suppressed only while rebuilding one face."""
        return set(self._suppressed_caps_by_face.get(face, set()))

    def point_inside(self, point, prism, suppressed_cap_indices):
        """Classify a reconstruction cell using the normalised graph caps."""
        return prism.point_inside_ignoring_caps(
            point,
            suppressed_cap_indices,
        )

    def canonical_point(self, point):
        """Return the graph coordinate for a nearby source-loop point."""
        nearest_node = None
        nearest_distance = self.epsilon
        for edge in self._edges:
            if not edge.active:
                continue
            for node in (edge.start, edge.end):
                distance = (node.point - point).length
                if distance <= nearest_distance:
                    nearest_node = node
                    nearest_distance = distance
        if nearest_node is None:
            return Vector(point).copy()
        return nearest_node.point.copy()

def build_canonical_cut_graph(
        bm, faces, prism, split_verts, face_interior_verts):
    """Build one canonical intersection graph before rebuilding any face.

    Individual source faces still need planar CDT reconstruction, but their
    cutter constraints now refer to shared graph nodes. Nodes at host edges or
    known cutter/face intersections are anchored to the BMesh vertices that
    reconstruction must reuse.
    """
    valid_faces = [face for face in faces if face is not None and face.is_valid]
    epsilon = _canonical_graph_epsilon(valid_faces, prism)
    canonical_epsilon = max(
        epsilon,
        _prism_extent_scale(prism) * 6e-7,
    )
    face_neighbors = _face_neighbors(valid_faces)
    split_verts_set = set(split_verts)
    nodes = []
    anchor_nodes = {}
    edges_by_face = {}
    graph_edges = {}

    for face in valid_faces:
        face_anchors = _face_graph_anchors(
            face,
            prism,
            split_verts_set,
            face_interior_verts.get(face, []),
        )
        origin = face.verts[0].co
        raw_segments = _prism_surface_plane_segments(
            prism,
            origin,
            face.normal,
            epsilon,
        )
        clipped_segments = _clip_segments_to_face(
            raw_segments,
            face,
            prism,
            epsilon,
        )
        face_edges = []
        seen_segments = set()
        for start, end in clipped_segments:
            start_node = _canonical_graph_node(
                nodes,
                anchor_nodes,
                start,
                face,
                face_anchors,
                face_neighbors,
                epsilon,
                canonical_epsilon,
            )
            end_node = _canonical_graph_node(
                nodes,
                anchor_nodes,
                end,
                face,
                face_anchors,
                face_neighbors,
                epsilon,
                canonical_epsilon,
            )
            if start_node is end_node:
                continue
            segment_key = frozenset((id(start_node), id(end_node)))
            if segment_key in seen_segments:
                continue
            seen_segments.add(segment_key)
            graph_edge = graph_edges.get(segment_key)
            if graph_edge is None:
                graph_edge = _CanonicalCutEdge(start_node, end_node)
                graph_edges[segment_key] = graph_edge
            graph_edge.faces.add(face)
            graph_edge.supporting_planes.update(
                _segment_supporting_planes(start, end, prism, epsilon)
            )
            face_edges.append(graph_edge)
        edges_by_face[face] = face_edges

    suppressed_caps_by_face = _remove_redundant_cap_paths(
        list(graph_edges.values()),
        prism,
    )

    active_nodes = {
        node
        for edge in graph_edges.values()
        if edge.active
        for node in (edge.start, edge.end)
    }
    for node in active_nodes:
        if node.anchor_vertex is None:
            node.anchor_vertex = bm.verts.new(node.point)

    node_degrees = {node: 0 for node in active_nodes}
    node_neighbors = {node: [] for node in active_nodes}
    active_edges = [edge for edge in graph_edges.values() if edge.active]
    for edge in active_edges:
        node_degrees[edge.start] += 1
        node_degrees[edge.end] += 1
        node_neighbors[edge.start].append(edge.end)
        node_neighbors[edge.end].append(edge.start)
    unexpected_nodes = [
        (tuple(node.point), degree)
        for node, degree in node_degrees.items()
        if degree != 2
    ]
    debug_log(
        f"[PrismCut] Canonical cut graph has {len(active_nodes)} nodes and "
        f"{len(active_edges)} active edges across {len(valid_faces)} faces; "
        f"unexpected node degrees: {unexpected_nodes}"
    )
    for node, degree in node_degrees.items():
        if degree != 2:
            debug_log(
                f"[PrismCut] Graph node {tuple(node.point)} degree {degree} "
                f"neighbors "
                f"{[tuple(neighbor.point) for neighbor in node_neighbors[node]]} "
                f"faces {[face.index for face in node.faces]}"
            )
    return CanonicalCutGraph(
        edges_by_face,
        list(graph_edges.values()),
        suppressed_caps_by_face,
        canonical_epsilon,
    )


def _segment_supporting_planes(start, end, prism, epsilon):
    midpoint = (start + end) * 0.5
    return {
        plane_index
        for plane_index, (plane_point, plane_normal) in enumerate(prism.planes)
        if abs((midpoint - plane_point).dot(plane_normal)) <= epsilon * 4
    }


def _remove_redundant_cap_paths(edges, prism):
    """Remove cap-only paths that make an otherwise cyclic graph branch."""
    active_edges = [edge for edge in edges if edge.active]
    node_edges = _graph_node_edges(active_edges)
    cap_indices = set(prism.cap_plane_indices)
    cap_only_edges = {
        edge for edge in active_edges
        if edge.supporting_planes
        and edge.supporting_planes <= cap_indices
    }
    visited = set()
    suppressed_caps_by_face = {}

    for starting_edge in cap_only_edges:
        if starting_edge in visited:
            continue
        component_edges = set()
        component_nodes = set()
        pending = [starting_edge]
        while pending:
            edge = pending.pop()
            if edge in visited:
                continue
            visited.add(edge)
            component_edges.add(edge)
            component_nodes.update((edge.start, edge.end))
            for node in (edge.start, edge.end):
                pending.extend(
                    candidate for candidate in node_edges[node]
                    if candidate in cap_only_edges
                    and candidate not in visited
                )

        attachment_nodes = [
            node for node in component_nodes
            if any(edge not in component_edges for edge in node_edges[node])
        ]
        if len(attachment_nodes) != 2:
            continue
        if not all(
                len(node_edges[node]) > 2
                and len(node_edges[node]) - sum(
                    edge in component_edges for edge in node_edges[node]
                ) >= 2
                for node in attachment_nodes):
            continue
        for edge in component_edges:
            edge.active = False
            for face in edge.faces:
                suppressed_caps_by_face.setdefault(face, set()).update(
                    edge.supporting_planes
                )
        affected_faces = {
            face.index: sorted(suppressed_caps_by_face[face])
            for edge in component_edges
            for face in edge.faces
        }
        debug_log(
            f"[PrismCut] Removed redundant cap path with "
            f"{len(component_edges)} graph edges from source faces "
            f"{affected_faces}"
        )
    return suppressed_caps_by_face


def _graph_node_edges(edges):
    result = {}
    for edge in edges:
        result.setdefault(edge.start, []).append(edge)
        result.setdefault(edge.end, []).append(edge)
    return result


def _clip_segments_to_face(segments, face, prism, epsilon):
    """Clip cutter-plane segments to one source polygon.

    The CDT used to perform this clipping independently for every face. Making
    the crossings explicit here lets adjacent faces share one canonical node
    at their common BMesh edge.
    """
    origin, axis_x, axis_y = _face_coordinate_system(
        list(face.verts),
        face.normal,
    )

    def project(point):
        offset = Vector(point) - origin
        return Vector((offset.dot(axis_x), offset.dot(axis_y)))

    source_loop = [project(vertex.co) for vertex in face.verts]
    boundary_segments = _loop_segments(source_loop)
    clipped = []

    for start_3d, end_3d in segments:
        start_2d = project(start_3d)
        end_2d = project(end_3d)
        segment_2d = end_2d - start_2d
        if segment_2d.length <= epsilon:
            continue

        parameters = [0.0, 1.0]
        for boundary_start, boundary_end in boundary_segments:
            parameters.extend(_segment_boundary_parameters(
                start_2d,
                end_2d,
                boundary_start,
                boundary_end,
                epsilon,
            ))
        parameter_epsilon = epsilon / segment_2d.length
        parameters = _unique_sorted_parameters(
            parameters,
            parameter_epsilon,
        )

        for index in range(len(parameters) - 1):
            start_parameter = parameters[index]
            end_parameter = parameters[index + 1]
            if end_parameter - start_parameter <= parameter_epsilon:
                continue
            midpoint_parameter = (
                start_parameter + end_parameter
            ) * 0.5
            midpoint = start_2d + segment_2d * midpoint_parameter
            if not _point_in_polygon_2d(
                    midpoint, source_loop, epsilon, True):
                continue
            if not _segment_separates_prism_regions(
                    midpoint,
                    segment_2d,
                    origin,
                    axis_x,
                    axis_y,
                    prism,
                    epsilon):
                continue
            segment_3d = end_3d - start_3d
            clipped.append((
                start_3d + segment_3d * start_parameter,
                start_3d + segment_3d * end_parameter,
            ))

    return _deduplicate_segments(clipped, epsilon)


def _segment_separates_prism_regions(
        midpoint, direction, origin, axis_x, axis_y, prism, epsilon):
    perpendicular = Vector((-direction.y, direction.x)).normalized()
    maximum_distance = direction.length * 0.2
    for multiplier in (4.0, 16.0, 64.0):
        distance = min(epsilon * multiplier, maximum_distance)
        if distance <= epsilon:
            continue
        first = midpoint + perpendicular * distance
        second = midpoint - perpendicular * distance
        first_3d = origin + axis_x * first.x + axis_y * first.y
        second_3d = origin + axis_x * second.x + axis_y * second.y
        if prism.point_inside(first_3d) != prism.point_inside(second_3d):
            return True
    return False


def _segment_boundary_parameters(
        start, end, boundary_start, boundary_end, epsilon):
    direction = end - start
    boundary_direction = boundary_end - boundary_start
    denominator = _cross_2d(direction, boundary_direction)
    offset = boundary_start - start
    scale = max(1.0, direction.length * boundary_direction.length)

    if abs(denominator) > epsilon * scale:
        parameter = _cross_2d(offset, boundary_direction) / denominator
        boundary_parameter = _cross_2d(offset, direction) / denominator
        parameter_epsilon = epsilon / max(direction.length, epsilon)
        boundary_parameter_epsilon = (
            epsilon / max(boundary_direction.length, epsilon)
        )
        if (
                -parameter_epsilon <= parameter <= 1.0 + parameter_epsilon
                and
                -boundary_parameter_epsilon <= boundary_parameter <=
                1.0 + boundary_parameter_epsilon):
            return [max(0.0, min(1.0, parameter))]
        return []

    if abs(_cross_2d(offset, direction)) > epsilon * max(
            1.0, direction.length):
        return []

    length_squared = direction.length_squared
    if length_squared <= epsilon * epsilon:
        return []
    return [
        max(0.0, min(1.0, (point - start).dot(direction) / length_squared))
        for point in (boundary_start, boundary_end)
        if _point_on_segment_2d(point, start, end, epsilon)
    ]


def _unique_sorted_parameters(parameters, epsilon):
    sorted_parameters = sorted(parameters)
    unique = []
    for parameter in sorted_parameters:
        if unique and abs(parameter - unique[-1]) <= epsilon:
            continue
        unique.append(parameter)
    return unique


def _cross_2d(first, second):
    return first.x * second.y - first.y * second.x


def _canonical_graph_epsilon(faces, prism):
    points = [vertex.co for face in faces for vertex in face.verts]
    if points:
        minimum = Vector((
            min(point.x for point in points),
            min(point.y for point in points),
            min(point.z for point in points),
        ))
        maximum = Vector((
            max(point.x for point in points),
            max(point.y for point in points),
            max(point.z for point in points),
        ))
        host_extent = (maximum - minimum).length
    else:
        host_extent = 0.0
    return max(
        EPSILON * 10,
        host_extent * 1e-7,
        prism.cap_extent * 1e-7,
    )


def _prism_extent_scale(prism):
    """Return a translation-independent scale for intersection precision."""
    return max(prism.cap_extent, prism.extrusion.length)


def _face_neighbors(faces):
    face_set = set(faces)
    neighbors = {face: set() for face in faces}
    for face in faces:
        for edge in face.edges:
            neighbors[face].update(
                linked_face for linked_face in edge.link_faces
                if linked_face in face_set and linked_face is not face
            )
    return neighbors


def _face_graph_anchors(
        face, prism, split_verts, interior_verts):
    anchors = list(interior_verts)
    for vertex in face.verts:
        if (
                vertex in split_verts
                or (
                    prism.point_inside(vertex.co)
                    and not prism.point_strictly_inside(vertex.co)
                )):
            anchors.append(vertex)
    return _unique_valid_verts(anchors)


def _canonical_graph_node(
        nodes, anchor_nodes, point, face, face_anchors, face_neighbors,
        epsilon, canonical_epsilon):
    anchor = _nearest_graph_anchor(
        point,
        face_anchors,
        canonical_epsilon,
    )
    if anchor is not None:
        node = anchor_nodes.get(anchor)
        if node is not None:
            node.faces.add(face)
            return node

    node = _nearest_compatible_graph_node(
        nodes,
        point,
        face,
        face_neighbors,
        epsilon,
        canonical_epsilon,
    )
    if node is None:
        canonical_point = anchor.co if anchor is not None else point
        node = _CanonicalCutNode(canonical_point, face, anchor)
        nodes.append(node)
    else:
        node.faces.add(face)
        if anchor is not None and node.anchor_vertex is None:
            node.point = anchor.co.copy()
            node.anchor_vertex = anchor

    if anchor is not None:
        anchor_nodes[anchor] = node
    return node


def _nearest_graph_anchor(point, anchors, epsilon):
    nearest = None
    nearest_distance = epsilon
    for anchor in anchors:
        distance = (anchor.co - point).length
        if distance <= nearest_distance:
            nearest = anchor
            nearest_distance = distance
    return nearest


def _nearest_compatible_graph_node(
        nodes, point, face, face_neighbors, epsilon, canonical_epsilon):
    nearest = None
    nearest_distance = canonical_epsilon
    compatible_faces = face_neighbors[face] | {face}
    for node in nodes:
        if not node.faces & compatible_faces:
            continue
        distance = (node.point - point).length
        tolerance = canonical_epsilon
        if distance <= tolerance and distance <= nearest_distance:
            nearest = node
            nearest_distance = distance
    return nearest


def reconstruct_concave_prism_face(
        bm, source_face_verts, cut_candidate_verts, face_normal, prism,
        cut_segments_3d, suppressed_cap_indices, cut_graph):
    """Rebuild one source face from a constrained planar subdivision."""
    if len(source_face_verts) < 3:
        return ([], [])

    origin, axis_x, axis_y = _face_coordinate_system(
        source_face_verts, face_normal
    )

    def project(point):
        offset = Vector(point) - origin
        return Vector((offset.dot(axis_x), offset.dot(axis_y)))

    def unproject(point):
        return origin + axis_x * point.x + axis_y * point.y

    source_loop_2d = [
        project(cut_graph.canonical_point(vertex.co))
        for vertex in source_face_verts
    ]
    face_extent = _polygon_extent(source_loop_2d)
    epsilon = max(EPSILON, face_extent * 1e-8)

    cut_segments_2d = [
        (project(start), project(end))
        for start, end in cut_segments_3d
        if (end - start).length > epsilon
    ]

    coordinates = []
    coordinate_indices = {}

    def add_coordinate(point):
        key = _coordinate_key(point, epsilon)
        index = coordinate_indices.get(key)
        if index is not None:
            return index
        index = len(coordinates)
        coordinates.append(Vector(point).copy())
        coordinate_indices[key] = index
        return index

    source_indices = [
        add_coordinate(point) for point in source_loop_2d
    ]
    constraint_edges = set(_loop_index_pairs(source_indices))
    for start, end in cut_segments_2d:
        start_index = add_coordinate(start)
        end_index = add_coordinate(end)
        if start_index != end_index:
            constraint_edges.add(_ordered_index_pair(start_index, end_index))

    if len(coordinates) < 3:
        return ([], [])

    result = delaunay_2d_cdt(
        coordinates,
        list(constraint_edges),
        [],
        0,
        epsilon,
        False,
    )
    output_coordinates = result[0]
    output_faces = result[2]

    subdivision_faces = []
    subdivision_output_indices = set()
    degenerate_cell_count = 0
    for output_face in output_faces:
        if len(output_face) < 3:
            continue
        cell_points_2d = [
            output_coordinates[index] for index in output_face
        ]
        if polygon_has_negligible_area(
                [unproject(point) for point in cell_points_2d],
                epsilon):
            degenerate_cell_count += 1
            continue
        centroid_2d = _polygon_centroid_2d(
            cell_points_2d
        )
        if not _point_in_polygon_2d(
                centroid_2d, source_loop_2d, epsilon, True):
            continue
        if cut_graph.point_inside(
                unproject(centroid_2d), prism, suppressed_cap_indices):
            continue
        subdivision_faces.append(output_face)
        subdivision_output_indices.update(output_face)

    if degenerate_cell_count:
        debug_log(
            f"[PrismCut] Rejected {degenerate_cell_count} negligible-area "
            "CDT cells before face creation"
        )

    known_verts = _unique_valid_verts(
        list(source_face_verts) + list(cut_candidate_verts)
    )
    known_verts_2d = [
        (vertex, project(vertex.co)) for vertex in known_verts
    ]
    output_verts = {}
    for output_index in subdivision_output_indices:
        point_2d = output_coordinates[output_index]
        matching_vert = _nearest_matching_vert(
            point_2d,
            known_verts_2d,
            epsilon * 4,
        )
        if matching_vert is None:
            matching_vert = bm.verts.new(unproject(point_2d))
            known_verts_2d.append((matching_vert, point_2d.copy()))
        output_verts[output_index] = matching_vert

    created_faces = []
    created_face_keys = set()
    for output_face in subdivision_faces:
        face_verts = [output_verts[index] for index in output_face]
        face_key = frozenset(face_verts)
        if len(face_key) < 3 or face_key in created_face_keys:
            continue
        polygon_normal = compute_normal_from_verts(
            [vertex.co for vertex in face_verts]
        )
        if polygon_normal is None:
            continue
        if polygon_normal.dot(face_normal) < 0:
            face_verts.reverse()
        try:
            created_faces.append(bm.faces.new(face_verts))
            created_face_keys.add(face_key)
        except ValueError as error:
            debug_log(
                f"[PrismCut] Failed to create CDT face: {error}"
            )

    protected_segments = list(_loop_segments(source_loop_2d))
    protected_segments.extend(cut_segments_2d)
    connector_edges = {
        edge
        for face in created_faces
        for edge in face.edges
        if not _edge_lies_on_constraints(
            edge,
            protected_segments,
            project,
            epsilon * 4,
        )
    }
    graph_boundary_edges = {
        edge
        for face in created_faces
        for edge in face.edges
        if _edge_lies_on_constraints(
            edge,
            cut_segments_2d,
            project,
            epsilon * 4,
        )
    }
    cut_graph.register_boundary_edges(graph_boundary_edges)
    debug_log(
        f"[PrismCut] CDT reconstructed {len(created_faces)} cells with "
        f"{len(connector_edges)} removable connectors"
    )
    return (created_faces, list(connector_edges))


def _face_coordinate_system(source_face_verts, face_normal):
    origin = source_face_verts[0].co.copy()
    axis_x = None
    for index, vertex in enumerate(source_face_verts):
        following = source_face_verts[
            (index + 1) % len(source_face_verts)
        ]
        edge = following.co - vertex.co
        if edge.length > EPSILON:
            axis_x = edge.normalized()
            break
    if axis_x is None:
        raise ValueError("Source face has no non-zero boundary edge")
    axis_y = face_normal.cross(axis_x).normalized()
    return (origin, axis_x, axis_y)


def _prism_surface_plane_segments(prism, plane_point, plane_normal, epsilon):
    segments = []
    for polygon in prism.surface_polygons():
        distances = [
            (vertex - plane_point).dot(plane_normal)
            for vertex in polygon
        ]
        if all(abs(distance) <= epsilon for distance in distances):
            segments.extend(_loop_segments(polygon))
            continue

        for triangle in _tessellate_polygon_vertices(polygon):
            segment = _triangle_plane_segment(
                triangle,
                plane_point,
                plane_normal,
                epsilon,
            )
            if segment is not None:
                segments.append(segment)
    return _deduplicate_segments(
        _merge_touching_collinear_segments(segments, epsilon),
        epsilon,
    )


def _merge_touching_collinear_segments(segments, epsilon):
    """Remove tessellation-only vertices from plane intersection segments."""
    merged = [
        (start.copy(), end.copy())
        for start, end in segments
        if (end - start).length > epsilon
    ]

    changed = True
    while changed:
        changed = False
        for first_index, (first_start, first_end) in enumerate(merged):
            first_direction = first_end - first_start
            first_length = first_direction.length
            axis = first_direction / first_length

            for second_index in range(first_index + 1, len(merged)):
                second_start, second_end = merged[second_index]
                second_direction = second_end - second_start
                second_length = second_direction.length
                if (
                        first_direction.cross(second_direction).length >
                        epsilon * first_length * second_length):
                    continue
                if (
                        (second_start - first_start).cross(axis).length >
                        epsilon
                        or
                        (second_end - first_start).cross(axis).length >
                        epsilon):
                    continue

                second_start_distance = (
                    second_start - first_start
                ).dot(axis)
                second_end_distance = (
                    second_end - first_start
                ).dot(axis)
                minimum_distance = min(
                    0.0,
                    second_start_distance,
                    second_end_distance,
                )
                maximum_distance = max(
                    first_length,
                    second_start_distance,
                    second_end_distance,
                )
                if (
                        min(second_start_distance, second_end_distance) >
                        first_length + epsilon
                        or
                        max(second_start_distance, second_end_distance) <
                        -epsilon):
                    continue

                merged[first_index] = (
                    first_start + axis * minimum_distance,
                    first_start + axis * maximum_distance,
                )
                merged.pop(second_index)
                changed = True
                break

            if changed:
                break

    return merged


def _tessellate_polygon_vertices(polygon):
    triangles = tessellate_polygon([[vertex.copy() for vertex in polygon]])
    result = []
    for triangle in triangles:
        if triangle and isinstance(triangle[0], int):
            result.append([polygon[index] for index in triangle])
        else:
            result.append([Vector(coordinate) for coordinate in triangle])
    return result


def _triangle_plane_segment(
        triangle, plane_point, plane_normal, epsilon):
    points = []
    for index, start in enumerate(triangle):
        end = triangle[(index + 1) % len(triangle)]
        start_distance = (start - plane_point).dot(plane_normal)
        end_distance = (end - plane_point).dot(plane_normal)
        if abs(start_distance) <= epsilon:
            _append_unique_point(points, start, epsilon)
        if (
                start_distance > epsilon and end_distance < -epsilon
                or start_distance < -epsilon and end_distance > epsilon):
            fraction = start_distance / (start_distance - end_distance)
            intersection = start + (end - start) * fraction
            _append_unique_point(points, intersection, epsilon)
    if len(points) != 2:
        return None
    if (points[1] - points[0]).length <= epsilon:
        return None
    return (points[0], points[1])


def _append_unique_point(points, point, epsilon):
    if any((existing - point).length <= epsilon for existing in points):
        return
    points.append(point.copy())


def _deduplicate_segments(segments, epsilon):
    unique_segments = {}
    for start, end in segments:
        start_key = _coordinate_key(start, epsilon)
        end_key = _coordinate_key(end, epsilon)
        if start_key == end_key:
            continue
        key = tuple(sorted((start_key, end_key)))
        unique_segments.setdefault(key, (start.copy(), end.copy()))
    return list(unique_segments.values())


def _unique_valid_verts(verts):
    result = []
    seen = set()
    for vertex in verts:
        if not vertex.is_valid or vertex in seen:
            continue
        seen.add(vertex)
        result.append(vertex)
    return result


def _nearest_matching_vert(point, known_verts_2d, tolerance):
    best_vert = None
    best_distance = tolerance
    for vertex, known_point in known_verts_2d:
        distance = (known_point - point).length
        if distance <= best_distance:
            best_vert = vertex
            best_distance = distance
    return best_vert


def _edge_lies_on_constraints(
        edge, constraint_segments, project, epsilon):
    start = project(edge.verts[0].co)
    end = project(edge.verts[1].co)
    return any(
        _segment_lies_on_segment(start, end, boundary_start, boundary_end,
                                 epsilon)
        for boundary_start, boundary_end in constraint_segments
    )


def _segment_lies_on_segment(start, end, boundary_start, boundary_end,
                             epsilon):
    return (
        _point_on_segment_2d(start, boundary_start, boundary_end, epsilon)
        and _point_on_segment_2d(end, boundary_start, boundary_end, epsilon)
    )


def _point_in_polygon_2d(point, vertices, epsilon, include_boundary):
    if any(
            _point_on_segment_2d(
                point,
                start,
                vertices[(index + 1) % len(vertices)],
                epsilon,
            )
            for index, start in enumerate(vertices)):
        return include_boundary
    inside = False
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        if (start.y > point.y) == (end.y > point.y):
            continue
        crossing_x = (
            start.x
            + (point.y - start.y) * (end.x - start.x) /
            (end.y - start.y)
        )
        if crossing_x > point.x:
            inside = not inside
    return inside


def _point_on_segment_2d(point, start, end, epsilon):
    segment = end - start
    offset = point - start
    cross = segment.x * offset.y - segment.y * offset.x
    if abs(cross) > epsilon * max(1.0, segment.length):
        return False
    return (
        point.x >= min(start.x, end.x) - epsilon
        and point.x <= max(start.x, end.x) + epsilon
        and point.y >= min(start.y, end.y) - epsilon
        and point.y <= max(start.y, end.y) + epsilon
    )


def _polygon_centroid_2d(points):
    centroid = Vector((0.0, 0.0))
    for point in points:
        centroid += point
    return centroid / len(points)


def _polygon_extent(points):
    minimum_x = min(point.x for point in points)
    maximum_x = max(point.x for point in points)
    minimum_y = min(point.y for point in points)
    maximum_y = max(point.y for point in points)
    return Vector((maximum_x - minimum_x, maximum_y - minimum_y)).length


def _loop_segments(points):
    return [
        (point, points[(index + 1) % len(points)])
        for index, point in enumerate(points)
    ]


def _loop_index_pairs(indices):
    return {
        _ordered_index_pair(index, indices[(offset + 1) % len(indices)])
        for offset, index in enumerate(indices)
        if index != indices[(offset + 1) % len(indices)]
    }


def _ordered_index_pair(first, second):
    return (first, second) if first < second else (second, first)


def _coordinate_key(point, epsilon):
    return tuple(round(component / epsilon) for component in point)
