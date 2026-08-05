"""Prism Cut modal operator."""

import json

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Matrix, Vector
from mathutils.geometry import intersect_line_line_2d

from . import geometry
from .prism import build_prism_cut_prism
from ..mesh_cut.analysis import analyze_convex_prism_cut
from ..mesh_cut.execution import (
    RECONSTRUCTION_MODE_NGONS,
    RECONSTRUCTION_MODE_QUADS,
)
from ..cube_cut.operator import _build_world_cut_vertex_markers
from ..modal_draw import snapping
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE, ModalDrawBase
from ..pending_mesh_action import store_prism_from_edge_selection
from ...core.logging import debug_log
from ...core.workspace_check import is_level_design_workspace
from ...handlers.face_cache import cache_face_data_for_objects


PROFILE_CLOSE_THRESHOLD_PIXELS = 14.0


class MESH_OT_prism_cut(ModalDrawBase, bpy.types.Operator):
    """Draw and extrude a simple polygon to cut a prism-shaped void"""

    bl_idname = "leveldesign.prism_cut"
    bl_label = "Prism Cut"
    bl_options = {'REGISTER', 'UNDO'}

    reconstruction_mode: EnumProperty(
        name="Face Reconstruction",
        description="Choose how cut faces are reconstructed",
        items=(
            (
                RECONSTRUCTION_MODE_QUADS,
                "Reconstruct Quads",
                "Reconstruct cut surfaces as quads where possible",
            ),
            (
                RECONSTRUCTION_MODE_NGONS,
                "Reconstruct Ngons",
                "Use the fewest face-local connector edges required for valid topology",
            ),
        ),
        default=RECONSTRUCTION_MODE_QUADS,
    )
    action_profile_json: StringProperty(
        options={'HIDDEN'},
    )
    action_depth: FloatProperty(
        options={'HIDDEN'},
    )
    action_local_z: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_matrix_world: FloatVectorProperty(
        size=16,
        default=(
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ),
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def draw(self, context):
        self.layout.prop(self, "reconstruction_mode")

    def invoke(self, context, event):
        self._profile_vertices = []
        self._profile_candidate = None
        self._closing_candidate = False
        self._cut_preview_dimensions = None
        return super().invoke(context, event)

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
        self._cut_preview_dimensions = None
        self._update_cut_vertex_preview(context)
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
            self._cut_preview_dimensions = None
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
        self._cut_preview_dimensions = None
        self._update_cut_vertex_preview(context)
        self._update_header(context)
        return {'RUNNING_MODAL'}

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._cut_preview_dimensions = None
        self._update_cut_vertex_preview(context)

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

    def _on_info_visibility_changed(self, context, visible):
        self._cut_preview_dimensions = None
        if visible:
            self._update_cut_vertex_preview(context)
        else:
            self._preview.update_cut_vertex_markers([])

    def _update_cut_vertex_preview(self, context):
        if not self._preview.is_info_visible():
            self._preview.update_cut_vertex_markers([])
            return
        if len(self._profile_vertices) < 3:
            self._preview.update_cut_vertex_markers([])
            return
        if self._state == self.STATE_SECOND_VERTEX:
            if not self._closing_candidate or self._invalid_message is not None:
                self._preview.update_cut_vertex_markers([])
                return

        preview_depth = self._depth if self._state == self.STATE_DEPTH else 0.0
        dimensions = (
            tuple(tuple(vertex) for vertex in self._profile_vertices),
            preview_depth,
        )
        if dimensions == self._cut_preview_dimensions:
            return
        self._cut_preview_dimensions = dimensions

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self._preview.update_cut_vertex_markers([])
            return

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            prism = build_prism_cut_prism(
                obj.matrix_world,
                self._profile_vertices,
                preview_depth,
                self._local_z,
            )
            analysis = analyze_convex_prism_cut(bm, prism)
            markers = _build_world_cut_vertex_markers(
                obj.matrix_world,
                analysis.candidate_vertex_markers,
            )
        except Exception as error:
            print(
                "Level Design Tools: Error updating Prism Cut vertex preview: "
                f"{error}"
            )
            markers = []
        self._preview.update_cut_vertex_markers(markers)

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))
        return self._execute_prism_cut(
            context,
            _profile_from_json(self.action_profile_json),
            depth,
            local_z,
            self.reconstruction_mode,
            matrix_world,
        )

    def _execute_prism_cut(self, context, profile_vertices, depth, local_z,
                           reconstruction_mode, matrix_world):
        obj = context.active_object
        pixels_per_meter = context.scene.level_design_props.pixels_per_meter
        result = geometry.execute_prism_cut_with_reconstruction(
            obj,
            context.tool_settings,
            pixels_per_meter,
            profile_vertices,
            depth,
            local_z,
            reconstruction_mode,
            matrix_world,
        )
        if result[0]:
            cache_face_data_for_objects(
                context.view_layer.objects,
                pixels_per_meter,
            )
            extrude_direction = -Vector(local_z).normalized()
            back_point = (
                Vector(profile_vertices[0]) + Vector(local_z) * depth
            )
            back_plane_offset = back_point.dot(extrude_direction)
            store_prism_from_edge_selection(
                obj,
                abs(depth),
                extrude_direction,
                back_plane_offset,
            )
            debug_log(
                f"[PrismCut] Stored {len(profile_vertices)}-point profile "
                f"at depth {depth:.4f}"
            )
        return result

    def _capture_action_properties(self, context, first_vertex, second_vertex,
                                   depth, local_x, local_y, local_z):
        self.action_profile_json = _profile_to_json(self._profile_vertices)
        self.action_depth = depth
        self.action_local_z = local_z
        self.action_matrix_world = tuple(
            value
            for row in context.active_object.matrix_world
            for value in row
        )

    def execute(self, context):
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))
        try:
            profile_vertices = _profile_from_json(self.action_profile_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            result = (False, f"Invalid captured Prism Cut profile: {error}")
        else:
            result = self._execute_prism_cut(
                context,
                profile_vertices,
                self.action_depth,
                Vector(self.action_local_z),
                self.reconstruction_mode,
                matrix_world,
            )
        self._last_action_result = result

        success, message = result
        if success:
            self.report({'INFO'}, message)
            self._action_reported = True
            return {'FINISHED'}
        self.report({'ERROR'}, message)
        self._action_reported = True
        return {'CANCELLED'}

    def _get_tool_name(self):
        return "Prism Cut"

    def _update_header(self, context):
        if self._state == self.STATE_FIRST_VERTEX:
            lock_hint = (
                " (Axis Locked)"
                if self._axis_lock_normal is not None
                else " | Ctrl to lock axis"
            )
            text = (
                f"Prism Cut: Click to set first point{lock_hint} | "
                "ESC to cancel"
            )
        elif self._state == self.STATE_SECOND_VERTEX:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            elif self._closing_candidate:
                prompt = "Click to close the profile"
            else:
                prompt = "Click to add point | Connect to first point to close"
            text = f"Prism Cut: {prompt} | ESC to cancel"
        elif self._state == self.STATE_DEPTH:
            text = (
                f"Prism Cut: Move mouse to set depth ({self._depth:.3f}) | "
                "Click to confirm | ESC to cancel"
            )
        else:
            text = "Prism Cut | ESC to cancel"
        if context.area is not None:
            context.area.header_text_set(text)


def _profile_to_json(profile_vertices):
    return json.dumps([list(vertex) for vertex in profile_vertices])


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
            build_prism_cut_prism(
                Matrix.Identity(4),
                profile_vertices,
                1.0,
                local_z,
            )
        except ValueError as error:
            return str(error)
    return None


def _profile_from_json(profile_json):
    values = json.loads(profile_json)
    if not isinstance(values, list):
        raise ValueError("Profile must be a list")
    return [Vector(value) for value in values]


def register():
    bpy.utils.register_class(MESH_OT_prism_cut)


def unregister():
    bpy.utils.unregister_class(MESH_OT_prism_cut)
