"""Clip tool registration and configurable keymap entries."""

import bpy

from . import hotkeys, operator
from ..modal_draw import preview


_addon_keymaps = []


def register():
    hotkeys.register()
    operator.register()
    key_config = bpy.context.window_manager.keyconfigs.addon
    if key_config:
        keymap = key_config.keymaps.new(name='Mesh', space_type='EMPTY')
        clip_item = keymap.keymap_items.new(
            operator.MESH_OT_clip.bl_idname,
            type='NONE',
            value='PRESS',
            ctrl=False,
            shift=False,
            alt=False,
        )
        _addon_keymaps.append((keymap, clip_item))
        for _action, operator_id, default_key in hotkeys.CLIP_MODE_HOTKEYS:
            mode_item = keymap.keymap_items.new(
                operator_id,
                type=default_key,
                value='PRESS',
                ctrl=False,
                shift=False,
                alt=False,
            )
            _addon_keymaps.append((keymap, mode_item))


def unregister():
    for keymap, keymap_item in _addon_keymaps:
        keymap.keymap_items.remove(keymap_item)
    _addon_keymaps.clear()
    preview.cleanup_preview()
    operator.unregister()
    hotkeys.unregister()
