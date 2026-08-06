import os

import bmesh
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from .base_test import AnvilTestCase
from ..handlers.lifecycle import get_undo_in_progress
from .helpers import (
    edit_mesh_cache_is_current,
    get_context_action_kind,
    get_undo_context,
    modal_operator_running,
    wait_for_condition,
    _get_context_override,
)

from ..operators.box_builder.geometry import execute_box_builder, execute_box_builder_object_mode
from ..operators.pending_mesh_action import (
    store_from_shape_builder,
    store_from_shape_builder_object_mode,
)


class BoxBuilderWeldTest(AnvilTestCase):
    """Test that box builder sets the correct weld mode based on surrounding geometry."""

    def tearDown(self):
        reload_filepath = getattr(
            self, "_pending_action_reload_filepath", "",
        )
        try:
            super().tearDown()
        finally:
            if reload_filepath and os.path.isfile(reload_filepath):
                os.remove(reload_filepath)

    def _create_empty_mesh(self, name):
        """Create an empty mesh object in edit mode."""
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        ctx = _get_context_override()
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        return obj

    def _assert_vector_close(self, actual, expected, message):
        self.assertLess(
            (Vector(actual) - Vector(expected)).length,
            0.001,
            message,
        )

    def _edit_mesh_bounds(self, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        coords = [vert.co.copy() for vert in bm.verts]
        self.assertNotEqual(coords, [])
        mins = [
            round(min(coord[index] for coord in coords), 4)
            for index in range(3)
        ]
        maxs = [
            round(max(coord[index] for coord in coords), 4)
            for index in range(3)
        ]
        return (tuple(mins), tuple(maxs))

    def _window_point_for_world(self, view_ctx, world_point):
        region = view_ctx["region"]
        rv3d = view_ctx["region_data"]
        screen_point = location_3d_to_region_2d(region, rv3d, world_point)
        self.assertIsNotNone(screen_point)
        return (
            int(region.x + screen_point.x),
            int(region.y + screen_point.y),
        )

    def _simulate_left_click(self, window, x, y):
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        yield
        window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        yield
        window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        yield

    def _modal_build_box_in_ortho_view(self, first_vertex, second_vertex, depth, view_type):
        view_ctx = self._get_3d_view_context()
        window = view_ctx["window"]
        region = view_ctx["region"]
        rv3d = view_ctx["region_data"]
        center = (first_vertex + second_vertex) * 0.5

        view_rotation_before = rv3d.view_rotation.copy()
        with bpy.context.temp_override(**view_ctx):
            bpy.ops.view3d.view_axis(type=view_type, align_active=False)
        yield from wait_for_condition(
            lambda: (
                rv3d.view_perspective == 'ORTHO'
                and rv3d.view_rotation.rotation_difference(
                    view_rotation_before).angle > 0.001
            ),
            f"Viewport did not switch to {view_type} orthographic view",
        )

        view_matrix_before = rv3d.view_matrix.copy()
        rv3d.view_location = center
        rv3d.view_distance = 8.0
        rv3d.view_perspective = 'ORTHO'
        yield from wait_for_condition(
            lambda: (
                (rv3d.view_location - center).length < 0.001
                and abs(rv3d.view_distance - 8.0) < 0.001
                and rv3d.view_perspective == 'ORTHO'
                and any(
                    abs(rv3d.view_matrix[row][column]
                        - view_matrix_before[row][column]) > 1e-6
                    for row in range(4)
                    for column in range(4)
                )
            ),
            "Viewport did not settle on the requested box-building view",
        )

        first_x, first_y = self._window_point_for_world(view_ctx, first_vertex)
        second_x, second_y = self._window_point_for_world(view_ctx, second_vertex)
        depth_x = second_x + int((depth * region.width) / 10.0)
        depth_y = second_y

        with bpy.context.temp_override(**view_ctx):
            result = bpy.ops.leveldesign.box_builder('INVOKE_DEFAULT')
        self.assertEqual(result, {'RUNNING_MODAL'})
        yield from wait_for_condition(
            lambda: modal_operator_running('LEVELDESIGN_OT_box_builder'),
            "Box Builder did not enter modal state",
        )

        yield from self._simulate_left_click(window, first_x, first_y)
        yield from self._simulate_left_click(window, second_x, second_y)

        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=depth_x, y=depth_y)
        yield
        yield from self._simulate_left_click(window, depth_x, depth_y)
        yield from wait_for_condition(
            lambda: (
                not modal_operator_running('LEVELDESIGN_OT_box_builder')
                and edit_mesh_cache_is_current()
            ),
            "Box Builder did not finish updating its mesh",
        )

    def _create_mesh_with_plane(self, name, verts):
        """Create a mesh object with a single quad face and enter edit mode.

        Args:
            name: Object name
            verts: List of 4 Vector-like vertex positions (winding determines normal)
        """
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bm_verts = [bm.verts.new(v) for v in verts]
        bm.faces.new(bm_verts)
        bm.to_mesh(mesh)
        bm.free()

        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        ctx = _get_context_override()
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        return obj

    def _create_mesh_with_faces(self, name, face_verts):
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        for verts in face_verts:
            bm_verts = [bm.verts.new(v) for v in verts]
            bm.faces.new(bm_verts)
        bm.to_mesh(mesh)
        bm.free()

        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        ctx = _get_context_override()
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')

        return obj

    def _select_first_face(self, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = False
        face = bm.faces[0]
        face.select = True
        bm.faces.active = face
        bm.select_flush(True)
        bmesh.update_edit_mesh(obj.data)

    def _build_box_against_selected_plane(self, name, keep_anti_parallel_coplanar_faces):
        obj = self._create_mesh_with_plane(name, [
            (0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0),
        ])
        self._select_first_face(obj)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), keep_anti_parallel_coplanar_faces,
        )
        self.assertTrue(result[0], result[1])
        return obj, result

    def _assert_original_plane_still_points_positive_y(self, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        self.assertGreater(
            bm.faces[0].normal.dot(Vector((0, 1, 0))), 0.99,
            "Original anti-parallel plane should not be inverted",
        )

    def _assert_box_faces_point_inward(self, obj, expected_box_face_count):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        center = Vector((0.5, 0.5, 0.5))
        box_faces = [face for index, face in enumerate(bm.faces) if index != 0]
        self.assertEqual(len(box_faces), expected_box_face_count)
        for face in box_faces:
            to_center = center - face.calc_center_median()
            self.assertGreater(
                face.normal.dot(to_center), 0.0,
                f"Box face at {face.calc_center_median()} should point inward",
            )

    def _assert_no_removed_front_face_exists(self, obj):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        removed_faces = [
            face for face in bm.faces
            if abs(face.calc_center_median().y) < 0.001
            and face.normal.dot(Vector((0, -1, 0))) > 0.99
        ]
        self.assertEqual(removed_faces, [])

    def _assert_no_loose_edge_between_points(self, obj, first_point, second_point):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        expected = {
            tuple(round(value, 4) for value in first_point),
            tuple(round(value, 4) for value in second_point),
        }
        matching_loose_edges = []
        for edge in bm.edges:
            if edge.link_faces:
                continue
            edge_points = {
                tuple(round(value, 4) for value in vert.co)
                for vert in edge.verts
            }
            if edge_points == expected:
                matching_loose_edges.append(edge)
        self.assertEqual(matching_loose_edges, [])

    def _assert_box_builder_keep_anti_parallel_flow_weld_undo_and_redo(self):
        ctx = _get_context_override()
        undo_ctx = get_undo_context()

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before anti-parallel box")

        obj, result = self._build_box_against_selected_plane("anti_parallel_box", True)
        obj_name = obj.name
        face_verts = result[2] if len(result) > 2 else []
        self.assertEqual(len(face_verts), 6)

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 7)

        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After anti-parallel box")

        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Invert action",
        )
        self.assertEqual(get_context_action_kind(), 'NONE')
        self._assert_original_plane_still_points_positive_y(obj)
        self._assert_box_faces_point_inward(obj, 6)

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'INVERT',
            "Undo did not restore the anti-parallel box Invert action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the anti-parallel box Invert action",
        )

        obj = bpy.data.objects[obj_name]
        self._assert_original_plane_still_points_positive_y(obj)
        self._assert_box_faces_point_inward(obj, 6)

    # ------------------------------------------------------------------
    # Object mode tests (box builder creates a new standalone object)
    # ------------------------------------------------------------------

    def test_standalone_box_object_mode_invert(self):
        """Object-mode box is always standalone → weld should be INVERT.

        This is the production path: user is in object mode, builds a box,
        the operator creates a new object and exposes an INVERT context action.
        """
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        success, msg = execute_box_builder_object_mode(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            ppm, Vector((0, -1, 0)), "",
        )
        self.assertTrue(success, msg)

        # Production path: the box owns the pending Invert action.
        store_from_shape_builder_object_mode(bpy.context.active_object)
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Object-mode standalone box should be INVERT")

        # Verify weld execution works from object mode
        ctx = _get_context_override()
        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result,
                       "Weld INVERT should succeed from object mode")
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued object-mode Invert action",
        )
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Weld mode should be NONE after executing invert")

    def test_object_mode_invert_selecting_another_object_consumes_next_weld_and_reselecting_shape_does_not_restore_it(self):
        shape_mesh = bpy.data.meshes.new("transient_invert_shape_mesh")
        shape_obj = bpy.data.objects.new("transient_invert_shape", shape_mesh)
        bpy.context.collection.objects.link(shape_obj)
        other_mesh = bpy.data.meshes.new("transient_invert_other_mesh")
        other_obj = bpy.data.objects.new("transient_invert_other", other_mesh)
        bpy.context.collection.objects.link(other_obj)
        bpy.context.view_layer.objects.active = shape_obj
        shape_obj.select_set(True)
        store_from_shape_builder_object_mode(shape_obj)
        self.assertEqual(get_context_action_kind(), 'INVERT')

        shape_obj.select_set(False)
        other_obj.select_set(True)
        bpy.context.view_layer.objects.active = other_obj
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Selecting another object did not dismiss the pending Invert action",
        )

        other_obj.select_set(False)
        shape_obj.select_set(True)
        bpy.context.view_layer.objects.active = shape_obj
        yield
        self.assertEqual(
            get_context_action_kind(),
            'NONE',
            "Reselecting an old shape must not restore its consumed Invert weld",
        )
        self.assertNotIn("_aw_mode", shape_mesh)

    def test_object_mode_pending_action_replacement_save_and_reload_does_not_restore_first_object_action(self):
        """Replacing A with B prevents A's pending action returning after reload."""
        first_mesh = bpy.data.meshes.new("replaced_action_first_mesh")
        first_obj = bpy.data.objects.new("replaced_action_first", first_mesh)
        bpy.context.collection.objects.link(first_obj)
        bpy.context.view_layer.objects.active = first_obj
        first_obj.select_set(True)
        store_from_shape_builder_object_mode(first_obj)
        self.assertEqual(get_context_action_kind(), 'INVERT')

        second_mesh = bpy.data.meshes.new("replaced_action_second_mesh")
        second_obj = bpy.data.objects.new("replaced_action_second", second_mesh)
        bpy.context.collection.objects.link(second_obj)
        first_obj.select_set(False)
        second_obj.select_set(True)
        bpy.context.view_layer.objects.active = second_obj

        # Arm B before Blender has an event-loop tick in which it could dismiss
        # A merely because the active object changed.
        store_from_shape_builder_object_mode(second_obj)
        self.assertEqual(get_context_action_kind(), 'INVERT')
        self.assertNotIn(
            "_aw_mode",
            first_mesh,
            "Arming B must clear A's replaced durable pending action",
        )

        second_obj.select_set(False)
        first_obj.select_set(True)
        bpy.context.view_layer.objects.active = first_obj

        output_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "test_outputs")
        )
        os.makedirs(output_root, exist_ok=True)
        filepath = os.path.join(
            output_root,
            "object_mode_pending_action_replacement_reload.blend",
        )
        self._pending_action_reload_filepath = filepath
        bpy.ops.wm.save_as_mainfile(filepath=filepath, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=filepath)

        restored_first = bpy.data.objects.get("replaced_action_first")
        self.assertIsNotNone(restored_first)
        reload_context = get_undo_context()
        with bpy.context.temp_override(**reload_context):
            self.assertEqual(bpy.context.active_object, restored_first)
            self.assertNotIn("_aw_mode", restored_first.data)
            self.assertEqual(
                get_context_action_kind(),
                'NONE',
                "Reloading with A active must not restore its replaced action",
            )

    def test_object_mode_invert_weld_undo_and_redo_restores_pending_action(self):
        undo_ctx = get_undo_context()
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before object-mode box")

        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        success, message = execute_box_builder_object_mode(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            ppm, Vector((0, -1, 0)), "",
        )
        self.assertTrue(success, message)
        box_obj = bpy.context.active_object
        box_name = box_obj.name
        store_from_shape_builder_object_mode(box_obj)

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After object-mode box")

        with bpy.context.temp_override(**_get_context_override()):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued object-mode Invert action",
        )
        self.assertEqual(get_context_action_kind(), 'NONE')

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'INVERT',
            "Undo did not restore the object-mode Invert action",
        )
        self.assertIn(box_name, bpy.data.objects)

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the object-mode Invert action",
        )

    # ------------------------------------------------------------------
    # Edit mode tests (box builder adds geometry to existing mesh)
    # ------------------------------------------------------------------

    def test_box_builder_keep_overlap_faces_property_is_visible_for_action_panel(self):
        """Box builder Keep Overlap Faces property is visible for action panel."""
        properties = bpy.ops.leveldesign.box_builder.get_rna_type().properties
        prop = properties[
            "keep_anti_parallel_coplanar_faces"
        ]
        self.assertFalse(prop.is_hidden)
        self.assertEqual(prop.name, "Keep Overlap Faces")
        self.assertTrue(prop.default)
        suffix_prop = properties["name_suffix"]
        self.assertFalse(suffix_prop.is_hidden)
        self.assertEqual(suffix_prop.name, "Suffix")
        self.assertEqual(suffix_prop.default, "")

    def test_box_builder_modal_anti_parallel_action_props_remove_face_at_original_position(self):
        """Box builder modal anti-parallel action props remove face at original position."""
        obj = self._create_mesh_with_plane("modal_action_panel_plane", [
            (2, 0, 0), (2, 0, 1), (3, 0, 1), (3, 0, 0),
        ])
        obj_name = obj.name
        self._select_first_face(obj)
        bpy.context.tool_settings.use_snap = True
        with bpy.context.temp_override(**get_undo_context()):
            bpy.ops.ed.undo_push(message="Before modal anti-parallel action panel box")

        first_vertex = Vector((2, 0, 0))
        second_vertex = Vector((3, 0, 1))
        yield from self._modal_build_box_in_ortho_view(
            first_vertex, second_vertex, 1.0, 'FRONT')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 7)
        original_bounds = self._edit_mesh_bounds(obj)

        action_props = bpy.context.window_manager.operator_properties_last(
            "leveldesign.box_builder"
        )
        self.assertIsNotNone(action_props)
        self._assert_vector_close(
            action_props.action_local_z,
            Vector((0, 1, 0)),
            "Front-view modal box should capture +Y as the depth axis",
        )
        self.assertAlmostEqual(action_props.action_depth, 1.0, places=3)
        redo_kwargs = {
            "action_first_vertex": tuple(action_props.action_first_vertex),
            "action_second_vertex": tuple(action_props.action_second_vertex),
            "action_depth": action_props.action_depth,
            "action_local_x": tuple(action_props.action_local_x),
            "action_local_y": tuple(action_props.action_local_y),
            "action_local_z": tuple(action_props.action_local_z),
            "action_had_selection": action_props.action_had_selection,
            "action_was_edit_mode": action_props.action_was_edit_mode,
            "action_object_name": action_props.action_object_name,
            "name_suffix": action_props.name_suffix,
            "keep_anti_parallel_coplanar_faces": False,
        }

        with bpy.context.temp_override(**self._get_3d_view_context()):
            result = bpy.ops.ed.undo()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: (
                obj_name in bpy.data.objects
                and bpy.context.mode == 'EDIT_MESH'
                and len(bmesh.from_edit_mesh(
                    bpy.data.objects[obj_name].data).faces) == 1
            ),
            "Undo did not restore the original single-face mesh",
        )

        with bpy.context.temp_override(**self._get_3d_view_context()):
            bpy.ops.object.mode_set(mode='OBJECT')
            result = bpy.ops.leveldesign.box_builder('EXEC_DEFAULT', **redo_kwargs)
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: (
                obj_name in bpy.data.objects
                and bpy.context.mode == 'EDIT_MESH'
                and len(bmesh.from_edit_mesh(
                    bpy.data.objects[obj_name].data).faces) == 6
            ),
            "Action-property replay did not finish rebuilding the box",
        )

        self.assertIn(
            obj_name,
            bpy.data.objects,
            "Action props should keep rebuilding the original edit object",
        )
        obj = bpy.data.objects[obj_name]
        self.assertEqual(
            bpy.context.mode,
            'EDIT_MESH',
            "Action props should stay on the edit-mode box builder path",
        )
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 6)
        self._assert_no_removed_front_face_exists(obj)
        self.assertEqual(
            self._edit_mesh_bounds(obj),
            original_bounds,
            "Action props should not move the anti-parallel box",
        )

    def test_standalone_box_edit_mode_invert(self):
        """Edit-mode box in empty mesh → weld should be INVERT."""
        obj = self._create_empty_mesh("standalone_box")
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])

        # Production path: operator stores the shape action after
        # execute_box_builder in edit mode
        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Edit-mode standalone box should set weld to INVERT")

    def test_box_builder_on_anti_parallel_coplanar_face_keeps_face_and_sets_invert(self):
        """Box built on the side a plane's normal points to should be INVERT.

        Plane at Y=0 with normal +Y. Box extends from Y=0 to Y=1.
        The box face at Y=0 has outward normal -Y, anti-parallel to the
        plane's +Y normal. The default keeps that face and marks the box
        faces for invert.
        """
        # Plane at Y=0, normal +Y (winding: CCW when viewed from +Y)
        obj = self._create_mesh_with_plane("plane_normal_side", [
            (0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0),
        ])
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])

        face_verts = result[2] if len(result) > 2 else []
        self.assertEqual(len(face_verts), 6,
                         "Default box build should keep all 6 box faces")
        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Box on anti-parallel coplanar face should set weld to INVERT")

    def test_box_builder_remove_adjacent_anti_parallel_coplanar_faces_removes_shared_edge(self):
        """Remove anti-parallel coplanar faces removes the edge shared by adjacent removed faces."""
        obj = self._create_mesh_with_faces("adjacent_removed_faces", [
            [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)],
            [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)],
        ])
        self._select_first_face(obj)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), False,
        )
        self.assertTrue(result[0], result[1])
        face_verts = result[2] if len(result) > 2 else []
        self.assertEqual(len(face_verts), 4)

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 6)
        self._assert_no_removed_front_face_exists(obj)
        removed_left_faces = [
            face for face in bm.faces
            if abs(face.calc_center_median().x) < 0.001
            and face.normal.dot(Vector((-1, 0, 0))) > 0.99
        ]
        self.assertEqual(removed_left_faces, [])
        self._assert_no_loose_edge_between_points(
            obj,
            (0, 0, 0),
            (0, 0, 1),
        )

    def test_box_builder_keep_anti_parallel_coplanar_faces_invert_undo_and_redo_preserves_six_faces(self):
        """Keep anti-parallel coplanar faces: undo and redo preserve the six box faces."""
        yield from self._assert_box_builder_keep_anti_parallel_flow_weld_undo_and_redo()

    def test_box_on_plane_opposite_side_invert(self):
        """Box built on the opposite side of a plane should be INVERT.

        Plane at Y=0 with normal +Y. Box extends from Y=0 to Y=-1.
        The box face at Y=0 has outward normal +Y, parallel to the plane's
        +Y normal. Not anti-parallel, so no blocking overlap -> INVERT.
        """
        # Same plane at Y=0, normal +Y
        obj = self._create_mesh_with_plane("plane_opposite_side", [
            (0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0),
        ])
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        # Negative depth: box extends in -Y direction
        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), -1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])

        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Box on plane opposite side should set weld to INVERT")

    def test_box_plane_intersects_invert(self):
        """Plane that intersects the box (not coplanar with any face) should be INVERT.

        Plane at Y=0.5, normal +Y. Box spans Y=0 to Y=1.
        No box face lies at Y=0.5, so no coplanar match -> INVERT.
        """
        # Plane at Y=0.5, normal +Y
        obj = self._create_mesh_with_plane("plane_intersects", [
            (0, 0.5, 0), (0, 0.5, 1), (1, 0.5, 1), (1, 0.5, 0),
        ])
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])

        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Box with intersecting plane should set weld to INVERT")

    # ------------------------------------------------------------------
    # Undo tests
    # ------------------------------------------------------------------

    def test_edit_mode_invert_extruding_box_invalidates_pending_action(self):
        """A geometry edit after Box makes its pending Invert unavailable."""
        obj = self._create_empty_mesh("extrude_invalidates_invert")
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])
        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)
        self.assertEqual(get_context_action_kind(), 'INVERT')

        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.mesh.extrude_region_move(
                TRANSFORM_OT_translate={"value": Vector((0, 0.25, 0))}
            )

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Extruding the box did not invalidate its pending Invert action",
        )

    def test_edit_mode_invert_mode_change_and_unrelated_undo_does_not_restore_dismissed_action(self):
        """Leaving Edit Mode physically dismisses Invert across later history."""
        obj = self._create_empty_mesh("mode_change_dismisses_invert")
        obj_name = obj.name
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        ctx = _get_context_override()
        undo_ctx = get_undo_context()
        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])
        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)
        self.assertEqual(get_context_action_kind(), 'INVERT')

        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='OBJECT')

        def durable_payload_is_cleared():
            current_obj = bpy.data.objects[obj_name]
            attribute = current_obj.data.attributes.get("_aw_mode")
            return (
                attribute is None
                or len(attribute.data) == 0
                or attribute.data[0].value == 0
            )

        yield from wait_for_condition(
            durable_payload_is_cleared,
            "Leaving Edit Mode did not clear the durable Invert payload",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After pending Invert dismissal")
        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.select_all(action='DESELECT')
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After unrelated selection")
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: (
                not get_undo_in_progress()
                and get_context_action_kind() == 'NONE'
            ),
            "An unrelated undo resurrected the dismissed Invert action",
        )

    def test_edit_mode_invert_survives_undo(self):
        """Edit-mode: build box → deselect → undo should restore INVERT.

        Verifies the weld stack correctly restores INVERT after undo
        re-selects the box edges.
        """
        obj = self._create_empty_mesh("undo_edit")
        obj_name = obj.name
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        ctx = _get_context_override()
        undo_ctx = get_undo_context()

        # Baseline undo step
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before box")

        # Build box
        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])
        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)

        yield

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Step 1: should be INVERT after box build")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After box")

        # Deselect all → weld should clear to NONE
        with bpy.context.temp_override(**ctx):
            bpy.ops.mesh.select_all(action='DESELECT')

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Deselecting the box did not invalidate the Invert action",
        )

        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 2: should be NONE after deselect")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After deselect")

        # Undo deselect → should restore INVERT
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'INVERT',
            "Undoing the deselection did not restore the Invert action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Step 3: should be INVERT after undo")

    def test_edit_mode_invert_weld_undo_and_redo_restores_pending_action(self):
        """Edit-mode Invert owns one Blender undo/redo history step."""
        obj = self._create_empty_mesh("undo_weld")
        obj_name = obj.name
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        ctx = _get_context_override()
        undo_ctx = get_undo_context()

        # Baseline undo step
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="Before box")

        # Build box
        result = execute_box_builder(
            Vector((0, 0, 0)), Vector((1, 0, 1)), 1.0,
            Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0)),
            obj, ppm, Vector((0, -1, 0)), True,
        )
        self.assertTrue(result[0], result[1])
        face_verts = result[2] if len(result) > 2 else []
        store_from_shape_builder(obj, face_verts)

        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Step 1: should be INVERT after box build")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo_push(message="After box")

        # Execute weld invert
        with bpy.context.temp_override(**ctx):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result,
                       "Weld INVERT should succeed")
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued edit-mode Invert action",
        )
        self.assertEqual(get_context_action_kind(), 'NONE',
                         "Step 2: should be NONE after weld invert")

        # Undo weld → should restore INVERT
        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.undo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'INVERT',
            "Undo did not restore the edit-mode Invert action",
        )

        obj = bpy.data.objects[obj_name]
        bpy.context.view_layer.objects.active = obj
        props = bpy.context.scene.level_design_props
        self.assertEqual(get_context_action_kind(), 'INVERT',
                         "Step 3: should be INVERT after undoing weld")

        with bpy.context.temp_override(**undo_ctx):
            bpy.ops.ed.redo()

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the edit-mode Invert action",
        )
