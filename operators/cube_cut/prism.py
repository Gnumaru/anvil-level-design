"""Convex prism representation used by Cube Cut analysis and execution.

A prism is defined by one ordered convex cap polygon and an extrusion vector.
The representation is intentionally independent of the Cube Cut rectangle UI so
other convex profiles can use the same intersection and reconstruction pipeline.
"""

from mathutils import Vector


EPSILON = 1e-5


class ConvexPrism:
    """A drawn convex polygon extruded along a non-coplanar vector.

    Coordinates are expected in the target mesh's local space. ``cap_vertices``
    must describe the drawn cap in perimeter order and ``extrusion`` must point
    from that cap to the far cap. Winding and extrusion sign are unrestricted;
    plane normals are oriented outward automatically.
    """

    def __init__(self, cap_vertices, extrusion):
        self.cap_vertices = [Vector(vertex).copy() for vertex in cap_vertices]
        self.extrusion = Vector(extrusion).copy()

        self._validate_basic_geometry()

        profile_normal = _polygon_normal(self.cap_vertices)
        if profile_normal.dot(self.extrusion) < 0:
            profile_normal = -profile_normal

        self.cap_normal = profile_normal
        self.back_vertices = [
            vertex + self.extrusion for vertex in self.cap_vertices
        ]
        self.vertices = self.cap_vertices + self.back_vertices
        self.cap_center = _average_points(self.cap_vertices)
        self.cap_extent = _bounding_box_diagonal(self.cap_vertices)

        vertex_count = len(self.cap_vertices)
        self.cap_plane_indices = (0, 1)
        self.side_plane_indices = tuple(range(2, vertex_count + 2))
        self.planes = self._build_planes()
        self.edge_indices = self._build_edge_indices()

        self._validate_convexity()

    def _validate_basic_geometry(self):
        if len(self.cap_vertices) < 3:
            raise ValueError("Convex prism cap requires at least three vertices")

        for index, vertex in enumerate(self.cap_vertices):
            next_vertex = self.cap_vertices[
                (index + 1) % len(self.cap_vertices)
            ]
            if (next_vertex - vertex).length <= EPSILON:
                raise ValueError(
                    "Convex prism cap contains duplicate consecutive vertices"
                )

        if self.extrusion.length <= EPSILON:
            raise ValueError("Convex prism extrusion must have non-zero length")

        profile_normal = _polygon_normal(self.cap_vertices)
        plane_point = self.cap_vertices[0]
        extent = max(
            (vertex - plane_point).length for vertex in self.cap_vertices
        )
        planarity_epsilon = max(EPSILON * 10, extent * 1e-7)

        for vertex in self.cap_vertices[1:]:
            distance = abs((vertex - plane_point).dot(profile_normal))
            if distance > planarity_epsilon:
                raise ValueError("Convex prism cap vertices must be coplanar")

        if abs(profile_normal.dot(self.extrusion)) <= EPSILON:
            raise ValueError(
                "Convex prism extrusion must leave the cap plane"
            )

    def _build_planes(self):
        planes = [
            (self.cap_vertices[0].copy(), -self.cap_normal.copy()),
            (self.back_vertices[0].copy(), self.cap_normal.copy()),
        ]
        extrusion_direction = self.extrusion.normalized()

        for index, vertex in enumerate(self.cap_vertices):
            next_vertex = self.cap_vertices[
                (index + 1) % len(self.cap_vertices)
            ]
            cap_edge = next_vertex - vertex
            side_normal = cap_edge.cross(extrusion_direction)
            parallel_epsilon = cap_edge.length * EPSILON
            if side_normal.length <= parallel_epsilon:
                raise ValueError(
                    "Convex prism extrusion is parallel to a cap edge"
                )
            side_normal.normalize()

            # Anchor side orientation to the drawn profile. Depth must not
            # influence which side of a profile edge is considered interior.
            if (self.cap_center - vertex).dot(side_normal) > 0:
                side_normal = -side_normal

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

    def _validate_convexity(self):
        validation_epsilon = max(EPSILON * 10, self.cap_extent * 1e-7)
        for plane_index in self.side_plane_indices:
            plane_point, plane_normal = self.planes[plane_index]
            for vertex in self.cap_vertices:
                distance = (vertex - plane_point).dot(plane_normal)
                if distance > validation_epsilon:
                    raise ValueError(
                        "Convex prism cap must be convex and perimeter-ordered"
                    )

    def point_inside(self, point):
        """Return whether a point is inside the prism or on its boundary."""
        return all(
            (point - plane_point).dot(plane_normal) <= EPSILON
            for plane_point, plane_normal in self.planes
        )

    def point_strictly_inside(self, point):
        """Return whether a point is inside and away from every boundary."""
        return all(
            (point - plane_point).dot(plane_normal) < -EPSILON
            for plane_point, plane_normal in self.planes
        )

    def point_on_surface(self, point):
        """Return whether a point lies on the prism boundary."""
        if not self.point_inside(point):
            return False
        return any(
            abs((point - plane_point).dot(plane_normal)) <= EPSILON
            for plane_point, plane_normal in self.planes
        )

    def point_within_plane_bounds(self, point, plane_index):
        """Return whether a point on one plane lies within its prism face."""
        plane_point, plane_normal = self.planes[plane_index]
        if abs((point - plane_point).dot(plane_normal)) > EPSILON * 10:
            return False
        return self.point_inside(point)

    def face_normal_parallel_to_caps(self, face_normal):
        """Return whether a normal is parallel to the cap planes."""
        return (
            abs(abs(face_normal.dot(self.cap_normal)) - 1.0) <
            EPSILON * 10
        )

    def is_effectively_zero_depth(self):
        """Return whether the cap separation is the zero-depth cut epsilon."""
        cap_separation = abs(self.extrusion.dot(self.cap_normal))
        return cap_separation <= EPSILON * 3


def build_convex_prism(matrix_world, cap_vertices, extrusion):
    """Build a mesh-local convex prism from world-space geometry."""
    world_to_local = matrix_world.inverted()
    world_to_local_rotation = world_to_local.to_3x3()
    local_cap_vertices = [
        world_to_local @ Vector(vertex) for vertex in cap_vertices
    ]
    local_extrusion = world_to_local_rotation @ Vector(extrusion)
    return ConvexPrism(local_cap_vertices, local_extrusion)


def build_cube_cut_prism(matrix_world, first_vertex, second_vertex, depth,
                         local_x, local_y, local_z):
    """Adapt the Cube Cut rectangle and depth to a generic convex prism."""
    effective_depth = depth
    if abs(depth) < EPSILON:
        effective_depth = EPSILON * 2 if depth >= 0 else -EPSILON * 2

    first = Vector(first_vertex)
    difference = Vector(second_vertex) - first
    axis_x = Vector(local_x).normalized()
    axis_y = Vector(local_y).normalized()
    axis_z = Vector(local_z).normalized()
    extent_x = difference.dot(axis_x)
    extent_y = difference.dot(axis_y)

    if extent_x < 0:
        extent_x = -extent_x
        axis_x = -axis_x
    if extent_y < 0:
        extent_y = -extent_y
        axis_y = -axis_y

    drawn_cap = [
        first,
        first + axis_x * extent_x,
        first + axis_x * extent_x + axis_y * extent_y,
        first + axis_y * extent_y,
    ]

    extrusion = axis_z * effective_depth
    return build_convex_prism(matrix_world, drawn_cap, extrusion)


def _polygon_normal(vertices):
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
    raise ValueError("Convex prism cap vertices must not be collinear")


def _average_points(points):
    result = Vector((0.0, 0.0, 0.0))
    for point in points:
        result += point
    return result / len(points)


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
