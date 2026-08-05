"""Stair Builder modal operator."""

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

from . import geometry, hotkeys
from ..modal_draw.base_operator import MIN_RECTANGLE_SIZE, ModalDrawBase
from ..modal_draw.default_grid_pivot import DefaultGridPivotMixin
from ..modal_draw import utils as modal_draw_utils
from ..shape_builder_operator import ShapeBuilderOperatorMixin


def _format_length(unit_settings, length):
    if unit_settings.system != 'NONE':
        try:
            return bpy.utils.units.to_string(
                unit_settings.system,
                'LENGTH',
                length * unit_settings.scale_length,
                precision=3,
                split_unit=unit_settings.use_separate,
                compatible_unit=False,
            )
        except Exception:
            pass
    return f"{length:.3f}"


class MESH_OT_stair_builder(
        ShapeBuilderOperatorMixin,
        DefaultGridPivotMixin,
        ModalDrawBase,
        bpy.types.Operator):
    """Draw a cuboid volume and fill it with configurable stairs."""

    bl_idname = "leveldesign.stair_builder"
    bl_label = "Stair Builder"
    bl_options = {'REGISTER', 'UNDO'}

    orientation: EnumProperty(
        name="Uphill Direction",
        description="Choose which captured horizontal box axis points uphill",
        items=(
            (
                geometry.ORIENTATION_AXIS_1_POSITIVE,
                "Axis 1 +",
                "Go uphill along the first horizontal box axis",
            ),
            (
                geometry.ORIENTATION_AXIS_1_NEGATIVE,
                "Axis 1 -",
                "Go uphill opposite the first horizontal box axis",
            ),
            (
                geometry.ORIENTATION_AXIS_2_POSITIVE,
                "Axis 2 +",
                "Go uphill along the second horizontal box axis",
            ),
            (
                geometry.ORIENTATION_AXIS_2_NEGATIVE,
                "Axis 2 -",
                "Go uphill opposite the second horizontal box axis",
            ),
        ),
        default=geometry.ORIENTATION_AXIS_1_POSITIVE,
    )
    sizing_mode: EnumProperty(
        name="Fill By",
        description="Control the stairs by step count or target riser height",
        items=(
            (
                geometry.SIZING_STEP_COUNT,
                "Step Count",
                "Create the requested number of riser-and-tread step units",
            ),
            (
                geometry.SIZING_STEP_HEIGHT,
                "Step Height",
                "Calculate the number of steps from a target riser height",
            ),
        ),
        default=geometry.SIZING_STEP_HEIGHT,
    )
    step_count: IntProperty(
        name="Steps",
        description="Number of generated riser-and-tread step units",
        default=8,
        min=1,
        max=geometry.MAX_STEP_COUNT,
        soft_max=64,
    )
    target_step_height: FloatProperty(
        name="Target Riser Height",
        description="Preferred vertical height of each riser",
        default=0.1,
        min=MIN_RECTANGLE_SIZE,
        soft_max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    height_distribution: EnumProperty(
        name="Height Fit",
        description="Choose how the total height is fitted to the target riser height",
        items=(
            (
                geometry.HEIGHT_SHORT_FIRST,
                "Short First Riser",
                "Keep later risers at the target height and put the smaller remainder first",
            ),
            (
                geometry.HEIGHT_EVEN,
                "Even Risers",
                "Divide the height evenly using the closest practical riser count",
            ),
        ),
        default=geometry.HEIGHT_EVEN,
    )
    termination: EnumProperty(
        name="Top Of Box",
        description="Choose whether Anvil builds a top tread or ends at a destination floor",
        items=(
            (
                geometry.TERMINATION_TOP_TREAD,
                "Final Tread",
                "Build the final tread at the top of the drawn box",
            ),
            (
                geometry.TERMINATION_DESTINATION,
                "Destination Floor",
                "Treat the top of the box as the floor after the last riser",
            ),
        ),
        default=geometry.TERMINATION_TOP_TREAD,
    )
    include_final_riser: BoolProperty(
        name="Create Final Riser",
        description="Create the last vertical face leading into the destination floor",
        default=False,
    )
    left_side: BoolProperty(
        name="Left Side",
        description="Create the outer left side face",
        default=True,
    )
    right_side: BoolProperty(
        name="Right Side",
        description="Create the outer right side face",
        default=True,
    )
    back: BoolProperty(
        name="Back",
        description="Create the rear closure below the final tread or riser",
        default=False,
    )
    left_border: BoolProperty(
        name="Left Border",
        description="Replace the left edge of the steps with a flush sloping strip",
        default=False,
    )
    right_border: BoolProperty(
        name="Right Border",
        description="Replace the right edge of the steps with a flush sloping strip",
        default=False,
    )
    border_width: FloatProperty(
        name="Border Width",
        description="Width of each enabled sloping border",
        default=0.2,
        min=MIN_RECTANGLE_SIZE,
        soft_max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    border_alignment: EnumProperty(
        name="Border Alignment",
        description="Choose whether the sloping border follows riser bottoms or tread tips",
        items=(
            (
                geometry.BORDER_ALIGN_RISER_BOTTOMS,
                "Riser Bottoms",
                "Keep the border beneath the tread tips so the steps project above it",
            ),
            (
                geometry.BORDER_ALIGN_STEP_TIPS,
                "Step Tips",
                "Align the border to tread tips so the steps do not project above it",
            ),
        ),
        default=geometry.BORDER_ALIGN_STEP_TIPS,
    )
    underside: EnumProperty(
        name="Underside",
        description="Leave the bottom open or create a solid or straight sloping underside",
        items=(
            (
                geometry.UNDERSIDE_NONE,
                "None",
                "Leave the bottom of the stair mesh open",
            ),
            (
                geometry.UNDERSIDE_SOLID,
                "Solid To Base",
                "Fill the complete volume beneath the steps",
            ),
            (
                geometry.UNDERSIDE_SLOPED,
                "Straight Slope",
                "Create a straight sloping underside beneath the steps",
            ),
        ),
        default=geometry.UNDERSIDE_NONE,
    )
    name_suffix: StringProperty(
        name="Suffix",
        description="Suffix appended after Blender numbering, e.g. Anvil.Stair.001-col",
        default="",
    )
    action_first_vertex: FloatVectorProperty(size=3)
    action_second_vertex: FloatVectorProperty(size=3)
    action_depth: FloatProperty()
    action_local_x: FloatVectorProperty(size=3)
    action_local_y: FloatVectorProperty(size=3)
    action_local_z: FloatVectorProperty(size=3)
    action_had_selection: BoolProperty()
    action_was_edit_mode: BoolProperty()
    action_object_name: StringProperty()

    def _calculated_layout(self):
        return geometry.calculate_stair_layout(
            Vector(self.action_first_vertex),
            Vector(self.action_second_vertex),
            self.action_depth,
            Vector(self.action_local_x),
            Vector(self.action_local_y),
            Vector(self.action_local_z),
            self.orientation,
            self.sizing_mode,
            self.step_count,
            self.target_step_height,
            self.height_distribution,
            self.termination,
            self.left_border,
            self.right_border,
            self.border_width,
        )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        unit_settings = context.scene.unit_settings

        settings = layout.column(align=True)
        settings.prop(self, "sizing_mode")
        if self.sizing_mode == geometry.SIZING_STEP_COUNT:
            settings.prop(self, "step_count")
        else:
            settings.prop(self, "target_step_height")
            settings.prop(self, "height_distribution")
        settings.prop(self, "termination")
        final_riser_settings = settings.column(align=True)
        final_riser_settings.enabled = (
            self.termination == geometry.TERMINATION_DESTINATION
        )
        final_riser_settings.prop(self, "include_final_riser")

        layout.separator()
        structure_settings = layout.column(align=True)
        structure_settings.prop(self, "left_side")
        structure_settings.prop(self, "right_side")
        structure_settings.prop(self, "back")
        structure_settings.prop(self, "underside")

        layout.separator()
        border_settings = layout.column(align=True)
        border_settings.prop(self, "left_border")
        border_settings.prop(self, "right_border")
        border_options = border_settings.column(align=True)
        border_options.enabled = self.left_border or self.right_border
        border_options.prop(self, "border_width")
        border_options.prop(self, "border_alignment")

        layout.separator()
        output_settings = layout.column(align=True)
        output_settings.prop(self, "name_suffix")

        try:
            calculated = self._calculated_layout()
        except ValueError as error:
            readouts = layout.box()
            readouts.label(text=str(error), icon='ERROR')
        else:
            readouts = layout.box()
            readouts.label(text=f"Generated Steps: {calculated['tread_count']}")
            readouts.label(
                text=(
                    "Tread Depth: "
                    f"{_format_length(unit_settings, calculated['tread_depth'])}"
                )
            )
            first_height = calculated['riser_heights'][0]
            other_height = calculated['riser_heights'][-1]
            if abs(first_height - other_height) > 1e-6:
                readouts.label(
                    text=(
                        "First Riser Height: "
                        f"{_format_length(unit_settings, first_height)}"
                    )
                )
                readouts.label(
                    text=(
                        "Other Riser Height: "
                        f"{_format_length(unit_settings, other_height)}"
                    )
                )
            else:
                readouts.label(
                    text=(
                        "Riser Height: "
                        f"{_format_length(unit_settings, first_height)}"
                    )
                )

    def _get_rectangle_invalid_message(self, local_dx, local_dy):
        if local_dx < MIN_RECTANGLE_SIZE or local_dy < MIN_RECTANGLE_SIZE:
            return "Stairs require two non-zero box dimensions"
        return None

    def _get_depth_invalid_message(self, depth):
        if abs(depth) < MIN_RECTANGLE_SIZE:
            return "Move away from the base to set stair height"
        return None

    def _update_flat_stair_wire(self, valid):
        if self._first_vertex is None or self._second_vertex is None:
            self._preview.clear_custom_wire()
            return
        try:
            vertices, edges, measurements = geometry.build_flat_stair_preview(
                self._first_vertex,
                self._second_vertex,
                self._local_x,
                self._local_y,
                self._local_z,
                self.orientation,
            )
        except ValueError:
            self._preview.clear_custom_wire()
            return
        self._preview.update_custom_wire(
            vertices,
            edges,
            measurements,
            valid,
        )

    def _update_full_stair_wire(self):
        try:
            vertices, edges, measurements = geometry.build_stair_preview(
                self._first_vertex,
                self._second_vertex,
                self._depth,
                self._local_x,
                self._local_y,
                self._local_z,
                self.orientation,
                self.sizing_mode,
                self.step_count,
                self.target_step_height,
                self.height_distribution,
                self.termination,
                self.include_final_riser,
                self.left_side,
                self.right_side,
                self.back,
                self.left_border,
                self.right_border,
                self.border_width,
                self.border_alignment,
                self.underside,
            )
        except ValueError:
            self._update_flat_stair_wire(False)
            return
        self._preview.update_custom_wire(
            vertices,
            edges,
            measurements,
            True,
        )

    def _refresh_stair_wire(self):
        if self._state == self.STATE_SECOND_VERTEX:
            self._update_flat_stair_wire(True)
        elif self._state == self.STATE_DEPTH:
            if self._get_depth_invalid_message(self._depth) is None:
                self._update_full_stair_wire()
            else:
                self._update_flat_stair_wire(False)

    def _update_second_vertex_preview(self, context, event):
        super()._update_second_vertex_preview(context, event)
        self._update_flat_stair_wire(True)

    def _confirm_first_vertex(self, context, event):
        result = super()._confirm_first_vertex(context, event)
        if self._state == self.STATE_SECOND_VERTEX:
            self._update_flat_stair_wire(True)
        return result

    def _confirm_line_end(self, context, event):
        result = super()._confirm_line_end(context, event)
        if self._state == self.STATE_SECOND_VERTEX:
            self._update_flat_stair_wire(True)
        return result

    def _confirm_second_vertex(self, context, event):
        result = super()._confirm_second_vertex(context, event)
        if self._state == self.STATE_DEPTH:
            self._update_flat_stair_wire(False)
        return result

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._refresh_stair_wire()

    def modal(self, context, event):
        can_rotate = (
            not getattr(self, "_cancelled", False)
            and self._state in {
                self.STATE_LINE_END,
                self.STATE_SECOND_VERTEX,
                self.STATE_DEPTH,
            }
        )
        if can_rotate:
            action = hotkeys.rotation_action_for_event(
                context.window_manager,
                context.mode,
                event,
            )
            if action is not None:
                quarter_turns = 1 if action == hotkeys.ROTATE_LEFT else -1
                self.orientation = geometry.rotate_orientation(
                    self.orientation,
                    quarter_turns,
                )
                self._refresh_stair_wire()
                modal_draw_utils.tag_redraw_all_3d_views()
                return {'RUNNING_MODAL'}
        return super().modal(context, event)

    def _execute_stair_builder(
            self, context, first_vertex, second_vertex, depth, local_x,
            local_y, local_z, action_was_edit_mode, action_object_name,
            name_suffix):
        pixels_per_meter = context.scene.level_design_props.pixels_per_meter
        common_parameters = (
            first_vertex,
            second_vertex,
            depth,
            local_x,
            local_y,
            local_z,
            self.orientation,
            self.sizing_mode,
            self.step_count,
            self.target_step_height,
            self.height_distribution,
            self.termination,
            self.include_final_riser,
            self.left_side,
            self.right_side,
            self.back,
            self.left_border,
            self.right_border,
            self.border_width,
            self.border_alignment,
            self.underside,
        )

        if not action_was_edit_mode:
            result = geometry.execute_stair_builder_object_mode(
                *common_parameters,
                pixels_per_meter,
                name_suffix,
            )
            return self._finish_object_builder_result(
                context.active_object,
                result,
                "Stair object created",
            )

        obj = self._restore_edit_action_context(context, action_object_name)
        if obj is None:
            return (False, "No active mesh object")
        result = geometry.execute_stair_builder_edit_mode(
            *common_parameters,
            obj,
            pixels_per_meter,
        )
        return self._finish_edit_builder_result(
            obj,
            result,
            "Stair created",
        )

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        active_object = context.active_object
        return self._execute_stair_builder(
            context,
            first_vertex,
            second_vertex,
            depth,
            local_x,
            local_y,
            local_z,
            context.mode == 'EDIT_MESH',
            active_object.name if active_object is not None else "",
            self.name_suffix,
        )

    def _capture_action_properties(self, context, first_vertex, second_vertex,
                                   depth, local_x, local_y, local_z):
        self.action_first_vertex = first_vertex
        self.action_second_vertex = second_vertex
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

    def execute(self, context):
        self._had_selection = self.action_had_selection
        result = self._execute_stair_builder(
            context,
            Vector(self.action_first_vertex),
            Vector(self.action_second_vertex),
            self.action_depth,
            Vector(self.action_local_x),
            Vector(self.action_local_y),
            Vector(self.action_local_z),
            self.action_was_edit_mode,
            self.action_object_name,
            self.name_suffix,
        )
        return self._report_builder_result(result)

    def _get_tool_name(self):
        return "Stair Builder"

    def _get_header_suffix(self, context):
        if getattr(self, "_state", self.STATE_FIRST_VERTEX) == self.STATE_FIRST_VERTEX:
            return ""
        labels = hotkeys.rotation_shortcut_labels(
            context.window_manager,
            context.mode,
        )
        return (
            f" | {labels[hotkeys.ROTATE_LEFT]}/"
            f"{labels[hotkeys.ROTATE_RIGHT]} rotate stairs"
        )


def register():
    bpy.utils.register_class(MESH_OT_stair_builder)


def unregister():
    bpy.utils.unregister_class(MESH_OT_stair_builder)
