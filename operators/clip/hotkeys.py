"""Configurable modal mode-cycle hotkeys for the Clip tool."""

import bpy
from bpy.types import Operator

from ...core.workspace_check import is_level_design_workspace


PREVIOUS_MODE = 'PREVIOUS_MODE'
NEXT_MODE = 'NEXT_MODE'

CLIP_MODE_HOTKEYS = (
    (PREVIOUS_MODE, "leveldesign.clip_previous_mode", 'Q'),
    (NEXT_MODE, "leveldesign.clip_next_mode", 'E'),
)

_ACTION_IDS = {
    operator_id: action
    for action, operator_id, _default_key in CLIP_MODE_HOTKEYS
}


def _event_matches_keymap_item(event, keymap_item):
    if not getattr(keymap_item, "active", True):
        return False
    if keymap_item.value != 'ANY' and event.value != keymap_item.value:
        return False
    if event.type != keymap_item.type:
        return False
    if getattr(keymap_item, "any", False):
        return True
    for attribute_name in ('ctrl', 'shift', 'alt', 'oskey'):
        if (
                getattr(event, attribute_name, False)
                != getattr(keymap_item, attribute_name, False)
        ):
            return False
    key_modifier = getattr(keymap_item, "key_modifier", 'NONE')
    if key_modifier != 'NONE':
        return getattr(event, "key_modifier", 'NONE') == key_modifier
    return True


def _effective_keymap_items(window_manager):
    user_keyconfig = window_manager.keyconfigs.user
    addon_keyconfig = window_manager.keyconfigs.addon
    user_keymap = (
        user_keyconfig.keymaps.get('Mesh')
        if user_keyconfig else None
    )
    addon_keymap = (
        addon_keyconfig.keymaps.get('Mesh')
        if addon_keyconfig else None
    )

    effective_items = []
    for _action, operator_id, _default_key in CLIP_MODE_HOTKEYS:
        user_items = []
        if user_keymap:
            user_items = [
                keymap_item
                for keymap_item in user_keymap.keymap_items
                if keymap_item.idname == operator_id
            ]
        if user_items:
            effective_items.extend(user_items)
            continue
        if addon_keymap:
            effective_items.extend(
                keymap_item
                for keymap_item in addon_keymap.keymap_items
                if keymap_item.idname == operator_id
            )
    return effective_items


def action_for_event(window_manager, event):
    for keymap_item in _effective_keymap_items(window_manager):
        if _event_matches_keymap_item(event, keymap_item):
            return _ACTION_IDS[keymap_item.idname]
    return None


def _key_type_label(key_type):
    if len(key_type) == 1:
        return key_type
    return key_type.replace('_', ' ').title()


def _keymap_item_label(keymap_item):
    if not getattr(keymap_item, "active", True) or keymap_item.type == 'NONE':
        return "Unbound"
    parts = []
    key_modifier = getattr(keymap_item, "key_modifier", 'NONE')
    if key_modifier != 'NONE':
        parts.append(_key_type_label(key_modifier))
    if getattr(keymap_item, "ctrl", False):
        parts.append("Ctrl")
    if getattr(keymap_item, "shift", False):
        parts.append("Shift")
    if getattr(keymap_item, "alt", False):
        parts.append("Alt")
    if getattr(keymap_item, "oskey", False):
        parts.append("OS")
    parts.append(_key_type_label(keymap_item.type))
    return "+".join(parts)


def shortcut_labels(window_manager):
    labels = {
        action: "Unbound"
        for action, _operator_id, _default_key in CLIP_MODE_HOTKEYS
    }
    for keymap_item in _effective_keymap_items(window_manager):
        labels[_ACTION_IDS[keymap_item.idname]] = _keymap_item_label(keymap_item)
    return labels


class _ClipModeHotkeyOperator(Operator):
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace() and context.mode == 'EDIT_MESH'

    def execute(self, context):
        return {'PASS_THROUGH'}


class LEVELDESIGN_OT_clip_previous_mode(_ClipModeHotkeyOperator):
    """Cycle to the previous Clip removal mode while drawing"""

    bl_idname = "leveldesign.clip_previous_mode"
    bl_label = "Clip Previous Mode"


class LEVELDESIGN_OT_clip_next_mode(_ClipModeHotkeyOperator):
    """Cycle to the next Clip removal mode while drawing"""

    bl_idname = "leveldesign.clip_next_mode"
    bl_label = "Clip Next Mode"


classes = (
    LEVELDESIGN_OT_clip_previous_mode,
    LEVELDESIGN_OT_clip_next_mode,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
