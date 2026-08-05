"""Cylinder Cut modal operator."""

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty
from mathutils import Matrix, Vector

from . import geometry
from .prism import build_cylinder_cut_prism
from ..mesh_cut.analysis import analyze_convex_prism_cut
from ..mesh_cut.execution import (
    RECONSTRUCTION_MODE_NGONS,
    RECONSTRUCTION_MODE_QUADS,
)
from ..cube_cut.operator import _build_world_cut_vertex_markers
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE, ModalDrawBase
from ..modal_draw.cylinder_profile import CylinderProfileDrawMixin
from ..pending_mesh_action import (
    build_cylinder_weld_params,
    store_cylinder_from_edge_selection,
)
from ...core.logging import debug_log
from ...core.workspace_check import is_level_design_workspace
from ...handlers.face_cache import cache_face_data_for_objects


class MESH_OT_cylinder_cut(
        CylinderProfileDrawMixin, ModalDrawBase, bpy.types.Operator):
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

    action_center: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_depth: FloatProperty(
        options={'HIDDEN'},
    )
    action_local_x: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_local_y: FloatVectorProperty(
        size=3,
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
        layout = self.layout
        layout.prop(self, "radius_x")
        layout.prop(self, "radius_y")
        layout.prop(self, "side_count")
        layout.prop(self, "radius_mode")
        layout.prop(self, "reconstruction_mode")

    def _on_cylinder_profile_invoked(self):
        self._cut_preview_dimensions = None

    def _on_cylinder_profile_changed(self, context):
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

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        difference = second_vertex - first_vertex
        radius_x = abs(difference.dot(local_x))
        radius_y = abs(difference.dot(local_y))
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))
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
            self.reconstruction_mode,
            matrix_world,
        )

    def _execute_cylinder_cut(self, context, center, radius_x, radius_y, depth,
                               local_x, local_y, local_z, side_count,
                               radius_mode, reconstruction_mode, matrix_world):
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
        result = geometry.execute_cylinder_cut_with_reconstruction(
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
            reconstruction_mode,
            matrix_world,
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
        self.action_matrix_world = tuple(
            value for row in context.active_object.matrix_world for value in row
        )
        self.side_count = self._side_count
        self.radius_mode = self._radius_mode

    def execute(self, context):
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))

        # Adjust Last Operation may run before Blender has refreshed the
        # runtime matrix_world cache after undo, so use the captured matrix.
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
        return "Cylinder Cut"

def register():
    bpy.utils.register_class(MESH_OT_cylinder_cut)


def unregister():
    bpy.utils.unregister_class(MESH_OT_cylinder_cut)
