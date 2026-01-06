import bpy
from bpy.types import Operator


class LEVELDESIGN_OT_double_pixels(Operator):
    bl_idname = "leveldesign.double_pixels"
    bl_label = "x2"
    bl_description = "Double pixels per meter"

    def execute(self, context):
        props = context.scene.level_design_props
        props.pixels_per_meter = int(props.pixels_per_meter * 2)
        return {'FINISHED'}


class LEVELDESIGN_OT_halve_pixels(Operator):
    bl_idname = "leveldesign.halve_pixels"
    bl_label = "x2"
    bl_description = "Halve pixels per meter"

    def execute(self, context):
        props = context.scene.level_design_props
        props.pixels_per_meter = int(props.pixels_per_meter / 2)
        return {'FINISHED'}


classes = (LEVELDESIGN_OT_double_pixels, LEVELDESIGN_OT_halve_pixels)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
