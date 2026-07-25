"""Cheap context-action presentation and authoritative W-key execution."""

from dataclasses import dataclass

import bmesh
import bpy

from ..core.workspace_check import is_level_design_workspace
from ..prefabs.repeat_action import has_repeat_prefab_candidate, resolve_repeat_prefab
from .pending_mesh_action import get_pending_action_kind, resolve_pending_action


@dataclass(frozen=True)
class ContextAction:
    kind: str
    label: str
    icon: str
    operator_id: str
    payload: dict
    error: str
    should_report_error: bool


_MESH_ACTION_PRESENTATION = {
    'BRIDGE': ("Bridge Edge Loops", 'AUTOMERGE_ON', "leveldesign.weld_bridge"),
    'CORRIDOR': ("Create Corridor", 'AUTOMERGE_ON', "leveldesign.weld_corridor"),
    'INVERT': ("Invert Box", 'NORMALS_FACE', "leveldesign.weld_invert"),
    'FOLDED_PLANE': ("Complete Folded Plane", 'AUTOMERGE_ON', "leveldesign.weld_folded_plane"),
}
_queued_action = None


def _none_action(error, should_report_error):
    return ContextAction('NONE', "None", 'AUTOMERGE_ON', "", {}, error, should_report_error)


def _presented_action(kind, payload):
    if kind == 'PREFAB':
        return ContextAction(
            'PREFAB', "Repeat Prefab", 'DUPLICATE',
            "leveldesign.prefab_instantiate", payload, "", False,
        )
    label, icon, operator_id = _MESH_ACTION_PRESENTATION[kind]
    return ContextAction(kind, label, icon, operator_id, payload, "", False)


def get_context_action_summary(active_object, context_mode):
    """Return cheap, read-only presentation state for the panel and poll."""
    if context_mode != 'EDIT_MESH' and has_repeat_prefab_candidate(active_object):
        return _presented_action('PREFAB', {})
    kind = get_pending_action_kind(active_object, context_mode)
    if kind == 'NONE':
        return _none_action("", False)
    return _presented_action(kind, {})


def resolve_context_action(scene, active_object, context_mode, bm):
    """Resolve exactly what W would do from current durable state."""
    if context_mode != 'EDIT_MESH':
        repeat_action = resolve_repeat_prefab(scene, active_object)
        if repeat_action is not None:
            if repeat_action.resolved_prefab is None:
                return _none_action(
                    repeat_action.resolution_error,
                    repeat_action.should_report_error,
                )
            return _presented_action(
                'PREFAB', {"resolved_prefab": repeat_action.resolved_prefab},
            )

    pending = resolve_pending_action(active_object, context_mode, bm)
    if pending is None:
        return _none_action("", False)
    return _presented_action(pending.kind, {"pending": pending})


def _invoke_repeat_prefab(resolved_prefab, window_manager):
    last_properties = window_manager.operator_properties_last(
        "leveldesign.prefab_instantiate"
    )
    object_name = resolved_prefab["asset_name"]
    name_suffix = ""
    make_fully_local = False
    if (
            last_properties is not None
            and last_properties.library_index == resolved_prefab["library_index"]
            and last_properties.source_object_name == resolved_prefab["asset_name"]):
        object_name = last_properties.object_name
        name_suffix = last_properties.name_suffix
        make_fully_local = last_properties.make_fully_local

    repeat_source_object_name = ""
    repeat_object = resolved_prefab.get("repeat_object")
    if repeat_object is not None:
        repeat_source_object_name = repeat_object.name
    return bpy.ops.leveldesign.prefab_instantiate(
        'INVOKE_DEFAULT',
        library_index=resolved_prefab["library_index"],
        source_object_name=resolved_prefab["asset_name"],
        repeat_source_object_name=repeat_source_object_name,
        object_name=object_name,
        name_suffix=name_suffix,
        make_fully_local=make_fully_local,
        asset_type='OBJECT',
        placement_rotation=0.0,
    )


def _run_queued_action():
    global _queued_action
    queued = _queued_action
    _queued_action = None
    if queued is None or not is_level_design_workspace():
        return None
    action, override = queued
    result = {'CANCELLED'}
    try:
        with bpy.context.temp_override(**override):
            if action.kind == 'PREFAB':
                _invoke_repeat_prefab(
                    action.payload["resolved_prefab"],
                    bpy.context.window_manager,
                )
                return None
            if action.kind == 'BRIDGE':
                result = bpy.ops.leveldesign.weld_bridge()
            elif action.kind == 'CORRIDOR':
                pending = action.payload["pending"]
                result = bpy.ops.leveldesign.weld_corridor(
                    depth=pending.depth,
                    direction=pending.direction,
                    back_plane_offset=pending.back_plane_offset,
                )
            elif action.kind == 'INVERT':
                pending = action.payload["pending"]
                indices = ",".join(str(index) for index in pending.face_indices)
                result = bpy.ops.leveldesign.weld_invert(
                    face_indices=indices,
                    object_mode=pending.object_mode,
                )
            elif action.kind == 'FOLDED_PLANE':
                pending = action.payload["pending"]
                origin, local_x, local_y, cdx, cdy = pending.cuboid_params
                result = bpy.ops.leveldesign.weld_folded_plane(
                    origin=origin,
                    local_x=local_x,
                    local_y=local_y,
                    cdx=cdx,
                    cdy=cdy,
                    coplanar_blocked=pending.coplanar_blocked,
                )
            if 'FINISHED' in result:
                bpy.ops.ed.undo_push(message=action.label)
    except (ReferenceError, RuntimeError) as exc:
        print(f"Anvil Level Design: Error running context action: {exc}")
    return None


class LEVELDESIGN_OT_context_action(bpy.types.Operator):
    """Run the action currently shown in the Anvil panel"""

    bl_idname = "leveldesign.context_weld"
    bl_label = "Run Context Action"

    @classmethod
    def poll(cls, context):
        if not is_level_design_workspace():
            return False
        action = get_context_action_summary(
            context.active_object, context.mode,
        )
        return action.kind != 'NONE'

    def execute(self, context):
        global _queued_action
        active_object = context.active_object
        bm = None
        if (
                context.mode == 'EDIT_MESH'
                and active_object is not None
                and active_object.type == 'MESH'):
            bm = bmesh.from_edit_mesh(active_object.data)
        action = resolve_context_action(context.scene, active_object, context.mode, bm)
        if action.kind == 'NONE':
            if action.should_report_error and action.error:
                self.report({'ERROR'}, action.error)
            return {'CANCELLED'}
        if _queued_action is not None:
            return {'CANCELLED'}
        override = {
            "window": context.window,
            "screen": context.screen,
            "area": context.area,
            "region": context.region,
        }
        _queued_action = (action, override)
        if not bpy.app.timers.is_registered(_run_queued_action):
            bpy.app.timers.register(_run_queued_action, first_interval=0.0)
        return {'FINISHED'}


_addon_keymaps = []


def register():
    bpy.utils.register_class(LEVELDESIGN_OT_context_action)
    key_config = bpy.context.window_manager.keyconfigs.addon
    if key_config is None:
        return
    for keymap_name, space_type in (('Mesh', 'EMPTY'), ('Object Mode', 'EMPTY')):
        keymap = key_config.keymaps.new(name=keymap_name, space_type=space_type)
        item = keymap.keymap_items.new(
            "leveldesign.context_weld", 'W', 'PRESS', head=True,
        )
        _addon_keymaps.append((keymap, item))


def unregister():
    global _queued_action
    if bpy.app.timers.is_registered(_run_queued_action):
        bpy.app.timers.unregister(_run_queued_action)
    _queued_action = None
    for keymap, item in _addon_keymaps:
        keymap.keymap_items.remove(item)
    _addon_keymaps.clear()
    bpy.utils.unregister_class(LEVELDESIGN_OT_context_action)
