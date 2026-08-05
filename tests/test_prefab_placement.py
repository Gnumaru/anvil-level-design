import os

import bpy
from mathutils import Vector

from .base_test import AnvilTestCase, _get_window
from .helpers import get_context_action_kind, get_undo_context, wait_for_condition
from ..operators.box_builder.geometry import execute_box_builder_object_mode
from ..operators.pending_mesh_action import store_from_shape_builder_object_mode
from ..prefabs.assets import (
    find_existing_linked_object,
    get_prefab_asset_reference,
    prefab_asset_reference_parts,
)
from ..prefabs.repeat_action import get_modal_override_reference

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


def _seed_prefab_library_assets(filepath_stem, asset_modes):
    scene = bpy.context.scene
    asset_objects = []
    for name, mesh_weld_mode in asset_modes:
        asset_obj = _create_asset_object(name)
        if mesh_weld_mode != 'NONE':
            asset_obj.data["_aw_mode"] = mesh_weld_mode
        asset_objects.append(asset_obj)
    output_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "test_outputs")
    )
    os.makedirs(output_root, exist_ok=True)
    filepath = os.path.join(output_root, f"{filepath_stem}_placement.blend")
    bpy.data.libraries.write(filepath, set(asset_objects))
    for asset_obj in asset_objects:
        bpy.data.objects.remove(asset_obj, do_unlink=True)

    lib_entry = scene.anvil_prefab_libraries.add()
    lib_entry.filepath = filepath
    for name, _mesh_weld_mode in asset_modes:
        item = lib_entry.objects.add()
        item.name = name
        item.asset_type = 'OBJECT'
    return filepath


def _seed_prefab_library(name, mesh_weld_mode):
    return _seed_prefab_library_assets(
        name.lower(), ((name, mesh_weld_mode),),
    )


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
    store_from_shape_builder_object_mode(obj)
    return obj


def _scene_has_object_name(name):
    return any(obj.name == name for obj in bpy.context.scene.collection.all_objects)


def _placed_prefab_names(asset_name):
    names = set()
    for obj in bpy.context.scene.collection.all_objects:
        parts = prefab_asset_reference_parts(get_prefab_asset_reference(obj))
        if parts is not None and parts[1] == asset_name:
            names.add(obj.name)
    return names


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

    def test_linked_prefab_selection_updates_live_panel_action_and_w_executes_repeat_prefab(self):
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

        other_mesh = bpy.data.meshes.new("panel_state_other_mesh")
        other_obj = bpy.data.objects.new("panel_state_other", other_mesh)
        bpy.context.collection.objects.link(other_obj)
        placed_obj.select_set(False)
        other_obj.select_set(True)
        bpy.context.view_layer.objects.active = other_obj

        self.assertEqual(get_context_action_kind(), 'NONE')

        other_obj.select_set(False)
        placed_obj.select_set(True)
        bpy.context.view_layer.objects.active = placed_obj

        self.assertEqual(
            get_context_action_kind(),
            'PREFAB',
            "The panel resolver and W must read the same live active-object state",
        )

        view_ctx = get_undo_context()
        with bpy.context.temp_override(**view_ctx):
            repeat_result = bpy.ops.leveldesign.context_weld()
        self.assertEqual(repeat_result, {'FINISHED'})

        yield from wait_for_condition(
            lambda: any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "W did not start Repeat Prefab placement",
        )

        window = _get_window()
        mx, my = self._get_3d_viewport_center()
        window.event_simulate(type='ESC', value='PRESS', x=mx, y=my)
        yield
        window.event_simulate(type='ESC', value='RELEASE', x=mx, y=my)
        yield from wait_for_condition(
            lambda: not any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "Repeat Prefab placement did not cancel",
        )

    def test_context_action_panel_summary_does_not_link_unselected_prefab_assets(self):
        """Repeated panel reads inspect loaded data without linking more assets."""
        filepath = _seed_prefab_library_assets(
            "panel_summary",
            (
                ("PanelSummarySelected", 'NONE'),
                ("PanelSummaryUnselected", 'NONE'),
            ),
        )
        result = bpy.ops.leveldesign.prefab_instantiate(
            library_index=0,
            source_object_name="PanelSummarySelected",
            object_name="PanelSummarySelected",
            asset_type='OBJECT',
        )
        self.assertEqual(result, {'FINISHED'})
        placed_obj = bpy.context.view_layer.objects.active
        del placed_obj["_anvil_prefab_asset_reference"]
        self.assertIsNone(
            find_existing_linked_object(filepath, "PanelSummaryUnselected"),
        )

        for _attempt in range(10):
            self.assertEqual(get_context_action_kind(), 'PREFAB')

        self.assertIsNone(
            find_existing_linked_object(filepath, "PanelSummaryUnselected"),
            "Drawing the context-action panel must not resolve or link other assets",
        )

    def test_prefab_placement_escape_does_not_push_repeat_prefab_undo_state(self):
        """Prefab placement Esc keeps Repeat Prefab transient and preserves box undo order."""
        filepath = _seed_prefab_library("EscRepeatPrefab", 'NONE')
        undo_ctx = get_undo_context()
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

        yield from wait_for_condition(
            lambda: any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "Prefab placement did not enter modal state",
        )

        window = _get_window()
        mx, my = self._get_3d_viewport_center()
        window.event_simulate(type='ESC', value='PRESS', x=mx, y=my)
        yield
        window.event_simulate(type='ESC', value='RELEASE', x=mx, y=my)
        yield from wait_for_condition(
            lambda: not any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "Prefab placement did not cancel",
        )

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'PREFAB')
        override_reference = get_modal_override_reference(
            bpy.context.active_object,
        )
        self.assertEqual(
            prefab_asset_reference_parts(override_reference)[1],
            "EscRepeatPrefab",
        )

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: not _scene_has_object_name(box_name),
            "Undo did not remove the box builder object",
        )

        self.assertFalse(_scene_has_object_name(box_name))
        self.assertNotEqual(
            get_context_action_kind(),
            'PREFAB',
            "Cancelled placement must not restore Repeat Prefab from the undo stack",
        )

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: _scene_has_object_name(box_name),
            "Redo did not restore the box builder object",
        )

        self.assertTrue(_scene_has_object_name(box_name))
        self.assertEqual(get_context_action_kind(), 'INVERT')
        self.assertTrue(os.path.isfile(filepath))

    def test_w_repeat_prefab_success_owns_undo_redo_and_preserves_prior_box_history(self):
        """Successful W placement owns one step above the initial prefab and box steps."""
        _seed_prefab_library("RedoRepeatPrefab", 'NONE')
        undo_ctx = get_undo_context()

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
        first_placed_name = placed_obj.name

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'PREFAB')
        self.assertEqual(
            prefab_asset_reference_parts(
                get_prefab_asset_reference(placed_obj),
            )[1],
            "RedoRepeatPrefab",
        )

        # The initial placement is setup for the W-history assertion.
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After initial prefab setup")

        names_before_repeat = _placed_prefab_names("RedoRepeatPrefab")
        self.assertEqual(names_before_repeat, {first_placed_name})

        with bpy.context.temp_override(**undo_ctx):
            repeat_result = bpy.ops.leveldesign.context_weld()
        self.assertEqual(repeat_result, {'FINISHED'})

        yield from wait_for_condition(
            lambda: any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "W did not start Repeat Prefab placement",
        )

        window = _get_window()
        mx, my = self._get_3d_viewport_center()
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=mx, y=my)
        yield
        window.event_simulate(type='LEFTMOUSE', value='PRESS', x=mx, y=my)
        yield
        window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=mx, y=my)

        yield from wait_for_condition(
            lambda: len(_placed_prefab_names("RedoRepeatPrefab")) == 2,
            "W-driven prefab placement did not create a second prefab",
        )
        yield from wait_for_condition(
            lambda: not any(
                operator.bl_idname == "LEVELDESIGN_OT_prefab_instantiate"
                for operator in _get_window().modal_operators
            ),
            "Repeat Prefab placement did not finish before history inspection",
        )
        repeated_names = _placed_prefab_names("RedoRepeatPrefab")
        repeated_name = next(iter(repeated_names - names_before_repeat))

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: not _scene_has_object_name(repeated_name),
            "Undo did not remove the W-driven prefab placement",
        )

        self.assertTrue(_scene_has_object_name(box_name))
        self.assertTrue(_scene_has_object_name(first_placed_name))
        self.assertEqual(get_context_action_kind(), 'PREFAB')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: not _scene_has_object_name(first_placed_name),
            "Undo did not remove the initial prefab placement",
        )
        self.assertTrue(_scene_has_object_name(box_name))
        self.assertEqual(get_context_action_kind(), 'INVERT')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: _scene_has_object_name(first_placed_name),
            "Redo did not restore the initial prefab placement",
        )
        self.assertEqual(get_context_action_kind(), 'PREFAB')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: _scene_has_object_name(repeated_name),
            "Redo did not restore the W-driven prefab placement",
        )

        self.assertEqual(get_context_action_kind(), 'PREFAB')
        self.assertEqual(
            prefab_asset_reference_parts(
                get_prefab_asset_reference(bpy.data.objects[repeated_name]),
            )[1],
            "RedoRepeatPrefab",
        )
