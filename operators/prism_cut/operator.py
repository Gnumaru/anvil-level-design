"""Prism Cut modal operator."""

import json

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from mathutils import Matrix, Vector

from . import geometry
from .prism import build_prism_cut_prism
from ..mesh_cut.analysis import analyze_convex_prism_cut
from ..mesh_cut.execution import (
    RECONSTRUCTION_MODE_NGONS,
    RECONSTRUCTION_MODE_QUADS,
)
from ..cube_cut.operator import _build_world_cut_vertex_markers
from ..modal_draw.base_operator import ModalDrawBase
from ..modal_draw.prism_profile import (
    PrismProfileDrawMixin,
    profile_candidate_invalid_message,
    profile_from_json,
    profile_to_json,
)
from ..pending_mesh_action import store_prism_from_edge_selection
from ...core.logging import debug_log
from ...core.workspace_check import is_level_design_workspace
from ...handlers.face_cache import cache_face_data_for_objects


class MESH_OT_prism_cut(
        PrismProfileDrawMixin, ModalDrawBase, bpy.types.Operator):
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

    def _on_prism_profile_invoked(self):
        self._cut_preview_dimensions = None

    def _on_prism_profile_changed(self, context):
        self._cut_preview_dimensions = None
        self._update_cut_vertex_preview(context)

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
            profile_from_json(self.action_profile_json),
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
        self.action_profile_json = profile_to_json(self._profile_vertices)
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
            profile_vertices = profile_from_json(self.action_profile_json)
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

def register():
    bpy.utils.register_class(MESH_OT_prism_cut)


def unregister():
    bpy.utils.unregister_class(MESH_OT_prism_cut)
