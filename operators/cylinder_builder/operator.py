"""Cylinder Builder modal operator."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from mathutils import Vector

from . import geometry
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE, ModalDrawBase
from ..modal_draw.cylinder_profile import CylinderProfileDrawMixin
from ..modal_draw.default_grid_pivot import DefaultGridPivotMixin
from ..profile_builder_geometry import (
    CAP_MODE_NGON,
    CAP_MODE_TRIANGLE_FAN,
)
from ..shape_builder_operator import ShapeBuilderOperatorMixin


class MESH_OT_cylinder_builder(
        ShapeBuilderOperatorMixin,
        DefaultGridPivotMixin,
        CylinderProfileDrawMixin,
        ModalDrawBase,
        bpy.types.Operator):
    """Create a cylinder or elliptical polygon prism."""

    bl_idname = "leveldesign.cylinder_builder"
    bl_label = "Cylinder Builder"
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
    skip_caps: BoolProperty(
        name="Skip Caps",
        description="Create only the cylinder side faces",
        default=False,
    )
    cap_fill: EnumProperty(
        name="Cap Fill",
        description="Choose how cylinder caps are filled",
        items=(
            (
                CAP_MODE_NGON,
                "Ngons",
                "Create each cap as a single ngon",
            ),
            (
                CAP_MODE_TRIANGLE_FAN,
                "Triangles to Center",
                "Triangulate each cap to a center vertex",
            ),
        ),
        default=CAP_MODE_TRIANGLE_FAN,
    )
    name_suffix: StringProperty(
        name="Suffix",
        description="Suffix appended after Blender numbering, e.g. Anvil.Cylinder.001-col",
        default="",
    )

    action_center: FloatVectorProperty(size=3)
    action_depth: FloatProperty()
    action_local_x: FloatVectorProperty(size=3)
    action_local_y: FloatVectorProperty(size=3)
    action_local_z: FloatVectorProperty(size=3)
    action_had_selection: BoolProperty()
    action_was_edit_mode: BoolProperty()
    action_object_name: StringProperty()

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = layout.column(align=True)
        settings.prop(self, "name_suffix")
        settings.prop(self, "radius_x")
        settings.prop(self, "radius_y")
        settings.prop(self, "side_count")
        settings.prop(self, "radius_mode")
        settings.prop(self, "skip_caps")
        cap_settings = settings.column(align=True)
        cap_settings.enabled = not self.skip_caps
        cap_settings.prop(self, "cap_fill")

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        difference = second_vertex - first_vertex
        radius_x = abs(difference.dot(local_x))
        radius_y = abs(difference.dot(local_y))
        return self._execute_cylinder_builder(
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
            context.mode == 'EDIT_MESH',
            context.active_object.name if context.active_object is not None else "",
            self.name_suffix,
        )

    def _execute_cylinder_builder(
            self, context, center, radius_x, radius_y, depth, local_x,
            local_y, local_z, side_count, radius_mode, action_was_edit_mode,
            action_object_name, name_suffix):
        pixels_per_meter = context.scene.level_design_props.pixels_per_meter

        if not action_was_edit_mode:
            result = geometry.execute_cylinder_builder_object_mode(
                center,
                radius_x,
                radius_y,
                depth,
                local_x,
                local_y,
                local_z,
                side_count,
                radius_mode,
                pixels_per_meter,
                self.skip_caps,
                self.cap_fill,
                name_suffix,
            )
            return self._finish_object_builder_result(
                context.active_object,
                result,
                "Cylinder object created",
            )

        obj = self._restore_edit_action_context(context, action_object_name)
        if obj is None:
            return (False, "No active mesh object")
        result = geometry.execute_cylinder_builder_edit_mode(
            center,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
            side_count,
            radius_mode,
            obj,
            pixels_per_meter,
            self.skip_caps,
            self.cap_fill,
        )
        return self._finish_edit_builder_result(
            obj,
            result,
            "Cylinder created",
        )

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
        self.action_had_selection = self._had_selection
        self.action_was_edit_mode = context.mode == 'EDIT_MESH'
        active_object = context.active_object
        self.action_object_name = (
            active_object.name if active_object is not None else ""
        )
        self.side_count = self._side_count
        self.radius_mode = self._radius_mode

    def execute(self, context):
        self._had_selection = self.action_had_selection
        result = self._execute_cylinder_builder(
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
            self.action_was_edit_mode,
            self.action_object_name,
            self.name_suffix,
        )
        return self._report_builder_result(result)

    def _get_tool_name(self):
        return "Cylinder Builder"


def register():
    bpy.utils.register_class(MESH_OT_cylinder_builder)


def unregister():
    bpy.utils.unregister_class(MESH_OT_cylinder_builder)
