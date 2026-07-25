"""Registration facade for context actions and mesh weld actions.

State, dispatch, and execution intentionally live in separate modules. The
re-exports keep geometry producers concise while existing files migrate to the
more accurate pending-action terminology.
"""

from .pending_mesh_action import (
    clear_on_bmesh,
    reset_runtime_state,
    snapshot_coplanar_sides,
    store_from_box_builder,
    store_from_box_builder_object_mode,
    store_from_edge_selection,
)
from . import context_action, weld_actions


clear_weld_on_bmesh = clear_on_bmesh
set_weld_from_box_builder = store_from_box_builder
set_weld_from_box_builder_object_mode = store_from_box_builder_object_mode
set_weld_from_edge_selection = store_from_edge_selection


def register():
    weld_actions.register()
    context_action.register()


def unregister():
    context_action.unregister()
    weld_actions.unregister()
    reset_runtime_state()
