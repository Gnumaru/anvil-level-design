bl_info = {
    "name": "Anvil Level Design",
    "author": "Alex Hetherington",
    "version": (1, 0, 0),
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


def register():
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


if __name__ == "__main__":
    register()
