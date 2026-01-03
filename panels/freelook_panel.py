import bpy
from bpy.types import Panel


class LEVELDESIGN_PT_freelook_panel(Panel):
    """Freelook Camera Settings Panel"""
    bl_label = "Freelook Settings"
    bl_idname = "LEVELDESIGN_PT_freelook_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Level Design'

    def draw(self, context):
        layout = self.layout

        addon = context.preferences.addons.get(__package__.rsplit('.', 1)[0])
        if not addon:
            layout.label(text="Addon preferences not found")
            return

        prefs = addon.preferences

        layout.prop(prefs, "mouse_sensitivity")
        layout.prop(prefs, "move_speed")

        # Instructions
        box = layout.box()
        box.label(text="Controls:", icon='INFO')
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="Hold RMB: Freelook")
        col.label(text="WASD: Move (camera dir)")
        col.label(text="Q/E: Down/Up (world)")
        col.label(text="Shift: Move faster")
        col.label(text="Scroll: Adjust speed")


classes = (
    LEVELDESIGN_PT_freelook_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
