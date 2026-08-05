"""Registration for context actions and mesh weld actions."""

from .pending_mesh_action import reset_runtime_state
from . import context_action, weld_actions


def register():
    weld_actions.register()
    context_action.register()


def unregister():
    context_action.unregister()
    weld_actions.unregister()
    reset_runtime_state()
