"""Configurable modal rotation hotkeys for Stair Builder."""

import bpy
from bpy.types import Operator

from ...core.workspace_check import is_level_design_workspace


ROTATE_LEFT = 'ROTATE_LEFT'
ROTATE_RIGHT = 'ROTATE_RIGHT'

STAIR_ROTATION_HOTKEYS = (
    (ROTATE_LEFT, "leveldesign.stair_rotate_left", 'Q'),
    (ROTATE_RIGHT, "leveldesign.stair_rotate_right", 'E'),
)

_ROTATION_ACTION_IDS = {
    operator_id: action
    for action, operator_id, _default_key in STAIR_ROTATION_HOTKEYS
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


def _effective_rotation_keymap_items(window_manager, mode):
    keymap_name = 'Mesh' if mode == 'EDIT_MESH' else 'Object Mode'
    user_keyconfig = window_manager.keyconfigs.user
    addon_keyconfig = window_manager.keyconfigs.addon
    user_keymap = (
        user_keyconfig.keymaps.get(keymap_name)
        if user_keyconfig else None
    )
    addon_keymap = (
        addon_keyconfig.keymaps.get(keymap_name)
        if addon_keyconfig else None
    )

    effective_items = []
    for _action, operator_id, _default_key in STAIR_ROTATION_HOTKEYS:
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


def rotation_action_for_event(window_manager, mode, event):
    for keymap_item in _effective_rotation_keymap_items(window_manager, mode):
        if _event_matches_keymap_item(event, keymap_item):
            return _ROTATION_ACTION_IDS[keymap_item.idname]
    return None


def _key_type_label(key_type):
    labels = {
        'LEFTMOUSE': "LMB",
        'MIDDLEMOUSE': "MMB",
        'RIGHTMOUSE': "RMB",
        'WHEELUPMOUSE': "Wheel Up",
        'WHEELDOWNMOUSE': "Wheel Down",
    }
    if key_type in labels:
        return labels[key_type]
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


def rotation_shortcut_labels(window_manager, mode):
    labels = {
        action: "Unbound"
        for action, _operator_id, _default_key in STAIR_ROTATION_HOTKEYS
    }
    for keymap_item in _effective_rotation_keymap_items(window_manager, mode):
        action = _ROTATION_ACTION_IDS[keymap_item.idname]
        labels[action] = _keymap_item_label(keymap_item)
    return labels


class _StairRotationHotkeyOperator(Operator):
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace()

    def execute(self, context):
        return {'PASS_THROUGH'}


class LEVELDESIGN_OT_stair_rotate_left(_StairRotationHotkeyOperator):
    """Rotate the Stair Builder uphill direction left while drawing"""

    bl_idname = "leveldesign.stair_rotate_left"
    bl_label = "Stair Builder Rotate Left"


class LEVELDESIGN_OT_stair_rotate_right(_StairRotationHotkeyOperator):
    """Rotate the Stair Builder uphill direction right while drawing"""

    bl_idname = "leveldesign.stair_rotate_right"
    bl_label = "Stair Builder Rotate Right"


classes = (
    LEVELDESIGN_OT_stair_rotate_left,
    LEVELDESIGN_OT_stair_rotate_right,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
