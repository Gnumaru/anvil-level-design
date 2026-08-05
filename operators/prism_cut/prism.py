"""Freeform Prism Cut profile adapter for shared prism representations."""

from mathutils import Vector

from ..mesh_cut.concave_prism import build_profile_prism
from ..mesh_cut.convex_prism import EPSILON


def build_prism_cut_prism(matrix_world, profile_vertices, depth, local_z):
    """Build the shared prism represented by captured Prism Cut values."""
    effective_depth = depth
    if abs(depth) < EPSILON:
        effective_depth = EPSILON * 2 if depth >= 0 else -EPSILON * 2
    extrusion = Vector(local_z).normalized() * effective_depth
    return build_profile_prism(matrix_world, profile_vertices, extrusion)
