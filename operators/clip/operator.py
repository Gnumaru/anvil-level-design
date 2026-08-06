"""Two-point modal plane Clip operator."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty
from mathutils import Matrix, Vector

from . import geometry, hotkeys
from ..modal_draw.base_operator import ModalDrawBase, MIN_RECTANGLE_SIZE
from ..modal_draw import utils as modal_draw_utils
from ..pending_mesh_action import store_clip_fill_from_edge_selection
from ...core.workspace_check import is_level_design_workspace
from ...handlers.face_cache import cache_face_data_for_objects


_MODE_ORDER = (
    geometry.CLIP_MODE_BISECT,
    geometry.CLIP_MODE_REMOVE_ABOVE,
    geometry.CLIP_MODE_REMOVE_BELOW,
)

_MODE_LABELS = {
    geometry.CLIP_MODE_BISECT: "Bisect Only",
    geometry.CLIP_MODE_REMOVE_ABOVE: "Remove Above",
    geometry.CLIP_MODE_REMOVE_BELOW: "Remove Below",
}


class MESH_OT_clip(ModalDrawBase, bpy.types.Operator):
    """Draw a line on the grid to bisect or trim edit-mode mesh faces"""

    bl_idname = "leveldesign.clip"
    bl_label = "Clip"
    bl_options = {'REGISTER', 'UNDO'}

    clip_mode: EnumProperty(
        name="Clip Mode",
        description="Choose whether to keep both sides or remove one side",
        items=(
            (
                geometry.CLIP_MODE_BISECT,
                "Bisect Only",
                "Keep both sides of the clipping plane",
            ),
            (
                geometry.CLIP_MODE_REMOVE_ABOVE,
                "Remove Above",
                "Remove the side indicated above the drawn line",
            ),
            (
                geometry.CLIP_MODE_REMOVE_BELOW,
                "Remove Below",
                "Remove the side opposite the above indicator",
            ),
        ),
        default=geometry.CLIP_MODE_BISECT,
    )
    prefer_quads: BoolProperty(
        name="Prefer Quads",
        description="Rebuild resulting n-gons as quads and triangles",
        default=True,
    )
    action_first_point: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_second_point: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_grid_normal: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_matrix_world: FloatVectorProperty(
        size=16,
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
        self.layout.prop(self, "clip_mode")
        self.layout.prop(self, "prefer_quads")

    def invoke(self, context, event):
        result = super().invoke(context, event)
        if 'RUNNING_MODAL' in result:
            self._preview.update_clip_removal_segments([])
        return result

    def _is_line_mode_key_held(self, context, event):
        return True

    def _get_line_end_invalid_message(self, line_length):
        if line_length < MIN_RECTANGLE_SIZE:
            return "Move away from the start point"
        return None

    def _update_line_end_preview(self, context, event):
        super()._update_line_end_preview(context, event)
        self._refresh_removal_indicator(context)

    def _confirm_first_vertex(self, context, event):
        result = super()._confirm_first_vertex(context, event)
        self._refresh_removal_indicator(context)
        return result

    def _confirm_line_end(self, context, event):
        if self._first_vertex is None or self._line_end is None:
            return {'RUNNING_MODAL'}
        invalid_message = self._get_line_end_invalid_message(
            (self._line_end - self._first_vertex).length
        )
        if invalid_message is not None:
            self._set_invalid_message(invalid_message)
            self._update_header(context)
            return {'RUNNING_MODAL'}

        self._second_vertex = self._line_end.copy()
        result = self._run_action(
            context,
            self._first_vertex,
            self._second_vertex,
            0.0,
            self._local_x,
            self._local_y,
            self._local_z,
        )
        success, message = result[0], result[1]
        if not getattr(self, "_action_reported", False):
            self.report({'INFO'} if success else {'ERROR'}, message)
        self._cleanup(context)
        return {'FINISHED'} if success else {'CANCELLED'}

    def modal(self, context, event):
        can_cycle = (
            not getattr(self, "_cancelled", False)
            and getattr(self, "_state", self.STATE_FIRST_VERTEX)
            in {self.STATE_FIRST_VERTEX, self.STATE_LINE_END}
        )
        if can_cycle:
            action = hotkeys.action_for_event(context.window_manager, event)
            if action is not None:
                current_index = _MODE_ORDER.index(self.clip_mode)
                direction = -1 if action == hotkeys.PREVIOUS_MODE else 1
                self.clip_mode = _MODE_ORDER[
                    (current_index + direction) % len(_MODE_ORDER)
                ]
                self._refresh_removal_indicator(context)
                self._update_header(context)
                modal_draw_utils.tag_redraw_all_3d_views()
                return {'RUNNING_MODAL'}
        return super().modal(context, event)

    def _refresh_removal_indicator(self, context):
        if (
                self.clip_mode == geometry.CLIP_MODE_BISECT
                or getattr(self, "_first_vertex", None) is None
                or getattr(self, "_line_end", None) is None
                or getattr(self, "_local_z", None) is None):
            if hasattr(self, "_preview"):
                self._preview.update_clip_removal_segments([])
            return

        line = self._line_end - self._first_vertex
        if line.length < MIN_RECTANGLE_SIZE:
            self._preview.update_clip_removal_segments([])
            return
        line_direction = line.normalized()
        side_direction = self._local_z.normalized().cross(line_direction)
        if self.clip_mode == geometry.CLIP_MODE_REMOVE_BELOW:
            side_direction = -side_direction
        if side_direction.length < MIN_RECTANGLE_SIZE:
            self._preview.update_clip_removal_segments([])
            return
        side_direction.normalize()

        grid_size = modal_draw_utils.get_grid_size(context)
        arrow_length = min(
            line.length * 0.22,
            max(grid_size * 0.5, line.length * 0.08),
        )
        segments = []
        for factor in (0.25, 0.5, 0.75):
            base = self._first_vertex + line * factor
            tip = base + side_direction * arrow_length
            wing_offset = line_direction * arrow_length * 0.28
            wing_back = side_direction * arrow_length * 0.36
            segments.extend((
                (base, tip),
                (tip, tip - wing_back + wing_offset),
                (tip, tip - wing_back - wing_offset),
            ))
        self._preview.update_clip_removal_segments(segments)

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))
        return self._execute_clip(
            context,
            first_vertex,
            second_vertex,
            local_z,
            matrix_world,
        )

    def _execute_clip(
            self, context, first_point, second_point, grid_normal,
            matrix_world):
        obj = context.active_object
        result = geometry.execute_clip(
            obj,
            context.tool_settings,
            first_point,
            second_point,
            grid_normal,
            self.clip_mode,
            self.prefer_quads,
            matrix_world,
        )
        if result[0]:
            pixels_per_meter = context.scene.level_design_props.pixels_per_meter
            cache_face_data_for_objects(
                context.view_layer.objects,
                pixels_per_meter,
            )
            removal_mode = self.clip_mode in {
                geometry.CLIP_MODE_REMOVE_ABOVE,
                geometry.CLIP_MODE_REMOVE_BELOW,
            }
            store_clip_fill_from_edge_selection(
                obj,
                removal_mode,
                self.prefer_quads,
            )
        return result

    def _capture_action_properties(self, context, first_vertex, second_vertex,
                                   depth, local_x, local_y, local_z):
        self.action_first_point = first_vertex
        self.action_second_point = second_vertex
        self.action_grid_normal = local_z
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
        result = self._execute_clip(
            context,
            Vector(self.action_first_point),
            Vector(self.action_second_point),
            Vector(self.action_grid_normal),
            matrix_world,
        )
        self._last_action_result = result
        success, message = result[0], result[1]
        self.report({'INFO'} if success else {'ERROR'}, message)
        self._action_reported = True
        return {'FINISHED'} if success else {'CANCELLED'}

    def _get_tool_name(self):
        return "Clip"

    def _get_header_suffix(self, context):
        labels = hotkeys.shortcut_labels(context.window_manager)
        return (
            f" | {_MODE_LABELS[self.clip_mode]} | "
            f"{labels[hotkeys.PREVIOUS_MODE]}/"
            f"{labels[hotkeys.NEXT_MODE]} cycle mode"
        )


def register():
    bpy.utils.register_class(MESH_OT_clip)


def unregister():
    bpy.utils.unregister_class(MESH_OT_clip)
