"""
Hotspot Mapping - Operators

Operators for managing hotspots: assign hotspottable, add/delete/select hotspots.
"""

import bpy
from bpy.props import StringProperty

from . import json_storage
from ..utils import debug_log


class HOTSPOT_OT_assign_hotspottable(bpy.types.Operator):
    """Mark the current image as hotspottable"""
    bl_idname = "hotspot.assign_hotspottable"
    bl_label = "Assign Hotspottable"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Must have an image in the editor and blend file must be saved
        if not bpy.data.filepath:
            return False
        space = context.space_data
        if space and space.type == 'IMAGE_EDITOR':
            return space.image is not None
        return False

    def execute(self, context):
        space = context.space_data
        image = space.image

        if not image:
            self.report({'WARNING'}, "No image selected")
            return {'CANCELLED'}

        # Get image dimensions
        width = image.size[0] if image.size[0] > 0 else 1
        height = image.size[1] if image.size[1] > 0 else 1

        # Add to hotspots.json
        if json_storage.add_texture_as_hotspottable(image.name, width, height):
            self.report({'INFO'}, f"Added '{image.name}' as hotspottable")
            # Update active texture in scene props
            context.scene.hotspot_mapping_props.active_texture = image.name
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Failed to save hotspots.json")
            return {'CANCELLED'}


class HOTSPOT_OT_remove_hotspottable(bpy.types.Operator):
    """Remove the current image from hotspottable list"""
    bl_idname = "hotspot.remove_hotspottable"
    bl_label = "Remove Hotspottable"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not bpy.data.filepath:
            return False
        space = context.space_data
        if space and space.type == 'IMAGE_EDITOR' and space.image:
            return json_storage.is_texture_hotspottable(space.image.name)
        return False

    def execute(self, context):
        space = context.space_data
        image = space.image

        if json_storage.remove_texture_as_hotspottable(image.name):
            self.report({'INFO'}, f"Removed '{image.name}' from hotspottable")
            # Clear active texture
            context.scene.hotspot_mapping_props.active_texture = ""
            context.scene.hotspot_mapping_props.active_hotspot_id = ""
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Failed to update hotspots.json")
            return {'CANCELLED'}


class HOTSPOT_OT_add_hotspot(bpy.types.Operator):
    """Add a new hotspot to the current texture"""
    bl_idname = "hotspot.add_hotspot"
    bl_label = "Add Hotspot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not bpy.data.filepath:
            return False
        space = context.space_data
        if space and space.type == 'IMAGE_EDITOR' and space.image:
            return json_storage.is_texture_hotspottable(space.image.name)
        return False

    def execute(self, context):
        space = context.space_data
        image = space.image
        texture_name = image.name

        # Get image dimensions
        img_width = image.size[0] if image.size[0] > 0 else 256
        img_height = image.size[1] if image.size[1] > 0 else 256

        # Default hotspot: 128x128 centered in image
        hotspot_size = 128
        x = (img_width - hotspot_size) // 2
        y = (img_height - hotspot_size) // 2

        # Clamp to valid range
        x = max(0, min(x, img_width - hotspot_size))
        y = max(0, min(y, img_height - hotspot_size))

        new_id = json_storage.add_hotspot(
            texture_name, x, y, hotspot_size, hotspot_size
        )

        if new_id:
            self.report({'INFO'}, f"Added hotspot: {new_id}")
            # Select the new hotspot
            context.scene.hotspot_mapping_props.active_hotspot_id = new_id
            # Force redraw
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.tag_redraw()
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Failed to add hotspot")
            return {'CANCELLED'}


class HOTSPOT_OT_delete_hotspot(bpy.types.Operator):
    """Delete a hotspot"""
    bl_idname = "hotspot.delete_hotspot"
    bl_label = "Delete Hotspot"
    bl_options = {'REGISTER', 'UNDO'}

    hotspot_id: StringProperty(
        name="Hotspot ID",
        description="ID of the hotspot to delete",
    )

    @classmethod
    def poll(cls, context):
        if not bpy.data.filepath:
            return False
        space = context.space_data
        if space and space.type == 'IMAGE_EDITOR' and space.image:
            return json_storage.is_texture_hotspottable(space.image.name)
        return False

    def execute(self, context):
        space = context.space_data
        image = space.image
        texture_name = image.name

        if not self.hotspot_id:
            self.report({'WARNING'}, "No hotspot specified")
            return {'CANCELLED'}

        if json_storage.delete_hotspot(texture_name, self.hotspot_id):
            self.report({'INFO'}, f"Deleted hotspot: {self.hotspot_id}")
            # Clear selection if we deleted the active hotspot
            props = context.scene.hotspot_mapping_props
            if props.active_hotspot_id == self.hotspot_id:
                props.active_hotspot_id = ""
            # Force redraw
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.tag_redraw()
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Failed to delete hotspot")
            return {'CANCELLED'}


class HOTSPOT_OT_select_hotspot(bpy.types.Operator):
    """Select a hotspot for editing"""
    bl_idname = "hotspot.select_hotspot"
    bl_label = "Select Hotspot"
    bl_options = {'REGISTER', 'UNDO'}

    hotspot_id: StringProperty(
        name="Hotspot ID",
        description="ID of the hotspot to select",
    )

    def execute(self, context):
        props = context.scene.hotspot_mapping_props
        props.active_hotspot_id = self.hotspot_id
        debug_log(f"[Hotspots] Selected: {self.hotspot_id}")
        # Force redraw
        for area in context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.tag_redraw()
        return {'FINISHED'}


classes = (
    HOTSPOT_OT_assign_hotspottable,
    HOTSPOT_OT_remove_hotspottable,
    HOTSPOT_OT_add_hotspot,
    HOTSPOT_OT_delete_hotspot,
    HOTSPOT_OT_select_hotspot,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
