"""Shared object/edit-mode behavior for profile-based builder operators."""

import bmesh
import bpy

from .modal_draw.default_grid_pivot import selected_vertex_world_coords
from .pending_mesh_action import (
    store_from_shape_builder,
    store_from_shape_builder_object_mode,
)
from ..core.workspace_check import is_level_design_workspace


class ShapeBuilderOperatorMixin:
    """Match the box builder's object/edit-mode execution behavior."""

    @classmethod
    def poll(cls, context):
        if not is_level_design_workspace():
            return False
        if context.mode == 'OBJECT':
            return True
        return (
            context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    def invoke(self, context, event):
        self._had_selection = bool(selected_vertex_world_coords(
            context.active_object,
            context.mode,
        ))
        return super().invoke(context, event)

    def _is_valid_mode(self, context):
        return context.mode in ('EDIT_MESH', 'OBJECT')

    def _restore_edit_action_context(self, context, object_name):
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != 'MESH':
            return None

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        context.view_layer.objects.active = obj
        obj.select_set(True)

        if context.mode != 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')

        return obj

    def _finish_edit_builder_result(self, obj, result, created_message):
        is_created = result[0] and result[1] == created_message
        new_face_vertices = result[2] if is_created and len(result) > 2 else []

        if result[0] and not self._had_selection:
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            for face in bm.faces:
                face.select = False
            for edge in bm.edges:
                edge.select = False
            for vertex in bm.verts:
                vertex.select = False
            bm.select_flush(False)
            bmesh.update_edit_mesh(me)

        if is_created:
            store_from_shape_builder(obj, new_face_vertices)
        return result

    def _finish_object_builder_result(self, obj, result, created_message):
        if result[0] and result[1] == created_message:
            store_from_shape_builder_object_mode(obj)
        return result

    def _report_builder_result(self, result):
        self._last_action_result = result
        success, message = result[0], result[1]
        if success:
            self.report({'INFO'}, message)
            self._action_reported = True
            return {'FINISHED'}
        self.report({'ERROR'}, message)
        self._action_reported = True
        return {'CANCELLED'}
