"""Cylinder Cut adapter for the shared convex-prism cutting engine."""

from .prism import build_cylinder_cut_prism
from ..cube_cut.geometry import (
    RECONSTRUCTION_MODE_QUADS,
    execute_convex_prism_cut_with_reconstruction,
)


def execute_cylinder_cut(obj, tool_settings, ppm, center, radius_x, radius_y,
                         depth, local_x, local_y, local_z, side_count,
                         radius_mode):
    """Run Cylinder Cut with the original quad reconstruction behavior."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    return execute_cylinder_cut_with_reconstruction(
        obj,
        tool_settings,
        ppm,
        center,
        radius_x,
        radius_y,
        depth,
        local_x,
        local_y,
        local_z,
        side_count,
        radius_mode,
        RECONSTRUCTION_MODE_QUADS,
        obj.matrix_world,
    )


def execute_cylinder_cut_with_reconstruction(
        obj, tool_settings, ppm, center, radius_x, radius_y, depth, local_x,
        local_y, local_z, side_count, radius_mode, reconstruction_mode,
        matrix_world):
    """Cut an elliptical polygon prism from an edit-mode mesh."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")

    try:
        prism = build_cylinder_cut_prism(
            matrix_world,
            center,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
            side_count,
            radius_mode,
        )
    except ValueError as error:
        return (False, f"Invalid cylinder cut prism: {error}")

    return execute_convex_prism_cut_with_reconstruction(
        obj,
        tool_settings,
        ppm,
        prism,
        reconstruction_mode,
    )
