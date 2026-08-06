import bmesh
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

from ..operators.clip.geometry import (
    CLIP_MODE_BISECT,
    CLIP_MODE_REMOVE_ABOVE,
    CLIP_MODE_REMOVE_BELOW,
    execute_clip,
)
from ..operators.modal_draw.preview import get_preview
from ..operators.pending_mesh_action import store_clip_fill_from_edge_selection
from .base_test import AnvilTestCase
from .helpers import (
    _get_context_override,
    edit_mesh_cache_is_current,
    get_context_action,
    get_context_action_kind,
    get_undo_context,
    modal_operator_running,
    wait_for_condition,
)


def _create_edit_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    context_override = _get_context_override()
    with bpy.context.temp_override(**context_override):
        bpy.ops.object.mode_set(mode='EDIT')

    bm = bmesh.from_edit_mesh(mesh)
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False
    bm.select_flush(False)
    bmesh.update_edit_mesh(mesh)
    return obj, context_override


def _execute_clip(
        obj, first_point, second_point, grid_normal, clip_mode,
        prefer_quads):
    return execute_clip(
        obj,
        bpy.context.tool_settings,
        Vector(first_point),
        Vector(second_point),
        Vector(grid_normal),
        clip_mode,
        prefer_quads,
        obj.matrix_world.copy(),
    )


def _create_cube(name):
    vertices = (
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    )
    faces = (
        (3, 2, 1, 0),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    return _create_edit_mesh(name, vertices, faces)


def _create_concave_prism(name):
    profile = (
        (0, 0), (2, 0), (2, 1),
        (1, 1), (1, 2), (0, 2),
    )
    vertices = tuple(
        (x, y, z)
        for z in (-1, 1)
        for x, y in profile
    )
    side_count = len(profile)
    faces = [tuple(reversed(range(side_count)))]
    faces.append(tuple(range(side_count, side_count * 2)))
    for index in range(side_count):
        next_index = (index + 1) % side_count
        faces.append((
            index,
            next_index,
            next_index + side_count,
            index + side_count,
        ))
    return _create_edit_mesh(name, vertices, tuple(faces))


def _create_two_cubes(name):
    vertices = []
    faces = []
    base_faces = (
        (3, 2, 1, 0),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    for y_offset in (0, 2):
        vertex_offset = len(vertices)
        vertices.extend((
            (0, y_offset, 0), (1, y_offset, 0),
            (1, y_offset + 1, 0), (0, y_offset + 1, 0),
            (0, y_offset, 1), (1, y_offset, 1),
            (1, y_offset + 1, 1), (0, y_offset + 1, 1),
        ))
        faces.extend(
            tuple(vertex_offset + index for index in face)
            for face in base_faces
        )
    return _create_edit_mesh(name, tuple(vertices), tuple(faces))


def _clip_cube_for_fill(name, prefer_quads):
    obj, context_override = _create_cube(name)
    success, message = _execute_clip(
        obj,
        (0.5, -1, 0),
        (0.5, 2, 0),
        (0, 0, 1),
        CLIP_MODE_REMOVE_ABOVE,
        prefer_quads,
    )
    if not success:
        raise AssertionError(message)
    store_clip_fill_from_edge_selection(obj, True, prefer_quads)
    return obj, context_override


class ClipGeometryTest(AnvilTestCase):

    def test_clip_bisects_visible_faces_and_selects_the_new_clip_edge(self):
        obj, _context_override = _create_edit_mesh(
            "clip_square",
            ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
            ((0, 1, 2, 3),),
        )

        success, message = _execute_clip(
            obj,
            (0, -2, 0),
            (0, 2, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            True,
        )
        self.assertTrue(success, message)

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.verts), 6)
        self.assertEqual(len(bm.faces), 2)
        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(len(selected_edges), 1)
        self.assertTrue(all(abs(vert.co.x) < 1e-5 for vert in selected_edges[0].verts))
        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, True, False],
        )
        self.assertFalse(any(face.select for face in bm.faces))

    def test_clip_uses_selected_faces_and_leaves_unselected_faces_unchanged(self):
        obj, _context_override = _create_edit_mesh(
            "clip_selected_face",
            (
                (-1, -3, 0), (1, -3, 0), (1, -1, 0), (-1, -1, 0),
                (-1, 1, 0), (1, 1, 0), (1, 3, 0), (-1, 3, 0),
            ),
            ((0, 1, 2, 3), (4, 5, 6, 7)),
        )
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces[0].select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

        success, message = _execute_clip(
            obj,
            (0, -4, 0),
            (0, 4, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            False,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        upper_faces = [
            face
            for face in bm.faces
            if face.calc_center_median().y > 0
        ]
        lower_faces = [
            face
            for face in bm.faces
            if face.calc_center_median().y < 0
        ]
        self.assertEqual(len(upper_faces), 1)
        self.assertEqual(len(lower_faces), 2)

    def test_clip_with_no_selection_excludes_hidden_faces(self):
        obj, _context_override = _create_edit_mesh(
            "clip_hidden_face",
            (
                (-1, -3, 0), (1, -3, 0), (1, -1, 0), (-1, -1, 0),
                (-1, 1, 0), (1, 1, 0), (1, 3, 0), (-1, 3, 0),
            ),
            ((0, 1, 2, 3), (4, 5, 6, 7)),
        )
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces[1].hide = True
        bmesh.update_edit_mesh(obj.data)

        success, message = _execute_clip(
            obj,
            (0, -4, 0),
            (0, 4, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            False,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        hidden_faces = [face for face in bm.faces if face.hide]
        visible_faces = [face for face in bm.faces if not face.hide]
        self.assertEqual(len(hidden_faces), 1)
        self.assertEqual(len(visible_faces), 2)

    def test_clip_remove_above_and_below_keep_opposite_plane_sides(self):
        for mode, expected_sign in (
                (CLIP_MODE_REMOVE_ABOVE, 1),
                (CLIP_MODE_REMOVE_BELOW, -1)):
            obj, context_override = _create_edit_mesh(
                f"clip_side_{mode.lower()}",
                ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
                ((0, 1, 2, 3),),
            )
            success, message = _execute_clip(
                obj,
                (0, -2, 0),
                (0, 2, 0),
                (0, 0, 1),
                mode,
                False,
            )
            self.assertTrue(success, message)
            bm = bmesh.from_edit_mesh(obj.data)
            self.assertEqual(len(bm.faces), 1)
            for vert in bm.verts:
                self.assertGreaterEqual(expected_sign * vert.co.x, -1e-5)

            with bpy.context.temp_override(**context_override):
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_clip_through_existing_vertices_reuses_them(self):
        obj, _context_override = _create_edit_mesh(
            "clip_reuse_vertices",
            ((0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)),
            ((0, 1, 2, 3),),
        )
        success, message = _execute_clip(
            obj,
            (0, -2, 0),
            (0, 2, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            False,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.verts), 4)
        self.assertEqual(len(bm.faces), 2)
        self.assertEqual(len([edge for edge in bm.edges if edge.select]), 1)

    def test_clip_on_existing_edge_reuses_and_selects_that_edge(self):
        obj, _context_override = _create_edit_mesh(
            "clip_reuse_edge",
            (
                (-1, -1, 0), (0, -1, 0), (1, -1, 0),
                (-1, 1, 0), (0, 1, 0), (1, 1, 0),
            ),
            ((0, 1, 4, 3), (1, 2, 5, 4)),
        )
        success, message = _execute_clip(
            obj,
            (0, -2, 0),
            (0, 2, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            False,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.verts), 6)
        self.assertEqual(len(bm.edges), 7)
        self.assertEqual(len(bm.faces), 2)
        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(len(selected_edges), 1)
        self.assertEqual(len(selected_edges[0].link_faces), 2)

    def test_clip_concave_result_prefers_quads_and_triangles(self):
        obj, _context_override = _create_edit_mesh(
            "clip_concave_quads",
            ((0, 0, 0), (2, 0, 0), (2, 1, 0),
             (1, 1, 0), (1, 2, 0), (0, 2, 0)),
            ((0, 1, 2, 3, 4, 5),),
        )
        success, message = _execute_clip(
            obj,
            (1.5, -1, 0),
            (1.5, 3, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            True,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertGreater(len(bm.faces), 2)
        self.assertTrue(all(len(face.verts) <= 4 for face in bm.faces))

    def test_clip_concave_result_can_leave_ngons(self):
        obj, _context_override = _create_edit_mesh(
            "clip_concave_ngon",
            ((0, 0, 0), (2, 0, 0), (2, 1, 0),
             (1, 1, 0), (1, 2, 0), (0, 2, 0)),
            ((0, 1, 2, 3, 4, 5),),
        )
        success, message = _execute_clip(
            obj,
            (1.5, -1, 0),
            (1.5, 3, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            False,
        )
        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertTrue(any(len(face.verts) > 4 for face in bm.faces))

    def test_clip_captured_modal_values_replay_through_execute(self):
        obj, context_override = _create_edit_mesh(
            "clip_action_replay",
            ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
            ((0, 1, 2, 3),),
        )
        matrix_values = tuple(
            value
            for row in obj.matrix_world
            for value in row
        )
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.clip(
                clip_mode=CLIP_MODE_BISECT,
                prefer_quads=True,
                action_first_point=(0, -2, 0),
                action_second_point=(0, 2, 0),
                action_grid_normal=(0, 0, 1),
                action_matrix_world=matrix_values,
            )
        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 2)
        self.assertEqual(len([edge for edge in bm.edges if edge.select]), 1)


    def test_clip_action_panel_exposes_mode_and_prefer_quads(self):
        properties = bpy.ops.leveldesign.clip.get_rna_type().properties
        expected = {
            "clip_mode": ("Clip Mode", CLIP_MODE_BISECT),
            "prefer_quads": ("Prefer Quads", True),
        }
        for identifier, (name, default) in expected.items():
            prop = properties[identifier]
            self.assertFalse(prop.is_hidden)
            self.assertEqual(prop.name, name)
            self.assertEqual(prop.default, default)


class ClipFillLoopsTest(AnvilTestCase):

    def test_removal_clip_with_closed_loop_offers_fill_clip_loops(self):
        obj, _context_override = _clip_cube_for_fill(
            "clip_fill_available", True,
        )
        self.assertEqual(get_context_action_kind(), 'FILL_LOOPS')
        action = get_context_action().payload["pending"]
        self.assertTrue(action.prefer_quads)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len([edge for edge in bm.edges if edge.select]), 4)

    def test_bisect_only_clip_does_not_offer_fill_clip_loops(self):
        obj, _context_override = _create_cube("clip_fill_bisect_only")
        success, message = _execute_clip(
            obj,
            (0.5, -1, 0),
            (0.5, 2, 0),
            (0, 0, 1),
            CLIP_MODE_BISECT,
            True,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, False, True)
        self.assertEqual(get_context_action_kind(), 'NONE')

    def test_removal_clip_with_open_cut_does_not_offer_fill_clip_loops(self):
        obj, _context_override = _create_edit_mesh(
            "clip_fill_open",
            ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
            ((0, 1, 2, 3),),
        )
        success, message = _execute_clip(
            obj,
            (0, -2, 0),
            (0, 2, 0),
            (0, 0, 1),
            CLIP_MODE_REMOVE_ABOVE,
            True,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, True, True)
        self.assertEqual(get_context_action_kind(), 'NONE')

    def test_fill_clip_loops_leaves_concave_ngon_when_prefer_quads_is_off(self):
        obj, context_override = _create_concave_prism(
            "clip_fill_concave_ngon",
        )
        success, message = _execute_clip(
            obj,
            (-1, 0, 0),
            (3, 0, 0),
            (0, 1, 0),
            CLIP_MODE_REMOVE_ABOVE,
            False,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, True, False)
        self.assertEqual(get_context_action_kind(), 'FILL_LOOPS')
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.weld_fill_loops()
        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        caps = [
            face
            for face in bm.faces
            if all(abs(vert.co.z) < 1e-5 for vert in face.verts)
        ]
        self.assertEqual(len(caps), 1)
        self.assertEqual(len(caps[0].verts), 6)

    def test_fill_clip_loops_quadriangulates_concave_cap_when_prefer_quads_is_on(self):
        obj, context_override = _create_concave_prism(
            "clip_fill_concave_quads",
        )
        success, message = _execute_clip(
            obj,
            (-1, 0, 0),
            (3, 0, 0),
            (0, 1, 0),
            CLIP_MODE_REMOVE_ABOVE,
            True,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, True, True)
        self.assertEqual(get_context_action_kind(), 'FILL_LOOPS')
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.weld_fill_loops()
        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        caps = [
            face
            for face in bm.faces
            if all(abs(vert.co.z) < 1e-5 for vert in face.verts)
        ]
        self.assertGreater(len(caps), 1)
        self.assertTrue(all(len(face.verts) <= 4 for face in caps))

    def test_fill_clip_loops_fills_each_disconnected_selected_loop(self):
        obj, context_override = _create_two_cubes(
            "clip_fill_multiple_loops",
        )
        success, message = _execute_clip(
            obj,
            (0.5, -1, 0),
            (0.5, 4, 0),
            (0, 0, 1),
            CLIP_MODE_REMOVE_ABOVE,
            True,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, True, True)
        self.assertEqual(get_context_action_kind(), 'FILL_LOOPS')
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.weld_fill_loops()
        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        caps = [
            face
            for face in bm.faces
            if all(abs(vert.co.x - 0.5) < 1e-5 for vert in face.verts)
        ]
        self.assertEqual(len(caps), 2)
        self.assertTrue(
            all(face.normal.x < -0.99 for face in caps),
            "Filled caps should face outward toward the removed side",
        )


class ClipFillLoopsUndoTest(AnvilTestCase):

    def test_fill_clip_loops_undo_and_redo_restores_geometry_and_pending_action(self):
        obj, context_override = _create_cube("clip_fill_undo")
        object_name = obj.name
        undo_context = get_undo_context()

        with bpy.context.temp_override(**undo_context):
            bpy.ops.ed.undo_push(message="Before Clip")

        success, message = _execute_clip(
            obj,
            (0.5, -1, 0),
            (0.5, 2, 0),
            (0, 0, 1),
            CLIP_MODE_REMOVE_ABOVE,
            True,
        )
        self.assertTrue(success, message)
        store_clip_fill_from_edge_selection(obj, True, True)
        self.assertEqual(get_context_action_kind(), 'FILL_LOOPS')

        with bpy.context.temp_override(**undo_context):
            bpy.ops.ed.undo_push(message="After Clip")

        faces_before_fill = len(bmesh.from_edit_mesh(obj.data).faces)
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)

        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued Fill Clip Loops action",
        )
        faces_after_fill = len(bmesh.from_edit_mesh(obj.data).faces)
        self.assertGreater(faces_after_fill, faces_before_fill)

        with bpy.context.temp_override(**undo_context):
            bpy.ops.ed.undo()
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'FILL_LOOPS',
            "Undo did not restore the pending Fill Clip Loops action",
        )
        obj = bpy.data.objects[object_name]
        bpy.context.view_layer.objects.active = obj
        self.assertEqual(len(bmesh.from_edit_mesh(obj.data).faces), faces_before_fill)

        with bpy.context.temp_override(**undo_context):
            bpy.ops.ed.redo()
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "Redo did not consume the pending Fill Clip Loops action",
        )
        obj = bpy.data.objects[object_name]
        self.assertEqual(len(bmesh.from_edit_mesh(obj.data).faces), faces_after_fill)


class ClipModalTest(AnvilTestCase):

    def test_clip_modal_preview_wall_clears_after_second_click_and_next_mode_removes_indicated_side(self):
        obj, _context_override = _create_edit_mesh(
            "clip_modal_two_click",
            ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)),
            ((0, 1, 2, 3),),
        )
        view_context = self._get_3d_view_context()
        window = view_context["window"]
        region = view_context["region"]
        region_data = view_context["region_data"]
        old_rotation = region_data.view_rotation.copy()

        with bpy.context.temp_override(**view_context):
            bpy.ops.view3d.view_axis(type='TOP', align_active=False)
        yield from wait_for_condition(
            lambda: (
                region_data.view_perspective == 'ORTHO'
                and region_data.view_rotation.rotation_difference(
                    old_rotation).angle > 0.001
            ),
            "Viewport did not switch to top orthographic view",
        )
        region_data.view_location = Vector((0, 0, 0))
        region_data.view_distance = 6.0
        region_data.view_perspective = 'ORTHO'
        yield

        window_points = []
        for world_point in (Vector((0, -1, 0)), Vector((0, 1, 0))):
            region_point = location_3d_to_region_2d(
                region,
                region_data,
                world_point,
            )
            self.assertIsNotNone(region_point)
            window_points.append((
                int(region.x + region_point.x),
                int(region.y + region_point.y),
            ))

        with bpy.context.temp_override(**view_context):
            result = bpy.ops.leveldesign.clip('INVOKE_DEFAULT')
        self.assertEqual(result, {'RUNNING_MODAL'})
        yield from wait_for_condition(
            lambda: modal_operator_running('LEVELDESIGN_OT_clip'),
            "Clip did not enter modal state",
        )
        self.assertTrue(get_preview()._clip_line_extension_enabled)

        first_x, first_y = window_points[0]
        window.event_simulate(
            type='MOUSEMOVE', value='NOTHING', x=first_x, y=first_y,
        )
        yield
        window.event_simulate(
            type='LEFTMOUSE', value='PRESS', x=first_x, y=first_y,
        )
        yield
        window.event_simulate(
            type='LEFTMOUSE', value='RELEASE', x=first_x, y=first_y,
        )
        yield

        yield from self._simulate_key_tap('E')

        second_x, second_y = window_points[1]
        window.event_simulate(
            type='MOUSEMOVE', value='NOTHING', x=second_x, y=second_y,
        )
        yield
        preview = get_preview()
        self.assertEqual(len(preview._clip_plane_vertices), 4)
        self.assertTrue(all(
            abs(vertex.x) <= 1e-5
            for vertex in preview._clip_plane_vertices
        ))
        window.event_simulate(
            type='LEFTMOUSE', value='PRESS', x=second_x, y=second_y,
        )
        yield
        window.event_simulate(
            type='LEFTMOUSE', value='RELEASE', x=second_x, y=second_y,
        )
        yield

        yield from wait_for_condition(
            lambda: (
                not modal_operator_running('LEVELDESIGN_OT_clip')
                and edit_mesh_cache_is_current()
            ),
            "Clip did not execute on the second click",
        )
        self.assertFalse(get_preview()._clip_line_extension_enabled)
        self.assertEqual(get_preview()._clip_plane_vertices, [])
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 1)
        self.assertTrue(all(vert.co.x >= -1e-5 for vert in bm.verts))
        action_properties = bpy.context.window_manager.operator_properties_last(
            "leveldesign.clip"
        )
        self.assertEqual(
            action_properties.clip_mode,
            CLIP_MODE_REMOVE_ABOVE,
        )
