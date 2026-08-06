"""Geometry creation and preview data for Stair Builder."""

import math
import random

import bmesh
import bpy
from mathutils import Vector

from ..box_builder.geometry import (
    _active_or_previous_material,
    _apply_material_and_uvs,
    _next_box_builder_datablock_name,
)
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE
from ..texture_apply import _dispatch_set_uv_from_other_face
from ...core.face_id import get_face_id_layer
from ...core.geometry import align_2d_shape_to_square
from ...core.materials import (
    MaterialMappingConflictError,
    ensure_material_slot,
    get_texture_dimensions_from_material,
)
from ...core.uv_layers import get_render_active_uv_layer
from ...core.uv_projection import (
    box_project,
    derive_transform_from_uvs,
    get_face_local_axes,
)
from ...handlers import cache_single_face


ORIENTATION_AXIS_1_POSITIVE = 'AXIS_1_POSITIVE'
ORIENTATION_AXIS_1_NEGATIVE = 'AXIS_1_NEGATIVE'
ORIENTATION_AXIS_2_POSITIVE = 'AXIS_2_POSITIVE'
ORIENTATION_AXIS_2_NEGATIVE = 'AXIS_2_NEGATIVE'

ORIENTATION_ROTATION_ORDER = (
    ORIENTATION_AXIS_1_POSITIVE,
    ORIENTATION_AXIS_2_POSITIVE,
    ORIENTATION_AXIS_1_NEGATIVE,
    ORIENTATION_AXIS_2_NEGATIVE,
)

SIZING_STEP_COUNT = 'STEP_COUNT'
SIZING_STEP_HEIGHT = 'STEP_HEIGHT'

HEIGHT_SHORT_FIRST = 'SHORT_FIRST'
HEIGHT_EVEN = 'EVEN'

TERMINATION_TOP_TREAD = 'TOP_TREAD'
TERMINATION_DESTINATION = 'DESTINATION'

UNDERSIDE_SOLID = 'SOLID'
UNDERSIDE_SLOPED = 'SLOPED'
UNDERSIDE_NONE = 'NONE'

BORDER_ALIGN_RISER_BOTTOMS = 'RISER_BOTTOMS'
BORDER_ALIGN_STEP_TIPS = 'STEP_TIPS'

MAX_STEP_COUNT = 10000


class StairBordersTooWideError(ValueError):
    """Raised when enabled borders leave no usable width for the steps."""


def _orientation_slot_and_sign(orientation):
    orientation_map = {
        ORIENTATION_AXIS_1_POSITIVE: (0, 1.0),
        ORIENTATION_AXIS_1_NEGATIVE: (0, -1.0),
        ORIENTATION_AXIS_2_POSITIVE: (1, 1.0),
        ORIENTATION_AXIS_2_NEGATIVE: (1, -1.0),
    }
    if orientation not in orientation_map:
        raise ValueError(f"Unknown stair orientation: {orientation}")
    return orientation_map[orientation]


def rotate_orientation(orientation, quarter_turns):
    if orientation not in ORIENTATION_ROTATION_ORDER:
        raise ValueError(f"Unknown stair orientation: {orientation}")
    current_index = ORIENTATION_ROTATION_ORDER.index(orientation)
    return ORIENTATION_ROTATION_ORDER[
        (current_index + quarter_turns) % len(ORIENTATION_ROTATION_ORDER)
    ]


def _canonical_frame(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation):
    """Return a positive-height stair frame independent of draw direction."""
    first = Vector(first_vertex)
    difference = Vector(second_vertex) - first
    axes = [
        Vector(local_x).normalized(),
        Vector(local_y).normalized(),
        Vector(local_z).normalized(),
    ]
    signed_lengths = [
        difference.dot(axes[0]),
        difference.dot(axes[1]),
        depth,
    ]

    if any(abs(length) < MIN_RECTANGLE_SIZE for length in signed_lengths):
        raise ValueError("Stair width, run, and height must be greater than zero")

    origin = first.copy()
    lengths = []
    for index, signed_length in enumerate(signed_lengths):
        if signed_length < 0.0:
            axes[index] = -axes[index]
            signed_length = -signed_length
        lengths.append(signed_length)

    world_up = Vector((0.0, 0.0, 1.0))
    vertical_index = max(
        range(3),
        key=lambda index: abs(axes[index].dot(world_up)),
    )
    vertical_axis = axes[vertical_index]
    height = lengths[vertical_index]
    if vertical_axis.dot(world_up) < 0.0:
        origin += vertical_axis * height
        vertical_axis = -vertical_axis

    horizontal_indices = [
        index for index in range(3)
        if index != vertical_index
    ]
    run_slot, run_sign = _orientation_slot_and_sign(orientation)
    run_index = horizontal_indices[run_slot]
    width_index = horizontal_indices[1 - run_slot]
    run_axis = axes[run_index]
    run_length = lengths[run_index]
    if run_sign < 0.0:
        origin += run_axis * run_length
        run_axis = -run_axis

    width_axis = axes[width_index]
    width = lengths[width_index]
    right_axis = run_axis.cross(vertical_axis)
    if right_axis.length < 1e-8:
        raise ValueError("Could not determine the stair width direction")
    right_axis.normalize()
    if width_axis.dot(right_axis) < 0.0:
        origin += width_axis * width
        width_axis = -width_axis

    return {
        'origin': origin,
        'run_axis': run_axis,
        'width_axis': width_axis,
        'vertical_axis': vertical_axis,
        'run_length': run_length,
        'width': width,
        'height': height,
    }


def _height_layout(
        total_height, sizing_mode, step_count, target_step_height,
        height_distribution, termination):
    destination = termination == TERMINATION_DESTINATION
    if termination not in {TERMINATION_TOP_TREAD, TERMINATION_DESTINATION}:
        raise ValueError(f"Unknown stair termination: {termination}")

    if sizing_mode == SIZING_STEP_COUNT:
        if step_count < 1:
            raise ValueError("Step count must be at least one")
        if step_count > MAX_STEP_COUNT:
            raise ValueError(f"Step count cannot exceed {MAX_STEP_COUNT}")
        tread_count = step_count
        riser_count = tread_count + (1 if destination else 0)
        riser_height = total_height / riser_count
        riser_heights = [riser_height] * riser_count
    elif sizing_mode == SIZING_STEP_HEIGHT:
        if target_step_height < MIN_RECTANGLE_SIZE:
            raise ValueError("Target step height must be greater than zero")
        minimum_risers = 2 if destination else 1
        ratio = total_height / target_step_height

        if height_distribution == HEIGHT_SHORT_FIRST:
            natural_riser_count = max(1, math.ceil(ratio))
            riser_count = max(minimum_risers, natural_riser_count)
            if riser_count > MAX_STEP_COUNT + (1 if destination else 0):
                raise ValueError(
                    f"Calculated step count cannot exceed {MAX_STEP_COUNT}"
                )
            first_height = total_height - target_step_height * (riser_count - 1)
            if first_height <= 0.0:
                riser_height = total_height / riser_count
                riser_heights = [riser_height] * riser_count
            else:
                riser_heights = [first_height]
                riser_heights.extend(
                    target_step_height
                    for _index in range(riser_count - 1)
                )
        elif height_distribution == HEIGHT_EVEN:
            nearest_riser_count = max(1, math.floor(ratio + 0.5))
            riser_count = max(minimum_risers, nearest_riser_count)
            if riser_count > MAX_STEP_COUNT + (1 if destination else 0):
                raise ValueError(
                    f"Calculated step count cannot exceed {MAX_STEP_COUNT}"
                )
            riser_height = total_height / riser_count
            riser_heights = [riser_height] * riser_count
        else:
            raise ValueError(
                f"Unknown step-height distribution: {height_distribution}"
            )

        tread_count = riser_count - (1 if destination else 0)
    else:
        raise ValueError(f"Unknown stair sizing mode: {sizing_mode}")

    return (tread_count, riser_heights)


def calculate_stair_layout(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation, sizing_mode, step_count, target_step_height,
        height_distribution, termination, left_border, right_border,
        border_width):
    """Calculate the captured frame and derived stair dimensions."""
    frame = _canonical_frame(
        first_vertex,
        second_vertex,
        depth,
        local_x,
        local_y,
        local_z,
        orientation,
    )
    tread_count, riser_heights = _height_layout(
        frame['height'],
        sizing_mode,
        step_count,
        target_step_height,
        height_distribution,
        termination,
    )
    if tread_count < 1:
        raise ValueError("Stair must contain at least one tread")

    if (left_border or right_border) and border_width < MIN_RECTANGLE_SIZE:
        raise ValueError("Enabled borders require a width greater than zero")
    left_border_width = border_width if left_border else 0.0
    right_border_width = border_width if right_border else 0.0
    if left_border_width + right_border_width > frame['width'] - MIN_RECTANGLE_SIZE:
        raise StairBordersTooWideError(
            "Border widths must leave room for the steps"
        )

    tread_depth = frame['run_length'] / tread_count
    tread_heights = []
    accumulated_height = 0.0
    for riser_height in riser_heights[:tread_count]:
        accumulated_height += riser_height
        tread_heights.append(accumulated_height)

    frame.update({
        'tread_count': tread_count,
        'riser_count': len(riser_heights),
        'riser_heights': riser_heights,
        'tread_heights': tread_heights,
        'tread_depth': tread_depth,
        'left_border_width': left_border_width,
        'right_border_width': right_border_width,
        'last_tread_height': tread_heights[-1],
    })
    return frame


def _polygon_normal(positions):
    normal = Vector((0.0, 0.0, 0.0))
    for index, current in enumerate(positions):
        following = positions[(index + 1) % len(positions)]
        normal.x += (current.y - following.y) * (current.z + following.z)
        normal.y += (current.z - following.z) * (current.x + following.x)
        normal.z += (current.x - following.x) * (current.y + following.y)
    return normal


class _StairMeshData:
    """Collect shared stair vertices and consistently oriented polygon faces."""

    def __init__(self, frame):
        self._frame = frame
        self.vertices = []
        self.faces = []
        self.uv_bottom_edges = {}
        self.uv_source_faces = {}
        self._vertex_indices = {}

    def _position(self, coordinate):
        run, width, height = coordinate
        return (
            self._frame['origin']
            + self._frame['run_axis'] * run
            + self._frame['width_axis'] * width
            + self._frame['vertical_axis'] * height
        )

    def _coordinate_key(self, coordinate):
        return tuple(round(value, 12) for value in coordinate)

    def _vertex_index(self, coordinate):
        key = self._coordinate_key(coordinate)
        index = self._vertex_indices.get(key)
        if index is not None:
            return index
        index = len(self.vertices)
        self.vertices.append(self._position(coordinate))
        self._vertex_indices[key] = index
        return index

    def add_face(self, coordinates, desired_normal):
        cleaned = []
        cleaned_keys = []
        for coordinate in coordinates:
            value = tuple(coordinate)
            key = self._coordinate_key(value)
            if not cleaned_keys or key != cleaned_keys[-1]:
                cleaned.append(value)
                cleaned_keys.append(key)
        if len(cleaned) > 1 and cleaned_keys[0] == cleaned_keys[-1]:
            cleaned.pop()
            cleaned_keys.pop()
        if len(cleaned) < 3:
            return None

        positions = [self._position(coordinate) for coordinate in cleaned]
        normal = _polygon_normal(positions)
        if normal.length < 1e-12:
            return None
        if normal.dot(desired_normal) < 0.0:
            cleaned.reverse()
        face_indices = [
            self._vertex_index(coordinate)
            for coordinate in cleaned
        ]
        self.faces.append(face_indices)
        return face_indices

    def add_uv_bottom_face(
            self, coordinates, desired_normal, bottom_edge_coordinates):
        face_indices = self.add_face(coordinates, desired_normal)
        if face_indices is None:
            return None
        bottom_edge_indices = frozenset(
            self._vertex_index(coordinate)
            for coordinate in bottom_edge_coordinates
        )
        if len(bottom_edge_indices) != 2:
            return face_indices
        self.uv_bottom_edges[frozenset(face_indices)] = bottom_edge_indices
        return face_indices

    def set_uv_source(self, target_face_indices, source_face_indices):
        if target_face_indices is None or source_face_indices is None:
            return
        self.uv_source_faces[frozenset(target_face_indices)] = frozenset(
            source_face_indices
        )


def _upper_profile(layout):
    points = []
    tread_depth = layout['tread_depth']
    for index, tread_height in enumerate(layout['tread_heights']):
        start = index * tread_depth
        end = (index + 1) * tread_depth
        points.append((start, tread_height))
        points.append((end, tread_height))
    return points


def _bottom_aligned_border_profile(layout):
    """Return a ramp through riser bottoms and up to a destination floor."""
    run_length = layout['run_length']
    end_height = layout['last_tread_height']
    slope = end_height / run_length
    for index, tread_height in enumerate(layout['tread_heights'][:-1]):
        tread_end = (index + 1) * layout['tread_depth']
        remaining_run = run_length - tread_end
        slope = max(
            slope,
            (end_height - tread_height) / remaining_run,
        )
    ramp_start = max(0.0, run_length - end_height / slope)
    profile = [(0.0, 0.0)]
    if ramp_start > 1e-9:
        profile.append((ramp_start, 0.0))
    profile.append((run_length, end_height))
    if layout['riser_count'] > layout['tread_count']:
        profile.append((run_length, layout['height']))
    return profile


def _tip_aligned_border_profile(layout):
    """Return a border through every tread tip, including a destination."""
    first_height = layout['tread_heights'][0]
    destination = layout['riser_count'] > layout['tread_count']
    end_height = (
        layout['height']
        if destination
        else layout['last_tread_height']
    )
    if layout['tread_count'] == 1:
        return [
            (0.0, first_height),
            (layout['run_length'], end_height),
        ]

    last_tip_run = (
        (layout['tread_count'] - 1) * layout['tread_depth']
    )
    return [
        (0.0, first_height),
        (last_tip_run, layout['last_tread_height']),
        (layout['run_length'], end_height),
    ]


def _border_profile(layout, border_alignment):
    if border_alignment == BORDER_ALIGN_RISER_BOTTOMS:
        return _bottom_aligned_border_profile(layout)
    if border_alignment == BORDER_ALIGN_STEP_TIPS:
        return _tip_aligned_border_profile(layout)
    raise ValueError(f"Unknown border alignment: {border_alignment}")


def _border_surface_profile(profile):
    if (
            len(profile) > 2
            and profile[0][1] == 0.0
            and profile[1][1] == 0.0
    ):
        return profile[1:]
    return profile


def _profile_height_at(profile, run):
    for index in range(len(profile) - 1):
        start_run, start_height = profile[index]
        end_run, end_height = profile[index + 1]
        if run > end_run and index < len(profile) - 2:
            continue
        segment_length = end_run - start_run
        if segment_length <= 1e-12:
            return end_height
        factor = (run - start_run) / segment_length
        return start_height + (end_height - start_height) * factor
    return profile[-1][1]


def _profile_points_between(profile, start_run, end_run):
    points = [(start_run, _profile_height_at(profile, start_run))]
    points.extend(
        (run, height)
        for run, height in profile[1:-1]
        if start_run < run < end_run
    )
    points.append((end_run, _profile_height_at(profile, end_run)))
    return points


def _profile_face_at_run(profile_faces, run):
    for start_run, end_run, face_indices in profile_faces:
        if start_run - 1e-9 <= run <= end_run + 1e-9:
            return face_indices
    return None


def _build_stair_mesh_data(
        layout, termination, include_final_riser, left_side, right_side,
        back, left_border, right_border, border_alignment, underside):
    if underside not in {
            UNDERSIDE_SOLID,
            UNDERSIDE_SLOPED,
            UNDERSIDE_NONE,
    }:
        raise ValueError(f"Unknown stair underside: {underside}")

    data = _StairMeshData(layout)
    run_axis = layout['run_axis']
    width_axis = layout['width_axis']
    vertical_axis = layout['vertical_axis']
    run_length = layout['run_length']
    width = layout['width']
    tread_depth = layout['tread_depth']
    left_inner = layout['left_border_width']
    right_inner = width - layout['right_border_width']

    riser_faces = []
    previous_height = 0.0
    for index, tread_height in enumerate(layout['tread_heights']):
        start = index * tread_depth
        end = (index + 1) * tread_depth
        riser_faces.append(data.add_uv_bottom_face(
            (
                (start, left_inner, previous_height),
                (start, right_inner, previous_height),
                (start, right_inner, tread_height),
                (start, left_inner, tread_height),
            ),
            -run_axis,
            (
                (start, left_inner, previous_height),
                (start, right_inner, previous_height),
            ),
        ))
        data.add_uv_bottom_face(
            (
                (start, left_inner, tread_height),
                (end, left_inner, tread_height),
                (end, right_inner, tread_height),
                (start, right_inner, tread_height),
            ),
            vertical_axis,
            (
                (start, left_inner, tread_height),
                (start, right_inner, tread_height),
            ),
        )
        previous_height = tread_height

    ramp_profile = _border_profile(layout, border_alignment)
    ramp_surface_profile = _border_surface_profile(ramp_profile)
    ramp_end_height = ramp_profile[-1][1]
    left_border_top_faces = []
    right_border_top_faces = []
    if left_border:
        for profile_index in range(len(ramp_profile) - 1):
            start_run, start_height = ramp_profile[profile_index]
            end_run, end_height = ramp_profile[profile_index + 1]
            if max(start_height, end_height) <= 1e-12:
                continue
            face_indices = data.add_uv_bottom_face(
                (
                    (start_run, 0.0, start_height),
                    (end_run, 0.0, end_height),
                    (end_run, left_inner, end_height),
                    (start_run, left_inner, start_height),
                ),
                vertical_axis,
                (
                    (start_run, 0.0, start_height),
                    (end_run, 0.0, end_height),
                ),
            )
            left_border_top_faces.append(
                (start_run, end_run, face_indices)
            )
    if right_border:
        for profile_index in range(len(ramp_profile) - 1):
            start_run, start_height = ramp_profile[profile_index]
            end_run, end_height = ramp_profile[profile_index + 1]
            if max(start_height, end_height) <= 1e-12:
                continue
            face_indices = data.add_uv_bottom_face(
                (
                    (start_run, right_inner, start_height),
                    (end_run, right_inner, end_height),
                    (end_run, width, end_height),
                    (start_run, width, start_height),
                ),
                vertical_axis,
                (
                    (start_run, width, start_height),
                    (end_run, width, end_height),
                ),
            )
            right_border_top_faces.append(
                (start_run, end_run, face_indices)
            )

    if border_alignment == BORDER_ALIGN_STEP_TIPS:
        front_height = layout['tread_heights'][0]
        if left_border:
            data.add_face(
                (
                    (0.0, 0.0, 0.0),
                    (0.0, left_inner, 0.0),
                    (0.0, left_inner, front_height),
                    (0.0, 0.0, front_height),
                ),
                -run_axis,
            )
        if right_border:
            data.add_face(
                (
                    (0.0, right_inner, 0.0),
                    (0.0, width, 0.0),
                    (0.0, width, front_height),
                    (0.0, right_inner, front_height),
                ),
                -run_axis,
            )

    stair_profile = _upper_profile(layout)
    if left_border:
        desired_normal = (
            width_axis
            if border_alignment == BORDER_ALIGN_STEP_TIPS
            else -width_axis
        )
        for index, tread_height in enumerate(layout['tread_heights']):
            start_run = index * tread_depth
            end_run = (index + 1) * tread_depth
            border_points = _profile_points_between(
                ramp_profile,
                start_run,
                end_run,
            )
            side_cap_face = data.add_face(
                (
                    [(start_run, left_inner, tread_height),
                     (end_run, left_inner, tread_height)]
                    + [
                        (run, left_inner, height)
                        for run, height in reversed(border_points)
                    ]
                ),
                desired_normal,
            )
            source_face = (
                riser_faces[index]
                if border_alignment == BORDER_ALIGN_RISER_BOTTOMS
                else _profile_face_at_run(
                    left_border_top_faces,
                    (start_run + end_run) * 0.5,
                )
            )
            data.set_uv_source(side_cap_face, source_face)
    if right_border:
        desired_normal = (
            -width_axis
            if border_alignment == BORDER_ALIGN_STEP_TIPS
            else width_axis
        )
        for index, tread_height in enumerate(layout['tread_heights']):
            start_run = index * tread_depth
            end_run = (index + 1) * tread_depth
            border_points = _profile_points_between(
                ramp_profile,
                start_run,
                end_run,
            )
            side_cap_face = data.add_face(
                (
                    [(start_run, right_inner, tread_height),
                     (end_run, right_inner, tread_height)]
                    + [
                        (run, right_inner, height)
                        for run, height in reversed(border_points)
                    ]
                ),
                desired_normal,
            )
            source_face = (
                riser_faces[index]
                if border_alignment == BORDER_ALIGN_RISER_BOTTOMS
                else _profile_face_at_run(
                    right_border_top_faces,
                    (start_run + end_run) * 0.5,
                )
            )
            data.set_uv_source(side_cap_face, source_face)

    sloped_underside_edge = None
    if underside in {UNDERSIDE_SOLID, UNDERSIDE_NONE}:
        underside_profile = [(0.0, 0.0), (run_length, 0.0)]
    else:
        last_generated_riser = layout['riser_heights'][layout['tread_count'] - 1]
        underside_end_height = max(
            0.0,
            layout['last_tread_height'] - last_generated_riser,
        )
        underside_reference = _bottom_aligned_border_profile(layout)
        ramp_segment_start = None
        ramp_segment_end = None
        for profile_index in range(len(underside_reference) - 2, -1, -1):
            candidate_start = underside_reference[profile_index]
            candidate_end = underside_reference[profile_index + 1]
            if abs(candidate_end[0] - candidate_start[0]) > 1e-12:
                ramp_segment_start = candidate_start
                ramp_segment_end = candidate_end
                break
        if ramp_segment_start is None or ramp_segment_end is None:
            raise ValueError("Could not calculate the sloping underside")
        ramp_slope = (
            (ramp_segment_end[1] - ramp_segment_start[1])
            / (ramp_segment_end[0] - ramp_segment_start[0])
        )
        underside_start = max(
            0.0,
            run_length - underside_end_height / ramp_slope,
        )
        underside_profile = [(0.0, 0.0)]
        if underside_start > 1e-9:
            underside_profile.append((underside_start, 0.0))
        underside_profile.append((run_length, underside_end_height))
        sloped_underside_edge = (
            (underside_start, 0.0),
            (run_length, underside_end_height),
        )

    if underside != UNDERSIDE_NONE:
        for profile_index in range(len(underside_profile) - 1):
            start_run, start_height = underside_profile[profile_index]
            end_run, end_height = underside_profile[profile_index + 1]
            data.add_face(
                (
                    (start_run, 0.0, start_height),
                    (start_run, width, start_height),
                    (end_run, width, end_height),
                    (end_run, 0.0, end_height),
                ),
                -vertical_axis,
            )

    underside_end_height = underside_profile[-1][1]

    left_top_profile = ramp_surface_profile if left_border else stair_profile
    right_top_profile = ramp_surface_profile if right_border else stair_profile
    lower_profile = list(reversed(underside_profile))
    if left_side:
        coordinates = (
            [(run, 0.0, height) for run, height in left_top_profile]
            + [(run, 0.0, height) for run, height in lower_profile]
        )
        if sloped_underside_edge is None:
            data.add_face(coordinates, -width_axis)
        else:
            data.add_uv_bottom_face(
                coordinates,
                -width_axis,
                tuple(
                    (run, 0.0, height)
                    for run, height in sloped_underside_edge
                ),
            )
    if right_side:
        coordinates = (
            [(run, width, height) for run, height in right_top_profile]
            + [(run, width, height) for run, height in lower_profile]
        )
        if sloped_underside_edge is None:
            data.add_face(coordinates, width_axis)
        else:
            data.add_uv_bottom_face(
                coordinates,
                width_axis,
                tuple(
                    (run, width, height)
                    for run, height in sloped_underside_edge
                ),
            )

    if back:
        back_sections = []
        if left_border:
            back_sections.append((0.0, left_inner, ramp_end_height))
        back_sections.append((left_inner, right_inner, layout['last_tread_height']))
        if right_border:
            back_sections.append((right_inner, width, ramp_end_height))
        for start_width, end_width, top_height in back_sections:
            data.add_face(
                (
                    (run_length, start_width, underside_end_height),
                    (run_length, end_width, underside_end_height),
                    (run_length, end_width, top_height),
                    (run_length, start_width, top_height),
                ),
                run_axis,
            )

    if termination == TERMINATION_DESTINATION and include_final_riser:
        final_riser_sections = []
        if left_border:
            final_riser_sections.append(
                (0.0, left_inner, ramp_end_height)
            )
        final_riser_sections.append(
            (left_inner, right_inner, layout['last_tread_height'])
        )
        if right_border:
            final_riser_sections.append(
                (right_inner, width, ramp_end_height)
            )
        for start_width, end_width, bottom_height in final_riser_sections:
            data.add_uv_bottom_face(
                (
                    (run_length, start_width, bottom_height),
                    (run_length, end_width, bottom_height),
                    (run_length, end_width, layout['height']),
                    (run_length, start_width, layout['height']),
                ),
                -run_axis,
                (
                    (run_length, start_width, bottom_height),
                    (run_length, end_width, bottom_height),
                ),
            )

    return data


def build_flat_stair_preview(
        first_vertex, second_vertex, local_x, local_y, local_z, orientation):
    """Return the incomplete red footprint and uphill arrow before extrusion."""
    first = Vector(first_vertex)
    second = Vector(second_vertex)
    axes = [
        Vector(local_x).normalized(),
        Vector(local_y).normalized(),
        Vector(local_z).normalized(),
    ]
    difference = second - first
    signed_lengths = [
        difference.dot(axes[0]),
        difference.dot(axes[1]),
        0.0,
    ]
    corners = [
        first,
        first + axes[0] * signed_lengths[0],
        second,
        first + axes[1] * signed_lengths[1],
    ]

    world_up = Vector((0.0, 0.0, 1.0))
    vertical_index = max(
        range(3),
        key=lambda index: abs(axes[index].dot(world_up)),
    )
    vertical_axis = axes[vertical_index]
    if vertical_axis.dot(world_up) < 0.0:
        vertical_axis = -vertical_axis
    horizontal_indices = [
        index for index in range(3)
        if index != vertical_index
    ]
    run_slot, run_sign = _orientation_slot_and_sign(orientation)
    run_index = horizontal_indices[run_slot]
    width_index = horizontal_indices[1 - run_slot]

    run_axis = axes[run_index]
    run_draw_length = signed_lengths[run_index]
    if run_draw_length < 0.0:
        run_axis = -run_axis
    if run_sign < 0.0:
        run_axis = -run_axis

    width_axis = axes[width_index]
    right_axis = run_axis.cross(vertical_axis)
    if right_axis.length > 1e-8:
        right_axis.normalize()
        if width_axis.dot(right_axis) < 0.0:
            width_axis = -width_axis

    rectangle_center = sum(
        corners,
        Vector((0.0, 0.0, 0.0)),
    ) / len(corners)
    available_length = max(
        abs(signed_lengths[0]),
        abs(signed_lengths[1]),
        MIN_RECTANGLE_SIZE * 10.0,
    )
    run_length = (
        abs(run_draw_length)
        if abs(run_draw_length) >= MIN_RECTANGLE_SIZE
        else available_length
    )
    half_arrow_length = run_length * 0.22
    arrow_start = rectangle_center - run_axis * half_arrow_length
    arrow_end = rectangle_center + run_axis * half_arrow_length
    head_length = available_length * 0.16
    head_width = head_length * 0.55
    arrow_left = (
        arrow_end
        - run_axis * head_length
        - width_axis * head_width
    )
    arrow_right = (
        arrow_end
        - run_axis * head_length
        + width_axis * head_width
    )

    vertices = corners + [
        arrow_start,
        arrow_end,
        arrow_left,
        arrow_right,
    ]
    edges = [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (5, 7),
    ]
    measurements = [
        (corners[index], corners[(index + 1) % len(corners)], None)
        for index in range(len(corners))
    ]
    return (vertices, edges, measurements)


def build_stair_preview(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation, sizing_mode, step_count, target_step_height,
        height_distribution, termination, include_final_riser, left_side,
        right_side, back, left_border, right_border, border_width,
        border_alignment, underside):
    """Return world-space stair wire data, an uphill arrow, and dimensions."""
    layout = calculate_stair_layout(
        first_vertex,
        second_vertex,
        depth,
        local_x,
        local_y,
        local_z,
        orientation,
        sizing_mode,
        step_count,
        target_step_height,
        height_distribution,
        termination,
        left_border,
        right_border,
        border_width,
    )
    data = _build_stair_mesh_data(
        layout,
        termination,
        include_final_riser,
        left_side,
        right_side,
        back,
        left_border,
        right_border,
        border_alignment,
        underside,
    )

    edge_set = set()
    edges = []
    for face in data.faces:
        for index, start in enumerate(face):
            end = face[(index + 1) % len(face)]
            key = tuple(sorted((start, end)))
            if key in edge_set:
                continue
            edge_set.add(key)
            edges.append((start, end))

    box_origin = layout['origin']
    box_run = layout['run_axis'] * layout['run_length']
    box_width = layout['width_axis'] * layout['width']
    box_height = layout['vertical_axis'] * layout['height']
    box_vertex_index = len(data.vertices)
    data.vertices.extend((
        box_origin,
        box_origin + box_run,
        box_origin + box_run + box_width,
        box_origin + box_width,
        box_origin + box_height,
        box_origin + box_run + box_height,
        box_origin + box_run + box_width + box_height,
        box_origin + box_width + box_height,
    ))
    edges.extend((
        (box_vertex_index, box_vertex_index + 1),
        (box_vertex_index + 1, box_vertex_index + 2),
        (box_vertex_index + 2, box_vertex_index + 3),
        (box_vertex_index + 3, box_vertex_index),
        (box_vertex_index + 4, box_vertex_index + 5),
        (box_vertex_index + 5, box_vertex_index + 6),
        (box_vertex_index + 6, box_vertex_index + 7),
        (box_vertex_index + 7, box_vertex_index + 4),
        (box_vertex_index, box_vertex_index + 4),
        (box_vertex_index + 1, box_vertex_index + 5),
        (box_vertex_index + 2, box_vertex_index + 6),
        (box_vertex_index + 3, box_vertex_index + 7),
    ))

    arrow_clearance = max(
        MIN_RECTANGLE_SIZE * 10.0,
        min(layout['run_length'], layout['width'], layout['height']) * 0.08,
    )
    arrow_center = (
        layout['origin']
        + layout['run_axis'] * (layout['run_length'] * 0.5)
        + layout['width_axis'] * (layout['width'] * 0.5)
        + layout['vertical_axis'] * (layout['height'] + arrow_clearance)
    )
    half_arrow_length = layout['run_length'] * 0.22
    arrow_start = arrow_center - layout['run_axis'] * half_arrow_length
    arrow_end = arrow_center + layout['run_axis'] * half_arrow_length
    head_length = min(layout['run_length'], layout['width']) * 0.16
    head_width = head_length * 0.55
    arrow_left = (
        arrow_end
        - layout['run_axis'] * head_length
        - layout['width_axis'] * head_width
    )
    arrow_right = (
        arrow_end
        - layout['run_axis'] * head_length
        + layout['width_axis'] * head_width
    )
    arrow_start_index = len(data.vertices)
    data.vertices.extend((arrow_start, arrow_end, arrow_left, arrow_right))
    edges.extend((
        (arrow_start_index, arrow_start_index + 1),
        (arrow_start_index + 1, arrow_start_index + 2),
        (arrow_start_index + 1, arrow_start_index + 3),
    ))

    measurements = [
        (
            layout['origin'],
            layout['origin'] + layout['run_axis'] * layout['run_length'],
            None,
        ),
        (
            layout['origin'],
            layout['origin'] + layout['width_axis'] * layout['width'],
            None,
        ),
        (
            layout['origin'],
            layout['origin'] + layout['vertical_axis'] * layout['height'],
            None,
        ),
    ]
    return (data.vertices, edges, measurements)


def _create_bmesh_geometry(bm, positions, face_indices):
    vertices = [bm.verts.new(position) for position in positions]
    faces = []
    for face_indices_item in face_indices:
        try:
            faces.append(bm.faces.new([
                vertices[index]
                for index in face_indices_item
            ]))
        except ValueError:
            continue
    return (vertices, faces)


def _uv_position_key(position):
    return tuple(round(value, 9) for value in position)


def _uv_bottom_edge_lookup(data, transformed_positions):
    lookup = {}
    for face_indices, edge_indices in data.uv_bottom_edges.items():
        face_key = frozenset(
            _uv_position_key(transformed_positions[index])
            for index in face_indices
        )
        edge_key = frozenset(
            _uv_position_key(transformed_positions[index])
            for index in edge_indices
        )
        lookup[face_key] = edge_key
    return lookup


def _align_face_uv_bottom_edge(
        face, uv_layer, bottom_edge_key, ppm, me):
    loops = list(face.loops)
    for edge_index, loop in enumerate(loops):
        following = loops[(edge_index + 1) % len(loops)]
        loop_edge_key = frozenset((
            _uv_position_key(loop.vert.co),
            _uv_position_key(following.vert.co),
        ))
        if loop_edge_key != bottom_edge_key:
            continue

        transform = derive_transform_from_uvs(face, uv_layer, ppm, me)
        scale_u = transform['scale_u'] if transform is not None else 1.0
        scale_v = transform['scale_v'] if transform is not None else 1.0
        if abs(scale_u) < 1e-8:
            scale_u = 1.0
        if abs(scale_v) < 1e-8:
            scale_v = 1.0

        face_axes = get_face_local_axes(face)
        if face_axes is None:
            return False
        face_local_x, face_local_y = face_axes
        edge = following.vert.co - loop.vert.co
        edge_angle = math.atan2(
            edge.dot(face_local_y),
            edge.dot(face_local_x),
        )
        cos_rotation = math.cos(-edge_angle)
        sin_rotation = math.sin(-edge_angle)
        projection_x = (
            face_local_x * cos_rotation
            - face_local_y * sin_rotation
        )
        projection_y = (
            face_local_x * sin_rotation
            + face_local_y * cos_rotation
        )
        material = (
            me.materials[face.material_index]
            if face.material_index < len(me.materials)
            else None
        )
        texture_meters_u, texture_meters_v = (
            get_texture_dimensions_from_material(material, ppm)
        )
        origin = loops[0].vert.co
        for item in loops:
            delta = item.vert.co - origin
            item[uv_layer].uv = (
                delta.dot(projection_x) / (scale_u * texture_meters_u),
                delta.dot(projection_y) / (scale_v * texture_meters_v),
            )

        shape = [
            (item[uv_layer].uv.x, item[uv_layer].uv.y)
            for item in loops
        ]
        aligned = align_2d_shape_to_square(shape, edge_index, 0)
        for item, uv in zip(loops, aligned):
            item[uv_layer].uv = uv
        return True
    return False


def _set_face_uv_x_offset(face, uv_layer, offset_x):
    loops = list(face.loops)
    if not loops:
        return
    offset_delta = offset_x - loops[0][uv_layer].uv.x
    for loop in loops:
        loop[uv_layer].uv.x += offset_delta


def _align_stair_uv_bottom_edges(
        data, transformed_positions, faces, uv_layer, ppm, me,
        uv_random_seed):
    lookup = _uv_bottom_edge_lookup(data, transformed_positions)
    random_generator = random.Random(uv_random_seed)
    aligned_faces = []
    for face in faces:
        if not face.is_valid:
            continue
        face_key = frozenset(
            _uv_position_key(vertex.co)
            for vertex in face.verts
        )
        bottom_edge_key = lookup.get(face_key)
        if bottom_edge_key is None:
            continue
        if _align_face_uv_bottom_edge(
                face, uv_layer, bottom_edge_key, ppm, me):
            _set_face_uv_x_offset(
                face,
                uv_layer,
                random_generator.random(),
            )
            aligned_faces.append(face)
    return aligned_faces


def _transfer_stair_uv_sources(
        data, transformed_positions, faces, uv_layer, ppm, me, obj_matrix,
        bm):
    face_lookup = {
        frozenset(
            _uv_position_key(vertex.co)
            for vertex in face.verts
        ): face
        for face in faces
        if face.is_valid
    }
    transferred_faces = []
    for target_indices, source_indices in data.uv_source_faces.items():
        target_key = frozenset(
            _uv_position_key(transformed_positions[index])
            for index in target_indices
        )
        source_key = frozenset(
            _uv_position_key(transformed_positions[index])
            for index in source_indices
        )
        target_face = face_lookup.get(target_key)
        source_face = face_lookup.get(source_key)
        if target_face is None or source_face is None:
            continue
        if _dispatch_set_uv_from_other_face(
                source_face,
                target_face,
                uv_layer,
                ppm,
                me,
                obj_matrix,
                bm=bm,
        ):
            transferred_faces.append(target_face)
    return transferred_faces


def _mesh_data_from_parameters(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation, sizing_mode, step_count, target_step_height,
        height_distribution, termination, include_final_riser, left_side,
        right_side, back, left_border, right_border, border_width,
        border_alignment, underside):
    layout = calculate_stair_layout(
        first_vertex,
        second_vertex,
        depth,
        local_x,
        local_y,
        local_z,
        orientation,
        sizing_mode,
        step_count,
        target_step_height,
        height_distribution,
        termination,
        left_border,
        right_border,
        border_width,
    )
    return _build_stair_mesh_data(
        layout,
        termination,
        include_final_riser,
        left_side,
        right_side,
        back,
        left_border,
        right_border,
        border_alignment,
        underside,
    )


def execute_stair_builder_edit_mode(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation, sizing_mode, step_count, target_step_height,
        height_distribution, termination, include_final_riser, left_side,
        right_side, back, left_border, right_border, border_width,
        border_alignment, underside, uv_random_seed, obj, ppm):
    """Add a stair mesh to the active edit-mode object."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")

    try:
        data = _mesh_data_from_parameters(
            first_vertex,
            second_vertex,
            depth,
            local_x,
            local_y,
            local_z,
            orientation,
            sizing_mode,
            step_count,
            target_step_height,
            height_distribution,
            termination,
            include_final_riser,
            left_side,
            right_side,
            back,
            left_border,
            right_border,
            border_width,
            border_alignment,
            underside,
        )
    except ValueError as error:
        return (False, f"Invalid stair geometry: {error}")

    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    active_face = bm.faces.active
    has_source_face = (
        active_face is not None
        and active_face.is_valid
        and not active_face.hide
        and active_face.select
    )
    default_material = None
    if not has_source_face:
        try:
            default_material = _active_or_previous_material()
        except MaterialMappingConflictError as error:
            return (False, f"{error}. Use Fix Material Mappings (Shift-4).")

    uv_layer = get_render_active_uv_layer(bm, me)
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.new("UVMap")
    get_face_id_layer(bm)
    source_face = bm.faces.active if has_source_face else None
    world_to_local = obj.matrix_world.inverted()
    local_positions = [world_to_local @ position for position in data.vertices]
    new_vertices, new_faces = _create_bmesh_geometry(
        bm,
        local_positions,
        data.faces,
    )
    if not new_faces:
        bmesh.update_edit_mesh(me)
        return (False, "Failed to create stair geometry")

    bm.normal_update()
    _apply_material_and_uvs(
        bm,
        new_faces,
        source_face,
        default_material,
        uv_layer,
        ppm,
        me,
        obj,
    )
    aligned_faces = _align_stair_uv_bottom_edges(
        data,
        local_positions,
        new_faces,
        uv_layer,
        ppm,
        me,
        uv_random_seed,
    )
    for face in aligned_faces:
        cache_single_face(face, bm, ppm, me)
    _transfer_stair_uv_sources(
        data,
        local_positions,
        new_faces,
        uv_layer,
        ppm,
        me,
        obj.matrix_world,
        bm,
    )

    new_face_vertex_positions = []
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    for face in new_faces:
        if not face.is_valid:
            continue
        face.select = True
        new_face_vertex_positions.append(
            (face.index, frozenset(tuple(vertex.co) for vertex in face.verts))
        )
    bm.select_flush(True)
    bmesh.update_edit_mesh(me)
    return (True, "Stair created", new_face_vertex_positions)


def execute_stair_builder_object_mode(
        first_vertex, second_vertex, depth, local_x, local_y, local_z,
        orientation, sizing_mode, step_count, target_step_height,
        height_distribution, termination, include_final_riser, left_side,
        right_side, back, left_border, right_border, border_width,
        border_alignment, underside, uv_random_seed, ppm, name_suffix):
    """Create a new stair object."""
    try:
        data = _mesh_data_from_parameters(
            first_vertex,
            second_vertex,
            depth,
            local_x,
            local_y,
            local_z,
            orientation,
            sizing_mode,
            step_count,
            target_step_height,
            height_distribution,
            termination,
            include_final_riser,
            left_side,
            right_side,
            back,
            left_border,
            right_border,
            border_width,
            border_alignment,
            underside,
        )
    except ValueError as error:
        return (False, f"Invalid stair geometry: {error}")

    try:
        material = _active_or_previous_material()
    except MaterialMappingConflictError as error:
        return (False, f"{error}. Use Fix Material Mappings (Shift-4).")

    object_origin = Vector(first_vertex)
    local_positions = [position - object_origin for position in data.vertices]
    bm = bmesh.new()
    _new_vertices, new_faces = _create_bmesh_geometry(
        bm,
        local_positions,
        data.faces,
    )
    if not new_faces:
        bm.free()
        return (False, "Failed to create stair geometry")
    bm.normal_update()

    data_block_name = _next_box_builder_datablock_name(
        "Anvil.Stair",
        name_suffix,
    )
    me = bpy.data.meshes.new(data_block_name)
    obj = bpy.data.objects.new(data_block_name, me)
    obj.location = object_origin
    bpy.context.collection.objects.link(obj)
    bm.to_mesh(me)
    bm.free()

    for scene_object in bpy.context.view_layer.objects:
        scene_object.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    material_index = None
    if material is not None:
        material_index = ensure_material_slot(me, material)
    bpy.ops.object.mode_set(mode='EDIT')
    bm_edit = bmesh.from_edit_mesh(me)
    bm_edit.faces.ensure_lookup_table()
    uv_layer = get_render_active_uv_layer(bm_edit, me)
    if uv_layer is None:
        uv_layer = bm_edit.loops.layers.uv.new("UVMap")
    for face in bm_edit.faces:
        if not face.is_valid:
            continue
        if material_index is not None:
            face.material_index = material_index
        box_project(face, uv_layer, material, ppm, 1.0)
        cache_single_face(face, bm_edit, ppm, me)
    aligned_faces = _align_stair_uv_bottom_edges(
        data,
        local_positions,
        list(bm_edit.faces),
        uv_layer,
        ppm,
        me,
        uv_random_seed,
    )
    for face in aligned_faces:
        cache_single_face(face, bm_edit, ppm, me)
    _transfer_stair_uv_sources(
        data,
        local_positions,
        list(bm_edit.faces),
        uv_layer,
        ppm,
        me,
        obj.matrix_world,
        bm_edit,
    )
    bmesh.update_edit_mesh(me)
    bpy.ops.object.mode_set(mode='OBJECT')

    return (True, "Stair object created")
