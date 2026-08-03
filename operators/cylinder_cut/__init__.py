"""Cylinder Cut tool registration and configurable unbound keymap entry."""

import bpy

from . import operator
from ..modal_draw import preview


_addon_keymaps = []


def register():
    operator.register()

    window_manager = bpy.context.window_manager
    key_config = window_manager.keyconfigs.addon
    if key_config:
        keymap = key_config.keymaps.new(name='Mesh', space_type='EMPTY')
        keymap_item = keymap.keymap_items.new(
            operator.MESH_OT_cylinder_cut.bl_idname,
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
