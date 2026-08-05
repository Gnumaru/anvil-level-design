"""Elliptical polygon-prism construction for Cylinder Cut."""

from mathutils import Vector

from ..cylinder_profile import (
    RADIUS_MODE_EDGES,
    RADIUS_MODE_FACES,
    RADIUS_MODES,
    build_cylinder_profile,
)
from ..mesh_cut.convex_prism import EPSILON, build_convex_prism

def build_cylinder_cut_prism(matrix_world, center, radius_x, radius_y, depth,
                              local_x, local_y, local_z, side_count,
                              radius_mode):
    """Build the mesh-local convex prism represented by Cylinder Cut values."""
    if radius_x <= EPSILON or radius_y <= EPSILON:
        raise ValueError("Cylinder radii must be greater than zero")

    effective_depth = depth
    if abs(depth) < EPSILON:
        effective_depth = EPSILON * 2 if depth >= 0 else -EPSILON * 2

    axis_z = Vector(local_z).normalized()
    drawn_cap = build_cylinder_profile(
        center,
        radius_x,
        radius_y,
        local_x,
        local_y,
        side_count,
        radius_mode,
    )

    extrusion = axis_z * effective_depth
    return build_convex_prism(matrix_world, drawn_cap, extrusion)
