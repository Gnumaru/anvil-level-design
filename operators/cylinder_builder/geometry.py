"""Cylinder Builder adapter for shared profile-prism mesh creation."""

from ..cylinder_profile import build_cylinder_profile
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE
from ..profile_builder_geometry import (
    CAP_MODE_NONE,
    execute_profile_builder_edit_mode,
    execute_profile_builder_object_mode,
)


def execute_cylinder_builder_edit_mode(
        center, radius_x, radius_y, depth, local_x, local_y, local_z,
        view_forward,
        side_count, radius_mode, obj, ppm, skip_caps, cap_fill):
    if radius_x < MIN_RECTANGLE_SIZE or radius_y < MIN_RECTANGLE_SIZE:
        return (False, "Cylinder radii must be greater than zero")
    profile_vertices = build_cylinder_profile(
        center,
        radius_x,
        radius_y,
        local_x,
        local_y,
        side_count,
        radius_mode,
    )
    cap_mode = CAP_MODE_NONE if skip_caps else cap_fill
    return execute_profile_builder_edit_mode(
        profile_vertices,
        depth,
        local_z,
        view_forward,
        obj,
        ppm,
        cap_mode,
        True,
        "Cylinder",
    )


def execute_cylinder_builder_object_mode(
        center, radius_x, radius_y, depth, local_x, local_y, local_z,
        view_forward,
        side_count, radius_mode, ppm, skip_caps, cap_fill, name_suffix):
    if radius_x < MIN_RECTANGLE_SIZE or radius_y < MIN_RECTANGLE_SIZE:
        return (False, "Cylinder radii must be greater than zero")
    profile_vertices = build_cylinder_profile(
        center,
        radius_x,
        radius_y,
        local_x,
        local_y,
        side_count,
        radius_mode,
    )
    cap_mode = CAP_MODE_NONE if skip_caps else cap_fill
    return execute_profile_builder_object_mode(
        profile_vertices,
        depth,
        local_z,
        view_forward,
        ppm,
        cap_mode,
        "Anvil.Cylinder",
        name_suffix,
        center,
        "Cylinder",
    )
