"""Planar face reconstruction for concave prism profiles."""

from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt, tessellate_polygon

from .convex_prism import EPSILON
from ...core.geometry import compute_normal_from_verts
from ...core.logging import debug_log


def reconstruct_concave_prism_face(
        bm, source_face_verts, cut_candidate_verts, face_normal, prism):
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

    source_loop_2d = [project(vertex.co) for vertex in source_face_verts]
    face_extent = _polygon_extent(source_loop_2d)
    epsilon = max(EPSILON, face_extent * 1e-8)

    cut_segments_3d = _prism_surface_plane_segments(
        prism,
        origin,
        face_normal,
        epsilon,
    )
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

    retained_faces = []
    retained_output_indices = set()
    for output_face in output_faces:
        if len(output_face) < 3:
            continue
        centroid_2d = _polygon_centroid_2d(
            [output_coordinates[index] for index in output_face]
        )
        if not _point_in_polygon_2d(
                centroid_2d, source_loop_2d, epsilon, True):
            continue
        if prism.point_inside(unproject(centroid_2d)):
            continue
        retained_faces.append(output_face)
        retained_output_indices.update(output_face)

    known_verts = _unique_valid_verts(
        list(source_face_verts) + list(cut_candidate_verts)
    )
    known_verts_2d = [
        (vertex, project(vertex.co)) for vertex in known_verts
    ]
    output_verts = {}
    for output_index in retained_output_indices:
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
    for output_face in retained_faces:
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
