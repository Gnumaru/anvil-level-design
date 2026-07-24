import os

import bpy
from mathutils import Vector

from .base_test import AnvilTestCase, _get_window
from ..operators.box_builder.geometry import execute_box_builder_object_mode
from ..operators import weld as weld_module
from ..prefabs.assets import (
    get_prefab_asset_reference,
    prefab_asset_reference_parts,
)


def _undo_ctx():
    """Build a full context override for ed.undo/redo."""
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    screen = window.screen
    area = next(area for area in screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return {"window": window, "screen": screen, "area": area, "region": region}


def _create_asset_object(name):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    coords = [
        (-0.25, -0.25, 0.0),
        (0.25, -0.25, 0.0),
        (0.25, 0.25, 0.0),
        (-0.25, 0.25, 0.0),
        (-0.25, -0.25, 0.5),
        (0.25, -0.25, 0.5),
        (0.25, 0.25, 0.5),
        (-0.25, 0.25, 0.5),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    mesh.from_pydata(coords, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.asset_mark()
    return obj


def _seed_prefab_library(name, mesh_weld_mode):
    scene = bpy.context.scene
    asset_obj = _create_asset_object(name)
    if mesh_weld_mode != 'NONE':
        asset_obj.data["_aw_mode"] = mesh_weld_mode
    output_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "test_outputs")
    )
    os.makedirs(output_root, exist_ok=True)
    filepath = os.path.join(output_root, f"{name.lower()}_placement.blend")
    bpy.data.libraries.write(filepath, {asset_obj})
    bpy.data.objects.remove(asset_obj, do_unlink=True)

    lib_entry = scene.anvil_prefab_libraries.add()
    lib_entry.filepath = filepath
    item = lib_entry.objects.add()
    item.name = name
    item.asset_type = 'OBJECT'
    return filepath


def _create_box_builder_object_with_invert():
    props = bpy.context.scene.level_design_props
    result = execute_box_builder_object_mode(
        Vector((0, 0, 0)),
        Vector((1, 0, 1)),
        1.0,
        Vector((1, 0, 0)),
        Vector((0, 0, 1)),
        Vector((0, 1, 0)),
        props.pixels_per_meter,
        Vector((0, -1, 0)),
        "",
    )
    if not result[0]:
        raise AssertionError(result[1])
    obj = bpy.context.view_layer.objects.active
    weld_module.set_weld_from_box_builder_object_mode(obj)
    props.weld_mode = 'INVERT'
    return obj


def _scene_has_object_name(name):
    return any(obj.name == name for obj in bpy.context.scene.collection.all_objects)


class PrefabPlacementUndoTest(AnvilTestCase):

    def tearDown(self):
        for lib_entry in bpy.context.scene.anvil_prefab_libraries:
            filepath = lib_entry.filepath
            if filepath and os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
        bpy.context.scene.anvil_prefab_libraries.clear()
        super().tearDown()

    def test_linked_prefab_without_reference_and_with_invert_mesh_state_displays_and_executes_repeat_prefab(self):
        _seed_prefab_library("LegacyRockPrefab", 'INVERT')
        result = bpy.ops.leveldesign.prefab_instantiate(
            library_index=0,
            source_object_name="LegacyRockPrefab",
            object_name="LegacyRockPrefab",
            asset_type='OBJECT',
        )
        self.assertEqual(result, {'FINISHED'})
        placed_obj = bpy.context.view_layer.objects.active
        del placed_obj["_anvil_prefab_asset_reference"]
        self.assertIsNotNone(placed_obj.data.library)
        self.assertEqual(placed_obj.data.get("_aw_mode"), 'INVERT')

        next_weld_state = weld_module.resolve_next_weld(
            bpy.context.scene,
            placed_obj,
            'OBJECT',
            None,
            False,
        )
        weld_module.sync_weld_props(
            bpy.context.scene,
            placed_obj,
            'OBJECT',
            None,
        )

        self.assertEqual(next_weld_state["mode"], 'PREFAB')
        self.assertEqual(
            next_weld_state["resolved_prefab"]["asset_name"],
            "LegacyRockPrefab",
        )
        self.assertEqual(
            bpy.context.scene.level_design_props.weld_mode,
            'PREFAB',
            "The displayed next weld must use the same prefab resolution as W",
        )

        view_ctx = _undo_ctx()
        with bpy.context.temp_override(**view_ctx):
            repeat_result = bpy.ops.leveldesign.context_weld()
        self.assertEqual(repeat_result, {'RUNNING_MODAL'})

        yield 0.05
        window = _get_window()
        mx, my = self._get_3d_viewport_center()
        window.event_simulate(type='ESC', value='PRESS', x=mx, y=my)
        yield
        window.event_simulate(type='ESC', value='RELEASE', x=mx, y=my)
        yield 0.2

    def test_prefab_placement_escape_does_not_push_repeat_prefab_undo_state(self):
        """Prefab placement Esc keeps Repeat Prefab transient and preserves box undo order."""
        filepath = _seed_prefab_library("EscRepeatPrefab", 'NONE')
        undo_ctx = _undo_ctx()
        view_ctx = undo_ctx

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before box")

        box_obj = _create_box_builder_object_with_invert()
        box_name = box_obj.name

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After box")

        with bpy.context.temp_override(**view_ctx):
            result = bpy.ops.leveldesign.prefab_instantiate(
                'INVOKE_DEFAULT',
                library_index=0,
                source_object_name="EscRepeatPrefab",
                object_name="EscRepeatPrefab",
                asset_type='OBJECT',
            )
        self.assertEqual(result, {'RUNNING_MODAL'})

        yield 0.05

        window = _get_window()
        mx, my = self._get_3d_viewport_center()
        window.event_simulate(type='ESC', value='PRESS', x=mx, y=my)
        yield
        window.event_simulate(type='ESC', value='RELEASE', x=mx, y=my)
        yield 0.2

        props = bpy.context.scene.level_design_props
        self.assertEqual(props.weld_mode, 'PREFAB')
        override_reference = weld_module._repeat_prefab_override_reference(
            bpy.context.active_object,
        )
        self.assertEqual(
            prefab_asset_reference_parts(override_reference)[1],
            "EscRepeatPrefab",
        )

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield 0.3

        self.assertFalse(_scene_has_object_name(box_name))
        self.assertNotEqual(
            bpy.context.scene.level_design_props.weld_mode,
            'PREFAB',
            "Cancelled placement must not restore Repeat Prefab from the undo stack",
        )

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield 0.3

        self.assertTrue(_scene_has_object_name(box_name))
        self.assertEqual(bpy.context.scene.level_design_props.weld_mode, 'INVERT')
        self.assertTrue(os.path.isfile(filepath))

    def test_prefab_placement_undo_redo_restores_repeat_prefab_after_successful_place(self):
        """Prefab placement undo restores box INVERT, redo restores Repeat Prefab."""
        _seed_prefab_library("RedoRepeatPrefab", 'NONE')
        undo_ctx = _undo_ctx()

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before box")

        box_obj = _create_box_builder_object_with_invert()
        box_name = box_obj.name

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After box")

        result = bpy.ops.leveldesign.prefab_instantiate(
            library_index=0,
            source_object_name="RedoRepeatPrefab",
            object_name="RedoRepeatPrefab",
            asset_type='OBJECT',
            use_placement=True,
            action_pivot=(2.0, 0.0, 0.0),
        )
        self.assertEqual(result, {'FINISHED'})
        placed_obj = bpy.context.view_layer.objects.active
        placed_name = placed_obj.name

        props = bpy.context.scene.level_design_props
        self.assertEqual(props.weld_mode, 'PREFAB')
        self.assertEqual(
            prefab_asset_reference_parts(
                get_prefab_asset_reference(placed_obj),
            )[1],
            "RedoRepeatPrefab",
        )

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After prefab placement")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield 0.3

        self.assertTrue(_scene_has_object_name(box_name))
        self.assertFalse(_scene_has_object_name(placed_name))
        self.assertEqual(bpy.context.scene.level_design_props.weld_mode, 'INVERT')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield 0.3

        self.assertTrue(_scene_has_object_name(placed_name))
        self.assertEqual(bpy.context.scene.level_design_props.weld_mode, 'PREFAB')
        self.assertEqual(
            prefab_asset_reference_parts(
                get_prefab_asset_reference(bpy.data.objects[placed_name]),
            )[1],
            "RedoRepeatPrefab",
        )
