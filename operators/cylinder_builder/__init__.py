"""Cylinder Builder registration and configurable keymap entries."""

import bpy

from . import operator
from ..modal_draw import preview


_addon_keymaps = []


def register():
    operator.register()
    key_config = bpy.context.window_manager.keyconfigs.addon
    if key_config:
        for keymap_name in ('Mesh', 'Object Mode'):
            keymap = key_config.keymaps.new(
                name=keymap_name,
                space_type='EMPTY',
            )
            keymap_item = keymap.keymap_items.new(
                operator.MESH_OT_cylinder_builder.bl_idname,
                type='NONE',
                value='PRESS',
                ctrl=False,
                shift=False,
                alt=False,
            )
            _addon_keymaps.append((keymap, keymap_item))


def unregister():
    for keymap, keymap_item in _addon_keymaps:
        keymap.keymap_items.remove(keymap_item)
    _addon_keymaps.clear()
    preview.cleanup_preview()
    operator.unregister()
