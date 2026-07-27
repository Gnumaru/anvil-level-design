"""UV Transform Modal Tool

A modal tool that displays a ghost texture preview on the selected face
with interactive handles for adjusting scale, offset, and rotation.
"""

import bpy
from . import operator
from . import hotkeys


_addon_keymaps = []


def register():
    """Register the UV transform modal operator and keymap."""
    hotkeys.register()
    operator.register()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')

        kmi = km.keymap_items.new(
            operator.MESH_OT_uv_transform_modal.bl_idname,
            type='T',
            value='PRESS',
            ctrl=False,
            shift=True,
            alt=False,
        )

        _addon_keymaps.append((km, kmi))

        kmi = km.keymap_items.new(
            hotkeys.PIXEL_SNAP_OPERATOR_ID,
            type=hotkeys.PIXEL_SNAP_DEFAULT_KEY,
            value='PRESS',
        )

        _addon_keymaps.append((km, kmi))


def unregister():
    """Unregister the UV transform modal operator and keymap."""
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()

    operator.unregister()
    hotkeys.unregister()
