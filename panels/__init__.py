from . import uv_panel
from . import freelook_panel


def register():
    uv_panel.register()
    freelook_panel.register()


def unregister():
    freelook_panel.unregister()
    uv_panel.unregister()
