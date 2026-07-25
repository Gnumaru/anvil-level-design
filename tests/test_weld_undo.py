import bmesh
import bpy
from mathutils import Vector
from unittest.mock import patch

from ..operators.box_builder.geometry import execute_box_builder
from ..operators import context_action, pending_mesh_action, weld_actions
from ..operators.weld import set_weld_from_edge_selection, set_weld_from_box_builder
from .base_test import AnvilTestCase
from .helpers import (
    create_textured_cube,
    create_vertical_plane,
    get_context_action_kind,
    get_undo_context,
    wait_for_condition,
    _get_context_override,
)


def _create_pending_corridor(name):
    obj = create_textured_cube(name, 1.0, 1.0)
    ctx = _get_context_override()
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.select_mode = {'FACE'}
    for face in bm.faces:
        face.select = face.normal.z > 0.9
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    with bpy.context.temp_override(**ctx):
        bpy.ops.mesh.delete(type='FACE')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.select_mode = {'EDGE'}
    for edge in bm.edges:
        edge.select = all(vert.co.z > 0.9 for vert in edge.verts)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    set_weld_from_edge_selection(
        obj, 0.5, (0, 0, -1), -0.5,
        Vector((0, 0, 0)), Vector((1, 0, 1)),
        Vector((1, 0, 0)), Vector((0, 0, 1)),
        0,
    )
    return obj, ctx


def _create_pending_bridge(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        (
            (0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
            (0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1),
        ),
        (
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
        ),
        (),
    )
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    ctx = _get_context_override()
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
    set_weld_from_edge_selection(
        obj, 0.0, (0, 1, 0), 1.0,
        Vector((0, 0, 0)), Vector((1, 0, 1)),
        Vector((1, 0, 0)), Vector((0, 0, 1)),
        0,
    )
    return obj, ctx


class CorridorWeldUndoTest(AnvilTestCase):
    """Test corridor weld undo: weld → undo → verify mode → re-weld → verify geometry."""

    def test_corridor_weld_undo_and_redo_restores_geometry_and_pending_action(self):
        """Corridor: weld → undo → verify CORRIDOR → re-weld → verify geometry.

        Uses operator-based geometry (delete face) so the undo system properly
        tracks subsequent BMesh layer changes.
        """
        obj = create_textured_cube("corridor_undo", 1.0, 1.0)
        obj_name = obj.name
        ctx = _get_context_override()
        uctx = get_undo_context()

        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo_push(message="Baseline")

        # Delete the top face via operator to create a boundary edge loop
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {'FACE'}
        for f in bm.faces:
            f.select = f.normal.z > 0.9
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.delete(type='FACE')

        # Select the top boundary edges
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'EDGE'}
        for e in bm.edges:
            e.select = all(v.co.z > 0.9 for v in e.verts)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        # Set weld state (stored in BMesh layers)
        set_weld_from_edge_selection(obj, 0.5, (0, 0, -1), -0.5,
                                     Vector((0, 0, 0)), Vector((1, 0, 1)),
                                     Vector((1, 0, 0)), Vector((0, 0, 1)),
                                     0)

        self.assertEqual(get_context_action_kind(), 'CORRIDOR',
                         "Should be CORRIDOR after setup")

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo_push(message="After weld setup")

        # Execute corridor weld
        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Corridor action",
        )

        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Should be NONE after corridor weld")
        faces_after_weld = len(bmesh.from_edit_mesh(obj.data).faces)

        # The dispatched concrete weld operator must create this history step.
        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'CORRIDOR',
            "Undo did not restore the pending Corridor action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'CORRIDOR',
                         "Should be CORRIDOR after undoing corridor")

        faces_before_weld = len(bmesh.from_edit_mesh(obj.data).faces)
        self.assertLess(faces_before_weld, faces_after_weld)

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the pending Corridor action",
        )

        obj = bpy.data.objects[obj_name]
        self.assertEqual(len(bmesh.from_edit_mesh(obj.data).faces), faces_after_weld)

    def test_corridor_edge_only_selection_change_invalidates_pending_action(self):
        """Changing selected edges invalidates Corridor even with no selected faces."""
        obj = create_textured_cube("corridor_edge_selection", 1.0, 1.0)
        ctx = _get_context_override()
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = face.normal.z > 0.9
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.delete(type='FACE')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'EDGE'}
        for edge in bm.edges:
            edge.select = all(vert.co.z > 0.9 for vert in edge.verts)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        set_weld_from_edge_selection(
            obj, 0.5, (0, 0, -1), -0.5,
            Vector((0, 0, 0)), Vector((1, 0, 1)),
            Vector((1, 0, 0)), Vector((0, 0, 1)),
            0,
        )
        self.assertEqual(get_context_action_kind(), 'CORRIDOR')
        self.assertFalse(any(face.select for face in bm.faces))

        selected_edge = next(edge for edge in bm.edges if edge.select)
        replacement_edge = next(edge for edge in bm.edges if not edge.select)
        selected_edge.select = False
        replacement_edge.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "An edge-only selection change did not invalidate Corridor",
        )

    def test_wrong_internal_mesh_action_operator_preserves_pending_corridor(self):
        """An inaccessible or incorrect child operator cannot consume W state."""
        _obj, ctx = _create_pending_corridor("wrong_operator_corridor")
        self.assertEqual(get_context_action_kind(), 'CORRIDOR')
        self.assertIn(
            'INTERNAL', weld_actions.LEVELDESIGN_OT_weld_bridge.bl_options,
        )
        try:
            with bpy.context.temp_override(**ctx):
                result = bpy.ops.leveldesign.weld_bridge()
        except RuntimeError:
            result = {'CANCELLED'}
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(
            get_context_action_kind(), 'CORRIDOR',
            "The wrong concrete operator consumed the pending Corridor",
        )

    def test_context_action_panel_summary_does_not_rebuild_mesh_geometry_fingerprint(self):
        """Panel display and W polling read only the already-validated guard."""
        _obj, _ctx = _create_pending_corridor("cheap_corridor_summary")
        with patch.object(
                pending_mesh_action,
                "_bmesh_geometry_signature",
                side_effect=AssertionError("Panel rebuilt the mesh fingerprint")):
            self.assertEqual(get_context_action_kind(), 'CORRIDOR')
            self.assertTrue(
                context_action.LEVELDESIGN_OT_context_action.poll(bpy.context),
            )


class BridgeWeldUndoTest(AnvilTestCase):
    """Test bridge weld undo: weld → undo → verify mode → re-weld → verify geometry."""

    def test_bridge_weld_undo_and_redo_restores_geometry_and_pending_action(self):
        """Bridge: weld → undo → verify BRIDGE → re-weld → verify 6 faces."""
        # Create two planes and join them via operators
        plane_a = create_vertical_plane("bridge_a")
        plane_b = create_vertical_plane("bridge_b")

        import math
        ctx = _get_context_override()
        uctx = get_undo_context()

        # Rotate plane_a 180° so normals face away
        plane_a.select_set(True)
        plane_b.select_set(False)
        bpy.context.view_layer.objects.active = plane_a
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
        plane_a.rotation_euler.z = math.pi
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.transform_apply(rotation=True)

        # Move plane_b 1 unit away
        plane_b.location.y = 1.0
        bpy.context.view_layer.objects.active = plane_b
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.transform_apply(location=True)

        # Join into one object
        plane_a.select_set(True)
        plane_b.select_set(True)
        bpy.context.view_layer.objects.active = plane_a
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.join()

        obj = plane_a
        obj_name = obj.name

        # Enter edit mode
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        # Select all edges via operator
        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.select_all(action='SELECT')

        yield

        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'EDGE'}
        for e in bm.edges:
            e.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        set_weld_from_edge_selection(obj, 1.0, (0, 1, 0), 1.0,
                                     Vector((0, 0, 0)), Vector((1, 0, 1)),
                                     Vector((1, 0, 0)), Vector((0, 0, 1)),
                                     0)

        self.assertEqual(get_context_action_kind(), 'BRIDGE',
                         "Should be BRIDGE with 2 edge groups")

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo_push(message="After bridge setup")

        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Bridge action",
        )

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 6,
                         f"Should have 6 faces after bridge, got {len(bm.faces)}")
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Should be NONE after bridge weld")

        # The dispatched concrete weld operator must create this history step.
        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'BRIDGE',
            "Undo did not restore the pending Bridge action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'BRIDGE',
                         "Should be BRIDGE after undoing bridge")

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 2,
                         f"Should have 2 faces after undo, got {len(bm.faces)}")

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the pending Bridge action",
        )

        obj = bpy.data.objects[obj_name]
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 6,
                         f"Should have 6 faces after redo, got {len(bm.faces)}")

    def test_failed_bridge_operation_preserves_pending_bridge_action(self):
        """A geometry failure that makes no changes leaves W available."""
        _obj, ctx = _create_pending_bridge("failed_bridge_pending")
        self.assertEqual(get_context_action_kind(), 'BRIDGE')
        with patch.object(
                weld_actions,
                "_bridge_edge_loops",
                side_effect=RuntimeError("Deliberate bridge failure")):
            try:
                with bpy.context.temp_override(**ctx):
                    result = bpy.ops.leveldesign.weld_bridge()
            except RuntimeError as exc:
                self.assertIn("Deliberate bridge failure", str(exc))
                result = {'CANCELLED'}
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(
            get_context_action_kind(), 'BRIDGE',
            "A failed Bridge consumed its still-valid pending action",
        )


class InvertWeldUndoTest(AnvilTestCase):
    """Test invert weld undo: weld → undo → verify mode → re-weld → verify normals."""

    def test_invert_weld_undo_and_redo_restores_normals_and_pending_action(self):
        """Invert: weld → undo → verify INVERT → re-weld → verify normals flipped."""
        mesh = bpy.data.meshes.new("invert_undo")
        obj = bpy.data.objects.new("invert_undo", mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        obj_name = obj.name

        ctx = _get_context_override()
        uctx = get_undo_context()

        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo_push(message="Before box")

        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])

        face_verts = result[2] if len(result) > 2 else []
        set_weld_from_box_builder(obj, face_verts)

        yield

        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Should be INVERT after box build")

        # Record normals before invert
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        normals_before = {f.index: tuple(round(v, 4) for v in f.normal) for f in bm.faces}

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo_push(message="After weld setup")

        # Execute invert weld
        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Invert action",
        )
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Should be NONE after invert")

        # Verify normals are flipped
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            before = normals_before.get(f.index)
            if before is not None:
                after = tuple(round(v, 4) for v in f.normal)
                for i in range(3):
                    self.assertAlmostEqual(after[i], -before[i], places=2,
                                           msg=f"Face {f.index} normal not flipped")

        # The dispatched concrete weld operator must create this history step.
        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'INVERT',
            "Undo did not restore the pending Invert action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Should be INVERT after undoing invert")

        # Verify normals are back to original
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            before = normals_before.get(f.index)
            if before is not None:
                restored = tuple(round(v, 4) for v in f.normal)
                for i in range(3):
                    self.assertAlmostEqual(restored[i], before[i], places=2,
                                           msg=f"Face {f.index} normal not restored after undo")

        with bpy.context.temp_override(**uctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the pending Invert action",
        )

        obj = bpy.data.objects[obj_name]
        # Verify normals are flipped again by Blender redo.
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            before = normals_before.get(f.index)
            if before is not None:
                after = tuple(round(v, 4) for v in f.normal)
                for i in range(3):
                    self.assertAlmostEqual(after[i], -before[i], places=2,
                                           msg=f"Face {f.index} normal not flipped on redo")
