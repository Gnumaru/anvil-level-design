"""Read-only analysis for Cube Cut geometry.

This module owns the cuboid representation and all discovery needed before
Cube Cut mutates a BMesh.  Geometry execution consumes the same analysis that
future previews can use, keeping intersection rules in one place.
"""

from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from ...core.logging import debug_log


# Epsilon for floating point comparisons
EPSILON = 1e-5


class CuboidPlanes:
    """Represents the 6 planes of a cuboid for intersection testing."""

    def __init__(self, first_vertex, second_vertex, depth,
                 local_x, local_y, local_z):
        # Calculate rectangle dimensions
        diff = second_vertex - first_vertex
        dx = diff.dot(local_x)
        dy = diff.dot(local_y)

        # Normalize direction so min/max work correctly
        if dx < 0:
            dx = -dx
            local_x = -local_x
        if dy < 0:
            dy = -dy
            local_y = -local_y

        # Handle depth direction
        if depth < 0:
            self.depth_min = depth
            self.depth_max = 0
        else:
            self.depth_min = 0
            self.depth_max = depth

        self.local_x = local_x
        self.local_y = local_y
        self.local_z = local_z
        self.dx = dx
        self.dy = dy
        self.origin = first_vertex.copy()

        # Track which plane index is the "rectangle plane" (at depth=0)
        if depth >= 0:
            self.rectangle_plane_idx = 0
        else:
            self.rectangle_plane_idx = 1

        # Build the 6 planes: (point_on_plane, outward_normal)
        self.planes = self._build_planes()

    def _build_planes(self):
        """Build the 6 bounding planes with outward-pointing normals."""
        return [
            # Plane 0: "Front" at depth_min
            (
                self.origin + self.local_z * self.depth_min,
                -self.local_z.copy(),
            ),
            # Plane 1: "Back" at depth_max
            (
                self.origin + self.local_z * self.depth_max,
                self.local_z.copy(),
            ),
            # Plane 2: "Left" at x=0
            (
                self.origin.copy(),
                -self.local_x.copy(),
            ),
            # Plane 3: "Right" at x=dx
            (
                self.origin + self.local_x * self.dx,
                self.local_x.copy(),
            ),
            # Plane 4: "Bottom" at y=0
            (
                self.origin.copy(),
                -self.local_y.copy(),
            ),
            # Plane 5: "Top" at y=dy
            (
                self.origin + self.local_y * self.dy,
                self.local_y.copy(),
            ),
        ]

    def point_inside(self, point):
        """Test if a point is inside the cuboid (or on boundary)."""
        local = self.to_local(point)
        x, y, z = local.x, local.y, local.z

        return (
            -EPSILON <= x <= self.dx + EPSILON and
            -EPSILON <= y <= self.dy + EPSILON and
            self.depth_min - EPSILON <= z <= self.depth_max + EPSILON
        )

    def point_strictly_inside(self, point):
        """Test if a point is strictly inside the cuboid (not on boundary)."""
        local = self.to_local(point)
        x, y, z = local.x, local.y, local.z

        return (
            EPSILON < x < self.dx - EPSILON and
            EPSILON < y < self.dy - EPSILON and
            self.depth_min + EPSILON < z < self.depth_max - EPSILON
        )

    def point_on_surface(self, point):
        """Test if a point is on the cuboid surface (on boundary, not strictly inside)."""
        return self.point_inside(point) and not self.point_strictly_inside(point)

    def to_local(self, point):
        """Convert point to local cuboid coordinates (x, y, z)."""
        offset = point - self.origin
        return Vector((
            offset.dot(self.local_x),
            offset.dot(self.local_y),
            offset.dot(self.local_z),
        ))


class CubeCutAnalysis:
    """Read-only result describing what a Cube Cut would operate on.

    The face and edge collections contain live BMesh references so geometry
    execution can consume this result without rediscovering intersections.
    candidate_vertex_points is the coordinate-only view intended for a future
    preview; its points are in mesh-local space and are unique by position.
    """

    def __init__(self, cuboid, any_faces_selected, faces_fully_inside,
                 faces_to_cut, face_interior_points, edge_splits,
                 skipped_unselected_count, candidate_vertex_points):
        self.cuboid = cuboid
        self.any_faces_selected = any_faces_selected
        self.faces_fully_inside = faces_fully_inside
        self.faces_to_cut = faces_to_cut
        self.face_interior_points = face_interior_points
        self.edge_splits = edge_splits
        self.skipped_unselected_count = skipped_unselected_count
        self.candidate_vertex_points = candidate_vertex_points


def build_cube_cut_cuboid(matrix_world, first_vertex, second_vertex, depth,
                          local_x, local_y, local_z):
    """Build the mesh-local cuboid used by analysis and execution."""
    # Handle zero depth
    effective_depth = depth
    if abs(depth) < EPSILON:
        effective_depth = EPSILON * 2 if depth >= 0 else -EPSILON * 2

    # Transform cuboid to object local space
    world_to_local = matrix_world.inverted()
    world_to_local_rotation = world_to_local.to_3x3()

    local_first = world_to_local @ first_vertex
    local_second = world_to_local @ second_vertex
    local_x_transformed = (world_to_local_rotation @ local_x).normalized()
    local_y_transformed = (world_to_local_rotation @ local_y).normalized()
    local_z_transformed = (world_to_local_rotation @ local_z).normalized()

    scale_factor = (world_to_local_rotation @ local_z).length
    local_depth = effective_depth * scale_factor

    return CuboidPlanes(
        local_first,
        local_second,
        local_depth,
        local_x_transformed,
        local_y_transformed,
        local_z_transformed,
    )


def face_is_available(face):
    return face.is_valid and not face.hide


def should_process_face(face, any_faces_selected):
    if not face_is_available(face):
        return False
    if any_faces_selected and not face.select:
        return False
    return True


def analyze_cube_cut(bm, cuboid):
    """Return all Cube Cut classifications without mutating the BMesh."""
    bm.faces.ensure_lookup_table()

    # Apply selection filter: only process selected faces (or all if none selected)
    any_faces_selected = any(
        face.select for face in bm.faces if face_is_available(face)
    )
    if any_faces_selected:
        debug_log("[CubeCut] Selection mode: only processing selected faces")
    else:
        debug_log("[CubeCut] No faces selected: processing all faces")

    # === PRE-STEP: Identify faces to delete (all vertices inside cuboid) ===
    debug_log("\n[CubeCut] === PRE-STEP: Find faces entirely inside cuboid ===")
    faces_fully_inside = set()
    for face in bm.faces:
        if not should_process_face(face, any_faces_selected):
            continue

        # Check if ALL vertices are inside the cuboid
        if all(cuboid.point_inside(vert.co) for vert in face.verts):
            faces_fully_inside.add(face)
            debug_log(
                f"[CubeCut] Face {face.index} has all vertices inside cuboid "
                "- will be deleted"
            )

    remaining_faces = [
        face for face in bm.faces
        if face_is_available(face) and face not in faces_fully_inside
    ]

    # === STEP 1: Determine which faces will be cut ===
    # A face needs cutting if:
    # 1. Any cuboid edge pierces the face interior, OR
    # 2. Any face edge crosses a cuboid plane (cube wider than face case)
    debug_log("\n[CubeCut] === STEP 1: Find faces to cut ===")
    face_interior_points = _find_cuboid_face_intersections(
        remaining_faces, cuboid
    )

    # Determine which faces will actually be cut
    faces_to_cut = set()
    skipped_unselected_count = 0

    # First: add faces with interior intersections (cuboid corners pierce face)
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

    zero_depth = (
        abs(cuboid.depth_max - cuboid.depth_min) <= EPSILON * 3
    )

    # Second: check for faces where face edges cross cuboid planes (cube wider than face)
    # Only mark for cutting if crossings are on at least 2 DIFFERENT edges (cube passes through)
    # If all crossings are on the same edge, it's just an edge that got split multiple times
    for face in remaining_faces:
        if not should_process_face(face, any_faces_selected):
            continue
        if face in faces_to_cut:
            continue  # Already marked for cutting

        # Skip faces coplanar with side boundary planes (2-5) only if
        # they face into the cuboid.  Outward-facing coplanar faces sit on
        # the boundary and need to be cut/deleted (e.g. the top face of a
        # cube when the cut is flush with the top).
        if _is_coplanar_side_inward(face, cuboid):
            debug_log(
                f"[CubeCut] Face {face.index} is coplanar with cuboid side "
                "boundary (inward) - skipping"
            )
            continue

        # Zero-depth cuts only affect faces coplanar with the depth planes.
        debug_log(
            f"[CubeCut] Face {face.index} zero_depth={zero_depth} "
            f"(depth_min={cuboid.depth_min}, depth_max={cuboid.depth_max})"
        )
        if zero_depth and not _is_coplanar_depth(face, cuboid):
            debug_log(
                f"[CubeCut] Face {face.index} skipped - zero-depth cut only "
                "affects coplanar faces"
            )
            continue

        # Collect all edges that have crossings within cuboid bounds
        edges_with_crossings = _find_face_edges_with_crossings(face, cuboid)

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

    # Third: check for faces where any vertex is inside the cuboid
    # (face partially overlaps cuboid but cuboid vertices land on existing edges)
    for face in remaining_faces:
        if not should_process_face(face, any_faces_selected):
            continue
        if face in faces_to_cut:
            continue

        # Skip faces coplanar with a cuboid side boundary only if inward (same guard as Second)
        if _is_coplanar_side_inward(face, cuboid):
            debug_log(
                f"[CubeCut] Face {face.index} is coplanar with cuboid side "
                "boundary (inward) - skipping vertex check"
            )
            continue

        # Zero-depth: only cut faces coplanar with depth planes (same guard as Second)
        if zero_depth and not _is_coplanar_depth(face, cuboid):
            debug_log(
                f"[CubeCut] Face {face.index} skipped - zero-depth cut only "
                "affects coplanar faces"
            )
            continue

        if any(cuboid.point_inside(vert.co) for vert in face.verts):
            faces_to_cut.add(face)

            # Initialize empty interior points list for this face
            face_interior_points.setdefault(face, [])
            debug_log(
                f"[CubeCut] Face {face.index} will be cut (vertex inside cuboid)"
            )

    debug_log(f"[CubeCut] Faces to be cut: {len(faces_to_cut)}")
    if skipped_unselected_count > 0:
        debug_log(
            f"[CubeCut] Skipped {skipped_unselected_count} unselected faces"
        )

    edge_splits = _find_edge_plane_intersections(bm, cuboid, faces_to_cut)
    candidate_vertex_points = _collect_candidate_vertex_points(
        face_interior_points, faces_to_cut, edge_splits
    )

    return CubeCutAnalysis(
        cuboid,
        any_faces_selected,
        faces_fully_inside,
        faces_to_cut,
        face_interior_points,
        edge_splits,
        skipped_unselected_count,
        candidate_vertex_points,
    )


def _is_coplanar_side_inward(face, cuboid):
    """Test whether a face lies on a side plane and faces into the cuboid.

    Outward-facing coplanar faces sit on the boundary and need to be
    cut/deleted (e.g. the top face of a cube when the cut is flush with the
    top). Only inward-facing coplanar faces are skipped.
    """
    for plane_point, plane_normal in cuboid.planes[2:]:
        if all(
            abs((vert.co - plane_point).dot(plane_normal)) <= EPSILON
            for vert in face.verts
        ):
            return face.normal.dot(plane_normal) < 0
    return False


def _is_coplanar_depth(face, cuboid):
    """Test whether a face is coplanar with either cuboid depth plane."""
    for plane_point, plane_normal in cuboid.planes[:2]:
        if all(
            abs((vert.co - plane_point).dot(plane_normal)) <= EPSILON
            for vert in face.verts
        ):
            return True
    return False


def _find_face_edges_with_crossings(face, cuboid):
    """Collect face edges whose plane crossings are within cuboid bounds."""
    edges_with_crossings = set()
    for edge in face.edges:
        v1_co = edge.verts[0].co
        v2_co = edge.verts[1].co

        for plane_idx, (plane_point, plane_normal) in enumerate(cuboid.planes):
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

            # Check if intersection is within cuboid bounds on this plane
            if _point_within_plane_bounds(intersection, plane_idx, cuboid):
                edges_with_crossings.add(edge)
                debug_log(
                    f"[CubeCut] Face {face.index} edge crosses cuboid "
                    f"plane {plane_idx}"
                )
                break  # This edge has a crossing, check next edge

    return edges_with_crossings


def _find_edge_plane_intersections(bm, cuboid, faces_to_cut):
    """
    Find all points where mesh edges cross cuboid planes.

    Only considers edges that belong to at least one face in faces_to_cut.
    This prevents adding edge splits to faces that won't actually be cut.

    Args:
        bm: BMesh
        cuboid: CuboidPlanes instance
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

        for plane_idx, (plane_point, plane_normal) in enumerate(cuboid.planes):
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

            # Check if intersection is within cuboid bounds on this plane
            if not _point_within_plane_bounds(intersection, plane_idx, cuboid):
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


def _find_cuboid_face_intersections(faces, cuboid):
    """
    Find where cuboid edges pierce mesh face interiors.

    Returns:
        dict: BMFace -> list of intersection points
    """
    face_interior_points = {}

    # Build cuboid vertices and edges
    cuboid_vertices = _build_cuboid_vertices_local(cuboid)
    cuboid_edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),  # Front face
        (4, 5), (5, 6), (6, 7), (7, 4),  # Back face
        (0, 4), (1, 5), (2, 6), (3, 7),  # Connecting edges
    ]

    debug_log(
        f"[CubeCut] Checking {len(faces)} faces for cuboid-face intersections"
    )
    debug_log(
        f"[CubeCut] Cuboid vertices: {[str(vertex) for vertex in cuboid_vertices]}"
    )

    for face in faces:
        face_normal = face.normal
        if face_normal.length < EPSILON:
            continue
        face_point = face.verts[0].co
        face_vertices = [vert.co for vert in face.verts]

        for edge_index, (v1_index, v2_index) in enumerate(cuboid_edges):
            edge_start = cuboid_vertices[v1_index]
            edge_end = cuboid_vertices[v2_index]
            d1 = (edge_start - face_point).dot(face_normal)
            d2 = (edge_end - face_point).dot(face_normal)

            # Check if edge crosses the face plane (endpoints on opposite sides)
            crosses = (
                (d1 > EPSILON and d2 < -EPSILON) or
                (d1 < -EPSILON and d2 > EPSILON)
            )

            # Special case: endpoint ON the face plane, other endpoint on one side
            # Only allow exact alignment cutting for front/back faces (parallel to local_z)
            # Side faces (left/right/top/bottom) still require crossing through
            endpoint_on_face = None
            if not crosses:
                # Check if mesh face is parallel to front/back planes (normal parallel to local_z)
                face_parallel_to_depth = (
                    abs(abs(face_normal.dot(cuboid.local_z)) - 1.0) <
                    EPSILON * 10
                )
                if face_parallel_to_depth:
                    # Check if one endpoint is on the face plane
                    if abs(d1) <= EPSILON and abs(d2) > EPSILON:
                        # edge_start (v1) is on the face plane
                        endpoint_on_face = edge_start.copy()
                        debug_log(
                            f"[CubeCut] Cuboid vertex {v1_index} is ON face "
                            f"{face.index} (depth-aligned)"
                        )
                    elif abs(d2) <= EPSILON and abs(d1) > EPSILON:
                        # edge_end (v2) is on the face plane
                        endpoint_on_face = edge_end.copy()
                        debug_log(
                            f"[CubeCut] Cuboid vertex {v2_index} is ON face "
                            f"{face.index} (depth-aligned)"
                        )

            if not crosses and endpoint_on_face is None:
                if edge_index >= 8:  # Connecting edges are indices 8-11
                    debug_log(
                        f"[CubeCut] Cuboid edge {edge_index} "
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
                f"[CubeCut] Cuboid edge {edge_index} intersects face "
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
                _intersection_boundary_epsilon(cuboid),
            )
            debug_log(
                f"[CubeCut]   in_polygon={in_polygon}, "
                f"in_interior={in_interior}"
            )
            if not in_interior:
                continue

            face_interior_points.setdefault(face, []).append(
                intersection.copy()
            )
            debug_log("[CubeCut]   Added interior intersection!")

    debug_log(
        f"[CubeCut] Found {len(face_interior_points)} faces with interior "
        "intersections"
    )
    return face_interior_points


def _collect_candidate_vertex_points(face_interior_points, faces_to_cut,
                                     edge_splits):
    """Collect unique locations where analysis predicts vertex creation."""
    unique_points = {}

    # Interior points become new vertices when their host face is rebuilt.
    for face in faces_to_cut:
        for point in face_interior_points.get(face, []):
            unique_points.setdefault(_point_key(point), point.copy())

    # Edge intersections at an existing endpoint record cuboid-plane membership
    # during execution, but do not create a new vertex.
    for edge, intersections in edge_splits.items():
        endpoint_a = edge.verts[0].co
        endpoint_b = edge.verts[1].co
        for point, _plane_index, _t in intersections:
            if (point - endpoint_a).length < EPSILON:
                continue
            if (point - endpoint_b).length < EPSILON:
                continue

            # Match execution's coordinate-level uniqueness: one marker location
            # represents a predicted vertex even if multiple faces share it.
            unique_points.setdefault(_point_key(point), point.copy())

    return list(unique_points.values())


def _point_key(point):
    return (round(point.x, 5), round(point.y, 5), round(point.z, 5))


def _point_within_plane_bounds(point, plane_idx, cuboid):
    """Check if a point on a cuboid plane is within that plane's bounds."""
    local = cuboid.to_local(point)
    x, y, z = local.x, local.y, local.z

    if plane_idx in (0, 1):  # Front/back planes
        return (
            -EPSILON <= x <= cuboid.dx + EPSILON and
            -EPSILON <= y <= cuboid.dy + EPSILON
        )
    if plane_idx in (2, 3):  # Left/right planes
        return (
            -EPSILON <= y <= cuboid.dy + EPSILON and
            cuboid.depth_min - EPSILON <= z <= cuboid.depth_max + EPSILON
        )
    # Top/bottom planes
    return (
        -EPSILON <= x <= cuboid.dx + EPSILON and
        cuboid.depth_min - EPSILON <= z <= cuboid.depth_max + EPSILON
    )


def _build_cuboid_vertices_local(cuboid):
    """Build the 8 vertices of the cuboid in mesh local space."""
    origin = cuboid.origin
    local_x = cuboid.local_x
    local_y = cuboid.local_y
    local_z = cuboid.local_z
    dx = cuboid.dx
    dy = cuboid.dy
    depth_min = cuboid.depth_min
    depth_max = cuboid.depth_max

    return [
        origin + local_z * depth_min,  # 0: front_bl
        origin + local_x * dx + local_z * depth_min,  # 1: front_br
        origin + local_x * dx + local_y * dy + local_z * depth_min,  # 2: front_tr
        origin + local_y * dy + local_z * depth_min,  # 3: front_tl
        origin + local_z * depth_max,  # 4: back_bl
        origin + local_x * dx + local_z * depth_max,  # 5: back_br
        origin + local_x * dx + local_y * dy + local_z * depth_max,  # 6: back_tr
        origin + local_y * dy + local_z * depth_max,  # 7: back_tl
    ]


def _intersection_boundary_epsilon(cuboid):
    """Return tolerance for classifying cuboid-face intersections near edges."""
    depth_extent = abs(cuboid.depth_max - cuboid.depth_min)
    max_extent = max(cuboid.dx, cuboid.dy, depth_extent)
    return max(EPSILON * 10, max_extent * 1e-7)


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
