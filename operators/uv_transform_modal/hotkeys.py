"""Configurable hotkeys for UV Transform mode."""

import bpy
from bpy.types import Operator

from ...core.workspace_check import is_level_design_workspace


PIXEL_SNAP_OPERATOR_ID = "leveldesign.uv_transform_pixel_snap"
PIXEL_SNAP_DEFAULT_KEY = 'LEFT_CTRL'
_MODIFIER_KEY_TYPES = {
    'ctrl': {'LEFT_CTRL', 'RIGHT_CTRL'},
    'shift': {'LEFT_SHIFT', 'RIGHT_SHIFT'},
    'alt': {'LEFT_ALT', 'RIGHT_ALT'},
    'oskey': {'OSKEY'},
}


def _effective_pixel_snap_keymap_items(window_manager):
    user_keyconfig = window_manager.keyconfigs.user
    addon_keyconfig = window_manager.keyconfigs.addon
    user_keymap = user_keyconfig.keymaps.get("Mesh") if user_keyconfig else None
    addon_keymap = addon_keyconfig.keymaps.get("Mesh") if addon_keyconfig else None

    if user_keymap:
        user_items = [
            keymap_item
            for keymap_item in user_keymap.keymap_items
            if keymap_item.idname == PIXEL_SNAP_OPERATOR_ID
        ]
        if user_items:
            return user_items

    if addon_keymap:
        return [
            keymap_item
            for keymap_item in addon_keymap.keymap_items
            if keymap_item.idname == PIXEL_SNAP_OPERATOR_ID
        ]

    return []


def _event_type_matches(event, keymap_item):
    if keymap_item.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
        return event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}
    return event.type == keymap_item.type


def _event_matches_keymap_item(event, keymap_item):
    if not getattr(keymap_item, "active", True):
        return False
    if keymap_item.type == 'NONE' or not _event_type_matches(event, keymap_item):
        return False
    if getattr(keymap_item, "any", False):
        return True

    for attr_name, key_types in _MODIFIER_KEY_TYPES.items():
        if keymap_item.type in key_types:
            continue
        if getattr(event, attr_name, False) != getattr(keymap_item, attr_name, False):
            return False

    key_modifier = getattr(keymap_item, "key_modifier", 'NONE')
    if key_modifier != 'NONE':
        return getattr(event, "key_modifier", 'NONE') == key_modifier
    return True


def pixel_snap_state_for_event(window_manager, event):
    """Return True/False for a configured hotkey press/release, else None."""
    if event.value not in {'PRESS', 'RELEASE'}:
        return None

    for keymap_item in _effective_pixel_snap_keymap_items(window_manager):
        if _event_matches_keymap_item(event, keymap_item):
            return event.value == 'PRESS'
    return None


def _key_type_label(key_type):
    if key_type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
        return "Ctrl"
    if key_type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        return "Shift"
    if key_type in {'LEFT_ALT', 'RIGHT_ALT'}:
        return "Alt"
    if len(key_type) == 1:
        return key_type
    return key_type.replace('_', ' ').title()


def pixel_snap_shortcut_label(window_manager):
    for keymap_item in _effective_pixel_snap_keymap_items(window_manager):
        if not getattr(keymap_item, "active", True) or keymap_item.type == 'NONE':
            continue

        parts = []
        key_modifier = getattr(keymap_item, "key_modifier", 'NONE')
        if key_modifier != 'NONE':
            parts.append(_key_type_label(key_modifier))
        for attr_name, label in (
                ('ctrl', "Ctrl"),
                ('shift', "Shift"),
                ('alt', "Alt"),
                ('oskey', "OS")):
            if (keymap_item.type not in _MODIFIER_KEY_TYPES[attr_name]
                    and getattr(keymap_item, attr_name, False)):
                parts.append(label)
        parts.append(_key_type_label(keymap_item.type))
        return "+".join(parts)
    return "Unbound"


class LEVELDESIGN_OT_uv_transform_pixel_snap(Operator):
    """Temporarily enable pixel snapping in UV Transform mode"""
    bl_idname = PIXEL_SNAP_OPERATOR_ID
    bl_label = "UV Transform Pixel Snap"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace()

    def execute(self, context):
        return {'PASS_THROUGH'}


classes = (
    LEVELDESIGN_OT_uv_transform_pixel_snap,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
