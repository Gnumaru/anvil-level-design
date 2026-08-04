import bmesh
import bpy
from mathutils import Vector

from .base_test import AnvilTestCase
from .helpers import (
    create_textured_cube,
    get_context_action,
    get_context_action_kind,
    get_undo_context,
    wait_for_condition,
    _get_context_override,
)


class WeldUndoStackTest(AnvilTestCase):
    """Test that the weld state follows the full undo chain correctly.

    Simulates the real workflow:
      cube cut → corridor → something else → undo × 3
    and verifies the resolved context action at every step.
    """

    def test_weld_undo_chain(self):
        """Walk through: cut → corridor → other op → undo × 3.

        Expected context action at each step:
        1. After simulated cube cut (set_weld): CORRIDOR
        2. After corridor (W):                  NONE
        3. After 'something else':              NONE
        4. Ctrl+Z (undo something else):        NONE  (still post-corridor)
        5. Ctrl+Z (undo corridor):              CORRIDOR
        6. Ctrl+Z (undo to before cut):         NONE
        """
        obj = create_textured_cube(
            "weld_stack", 1.0, 1.0, use_box_project=False
        )
        obj_name = obj.name

        ctx = _get_context_override()
        undo_ctx = get_undo_context()

        # Enter edit mode
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        # --- Baseline undo step (before weld setup) ---
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before weld setup")

        # Delete the top face to create a boundary edge loop
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {'FACE'}
        for f in bm.faces:
            f.select = False
        for f in bm.faces:
            if f.normal.z > 0.9:
                f.select = True
                break
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.delete(type='FACE')

        yield

        # Select the top boundary edges
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'EDGE'}
        for e in bm.edges:
            e.select = all(v.co.z > 0.9 for v in e.verts)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        # Simulate cube cut setting the weld state
        from ..operators.weld import set_weld_from_edge_selection
        set_weld_from_edge_selection(obj, 0.5, (0, 0, -1), -0.5,
                                         Vector((0, 0, 0)), Vector((1, 0, 1)),
                                         Vector((1, 0, 0)), Vector((0, 0, 1)),
                                         0)

        yield

        # --- Step 1: After simulated cube cut → CORRIDOR ---
        self.assertEqual(get_context_action_kind(), 'CORRIDOR',
                         "Step 1: weld should be CORRIDOR after cube cut")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After cube cut")

        # --- Step 2: Execute corridor → NONE ---
        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Corridor action",
        )

        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 2: weld should be NONE after corridor")

        # --- Step 3: Do something else (select all) → still NONE ---
        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.select_all(action='SELECT')

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Undoing the later operation changed the completed Corridor state",
        )

        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 3: weld should be NONE after other op")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After something else")

        # --- Step 4: Ctrl+Z (undo 'something else') → NONE ---
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Undoing the later operation changed the completed Corridor state",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 4: weld should be NONE (still post-corridor)")

        # --- Step 5: Ctrl+Z (undo corridor) → CORRIDOR ---
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'CORRIDOR',
            "Undo did not reach Context Weld's own history step",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'CORRIDOR',
                         "Step 5: weld should be CORRIDOR after undoing corridor")
        self.assertAlmostEqual(get_context_action().payload["pending"].depth, 0.5, places=3,
                               msg="Step 5: weld depth should be 0.5")

        # --- Step 6: Ctrl+Z (undo to before cut) → NONE ---
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Undo did not return to the state before the Corridor setup",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 6: weld should be NONE before cube cut")
