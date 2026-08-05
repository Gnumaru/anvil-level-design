"""Shared elliptical polygon profile construction for cylinder tools."""

import math

from mathutils import Vector


RADIUS_MODE_EDGES = 'EDGES'
RADIUS_MODE_FACES = 'FACES'
RADIUS_MODES = {RADIUS_MODE_EDGES, RADIUS_MODE_FACES}


def build_cylinder_profile(center, radius_x, radius_y, local_x, local_y,
                           side_count, radius_mode):
    """Build a world-space elliptical polygon centered on ``center``."""
    if radius_x < 0 or radius_y < 0:
        raise ValueError("Cylinder radii must not be negative")
    if side_count < 3:
        raise ValueError("Cylinder requires at least three sides")
    if radius_mode not in RADIUS_MODES:
        raise ValueError(f"Unknown cylinder radius mode: {radius_mode}")

    axis_x = Vector(local_x).normalized()
    axis_y = Vector(local_y).normalized()
    profile_center = Vector(center)
    angle_step = math.tau / side_count
    angle_offset = 0.0
    radius_scale = 1.0

    if radius_mode == RADIUS_MODE_FACES:
        angle_offset = angle_step * 0.5
        radius_scale = 1.0 / math.cos(angle_step * 0.5)

    return [
        profile_center
        + axis_x * (
            math.cos(angle_offset + index * angle_step)
            * radius_x
            * radius_scale
        )
        + axis_y * (
            math.sin(angle_offset + index * angle_step)
            * radius_y
            * radius_scale
        )
        for index in range(side_count)
    ]
