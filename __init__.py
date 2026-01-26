bl_info = {
    "name": "Anvil Level Design",
    "author": "Alex Hetherington",
    "version": (1, 0, 3),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Level Design",
    "description": "TrenchBroom-style UV tools, texture application, and grid controls for level design",
    "category": "3D View",
}

import bpy
from bpy.props import FloatProperty

from . import properties
from . import handlers
from . import operators
from . import panels
from . import workspace


class LEVELDESIGN_OT_restore_default_keybindings(bpy.types.Operator):
    """Restore all addon keybindings to their default values"""
    bl_idname = "leveldesign.restore_default_keybindings"
    bl_label = "Restore Default Keybindings"
    bl_options = {'REGISTER'}

    def execute(self, context):
        wm = context.window_manager
        kc_addon = wm.keyconfigs.addon
        kc_user = wm.keyconfigs.user
        if kc_addon and kc_user:
            # Find addon keymaps that contain our items, then restore the user versions
            for km_addon in kc_addon.keymaps:
                has_addon_items = any(
                    kmi.idname.startswith("leveldesign.")
                    for kmi in km_addon.keymap_items
                )
                if has_addon_items:
                    # Restore the user keymap (which references addon keymap as default)
                    km_user = kc_user.keymaps.get(km_addon.name)
                    if km_user:
                        km_user.restore_to_default()

        self.report({'INFO'}, "Keybindings restored to defaults")
        return {'FINISHED'}


class LevelDesignPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    mouse_sensitivity: FloatProperty(
        name="Mouse Sensitivity",
        description="Mouse look sensitivity for freelook camera",
        default=0.006,
        min=0.001,
        max=0.05,
        precision=4,
    )

    move_speed: FloatProperty(
        name="Move Speed",
        description="Movement speed for freelook camera (adjust with scroll wheel)",
        default=0.1,
        min=0.001,
        max=10.0,
        precision=3,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mouse_sensitivity")
        layout.prop(self, "move_speed")

        # Keybindings section
        layout.separator()
        row = layout.row()
        row.label(text="Keybindings")
        row.operator("leveldesign.restore_default_keybindings", text="Restore Defaults")
        layout.label(text="Context Menu is a default Blender item but is here because by default this addon remaps it", icon='INFO')

        wm = context.window_manager
        kc_addon = wm.keyconfigs.addon
        kc_user = wm.keyconfigs.user
        if kc_addon:
            # Collect all addon keymap items with context
            # We iterate addon keymaps to find our items, then look up the user's version
            keymap_entries = []
            for km_addon in kc_addon.keymaps:
                # Find the corresponding user keymap
                km_user = kc_user.keymaps.get(km_addon.name) if kc_user else None

                for kmi_addon in km_addon.keymap_items:
                    if kmi_addon.idname.startswith("leveldesign."):
                        base_name = kmi_addon.name if kmi_addon.name else kmi_addon.idname

                        # Find the matching user keymap item
                        kmi_user = None
                        if km_user:
                            for kmi in km_user.keymap_items:
                                if kmi.idname == kmi_addon.idname:
                                    # For operators with properties, match on properties too
                                    if kmi_addon.idname == "leveldesign.ortho_view":
                                        if (hasattr(kmi.properties, "view_type") and
                                            hasattr(kmi_addon.properties, "view_type") and
                                            kmi.properties.view_type == kmi_addon.properties.view_type):
                                            kmi_user = kmi
                                            break
                                    else:
                                        kmi_user = kmi
                                        break

                        # Use user keymap item if found, otherwise fall back to addon
                        kmi_display = kmi_user if kmi_user else kmi_addon

                        # Check for property-based differentiation (e.g., ortho_view with view_type)
                        if kmi_addon.idname == "leveldesign.ortho_view" and hasattr(kmi_addon.properties, "view_type"):
                            display_name = f"{base_name} - {kmi_addon.properties.view_type.title()}"
                        else:
                            # Add keymap context in brackets for mode-based differentiation
                            display_name = f"{base_name} ({km_addon.name})"

                        keymap_entries.append((display_name, km_addon, kmi_display))

            # Sort alphabetically by display name
            keymap_entries.sort(key=lambda x: x[0].lower())

            # Draw sorted entries
            for display_name, km, kmi in keymap_entries:
                col = layout.column()
                row = col.row(align=True)
                row.label(text=display_name)
                row.prop(kmi, "map_type", text="")
                row.prop(kmi, "type", text="", full_event=True)
                row.prop(kmi, "active", text="", emboss=False)


def register():
    bpy.utils.register_class(LEVELDESIGN_OT_restore_default_keybindings)
    bpy.utils.register_class(LevelDesignPreferences)
    properties.register()
    handlers.register()
    operators.register()
    panels.register()
    workspace.register()


def unregister():
    workspace.unregister()
    panels.unregister()
    operators.unregister()
    handlers.unregister()
    properties.unregister()
    bpy.utils.unregister_class(LevelDesignPreferences)
    bpy.utils.unregister_class(LEVELDESIGN_OT_restore_default_keybindings)


if __name__ == "__main__":
    register()
