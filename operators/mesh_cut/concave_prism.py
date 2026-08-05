"""Concave prism representation and convex/concave profile factory."""

from mathutils import Vector

from .convex_prism import ConvexPrism, EPSILON


class ConcavePrism:
    """A mesh-local simple concave polygon extruded into a prism."""

    def __init__(self, cap_vertices, extrusion):
        self.cap_vertices = [Vector(vertex).copy() for vertex in cap_vertices]
        self.extrusion = Vector(extrusion).copy()

        plane_normal, profile_vertices_2d, boundary_epsilon = (
            _validated_profile_geometry(self.cap_vertices, self.extrusion)
        )
        if not _polygon_is_concave(
                profile_vertices_2d, boundary_epsilon):
            raise ValueError("Concave prism profile must contain a re-entrant corner")

        self._plane_normal = plane_normal
        self._profile_axis_x = _first_edge_direction(self.cap_vertices)
        self._profile_axis_y = self._plane_normal.cross(
            self._profile_axis_x
        ).normalized()
        self._profile_vertices_2d = profile_vertices_2d
        self.boundary_epsilon = boundary_epsilon

        ordered_profile_normal = _polygon_normal(self.cap_vertices)
        self._ordered_profile_normal = ordered_profile_normal.copy()
        if ordered_profile_normal.dot(self.extrusion) < 0:
            ordered_profile_normal.negate()

        self.cap_normal = ordered_profile_normal
        self.back_vertices = [
            vertex + self.extrusion for vertex in self.cap_vertices
        ]
        self.vertices = self.cap_vertices + self.back_vertices
        self.cap_extent = _bounding_box_diagonal(self.cap_vertices)
        self._cap_separation = self.extrusion.dot(self.cap_normal)

        vertex_count = len(self.cap_vertices)
        self.cap_plane_indices = (0, 1)
        self.side_plane_indices = tuple(range(2, vertex_count + 2))
        self.planes = self._build_planes()
        self.edge_indices = self._build_edge_indices()

    def _build_planes(self):
        planes = [
            (self.cap_vertices[0].copy(), -self.cap_normal.copy()),
            (self.back_vertices[0].copy(), self.cap_normal.copy()),
        ]
        extrusion_direction = self.extrusion.normalized()
        winding_follows_extrusion = (
            self._ordered_profile_normal.dot(self.extrusion) > 0
        )

        for index, vertex in enumerate(self.cap_vertices):
            next_vertex = self.cap_vertices[
                (index + 1) % len(self.cap_vertices)
            ]
            cap_edge = next_vertex - vertex
            side_normal = cap_edge.cross(extrusion_direction)
            if side_normal.length <= cap_edge.length * EPSILON:
                raise ValueError(
                    "Prism extrusion is parallel to a profile edge"
                )
            side_normal.normalize()
            if not winding_follows_extrusion:
                side_normal.negate()
            planes.append((vertex.copy(), side_normal))

        return planes

    def _build_edge_indices(self):
        vertex_count = len(self.cap_vertices)
        edges = []
        for index in range(vertex_count):
            next_index = (index + 1) % vertex_count
            edges.append((index, next_index))
            edges.append((index + vertex_count, next_index + vertex_count))
            edges.append((index, index + vertex_count))
        return edges

    def point_inside(self, point):
        """Return whether a point is inside the prism or on its boundary."""
        profile_point = self._point_on_drawn_cap(point)
        if profile_point is None:
            return False
        return _point_in_polygon_2d(
            self._project_to_profile(profile_point),
            self._profile_vertices_2d,
            self.boundary_epsilon,
            True,
        )

    def point_inside_ignoring_caps(self, point, ignored_cap_indices):
        """Return inside status while treating selected caps as unbounded."""
        signed_depth = (Vector(point) - self.cap_vertices[0]).dot(
            self.cap_normal
        )
        if 0 not in ignored_cap_indices and signed_depth < -EPSILON:
            return False
        if (
                1 not in ignored_cap_indices
                and signed_depth > self._cap_separation + EPSILON):
            return False
        depth_fraction = signed_depth / self._cap_separation
        profile_point = Vector(point) - self.extrusion * depth_fraction
        return _point_in_polygon_2d(
            self._project_to_profile(profile_point),
            self._profile_vertices_2d,
            self.boundary_epsilon,
            True,
        )

    def point_strictly_inside(self, point):
        """Return whether a point is inside and away from every boundary."""
        signed_depth = (Vector(point) - self.cap_vertices[0]).dot(
            self.cap_normal
        )
        if signed_depth <= EPSILON:
            return False
        if signed_depth >= self._cap_separation - EPSILON:
            return False

        depth_fraction = signed_depth / self._cap_separation
        profile_point = Vector(point) - self.extrusion * depth_fraction
        return _point_in_polygon_2d(
            self._project_to_profile(profile_point),
            self._profile_vertices_2d,
            self.boundary_epsilon,
            False,
        )

    def point_on_surface(self, point):
        """Return whether a point lies on the concave prism boundary."""
        if not self.point_inside(point):
            return False
        signed_depth = (Vector(point) - self.cap_vertices[0]).dot(
            self.cap_normal
        )
        if signed_depth <= EPSILON:
            return True
        if signed_depth >= self._cap_separation - EPSILON:
            return True

        depth_fraction = signed_depth / self._cap_separation
        profile_point = Vector(point) - self.extrusion * depth_fraction
        return _point_on_polygon_boundary_2d(
            self._project_to_profile(profile_point),
            self._profile_vertices_2d,
            self.boundary_epsilon,
        )

    def point_within_plane_bounds(self, point, plane_index):
        """Return whether a point lies on the finite indexed prism surface."""
        plane_point, plane_normal = self.planes[plane_index]
        if abs((point - plane_point).dot(plane_normal)) > EPSILON * 10:
            return False

        signed_depth = (Vector(point) - self.cap_vertices[0]).dot(
            self.cap_normal
        )
        if signed_depth < -EPSILON:
            return False
        if signed_depth > self._cap_separation + EPSILON:
            return False

        depth_fraction = signed_depth / self._cap_separation
        profile_point = Vector(point) - self.extrusion * depth_fraction
        if plane_index in self.cap_plane_indices:
            return _point_in_polygon_2d(
                self._project_to_profile(profile_point),
                self._profile_vertices_2d,
                self.boundary_epsilon,
                True,
            )

        edge_index = plane_index - len(self.cap_plane_indices)
        edge_start = self.cap_vertices[edge_index]
        edge_end = self.cap_vertices[
            (edge_index + 1) % len(self.cap_vertices)
        ]
        return _point_on_segment_3d(
            profile_point,
            edge_start,
            edge_end,
            self.boundary_epsilon,
        )

    def face_normal_parallel_to_caps(self, face_normal):
        """Return whether a normal is parallel to the cap planes."""
        return (
            abs(abs(face_normal.dot(self.cap_normal)) - 1.0) <
            EPSILON * 10
        )

    def is_effectively_zero_depth(self):
        """Return whether the cap separation is the zero-depth cut epsilon."""
        return self._cap_separation <= EPSILON * 3

    def surface_polygons(self):
        """Return both caps and every side quad as ordered vertex loops."""
        polygons = [
            list(self.cap_vertices),
            list(self.back_vertices),
        ]
        for index, vertex in enumerate(self.cap_vertices):
            next_index = (index + 1) % len(self.cap_vertices)
            polygons.append([
                vertex,
                self.cap_vertices[next_index],
                self.back_vertices[next_index],
                self.back_vertices[index],
            ])
        return polygons

    def _point_on_drawn_cap(self, point):
        signed_depth = (Vector(point) - self.cap_vertices[0]).dot(
            self.cap_normal
        )
        if signed_depth < -EPSILON:
            return None
        if signed_depth > self._cap_separation + EPSILON:
            return None
        depth_fraction = signed_depth / self._cap_separation
        return Vector(point) - self.extrusion * depth_fraction

    def _project_to_profile(self, point):
        offset = Vector(point) - self.cap_vertices[0]
        return Vector((
            offset.dot(self._profile_axis_x),
            offset.dot(self._profile_axis_y),
        ))


def build_profile_prism(matrix_world, profile_vertices, extrusion_world):
    """Build a convex or concave mesh-local prism from world-space values."""
    world_to_local = matrix_world.inverted()
    world_to_local_rotation = world_to_local.to_3x3()
    local_cap_vertices = [
        world_to_local @ Vector(vertex) for vertex in profile_vertices
    ]
    local_extrusion = world_to_local_rotation @ extrusion_world

    _plane_normal, profile_vertices_2d, boundary_epsilon = (
        _validated_profile_geometry(local_cap_vertices, local_extrusion)
    )
    if _polygon_is_concave(profile_vertices_2d, boundary_epsilon):
        return ConcavePrism(local_cap_vertices, local_extrusion)
    return ConvexPrism(local_cap_vertices, local_extrusion)


def _validated_profile_geometry(cap_vertices, extrusion):
    if len(cap_vertices) < 3:
        raise ValueError("Prism cap requires at least three vertices")
    if extrusion.length <= EPSILON:
        raise ValueError("Prism extrusion must have non-zero length")

    for index, vertex in enumerate(cap_vertices):
        next_vertex = cap_vertices[(index + 1) % len(cap_vertices)]
        if (next_vertex - vertex).length <= EPSILON:
            raise ValueError("Prism cap contains duplicate consecutive vertices")
        for other_vertex in cap_vertices[index + 1:]:
            if (other_vertex - vertex).length <= EPSILON:
                raise ValueError("Prism cap contains duplicate vertices")

    plane_normal = _non_collinear_normal(cap_vertices)
    plane_point = cap_vertices[0]
    extent = max((vertex - plane_point).length for vertex in cap_vertices)
    boundary_epsilon = max(EPSILON * 10, extent * 1e-7)
    for vertex in cap_vertices[1:]:
        if abs((vertex - plane_point).dot(plane_normal)) > boundary_epsilon:
            raise ValueError("Prism cap vertices must be coplanar")
    if abs(plane_normal.dot(extrusion)) <= EPSILON:
        raise ValueError("Prism extrusion must leave the cap plane")

    axis_x = _first_edge_direction(cap_vertices)
    axis_y = plane_normal.cross(axis_x).normalized()
    profile_vertices_2d = []
    for vertex in cap_vertices:
        offset = vertex - plane_point
        profile_vertices_2d.append(Vector((
            offset.dot(axis_x),
            offset.dot(axis_y),
        )))
    _validate_simple_polygon(profile_vertices_2d, boundary_epsilon)
    return (plane_normal, profile_vertices_2d, boundary_epsilon)


def _polygon_normal(vertices):
    normal = Vector((0.0, 0.0, 0.0))
    for index, current in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        normal.x += (current.y - following.y) * (current.z + following.z)
        normal.y += (current.z - following.z) * (current.x + following.x)
        normal.z += (current.x - following.x) * (current.y + following.y)
    if normal.length <= EPSILON:
        raise ValueError("Prism cap vertices must enclose a non-zero area")
    normal.normalize()
    return normal


def _non_collinear_normal(vertices):
    for first_index in range(len(vertices) - 2):
        first = vertices[first_index]
        for second_index in range(first_index + 1, len(vertices) - 1):
            first_edge = vertices[second_index] - first
            for third_index in range(second_index + 1, len(vertices)):
                second_edge = vertices[third_index] - first
                normal = first_edge.cross(second_edge)
                if normal.length > EPSILON:
                    normal.normalize()
                    return normal
    raise ValueError("Prism cap vertices must not be collinear")


def _first_edge_direction(vertices):
    for index, vertex in enumerate(vertices):
        edge = vertices[(index + 1) % len(vertices)] - vertex
        if edge.length > EPSILON:
            return edge.normalized()
    raise ValueError("Prism cap must contain a non-zero edge")


def _validate_simple_polygon(vertices, epsilon):
    edge_count = len(vertices)
    for first_index in range(edge_count):
        first_start = vertices[first_index]
        first_end = vertices[(first_index + 1) % edge_count]
        for second_index in range(first_index + 1, edge_count):
            if second_index == first_index + 1:
                continue
            if first_index == 0 and second_index == edge_count - 1:
                continue
            second_start = vertices[second_index]
            second_end = vertices[(second_index + 1) % edge_count]
            if _segments_intersect_2d(
                    first_start,
                    first_end,
                    second_start,
                    second_end,
                    epsilon):
                raise ValueError("Prism cap edges must not cross or touch")


def _polygon_is_concave(vertices, epsilon):
    turn_sign = 0
    for index, current in enumerate(vertices):
        previous = vertices[index - 1]
        following = vertices[(index + 1) % len(vertices)]
        turn = _cross_2d(current - previous, following - current)
        if abs(turn) <= epsilon:
            continue
        current_sign = 1 if turn > 0 else -1
        if turn_sign == 0:
            turn_sign = current_sign
        elif current_sign != turn_sign:
            return True
    return False


def _segments_intersect_2d(first_start, first_end, second_start, second_end,
                           epsilon):
    first_side_start = _cross_2d(
        first_end - first_start,
        second_start - first_start,
    )
    first_side_end = _cross_2d(
        first_end - first_start,
        second_end - first_start,
    )
    second_side_start = _cross_2d(
        second_end - second_start,
        first_start - second_start,
    )
    second_side_end = _cross_2d(
        second_end - second_start,
        first_end - second_start,
    )
    if (
            first_side_start * first_side_end < -epsilon * epsilon
            and second_side_start * second_side_end < -epsilon * epsilon):
        return True
    return (
        abs(first_side_start) <= epsilon
        and _point_on_segment_2d(
            second_start, first_start, first_end, epsilon
        )
    ) or (
        abs(first_side_end) <= epsilon
        and _point_on_segment_2d(
            second_end, first_start, first_end, epsilon
        )
    ) or (
        abs(second_side_start) <= epsilon
        and _point_on_segment_2d(
            first_start, second_start, second_end, epsilon
        )
    ) or (
        abs(second_side_end) <= epsilon
        and _point_on_segment_2d(
            first_end, second_start, second_end, epsilon
        )
    )


def _point_in_polygon_2d(point, vertices, epsilon, include_boundary):
    if _point_on_polygon_boundary_2d(point, vertices, epsilon):
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


def _point_on_polygon_boundary_2d(point, vertices, epsilon):
    return any(
        _point_on_segment_2d(
            point,
            start,
            vertices[(index + 1) % len(vertices)],
            epsilon,
        )
        for index, start in enumerate(vertices)
    )


def _point_on_segment_2d(point, start, end, epsilon):
    segment = end - start
    point_offset = point - start
    if abs(_cross_2d(segment, point_offset)) > epsilon * max(
            1.0, segment.length):
        return False
    return (
        point.x >= min(start.x, end.x) - epsilon
        and point.x <= max(start.x, end.x) + epsilon
        and point.y >= min(start.y, end.y) - epsilon
        and point.y <= max(start.y, end.y) + epsilon
    )


def _point_on_segment_3d(point, start, end, epsilon):
    segment = end - start
    if segment.length <= epsilon:
        return (point - start).length <= epsilon
    fraction = (point - start).dot(segment) / segment.length_squared
    if fraction < -epsilon / segment.length:
        return False
    if fraction > 1.0 + epsilon / segment.length:
        return False
    closest = start + segment * fraction
    return (point - closest).length <= epsilon


def _cross_2d(first, second):
    return first.x * second.y - first.y * second.x


def _bounding_box_diagonal(points):
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
    return (maximum - minimum).length
