"""Shared freeform prism profile drawing workflow for cut and builder tools."""

import json

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Matrix, Vector
from mathutils.geometry import intersect_line_line_2d

from . import snapping
from .base_operator import MIN_RECTANGLE_SIZE
from ..mesh_cut.concave_prism import build_profile_prism


PROFILE_CLOSE_THRESHOLD_PIXELS = 14.0


class PrismProfileDrawMixin:
    """Draw a simple polygon profile point by point, then extrude it."""

    def invoke(self, context, event):
        self._profile_vertices = []
        self._profile_candidate = None
        self._closing_candidate = False
        self._on_prism_profile_invoked()
        return super().invoke(context, event)

    def _on_prism_profile_invoked(self):
        """Allow a consumer to initialise data tied to its profile."""

    def _on_prism_profile_changed(self, context):
        """Allow a consumer to refresh data tied to its profile."""

    def _is_line_mode_key_held(self, context, event):
        return False

    def _confirm_first_vertex(self, context, event):
        result = super()._confirm_first_vertex(context, event)
        if self._first_vertex is None:
            return result

        self._profile_vertices = [self._first_vertex.copy()]
        self._profile_candidate = self._first_vertex.copy()
        self._second_vertex = self._first_vertex.copy()
        self._preview.update_open_custom_profile(
            self._profile_vertices + [self._profile_candidate]
        )
        self._set_invalid_message("Move away from the first point")
        self._update_header(context)
        return result

    def _update_second_vertex_preview(self, context, event):
        if not self._profile_vertices:
            return

        candidate = snapping.calculate_second_vertex_snap(
            context,
            event,
            self._first_vertex,
            self._local_x,
            self._local_y,
            self._plane_point,
            self._plane_normal,
        )
        if candidate is None:
            return

        self._closing_candidate = self._candidate_closes_profile(
            context,
            event,
            candidate,
        )
        if self._closing_candidate:
            candidate = self._profile_vertices[0].copy()

        self._profile_candidate = candidate
        self._second_vertex = candidate
        self._preview.update_open_custom_profile(
            self._profile_vertices + [candidate]
        )
        self._set_invalid_message(
            self._profile_candidate_invalid_message(
                candidate,
                self._closing_candidate,
            )
        )
        self._on_prism_profile_changed(context)
        self._update_header(context)

    def _confirm_second_vertex(self, context, event):
        if self._profile_candidate is None:
            return {'RUNNING_MODAL'}

        invalid_message = self._profile_candidate_invalid_message(
            self._profile_candidate,
            self._closing_candidate,
        )
        if invalid_message is not None:
            self._set_invalid_message(invalid_message)
            self._update_header(context)
            return {'RUNNING_MODAL'}

        if not self._closing_candidate:
            self._profile_vertices.append(self._profile_candidate.copy())
            self._preview.update_open_custom_profile(
                self._profile_vertices + [self._profile_candidate]
            )
            self._set_invalid_message("Move away from the last point")
            self._on_prism_profile_changed(context)
            self._update_header(context)
            return {'RUNNING_MODAL'}

        self._second_vertex = self._profile_vertices[0].copy()
        self._preview.update_custom_profile(self._profile_vertices, ())
        self._preview.set_state(self.STATE_DEPTH)
        self._state = self.STATE_DEPTH
        self._depth_start_mouse_pos = (
            event.mouse_region_x,
            event.mouse_region_y,
        )
        self._depth_cursor_wrap_offset = 0
        self._depth = 0.0
        self._preview.update_depth(0.0)
        self._set_invalid_message(self._get_depth_invalid_message(0.0))
        self._on_prism_profile_changed(context)
        self._update_header(context)
        return {'RUNNING_MODAL'}

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._on_prism_profile_changed(context)

    def _candidate_closes_profile(self, context, event, candidate):
        if len(self._profile_vertices) < 2:
            return False
        if (candidate - self._profile_vertices[0]).length <= MIN_RECTANGLE_SIZE:
            return True

        screen_point = location_3d_to_region_2d(
            context.region,
            context.region_data,
            self._profile_vertices[0],
        )
        if screen_point is None:
            return False
        mouse_point = Vector((event.mouse_region_x, event.mouse_region_y))
        return (
            mouse_point - screen_point
        ).length <= PROFILE_CLOSE_THRESHOLD_PIXELS

    def _profile_candidate_invalid_message(self, candidate, closing):
        return profile_candidate_invalid_message(
            self._profile_vertices,
            candidate,
            self._local_x,
            self._local_y,
            self._local_z,
            closing,
        )

    def _update_header(self, context):
        tool_name = self._get_tool_name()
        if self._state == self.STATE_FIRST_VERTEX:
            lock_hint = (
                " (Axis Locked)"
                if self._axis_lock_normal is not None
                else " | Ctrl to lock axis"
            )
            text = (
                f"{tool_name}: Click to set first point{lock_hint} | "
                "ESC to cancel"
            )
        elif self._state == self.STATE_SECOND_VERTEX:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            elif self._closing_candidate:
                prompt = "Click to close the profile"
            else:
                prompt = "Click to add point | Connect to first point to close"
            text = f"{tool_name}: {prompt} | ESC to cancel"
        elif self._state == self.STATE_DEPTH:
            text = (
                f"{tool_name}: Move mouse to set depth ({self._depth:.3f}) | "
                "Click to confirm | ESC to cancel"
            )
        else:
            text = f"{tool_name} | ESC to cancel"
        if context.area is not None:
            context.area.header_text_set(text)


def profile_to_json(profile_vertices):
    return json.dumps([list(vertex) for vertex in profile_vertices])


def profile_from_json(profile_json):
    values = json.loads(profile_json)
    if not isinstance(values, list):
        raise ValueError("Profile must be a list")
    return [Vector(value) for value in values]


def profile_candidate_invalid_message(
        profile_vertices, candidate, local_x, local_y, local_z, closing):
    """Return why a candidate edge cannot be accepted, or None when valid."""
    if len(profile_vertices) < 1:
        return "Click to set the first point"
    if closing and len(profile_vertices) < 3:
        return "Add at least three profile points"

    edge_start = profile_vertices[-1]
    if (candidate - edge_start).length < MIN_RECTANGLE_SIZE:
        return "Move away from the last point"

    for index, vertex in enumerate(profile_vertices):
        if closing and index == 0:
            continue
        if (candidate - vertex).length < MIN_RECTANGLE_SIZE:
            return "Choose a point that has not already been used"

    def profile_point_2d(point):
        offset = point - profile_vertices[0]
        return Vector((offset.dot(local_x), offset.dot(local_y)))

    edge_start_2d = profile_point_2d(edge_start)
    candidate_2d = profile_point_2d(candidate)
    edge_count = len(profile_vertices) - 1
    for edge_index in range(edge_count):
        if edge_index == edge_count - 1:
            continue
        if closing and edge_index == 0:
            continue
        existing_start = profile_point_2d(profile_vertices[edge_index])
        existing_end = profile_point_2d(profile_vertices[edge_index + 1])
        if intersect_line_line_2d(
                edge_start_2d,
                candidate_2d,
                existing_start,
                existing_end) is not None:
            return "Profile edges must not cross"

    if closing:
        try:
            build_profile_prism(
                Matrix.Identity(4),
                profile_vertices,
                Vector(local_z).normalized(),
            )
        except ValueError as error:
            return str(error)
    return None
