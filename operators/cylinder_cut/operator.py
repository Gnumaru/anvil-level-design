"""Cylinder Cut modal operator."""

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty
from mathutils import Vector

from . import geometry
from .prism import build_cylinder_cut_prism, build_cylinder_profile
from ..cube_cut.analysis import analyze_convex_prism_cut
from ..cube_cut.operator import _build_world_cut_vertex_markers
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE, ModalDrawBase
from ..modal_draw import utils as modal_draw_utils
from ..pending_mesh_action import (
    build_cylinder_weld_params,
    store_cylinder_from_edge_selection,
)
from ...core.logging import debug_log
from ...core.workspace_check import is_level_design_workspace
from ...handlers.face_cache import cache_face_data_for_objects


class MESH_OT_cylinder_cut(ModalDrawBase, bpy.types.Operator):
    """Cut a cylindrical or elliptical polygon-prism void from mesh geometry"""

    bl_idname = "leveldesign.cylinder_cut"
    bl_label = "Cylinder Cut"
    bl_options = {'REGISTER', 'UNDO'}

    radius_x: FloatProperty(
        name="Radius X",
        description="Radius along the first profile axis",
        default=1.0,
        min=MIN_RECTANGLE_SIZE,
        soft_max=1000.0,
    )
    radius_y: FloatProperty(
        name="Radius Y",
        description="Radius along the perpendicular profile axis",
        default=1.0,
        min=MIN_RECTANGLE_SIZE,
        soft_max=1000.0,
    )
    side_count: IntProperty(
        name="Sides",
        description="Number of faces around the cylinder",
        default=32,
        min=3,
        soft_max=128,
    )
    radius_mode: EnumProperty(
        name="Radius To",
        description="Choose whether polygon edges or face centers meet the radius",
        items=(
            ('EDGES', "Edges", "Polygon side edges touch the radius boundary"),
            ('FACES', "Faces", "Polygon face centers touch the radius boundary"),
        ),
        default='EDGES',
    )

    action_center: FloatVectorProperty(
        size=3,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_depth: FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_local_x: FloatVectorProperty(
        size=3,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_local_y: FloatVectorProperty(
        size=3,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_local_z: FloatVectorProperty(
        size=3,
        options={'HIDDEN', 'SKIP_SAVE'},
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
        layout = self.layout
        layout.prop(self, "radius_x")
        layout.prop(self, "radius_y")
        layout.prop(self, "side_count")
        layout.prop(self, "radius_mode")

    def invoke(self, context, event):
        self._side_count = self.side_count
        self._radius_mode = self.radius_mode
        self._cut_preview_dimensions = None
        return super().invoke(context, event)

    def modal(self, context, event):
        if event.value == 'PRESS' and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if event.type == 'WHEELUPMOUSE':
                self._side_count += 1
            else:
                self._side_count = max(3, self._side_count - 1)
            self.side_count = self._side_count
            self._cut_preview_dimensions = None
            self._update_shape_preview()
            self._update_cut_vertex_preview(context)
            self._update_header(context)
            modal_draw_utils.tag_redraw_all_3d_views()
            return {'RUNNING_MODAL'}

        return super().modal(context, event)

    def _confirm_first_vertex(self, context, event):
        result = super()._confirm_first_vertex(context, event)
        self._update_shape_preview()
        return result

    def _confirm_line_end(self, context, event):
        result = super()._confirm_line_end(context, event)
        self._update_shape_preview()
        self._update_cut_vertex_preview(context)
        return result

    def _update_second_vertex_preview(self, context, event):
        super()._update_second_vertex_preview(context, event)
        self._update_shape_preview()
        self._update_cut_vertex_preview(context)

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._update_shape_preview()
        self._update_cut_vertex_preview(context)

    def _on_info_visibility_changed(self, context, visible):
        self._cut_preview_dimensions = None
        if visible:
            self._update_cut_vertex_preview(context)
        else:
            self._preview.update_cut_vertex_markers([])

    def _update_shape_preview(self):
        if self._first_vertex is None or self._second_vertex is None:
            return
        if self._local_x is None or self._local_y is None:
            return

        difference = self._second_vertex - self._first_vertex
        signed_radius_x = difference.dot(self._local_x)
        signed_radius_y = difference.dot(self._local_y)
        radius_x = abs(signed_radius_x)
        radius_y = abs(signed_radius_y)

        profile = build_cylinder_profile(
            self._first_vertex,
            radius_x,
            radius_y,
            self._local_x,
            self._local_y,
            self._side_count,
            self._radius_mode,
        )
        x_point = self._first_vertex + self._local_x * signed_radius_x
        y_point = self._first_vertex + self._local_y * signed_radius_y
        quarter_corner = x_point + self._local_y * signed_radius_y
        guides = (
            (self._first_vertex, x_point),
            (x_point, quarter_corner),
            (quarter_corner, y_point),
            (y_point, self._first_vertex),
        )
        self._preview.update_custom_profile(profile, guides)

    def _update_cut_vertex_preview(self, context):
        if not self._preview.is_info_visible():
            self._cut_preview_dimensions = None
            self._preview.update_cut_vertex_markers([])
            return
        if self._first_vertex is None or self._second_vertex is None:
            return
        if self._local_x is None or self._local_y is None or self._local_z is None:
            return

        difference = self._second_vertex - self._first_vertex
        radius_x = abs(difference.dot(self._local_x))
        radius_y = abs(difference.dot(self._local_y))
        preview_depth = self._depth if self._state == self.STATE_DEPTH else 0.0
        dimensions = (
            radius_x,
            radius_y,
            preview_depth,
            self._side_count,
            self._radius_mode,
        )
        if dimensions == self._cut_preview_dimensions:
            return
        self._cut_preview_dimensions = dimensions

        if self._invalid_message is not None:
            self._preview.update_cut_vertex_markers([])
            return

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self._preview.update_cut_vertex_markers([])
            return

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            prism = build_cylinder_cut_prism(
                obj.matrix_world,
                self._first_vertex,
                radius_x,
                radius_y,
                preview_depth,
                self._local_x,
                self._local_y,
                self._local_z,
                self._side_count,
                self._radius_mode,
            )
            analysis = analyze_convex_prism_cut(bm, prism)
            markers = _build_world_cut_vertex_markers(
                obj.matrix_world,
                analysis.candidate_vertex_markers,
            )
        except Exception as error:
            print(
                "Level Design Tools: Error updating Cylinder Cut vertex preview: "
                f"{error}"
            )
            markers = []

        self._preview.update_cut_vertex_markers(markers)

    def _get_rectangle_invalid_message(self, local_dx, local_dy):
        if local_dx < MIN_RECTANGLE_SIZE and local_dy < MIN_RECTANGLE_SIZE:
            return "Move away from the center"
        if local_dx < MIN_RECTANGLE_SIZE:
            return "First radius must be greater than zero"
        if local_dy < MIN_RECTANGLE_SIZE:
            return "Second radius must be greater than zero"
        return None

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        difference = second_vertex - first_vertex
        radius_x = abs(difference.dot(local_x))
        radius_y = abs(difference.dot(local_y))
        return self._execute_cylinder_cut(
            context,
            first_vertex,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
            self._side_count,
            self._radius_mode,
        )

    def _execute_cylinder_cut(self, context, center, radius_x, radius_y, depth,
                              local_x, local_y, local_z, side_count,
                              radius_mode):
        obj = context.active_object
        cylinder_weld_params = build_cylinder_weld_params(
            obj,
            center,
            radius_x,
            radius_y,
            local_x,
            local_y,
            local_z,
            side_count,
            radius_mode,
        )

        pixels_per_meter = context.scene.level_design_props.pixels_per_meter
        result = geometry.execute_cylinder_cut(
            obj,
            context.tool_settings,
            pixels_per_meter,
            center,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
            side_count,
            radius_mode,
        )

        if result[0]:
            cache_face_data_for_objects(context.view_layer.objects, pixels_per_meter)
            axis_z = Vector(local_z).normalized()
            extrude_direction = -axis_z
            back_point = Vector(center) + axis_z * depth
            back_plane_offset = back_point.dot(extrude_direction)
            debug_log(
                f"[CylinderCut] Weld setup: radii=({radius_x:.4f}, "
                f"{radius_y:.4f}), depth={depth:.4f}, sides={side_count}, "
                f"radius_mode={radius_mode}"
            )
            store_cylinder_from_edge_selection(
                obj,
                abs(depth),
                extrude_direction,
                back_plane_offset,
                cylinder_weld_params,
            )

        return result

    def _capture_action_properties(self, context, first_vertex, second_vertex,
                                   depth, local_x, local_y, local_z):
        difference = second_vertex - first_vertex
        self.action_center = first_vertex
        self.radius_x = abs(difference.dot(local_x))
        self.radius_y = abs(difference.dot(local_y))
        self.action_depth = depth
        self.action_local_x = local_x
        self.action_local_y = local_y
        self.action_local_z = local_z
        self.side_count = self._side_count
        self.radius_mode = self._radius_mode

    def execute(self, context):
        result = self._execute_cylinder_cut(
            context,
            Vector(self.action_center),
            self.radius_x,
            self.radius_y,
            self.action_depth,
            Vector(self.action_local_x),
            Vector(self.action_local_y),
            Vector(self.action_local_z),
            self.side_count,
            self.radius_mode,
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
        return "Cylinder Cut"

    def _update_header(self, context):
        sides_hint = f"Sides: {self._side_count} (Wheel)"
        if self._state == self.STATE_FIRST_VERTEX:
            lock_hint = (
                " (Axis Locked)"
                if self._axis_lock_normal is not None
                else " | Ctrl to lock axis"
            )
            text = (
                f"Cylinder Cut: Click to set center{lock_hint} | "
                f"{sides_hint} | ESC to cancel"
            )
        elif self._state == self.STATE_LINE_END:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            else:
                prompt = "Click to set first radius"
            text = f"Cylinder Cut: {prompt} | {sides_hint} | ESC to cancel"
        elif self._state == self.STATE_SECOND_VERTEX:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            elif self._line_mode:
                prompt = "Click to set perpendicular radius"
            else:
                prompt = "Click to set quarter profile"
            text = f"Cylinder Cut: {prompt} | {sides_hint} | ESC to cancel"
        elif self._state == self.STATE_DEPTH:
            if self._invalid_message is not None:
                prompt = f"{self._invalid_message} ({self._depth:.3f})"
            else:
                prompt = (
                    f"Move mouse to set depth ({self._depth:.3f}) | "
                    "Click to confirm"
                )
            text = f"Cylinder Cut: {prompt} | {sides_hint} | ESC to cancel"
        else:
            text = f"Cylinder Cut | {sides_hint} | ESC to cancel"
        context.area.header_text_set(text)


def register():
    bpy.utils.register_class(MESH_OT_cylinder_cut)


def unregister():
    bpy.utils.unregister_class(MESH_OT_cylinder_cut)
