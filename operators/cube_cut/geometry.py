"""Cube Cut adapter for the shared mesh-cut engine."""

from .prism import build_cube_cut_prism
from ..mesh_cut.execution import (
    RECONSTRUCTION_MODE_NGONS,
    RECONSTRUCTION_MODE_QUADS,
    execute_convex_prism_cut_with_reconstruction,
)
from ...handlers import cache_face_data


def execute_cube_cut(context, first_vertex, second_vertex, depth, local_x,
                     local_y, local_z):
    """Run Cube Cut with the original quad reconstruction behavior."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    return execute_cube_cut_with_reconstruction(
        context,
        first_vertex,
        second_vertex,
        depth,
        local_x,
        local_y,
        local_z,
        RECONSTRUCTION_MODE_QUADS,
        obj.matrix_world,
    )


def execute_cube_cut_with_reconstruction(
        context, first_vertex, second_vertex, depth, local_x, local_y, local_z,
        reconstruction_mode, matrix_world):
    """Adapt Cube Cut values to the shared convex-prism engine."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")

    ppm = context.scene.level_design_props.pixels_per_meter
    try:
        prism = build_cube_cut_prism(
            matrix_world,
            first_vertex,
            second_vertex,
            depth,
            local_x,
            local_y,
            local_z,
        )
    except ValueError as error:
        return (False, f"Invalid cut prism: {error}")

    result = execute_convex_prism_cut_with_reconstruction(
        obj,
        context.tool_settings,
        ppm,
        prism,
        reconstruction_mode,
    )
    if result[0]:
        cache_face_data(context)
    return result
