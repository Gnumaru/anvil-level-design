"""Cube Cut rectangle adapter for the shared convex prism."""

from mathutils import Vector

from ..mesh_cut.convex_prism import EPSILON, build_convex_prism


def build_cube_cut_prism(matrix_world, first_vertex, second_vertex, depth,
                         local_x, local_y, local_z):
    """Adapt the Cube Cut rectangle and depth to a convex prism."""
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
