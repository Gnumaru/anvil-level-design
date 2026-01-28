"""
Hotspot Mapping - Workspace

Creates the "Hotspot Mapping" workspace with Image Editor layout.
Note: Registration and timer handling is done in the main workspace.py
"""

import bpy

from ..utils import debug_log


WORKSPACE_NAME = "Hotspot Mapping"


def workspace_exists():
    """Check if the Hotspot Mapping workspace already exists."""
    return WORKSPACE_NAME in bpy.data.workspaces


def create_hotspot_mapping_workspace():
    """Create the Hotspot Mapping workspace if it doesn't exist.

    Layout:
    +----------------------------------+----------+
    |                                  | OUTLINER |
    |         IMAGE_EDITOR             +----------+
    |         N-panel open (Anvil)     |  PROPS   |
    +----------------------------------+          |
    |      FILE_BROWSER                |          |
    +----------------------------------+----------+

    Returns True if workspace was created, False if it already existed or failed.
    """
    if workspace_exists():
        return False

    # Store current workspace
    original_workspace = bpy.context.window.workspace
    original_name = original_workspace.name

    try:
        # Get workspace names before duplicate
        existing_names = set(bpy.data.workspaces.keys())

        # Create new workspace by duplicating current one
        bpy.ops.workspace.duplicate()

        # Find the new workspace
        new_workspace = None
        for ws in bpy.data.workspaces:
            if ws.name not in existing_names:
                new_workspace = ws
                break

        # Fallback: look for the .001 suffix pattern
        if not new_workspace:
            for suffix in ['.001', '.002', '.003']:
                candidate_name = original_name + suffix
                if candidate_name in bpy.data.workspaces:
                    new_workspace = bpy.data.workspaces[candidate_name]
                    break

        if not new_workspace:
            print(f"Anvil Hotspot Mapping: Could not find duplicated workspace")
            return False

        new_workspace.name = WORKSPACE_NAME

        # Schedule the layout configuration
        bpy.app.timers.register(
            lambda: _setup_workspace_deferred(original_workspace),
            first_interval=0.1
        )

        return True

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error creating workspace: {e}")
        return False


def _setup_workspace_deferred(original_workspace):
    """Deferred workspace setup to ensure Blender is ready."""
    try:
        workspace = bpy.data.workspaces.get(WORKSPACE_NAME)
        if not workspace:
            return None

        # Switch to the Hotspot Mapping workspace to configure it
        bpy.context.window.workspace = workspace

        # Start by closing areas until only one remains
        bpy.app.timers.register(_close_areas_step, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in deferred setup: {e}")

    return None


def _close_areas_step():
    """Close areas until only one remains."""
    try:
        screen = bpy.context.window.screen
        areas = list(screen.areas)

        if len(areas) <= 1:
            # Done closing, now start splitting
            bpy.app.timers.register(_configure_layout_step1, first_interval=0.1)
            return None

        # Try to close the first area that isn't the largest
        largest = max(areas, key=lambda a: a.width * a.height)

        for area in areas:
            if area != largest:
                try:
                    region = None
                    for r in area.regions:
                        if r.type == 'WINDOW':
                            region = r
                            break

                    if region:
                        with bpy.context.temp_override(area=area, region=region):
                            bpy.ops.screen.area_close()

                        # Schedule next close
                        bpy.app.timers.register(_close_areas_step, first_interval=0.1)
                        return None
                except Exception as e:
                    debug_log(f"[Hotspots] Close failed for area: {e}")
                    continue

        # If we get here, couldn't close any areas - proceed anyway
        debug_log("[Hotspots] Could not close all areas, proceeding with current layout")
        bpy.app.timers.register(_configure_layout_step1, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in close step: {e}")
        bpy.app.timers.register(_configure_layout_step1, first_interval=0.1)

    return None


def _get_areas_by_type(screen, area_type):
    """Get all areas of a specific type."""
    return [a for a in screen.areas if a.type == area_type]


def _split_area(area, direction, factor):
    """Split an area using operator with override context."""
    region = None
    for r in area.regions:
        if r.type == 'WINDOW':
            region = r
            break

    if not region:
        return False

    try:
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.screen.area_split(direction=direction, factor=factor)
        return True
    except Exception as e:
        debug_log(f"[Hotspots] Error splitting area: {e}")
        return False


def _configure_layout_step1():
    """Step 1: Create main IMAGE_EDITOR and split for right column."""
    try:
        workspace = bpy.data.workspaces.get(WORKSPACE_NAME)
        if not workspace or bpy.context.window.workspace != workspace:
            return None

        screen = bpy.context.window.screen
        areas = list(screen.areas)

        # Get the main area and set it to IMAGE_EDITOR
        if len(areas) == 1:
            main_area = areas[0]
        else:
            main_area = max(areas, key=lambda a: a.width * a.height)

        main_area.type = 'IMAGE_EDITOR'

        # Split vertically: 80% left (image editor + file browser), 20% right (outliner+props)
        _split_area(main_area, 'VERTICAL', 0.80)

        bpy.app.timers.register(_configure_layout_step2, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in layout step 1: {e}")

    return None


def _configure_layout_step2():
    """Step 2: Mark right column as OUTLINER, then split for Properties."""
    try:
        screen = bpy.context.window.screen

        # Find the rightmost IMAGE_EDITOR area - that's our right column
        image_areas = _get_areas_by_type(screen, 'IMAGE_EDITOR')
        if len(image_areas) < 2:
            bpy.app.timers.register(_configure_layout_step3, first_interval=0.1)
            return None

        # Sort by x, rightmost is our right column
        image_areas.sort(key=lambda a: a.x, reverse=True)
        right_column = image_areas[0]

        # Mark it as OUTLINER
        right_column.type = 'OUTLINER'

        # Split it horizontally: 30% top (Outliner), 70% bottom (Properties)
        _split_area(right_column, 'HORIZONTAL', 0.3)

        bpy.app.timers.register(_configure_layout_step3, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in layout step 2: {e}")

    return None


def _configure_layout_step3():
    """Step 3: Set bottom of right column to PROPERTIES."""
    try:
        screen = bpy.context.window.screen

        # Find OUTLINER areas (from the split in step 2)
        outliner_areas = _get_areas_by_type(screen, 'OUTLINER')

        if len(outliner_areas) >= 2:
            # Sort by y - lowest one becomes PROPERTIES
            outliner_areas.sort(key=lambda a: a.y)
            outliner_areas[0].type = 'PROPERTIES'

        bpy.app.timers.register(_configure_layout_step4, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in layout step 3: {e}")

    return None


def _configure_layout_step4():
    """Step 4: Split left area horizontally for file browser at bottom."""
    try:
        screen = bpy.context.window.screen

        # Find IMAGE_EDITOR areas (should only be on the left side now)
        image_areas = _get_areas_by_type(screen, 'IMAGE_EDITOR')
        if not image_areas:
            bpy.app.timers.register(_configure_layout_step5, first_interval=0.1)
            return None

        # Get the IMAGE_EDITOR area
        main_area = max(image_areas, key=lambda a: a.width * a.height)

        # Split horizontally: 80% top (image editor), 20% bottom (file browser)
        _split_area(main_area, 'HORIZONTAL', 0.20)

        bpy.app.timers.register(_configure_layout_step5, first_interval=0.1)

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in layout step 4: {e}")

    return None


def _configure_layout_step5():
    """Step 5: Mark bottom left as FILE_BROWSER and finalize."""
    try:
        screen = bpy.context.window.screen

        # Find IMAGE_EDITOR areas
        image_areas = _get_areas_by_type(screen, 'IMAGE_EDITOR')
        if image_areas:
            # Sort by y - lowest becomes file browser
            image_areas.sort(key=lambda a: a.y)
            image_areas[0].type = 'FILE_BROWSER'

        # Configure the remaining Image Editor
        image_areas = _get_areas_by_type(screen, 'IMAGE_EDITOR')
        for area in image_areas:
            _configure_image_editor(area)

        # Configure file browser
        file_browsers = _get_areas_by_type(screen, 'FILE_BROWSER')
        for area in file_browsers:
            for space in area.spaces:
                if space.type == 'FILE_BROWSER' and space.params:
                    space.params.display_type = 'THUMBNAIL'
                    break

        print(f"Anvil Hotspot Mapping: Created '{WORKSPACE_NAME}' workspace")

        # Switch to Level Design workspace if it exists
        level_design_ws = bpy.data.workspaces.get("Level Design")
        if level_design_ws:
            bpy.context.window.workspace = level_design_ws

    except Exception as e:
        print(f"Anvil Hotspot Mapping: Error in layout step 5: {e}")

    return None


def _configure_image_editor(area):
    """Configure the Image Editor area."""
    for space in area.spaces:
        if space.type == 'IMAGE_EDITOR':
            # Show the sidebar (N-panel)
            space.show_region_ui = True

            # Show the toolbar (T-panel)
            space.show_region_toolbar = True

            break


def register():
    # Registration is handled by main workspace.py
    pass


def unregister():
    # Unregistration is handled by main workspace.py
    pass
