"""Prism Builder adapter for shared profile-prism mesh creation."""

from ..profile_builder_geometry import (
    CAP_MODE_NGON,
    CAP_MODE_QUADS,
    execute_profile_builder_edit_mode,
    execute_profile_builder_object_mode,
)


def execute_prism_builder_edit_mode(
        profile_vertices, depth, local_z, obj, ppm,
        keep_anti_parallel_coplanar_faces, quadify_caps):
    cap_mode = CAP_MODE_QUADS if quadify_caps else CAP_MODE_NGON
    return execute_profile_builder_edit_mode(
        profile_vertices,
        depth,
        local_z,
        obj,
        ppm,
        cap_mode,
        keep_anti_parallel_coplanar_faces,
        "Prism",
    )


def execute_prism_builder_object_mode(
        profile_vertices, depth, local_z, ppm, quadify_caps, name_suffix):
    if not profile_vertices:
        return (False, "Prism profile must contain at least three points")
    cap_mode = CAP_MODE_QUADS if quadify_caps else CAP_MODE_NGON
    return execute_profile_builder_object_mode(
        profile_vertices,
        depth,
        local_z,
        ppm,
        cap_mode,
        "Anvil.Prism",
        name_suffix,
        profile_vertices[0],
        "Prism",
    )
