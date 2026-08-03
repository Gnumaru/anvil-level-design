"""Read-only analysis for Cube Cut geometry.

This module owns the discovery needed before Cube Cut mutates a BMesh.
Geometry execution consumes the same convex-prism analysis that future previews
can use, keeping intersection rules in one place.
"""

from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from .prism import EPSILON
from ...core.logging import debug_log


class CubeCutCandidateMarker:
    """A predicted vertex location paired with one supporting face normal.

    A new vertex on a sharp edge can have more than one marker entry because
    each affected face supplies a different plane for orienting the preview X.
    Coplanar face entries are deduplicated later by point and normal.
    """

    def __init__(self, point, face_normal):
        self.point = point
        self.face_normal = face_normal


class CubeCutAnalysis:
    """Read-only result describing what a Cube Cut would operate on.

    The face and edge collections contain live BMesh references so geometry
    execution can consume this result without rediscovering intersections.
    candidate_vertex_points is the coordinate-only view used for counts; its
    points are in mesh-local space and are unique by position. The corresponding
    candidate_vertex_markers retain supporting face normals for preview drawing.
    """

    def __init__(self, prism, any_faces_selected, faces_fully_inside,
                 faces_to_cut, face_interior_points, edge_splits,
                 skipped_unselected_count, candidate_vertex_points,
                 candidate_vertex_markers):
        self.prism = prism
        self.any_faces_selected = any_faces_selected
        self.faces_fully_inside = faces_fully_inside
        self.faces_to_cut = faces_to_cut
        self.face_interior_points = face_interior_points
        self.edge_splits = edge_splits
        self.skipped_unselected_count = skipped_unselected_count
        self.candidate_vertex_points = candidate_vertex_points
        self.candidate_vertex_markers = candidate_vertex_markers


def face_is_available(face):
    return face.is_valid and not face.hide


def should_process_face(face, any_faces_selected):
    if not face_is_available(face):
        return False
    if any_faces_selected and not face.select:
        return False
    return True


def analyze_convex_prism_cut(bm, prism):
    """Return all convex-prism cut classifications without mutating BMesh."""
    bm.faces.ensure_lookup_table()

    # Apply selection filter: only process selected faces (or all if none selected)
    any_faces_selected = any(
        face.select for face in bm.faces if face_is_available(face)
    )
    if any_faces_selected:
        debug_log("[CubeCut] Selection mode: only processing selected faces")
    else:
        debug_log("[CubeCut] No faces selected: processing all faces")

    # === PRE-STEP: Identify faces to delete (all vertices inside prism) ===
    debug_log("\n[CubeCut] === PRE-STEP: Find faces entirely inside prism ===")
    faces_fully_inside = set()
    for face in bm.faces:
        if not should_process_face(face, any_faces_selected):
            continue

        # Check if ALL vertices are inside the prism
        if all(prism.point_inside(vert.co) for vert in face.verts):
            faces_fully_inside.add(face)
            debug_log(
                f"[CubeCut] Face {face.index} has all vertices inside prism "
                "- will be deleted"
            )

    remaining_faces = [
        face for face in bm.faces
        if face_is_available(face) and face not in faces_fully_inside
    ]

    # === STEP 1: Determine which faces will be cut ===
    # A face needs cutting if:
    # 1. Any prism edge pierces the face interior, OR
    # 2. Any face edge crosses a prism plane (prism wider than face case)
    debug_log("\n[CubeCut] === STEP 1: Find faces to cut ===")
    face_interior_points = _find_prism_face_intersections(
        remaining_faces, prism
    )

    # Determine which faces will actually be cut
    faces_to_cut = set()
    skipped_unselected_count = 0

    # First: add faces with interior intersections (prism edges pierce face)
    for face, points in face_interior_points.items():
        if not points:
            continue

        # Only cut selected faces (unless no faces are selected, then cut all)
        if any_faces_selected and not face.select:
            skipped_unselected_count += 1
            debug_log(f"[CubeCut] Face {face.index} skipped (not selected)")
            continue

        faces_to_cut.add(face)
        debug_log(
            f"[CubeCut] Face {face.index} will be cut "
            f"({len(points)} interior points)"
        )

    zero_depth = prism.is_effectively_zero_depth()

    # Second: check for faces where face edges cross prism planes
    # Only mark for cutting if crossings are on at least 2 DIFFERENT edges (cube passes through)
    # If all crossings are on the same edge, it's just an edge that got split multiple times
    for face in remaining_faces:
        if not should_process_face(face, any_faces_selected):
            continue
        if face in faces_to_cut:
            continue  # Already marked for cutting

        # Skip faces coplanar with side boundary planes only if they face into
        # the prism. Outward-facing coplanar faces sit on
        # the boundary and need to be cut/deleted (e.g. the top face of a
        # cube when the cut is flush with the top).
        if _is_coplanar_side_inward(face, prism):
            debug_log(
                f"[CubeCut] Face {face.index} is coplanar with prism side "
                "boundary (inward) - skipping"
            )
            continue

        # Zero-depth cuts only affect faces coplanar with the depth planes.
        debug_log(
            f"[CubeCut] Face {face.index} zero_depth={zero_depth}"
        )
        if zero_depth and not _is_coplanar_depth(face, prism):
            debug_log(
                f"[CubeCut] Face {face.index} skipped - zero-depth cut only "
                "affects coplanar faces"
            )
            continue

        # Collect all edges that have crossings within prism bounds
        edges_with_crossings = _find_face_edges_with_crossings(face, prism)

        # Only mark for cutting if crossings are on at least 2 different edges
        # (meaning cube actually passes through the face, not just touches one edge)
        if len(edges_with_crossings) >= 2:
            faces_to_cut.add(face)

            # Initialize empty interior points list for this face
            face_interior_points.setdefault(face, [])
            debug_log(
                f"[CubeCut] Face {face.index} will be cut "
                f"({len(edges_with_crossings)} edges have crossings)"
            )
        elif len(edges_with_crossings) == 1:
            debug_log(
                f"[CubeCut] Face {face.index} has only 1 edge with crossings "
                "- NOT cutting (just edge split)"
            )

    # Third: check for faces where any vertex is inside the prism
    # (face partially overlaps prism but prism vertices land on existing edges)
    for face in remaining_faces:
        if not should_process_face(face, any_faces_selected):
            continue
        if face in faces_to_cut:
            continue

        # Skip faces coplanar with a prism side boundary only if inward.
        if _is_coplanar_side_inward(face, prism):
            debug_log(
                f"[CubeCut] Face {face.index} is coplanar with prism side "
                "boundary (inward) - skipping vertex check"
            )
            continue

        # Zero-depth: only cut faces coplanar with depth planes (same guard as Second)
        if zero_depth and not _is_coplanar_depth(face, prism):
            debug_log(
                f"[CubeCut] Face {face.index} skipped - zero-depth cut only "
                "affects coplanar faces"
            )
            continue

        if any(prism.point_inside(vert.co) for vert in face.verts):
            faces_to_cut.add(face)

            # Initialize empty interior points list for this face
            face_interior_points.setdefault(face, [])
            debug_log(
                f"[CubeCut] Face {face.index} will be cut (vertex inside prism)"
            )

    debug_log(f"[CubeCut] Faces to be cut: {len(faces_to_cut)}")
    if skipped_unselected_count > 0:
        debug_log(
            f"[CubeCut] Skipped {skipped_unselected_count} unselected faces"
        )

    edge_splits = _find_edge_plane_intersections(bm, prism, faces_to_cut)
    candidate_vertex_points, candidate_vertex_markers = (
        _collect_candidate_vertex_data(
            face_interior_points,
            faces_to_cut,
            edge_splits,
        )
    )

    return CubeCutAnalysis(
        prism,
        any_faces_selected,
        faces_fully_inside,
        faces_to_cut,
        face_interior_points,
        edge_splits,
        skipped_unselected_count,
        candidate_vertex_points,
        candidate_vertex_markers,
    )


def analyze_cube_cut(bm, prism):
    """Compatibility wrapper for existing Cube Cut callers."""
    return analyze_convex_prism_cut(bm, prism)


def _is_coplanar_side_inward(face, prism):
    """Test whether a face lies on a side plane and faces into the prism.

    Outward-facing coplanar faces sit on the boundary and need to be
    cut/deleted (e.g. the top face of a cube when the cut is flush with the
    top). Only inward-facing coplanar faces are skipped.
    """
    for plane_index in prism.side_plane_indices:
        plane_point, plane_normal = prism.planes[plane_index]
        if all(
            abs((vert.co - plane_point).dot(plane_normal)) <= EPSILON
            for vert in face.verts
        ):
            return face.normal.dot(plane_normal) < 0
    return False


def _is_coplanar_side_outward(face, prism):
    """Test whether a face lies on and faces out of a prism side plane."""
    for plane_index in prism.side_plane_indices:
        plane_point, plane_normal = prism.planes[plane_index]
        if all(
            abs((vert.co - plane_point).dot(plane_normal)) <= EPSILON
            for vert in face.verts
        ):
            return face.normal.dot(plane_normal) > 0
    return False


def _is_coplanar_depth(face, prism):
    """Test whether a face is coplanar with either prism cap plane."""
    for plane_index in prism.cap_plane_indices:
        plane_point, plane_normal = prism.planes[plane_index]
        if all(
            abs((vert.co - plane_point).dot(plane_normal)) <= EPSILON
            for vert in face.verts
        ):
            return True
    return False


def _find_face_edges_with_crossings(face, prism):
    """Collect face edges whose plane crossings are within prism bounds."""
    edges_with_crossings = set()
    for edge in face.edges:
        v1_co = edge.verts[0].co
        v2_co = edge.verts[1].co

        for plane_idx, (plane_point, plane_normal) in enumerate(prism.planes):
            d1 = (v1_co - plane_point).dot(plane_normal)
            d2 = (v2_co - plane_point).dot(plane_normal)

            # Edge crosses plane if endpoints are on strictly opposite sides
            crosses = (
                (d1 > EPSILON and d2 < -EPSILON) or
                (d1 < -EPSILON and d2 > EPSILON)
            )
            if not crosses:
                continue

            intersection = intersect_line_plane(
                v1_co, v2_co, plane_point, plane_normal
            )
            if intersection is None:
                continue

            # Check if intersection is within prism bounds on this plane
            if prism.point_within_plane_bounds(intersection, plane_idx):
                edges_with_crossings.add(edge)
                debug_log(
                    f"[CubeCut] Face {face.index} edge crosses prism "
                    f"plane {plane_idx}"
                )
                break  # This edge has a crossing, check next edge

    return edges_with_crossings


def _find_edge_plane_intersections(bm, prism, faces_to_cut):
    """
    Find all points where mesh edges cross prism planes.

    Only considers edges that belong to at least one face in faces_to_cut.
    This prevents adding edge splits to faces that won't actually be cut.

    Args:
        bm: BMesh
        prism: ConvexPrism instance
        faces_to_cut: Set of BMFace that will be cut

    Returns:
        dict: edge -> list of (intersection_point, plane_idx, t)
    """
    edge_splits = {}
    debug_log(f"[CubeCut] Checking {len(bm.edges)} edges for plane intersections")

    for edge in bm.edges:
        if not edge.is_valid:
            continue

        # Only split edges that belong to faces that will be cut
        if not any(face in faces_to_cut for face in edge.link_faces):
            continue

        v1_co = edge.verts[0].co
        v2_co = edge.verts[1].co
        intersections = []

        for plane_idx, (plane_point, plane_normal) in enumerate(prism.planes):
            d1 = (v1_co - plane_point).dot(plane_normal)
            d2 = (v2_co - plane_point).dot(plane_normal)

            # Edge crosses plane only if endpoints are on strictly opposite sides
            crosses = (
                (d1 > EPSILON and d2 < -EPSILON) or
                (d1 < -EPSILON and d2 > EPSILON)
            )
            if not crosses:
                continue

            intersection = intersect_line_plane(
                v1_co, v2_co, plane_point, plane_normal
            )
            if intersection is None:
                continue

            # Check if intersection is within prism bounds on this plane
            if not prism.point_within_plane_bounds(intersection, plane_idx):
                debug_log(
                    f"[CubeCut] Edge {edge.index} crosses plane {plane_idx} "
                    f"at {intersection} but OUTSIDE bounds"
                )
                continue

            # Calculate parameter t along edge (for ordering multiple splits)
            edge_vector = v2_co - v1_co
            t = (
                (intersection - v1_co).dot(edge_vector) /
                edge_vector.length_squared
            )
            intersections.append((intersection.copy(), plane_idx, t))
            debug_log(
                f"[CubeCut] Edge {edge.index} ({v1_co} -> {v2_co}) crosses "
                f"plane {plane_idx} at {intersection}, t={t:.3f}"
            )

        if intersections:
            # Sort by t parameter so we split in order from v1 to v2
            intersections.sort(key=lambda item: item[2])
            edge_splits[edge] = intersections

    return edge_splits


def _find_prism_face_intersections(faces, prism):
    """
    Find where prism edges pierce mesh face interiors.

    Returns:
        dict: BMFace -> list of intersection points
    """
    face_interior_points = {}

    prism_vertices = prism.vertices
    prism_edges = prism.edge_indices

    debug_log(
        f"[CubeCut] Checking {len(faces)} faces for prism-face intersections"
    )
    debug_log(
        f"[CubeCut] Prism vertices: {[str(vertex) for vertex in prism_vertices]}"
    )

    for face in faces:
        face_normal = face.normal
        if face_normal.length < EPSILON:
            continue
        face_point = face.verts[0].co
        face_vertices = [vert.co for vert in face.verts]
        face_parallel_to_depth = prism.face_normal_parallel_to_caps(
            face_normal
        )
        face_on_inward_side = _is_coplanar_side_inward(face, prism)
        face_on_outward_side = _is_coplanar_side_outward(face, prism)

        for edge_index, (v1_index, v2_index) in enumerate(prism_edges):
            edge_start = prism_vertices[v1_index]
            edge_end = prism_vertices[v2_index]
            d1 = (edge_start - face_point).dot(face_normal)
            d2 = (edge_end - face_point).dot(face_normal)

            # A prism edge can lie entirely in the host face plane. This
            # happens when an edge-radius cylinder is centered on a mesh edge:
            # the cylinder's longitudinal edges on that plane contain cut
            # corners that strict plane-crossing checks cannot discover.
            if abs(d1) <= EPSILON and abs(d2) <= EPSILON:
                if face_on_inward_side:
                    continue
                boundary_epsilon = _intersection_boundary_epsilon(prism)
                for endpoint in (edge_start, edge_end):
                    if not _point_in_face_interior(
                            endpoint,
                            face_vertices,
                            face_normal,
                            boundary_epsilon):
                        continue
                    if _append_face_interior_point(
                            face_interior_points, face, endpoint):
                        debug_log(
                            f"[CubeCut] Prism edge {edge_index} lies in face "
                            f"{face.index}; added interior endpoint {endpoint}"
                        )
                continue

            # Check if edge crosses the face plane (endpoints on opposite sides)
            crosses = (
                (d1 > EPSILON and d2 < -EPSILON) or
                (d1 < -EPSILON and d2 > EPSILON)
            )

            # Special case: endpoint ON the face plane, other endpoint on one
            # side. Allow this for cap-parallel faces and for outward-facing
            # mesh faces coplanar with a prism side plane.
            endpoint_on_face = None
            if not crosses:
                if face_parallel_to_depth or face_on_outward_side:
                    # Check if one endpoint is on the face plane
                    if abs(d1) <= EPSILON and abs(d2) > EPSILON:
                        # edge_start (v1) is on the face plane
                        endpoint_on_face = edge_start.copy()
                        debug_log(
                            f"[CubeCut] Prism vertex {v1_index} is ON face "
                            f"{face.index} (boundary-aligned)"
                        )
                    elif abs(d2) <= EPSILON and abs(d1) > EPSILON:
                        # edge_end (v2) is on the face plane
                        endpoint_on_face = edge_end.copy()
                        debug_log(
                            f"[CubeCut] Prism vertex {v2_index} is ON face "
                            f"{face.index} (boundary-aligned)"
                        )

            if not crosses and endpoint_on_face is None:
                debug_log(
                    f"[CubeCut] Prism edge {edge_index} "
                    f"({v1_index}->{v2_index}) did NOT cross face "
                    f"{face.index}: d1={d1:.4f}, d2={d2:.4f}"
                )
                continue

            # Determine intersection point
            if endpoint_on_face is not None:
                intersection = endpoint_on_face
            else:
                intersection = intersect_line_plane(
                    edge_start, edge_end, face_point, face_normal
                )
                if intersection is None:
                    continue

            debug_log(
                f"[CubeCut] Prism edge {edge_index} intersects face "
                f"{face.index} plane at {intersection}"
            )

            # Check if inside face polygon (not on edge)
            in_polygon = _point_in_polygon(
                intersection, face_vertices, face_normal
            )
            in_interior = _point_in_face_interior(
                intersection,
                face_vertices,
                face_normal,
                _intersection_boundary_epsilon(prism),
            )
            debug_log(
                f"[CubeCut]   in_polygon={in_polygon}, "
                f"in_interior={in_interior}"
            )
            if not in_interior:
                continue

            if _append_face_interior_point(
                    face_interior_points, face, intersection):
                debug_log("[CubeCut]   Added interior intersection!")

    debug_log(
        f"[CubeCut] Found {len(face_interior_points)} faces with interior "
        "intersections"
    )
    return face_interior_points


def _append_face_interior_point(face_interior_points, face, point):
    """Append one face-interior point unless that position is already known."""
    points = face_interior_points.setdefault(face, [])
    point_key = _point_key(point)
    if any(_point_key(existing) == point_key for existing in points):
        return False
    points.append(point.copy())
    return True


def _collect_candidate_vertex_data(face_interior_points, faces_to_cut,
                                   edge_splits):
    """Collect predicted vertex locations and their supporting face planes."""
    unique_points = {}
    unique_markers = {}

    # Interior points become new vertices when their host face is rebuilt.
    for face in faces_to_cut:
        for point in face_interior_points.get(face, []):
            _add_candidate_vertex(
                unique_points, unique_markers, point, face.normal
            )

    # Edge intersections at an existing endpoint record prism-plane membership
    # during execution, but do not create a new vertex.
    for edge, intersections in edge_splits.items():
        endpoint_a = edge.verts[0].co
        endpoint_b = edge.verts[1].co
        for point, _plane_index, _t in intersections:
            if (point - endpoint_a).length < EPSILON:
                continue
            if (point - endpoint_b).length < EPSILON:
                continue

            # The edge is split once, so its position stays unique. Keep a
            # marker orientation for each distinct affected face plane. This
            # lets a sharp edge show the prediction on both of its faces while
            # coplanar faces still collapse to one X.
            for face in edge.link_faces:
                if face not in faces_to_cut or not face_is_available(face):
                    continue
                _add_candidate_vertex(
                    unique_points, unique_markers, point, face.normal
                )

    return (list(unique_points.values()), list(unique_markers.values()))


def _add_candidate_vertex(unique_points, unique_markers, point, face_normal):
    """Add one coordinate and one distinct face-oriented marker entry."""
    point_key = _point_key(point)
    unique_points.setdefault(point_key, point.copy())

    if face_normal.length < EPSILON:
        return

    normalized_normal = face_normal.normalized()
    marker_key = point_key + _face_plane_key(normalized_normal)
    unique_markers.setdefault(
        marker_key,
        CubeCutCandidateMarker(point.copy(), normalized_normal.copy()),
    )


def _point_key(point):
    return (round(point.x, 5), round(point.y, 5), round(point.z, 5))


def _face_plane_key(normal):
    """Return one orientation key for parallel normals in either direction."""
    normal_key = _point_key(normal)
    opposite_key = tuple(-component for component in normal_key)
    return min(normal_key, opposite_key)


def _intersection_boundary_epsilon(prism):
    """Return tolerance for classifying prism-face intersections near edges."""
    return max(EPSILON * 10, prism.cap_extent * 1e-7)


def _point_in_face_interior(point, face_vertices, face_normal,
                            boundary_epsilon):
    """
    Test if a point is strictly inside a face (not on edges).
    """
    if len(face_vertices) < 3:
        return False

    # First check if point is in the polygon at all
    if not _point_in_polygon(point, face_vertices, face_normal):
        return False

    # Check distance to all edges - must not be too close
    for index, v1 in enumerate(face_vertices):
        v2 = face_vertices[(index + 1) % len(face_vertices)]

        # Distance from point to edge
        edge_vector = v2 - v1
        edge_length_squared = edge_vector.length_squared
        if edge_length_squared < EPSILON * EPSILON:
            continue

        t = max(
            0,
            min(1, (point - v1).dot(edge_vector) / edge_length_squared),
        )
        closest = v1 + edge_vector * t
        if (point - closest).length < boundary_epsilon:  # Too close to edge
            return False

    return True


def _point_in_polygon(point, face_vertices, face_normal):
    """Test if a point is inside a polygon using ray casting."""
    if len(face_vertices) < 3:
        return False

    normal_absolute = Vector([abs(component) for component in face_normal])
    if (
        normal_absolute.x >= normal_absolute.y and
        normal_absolute.x >= normal_absolute.z
    ):
        def to_2d(value):
            return (value.y, value.z)
    elif normal_absolute.y >= normal_absolute.z:
        def to_2d(value):
            return (value.x, value.z)
    else:
        def to_2d(value):
            return (value.x, value.y)

    point_x, point_y = to_2d(point)
    inside = False
    previous_index = len(face_vertices) - 1

    for index, vertex in enumerate(face_vertices):
        x_current, y_current = to_2d(vertex)
        x_previous, y_previous = to_2d(face_vertices[previous_index])
        crosses = (
            (y_current > point_y) != (y_previous > point_y) and
            point_x < (
                (x_previous - x_current) *
                (point_y - y_current) /
                (y_previous - y_current) +
                x_current
            )
        )
        if crosses:
            inside = not inside
        previous_index = index

    return inside
