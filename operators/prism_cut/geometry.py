"""Prism Cut adapters for convex and concave reconstruction paths."""

from .prism import build_prism_cut_prism
from ..mesh_cut.analysis import analyze_concave_prism_cut
from ..mesh_cut.concave_prism import ConcavePrism
from ..mesh_cut.concave_reconstruction import reconstruct_concave_prism_face
from ..mesh_cut.execution import (
    RECONSTRUCTION_MODE_QUADS,
    execute_convex_prism_cut_with_reconstruction,
    execute_prism_cut_with_face_reconstruction,
)


def execute_prism_cut(obj, tool_settings, ppm, profile_vertices, depth,
                      local_z, matrix_world):
    """Run Prism Cut with the original quad reconstruction behavior."""
    return execute_prism_cut_with_reconstruction(
        obj,
        tool_settings,
        ppm,
        profile_vertices,
        depth,
        local_z,
        RECONSTRUCTION_MODE_QUADS,
        matrix_world,
    )


def execute_prism_cut_with_reconstruction(
        obj, tool_settings, ppm, profile_vertices, depth, local_z,
        reconstruction_mode, matrix_world):
    """Validate captured values and run the shared polygon-prism cutter."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")

    try:
        prism = build_prism_cut_prism(
            matrix_world,
            profile_vertices,
            depth,
            local_z,
        )
    except ValueError as error:
        return (False, f"Invalid prism cut profile: {error}")

    if isinstance(prism, ConcavePrism):
        return execute_prism_cut_with_face_reconstruction(
            obj,
            tool_settings,
            ppm,
            prism,
            reconstruction_mode,
            analyze_concave_prism_cut,
            reconstruct_concave_prism_face,
        )
    return execute_convex_prism_cut_with_reconstruction(
        obj, tool_settings, ppm, prism, reconstruction_mode
    )
