import bmesh
import bpy
from mathutils import Vector

from ..core.uv_projection import derive_transform_from_uvs
from ..operators.cylinder_cut.geometry import execute_cylinder_cut
from ..operators.pending_mesh_action import store_from_edge_selection
from .base_test import AnvilTestCase
from .helpers import (
    _get_context_override,
    _apply_material_box_project,
    add_uv_layer_box_project,
    create_textured_cube,
    get_context_action_kind,
    wait_for_condition,
)


class CylinderCutTest(AnvilTestCase):
    """Test cylinder cut geometry, UV preservation, and weld integration."""

    def _execute_textured_cube_cut(self, object_name, side_count, radius_mode):
        obj = create_textured_cube(object_name, 1.0, 1.0, use_box_project=True)
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        add_uv_layer_box_project(obj, "UVMap.001", 0.5)

        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        center = Vector((0.5, -0.5, 0.5))
        local_x = Vector((1.0, 0.0, 0.0))
        local_y = Vector((0.0, 0.0, 1.0))
        local_z = Vector((0.0, 1.0, 0.0))
        depth = 2.0
        radius_x = 0.25
        radius_y = 0.25

        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                center,
                radius_x,
                radius_y,
                depth,
                local_x,
                local_y,
                local_z,
                side_count,
                radius_mode,
            )

        self.assertTrue(success, message)
        return (
            obj,
            context_override,
            center,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
        )

    def _assert_through_hole_preserves_uvs_and_selects_boundary_edges(
            self, radius_mode):
        """Cut an octagonal hole through a textured cube and verify cube-cut invariants."""
        obj, context_override, _center, _radius_x, _radius_y, _depth, _local_x, _local_y, _local_z = (
            self._execute_textured_cube_cut(
                f"cylinder_cut_cube_{radius_mode.lower()}", 8, radius_mode,
            )
        )

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv[0]
        uv_layer_second = bm.loops.layers.uv[1]

        self.assertEqual(
            len(bm.faces),
            20,
            "Cylinder cut should leave 4 uncut faces and 8 frame faces on each opening",
        )

        pixels_per_meter = bpy.context.scene.level_design_props.pixels_per_meter
        clean_rotations = {
            step * 22.5 for step in range(-16, 17)
        }
        for face in bm.faces:
            transform = derive_transform_from_uvs(
                face, uv_layer, pixels_per_meter, obj.data
            )
            second_transform = derive_transform_from_uvs(
                face, uv_layer_second, pixels_per_meter, obj.data
            )
            self.assertIsNotNone(transform, "Every remaining face should retain first-layer UVs")
            self.assertIsNotNone(
                second_transform,
                "Every remaining face should retain second-layer UVs",
            )
            self.assertAlmostEqual(transform['scale_u'], 1.0, places=2)
            self.assertAlmostEqual(transform['scale_v'], 1.0, places=2)
            self.assertAlmostEqual(second_transform['scale_u'], 0.5, places=2)
            self.assertAlmostEqual(second_transform['scale_v'], 0.5, places=2)
            closest_rotation = min(
                clean_rotations,
                key=lambda rotation: abs(rotation - transform['rotation']),
            )
            second_closest_rotation = min(
                clean_rotations,
                key=lambda rotation: abs(rotation - second_transform['rotation']),
            )
            self.assertAlmostEqual(transform['rotation'], closest_rotation, places=2)
            self.assertAlmostEqual(
                second_transform['rotation'], second_closest_rotation, places=2
            )

        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, True, False],
            "Cylinder cut should finish in edge select mode",
        )
        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(
            len(selected_edges),
            16,
            "Two octagonal openings should leave 16 selected boundary edges",
        )
        for edge in selected_edges:
            self.assertEqual(
                len(edge.link_faces),
                1,
                "Every selected cylinder boundary edge should be open",
            )
        self.assertEqual(
            len([face for face in bm.faces if face.select]),
            0,
            "No faces should be selected after cylinder cut",
        )

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_through_hole_preserves_uvs_and_selects_boundary_edges(self):
        self._assert_through_hole_preserves_uvs_and_selects_boundary_edges(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_through_hole_preserves_uvs_and_selects_boundary_edges(self):
        self._assert_through_hole_preserves_uvs_and_selects_boundary_edges(
            'FACES'
        )

    def _assert_through_hole_bridge_weld_creates_tunnel_faces(
            self, radius_mode):
        """Bridge weld should connect both cylinder cut openings with one face per side."""
        (
            obj,
            context_override,
            center,
            radius_x,
            radius_y,
            depth,
            local_x,
            local_y,
            local_z,
        ) = self._execute_textured_cube_cut(
            f"cylinder_cut_bridge_{radius_mode.lower()}", 8, radius_mode,
        )

        first_vertex = center - local_x * radius_x - local_y * radius_y
        second_vertex = center + local_x * radius_x + local_y * radius_y
        extrude_direction = -local_z
        back_point = center + local_z * depth
        back_plane_offset = back_point.dot(extrude_direction.normalized())
        store_from_edge_selection(
            obj,
            abs(depth),
            extrude_direction,
            back_plane_offset,
            first_vertex,
            second_vertex,
            local_x,
            local_y,
            0,
        )

        self.assertEqual(
            get_context_action_kind(),
            'BRIDGE',
            "A through cylinder cut should offer Bridge",
        )

        pre_bridge_bmesh = bmesh.from_edit_mesh(obj.data)
        existing_faces = set(pre_bridge_bmesh.faces)
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued cylinder Bridge action",
        )
        yield

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(
            len(bm.faces),
            28,
            "Bridge should add 8 tunnel faces to the 20 cylinder-cut faces",
        )

        pixels_per_meter = bpy.context.scene.level_design_props.pixels_per_meter
        uv_layer = bm.loops.layers.uv[0]
        uv_layer_second = bm.loops.layers.uv[1]
        bridge_faces = set(bm.faces) - existing_faces
        self.assertEqual(len(bridge_faces), 8)

        for face in set(bm.faces) - bridge_faces:
            for layer, expected_scale in (
                    (uv_layer, 1.0), (uv_layer_second, 0.5)):
                transform = derive_transform_from_uvs(
                    face, layer, pixels_per_meter, obj.data
                )
                self.assertIsNotNone(transform)
                self.assertAlmostEqual(
                    transform['scale_u'], expected_scale, places=2,
                )
                self.assertAlmostEqual(
                    transform['scale_v'], expected_scale, places=2,
                )

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_through_hole_bridge_weld_creates_tunnel_faces(self):
        yield from self._assert_through_hole_bridge_weld_creates_tunnel_faces(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_through_hole_bridge_weld_creates_tunnel_faces(self):
        yield from self._assert_through_hole_bridge_weld_creates_tunnel_faces(
            'FACES'
        )

    def _assert_corner_cut_folded_plane_weld_completes_cylinder_wall(
            self, radius_mode):
        """A cylinder centred on a cube corner should weld its open wall."""
        obj = create_textured_cube(
            f"cylinder_cut_folded_weld_{radius_mode.lower()}",
            1.0,
            1.0,
            use_box_project=True,
        )
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        center = Vector((0.0, -0.5, 0.0))
        local_x = Vector((1.0, 0.0, 0.0))
        local_y = Vector((0.0, 0.0, 1.0))
        local_z = Vector((0.0, 1.0, 0.0))
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.cylinder_cut(
                action_center=center,
                radius_x=0.25,
                radius_y=0.25,
                action_depth=2.0,
                action_local_x=local_x,
                action_local_y=local_y,
                action_local_z=local_z,
                side_count=8,
                radius_mode=radius_mode,
            )
        self.assertIn('FINISHED', result)

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(
            get_context_action_kind(),
            'FOLDED_PLANE',
            "A cylinder corner cut should offer Folded Plane",
        )
        faces_before_weld = set(bm.faces)
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.context_weld()
        self.assertIn('FINISHED', result)
        yield from wait_for_condition(
            lambda: get_context_action_kind() == 'NONE',
            "W did not execute the queued cylinder Folded Plane action",
        )
        yield

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        wall_faces = set(bm.faces) - faces_before_weld
        expected_wall_face_count = {'EDGES': 2, 'FACES': 3}
        self.assertEqual(
            len(wall_faces),
            expected_wall_face_count[radius_mode],
            "Folded Plane should complete every cylinder side above the cube bottom",
        )
        for face in wall_faces:
            self.assertEqual(len(face.verts), 4)
            self.assertGreater(face.calc_area(), 1e-6)
            self.assertAlmostEqual(face.normal.y, 0.0, places=4)
            self.assertEqual(
                {round(vertex.co.y, 4) for vertex in face.verts},
                {0.0, 1.0},
            )
        self.assertEqual(
            {face for face in bm.faces if face.select},
            wall_faces,
            "Folded Plane should select only the new cylinder wall faces",
        )
        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, False, True],
            "Folded Plane should finish in face select mode",
        )
        self.assertIsNotNone(
            bm.loops.layers.uv.active,
            "Folded cylinder wall faces should be UV projected",
        )

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_corner_cut_folded_plane_weld_completes_cylinder_wall(self):
        yield from self._assert_corner_cut_folded_plane_weld_completes_cylinder_wall(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_corner_cut_folded_plane_weld_completes_cylinder_wall(self):
        yield from self._assert_corner_cut_folded_plane_weld_completes_cylinder_wall(
            'FACES'
        )

    def _assert_edge_aligned_hole_preserves_valid_geometry(self, radius_mode):
        """Cut a through-hole whose profile touches the cube's top edge."""
        obj = create_textured_cube(
            f"cylinder_cut_edge_aligned_{radius_mode.lower()}",
            1.0,
            1.0,
            use_box_project=True,
        )
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                Vector((0.5, -0.5, 0.75)),
                0.25,
                0.25,
                2.0,
                Vector((1.0, 0.0, 0.0)),
                Vector((0.0, 0.0, 1.0)),
                Vector((0.0, 1.0, 0.0)),
                8,
                radius_mode,
            )

        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, True, False],
            "Edge-aligned Cylinder Cut should finish in edge select mode",
        )
        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(
            len(selected_edges),
            16,
            "Two edge-aligned octagonal openings should select 16 boundary edges",
        )
        for edge in selected_edges:
            self.assertEqual(len(edge.link_faces), 1)
        for face in bm.faces:
            self.assertGreaterEqual(len(face.verts), 3)
            self.assertGreater(face.calc_area(), 1e-8)
        self.assertEqual(len([face for face in bm.faces if face.select]), 0)

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_edge_aligned_hole_preserves_valid_geometry(self):
        self._assert_edge_aligned_hole_preserves_valid_geometry('EDGES')

    def test_cylinder_cut_faces_radius_mode_edge_aligned_hole_preserves_valid_geometry(self):
        self._assert_edge_aligned_hole_preserves_valid_geometry('FACES')

    def _assert_cut_in_concave_ngon_face_produces_expected_geometry(
            self, radius_mode):
        """An octagonal profile should preserve the concave n-gon cut result."""
        mesh = bpy.data.meshes.new(
            f"cylinder_cut_u_face_{radius_mode.lower()}"
        )
        bm_new = bmesh.new()
        loop = [
            (0, 6, 6), (0, 6, 0), (0, 0, 0), (0, 0, 8),
            (0, 14, 8), (0, 14, 0), (0, 10, 0), (0, 10, 6),
        ]
        vertices = [bm_new.verts.new(position) for position in loop]
        bm_new.faces.new(vertices)
        bm_new.to_mesh(mesh)
        bm_new.free()

        obj = bpy.data.objects.new(
            f"cylinder_cut_u_face_{radius_mode.lower()}", mesh,
        )
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        _apply_material_box_project(obj, 5.0)

        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(mesh)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(mesh)

        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                Vector((0.0, 6.0, 7.0)),
                1.0,
                0.5,
                0.0,
                Vector((0.0, -1.0, 0.0)),
                Vector((0.0, 0.0, -1.0)),
                Vector((-1.0, 0.0, 0.0)),
                8,
                radius_mode,
            )

        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(mesh)

        def rounded_vertex(vertex):
            return (
                round(vertex.co.x, 4),
                round(vertex.co.y, 4),
                round(vertex.co.z, 4),
            )

        expected_profiles = {
            'EDGES': (
                (0.0, 5.0, 7.0),
                (0.0, 5.2929, 6.6464),
                (0.0, 6.0, 6.5),
                (0.0, 6.7071, 6.6464),
                (0.0, 7.0, 7.0),
                (0.0, 6.7071, 7.3536),
                (0.0, 6.0, 7.5),
                (0.0, 5.2929, 7.3536),
            ),
            'FACES': (
                (0.0, 5.0, 6.7929),
                (0.0, 5.5858, 6.5),
                (0.0, 6.4142, 6.5),
                (0.0, 7.0, 6.7929),
                (0.0, 7.0, 7.2071),
                (0.0, 6.4142, 7.5),
                (0.0, 5.5858, 7.5),
                (0.0, 5.0, 7.2071),
            ),
        }
        expected_profile = expected_profiles[radius_mode]
        actual_vertices = {
            rounded_vertex(vertex) for vertex in bm.verts if vertex.is_valid
        }
        self.assertTrue(set(expected_profile).issubset(actual_vertices))

        expected_boundary_edges = {
            tuple(sorted((expected_profile[index], expected_profile[(index + 1) % 8])))
            for index in range(8)
        }
        selected_edges = [edge for edge in bm.edges if edge.select]
        actual_boundary_edges = {
            tuple(sorted((rounded_vertex(edge.verts[0]), rounded_vertex(edge.verts[1]))))
            for edge in selected_edges
        }
        self.assertEqual(actual_boundary_edges, expected_boundary_edges)
        for edge in selected_edges:
            self.assertEqual(len(edge.link_faces), 1)
        for face in bm.faces:
            self.assertGreaterEqual(len(face.verts), 3)
            self.assertGreater(face.calc_area(), 1e-8)

        bmesh.update_edit_mesh(mesh)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_in_concave_ngon_face_produces_expected_geometry(self):
        self._assert_cut_in_concave_ngon_face_produces_expected_geometry(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_in_concave_ngon_face_produces_expected_geometry(self):
        self._assert_cut_in_concave_ngon_face_produces_expected_geometry(
            'FACES'
        )

    def _assert_overlapping_disconnected_planes_preserve_separate_colocated_vertices(
            self, radius_mode):
        """Cylinder Cut should not merge disconnected overlapping mesh islands."""
        mesh = bpy.data.meshes.new(
            f"cylinder_cut_overlapping_planes_{radius_mode.lower()}"
        )
        bm_new = bmesh.new()
        for _index in range(2):
            vertex_0 = bm_new.verts.new((0, 0, 0))
            vertex_1 = bm_new.verts.new((1, 0, 0))
            vertex_2 = bm_new.verts.new((1, 0, 1))
            vertex_3 = bm_new.verts.new((0, 0, 1))
            bm_new.faces.new((vertex_0, vertex_1, vertex_2, vertex_3))
        bm_new.to_mesh(mesh)
        bm_new.free()

        obj = bpy.data.objects.new(
            f"cylinder_cut_overlapping_planes_{radius_mode.lower()}", mesh,
        )
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        _apply_material_box_project(obj, 1.0)

        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(mesh)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(mesh)

        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                Vector((0.5, -0.5, 0.5)),
                0.25,
                0.25,
                1.0,
                Vector((1.0, 0.0, 0.0)),
                Vector((0.0, 0.0, 1.0)),
                Vector((0.0, 1.0, 0.0)),
                8,
                radius_mode,
            )

        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(mesh)

        remaining = {
            vertex for vertex in bm.verts if vertex.is_valid and vertex.link_edges
        }
        components = []
        while remaining:
            start = remaining.pop()
            component = [start]
            stack = [start]
            while stack:
                current = stack.pop()
                for edge in current.link_edges:
                    other = edge.other_vert(current)
                    if other not in remaining:
                        continue
                    remaining.remove(other)
                    component.append(other)
                    stack.append(other)
            components.append(component)
        self.assertEqual(len(components), 2)

        coordinate_counts = {}
        for vertex in bm.verts:
            if not vertex.is_valid:
                continue
            key = (
                round(vertex.co.x, 4),
                round(vertex.co.y, 4),
                round(vertex.co.z, 4),
            )
            coordinate_counts[key] = coordinate_counts.get(key, 0) + 1
        self.assertTrue(coordinate_counts)
        for key, count in coordinate_counts.items():
            self.assertEqual(
                count,
                2,
                f"Coordinate {key} should retain one vertex per disconnected plane",
            )

        bmesh.update_edit_mesh(mesh)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_overlapping_disconnected_planes_preserves_separate_colocated_vertices(self):
        self._assert_overlapping_disconnected_planes_preserve_separate_colocated_vertices(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_overlapping_disconnected_planes_preserves_separate_colocated_vertices(self):
        self._assert_overlapping_disconnected_planes_preserve_separate_colocated_vertices(
            'FACES'
        )

    def _assert_cut_centered_on_bottom_edge_and_flush_with_side_preserves_valid_topology(
            self, radius_mode):
        """A bottom-edge cylinder tangent to the cube side should remain valid."""
        mesh = bpy.data.meshes.new(
            f"cylinder_cut_flush_with_mesh_corner_{radius_mode.lower()}"
        )
        bm_new = bmesh.new()
        bmesh.ops.create_cube(bm_new, size=3.0)
        for vertex in bm_new.verts:
            vertex.co += Vector((1.5, 1.5, 1.5))
        bm_new.to_mesh(mesh)
        bm_new.free()

        obj = bpy.data.objects.new(
            f"cylinder_cut_flush_with_mesh_corner_{radius_mode.lower()}", mesh,
        )
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        _apply_material_box_project(obj, 1.0)

        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(mesh)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(mesh)

        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                Vector((2.5, 0.0, 0.0)),
                0.5,
                0.5,
                1.0,
                Vector((1.0, 0.0, 0.0)),
                Vector((0.0, 0.0, 1.0)),
                Vector((0.0, 1.0, 0.0)),
                8,
                radius_mode,
            )

        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        expected_face_counts = {'EDGES': 11, 'FACES': 11}
        self.assertEqual(len(bm.faces), expected_face_counts[radius_mode])
        selected_edges = [edge for edge in bm.edges if edge.select]
        expected_topology_counts = {
            'EDGES': (14, 7),
            'FACES': (15, 8),
        }
        self.assertEqual(
            (len(bm.verts), len(selected_edges)),
            expected_topology_counts[radius_mode],
        )
        for edge in selected_edges:
            self.assertEqual(len(edge.link_faces), 1)
        for face in bm.faces:
            self.assertGreaterEqual(len(face.verts), 3)
            self.assertGreater(face.calc_area(), 1e-8)
        minimum = Vector((
            min(vertex.co.x for vertex in bm.verts),
            min(vertex.co.y for vertex in bm.verts),
            min(vertex.co.z for vertex in bm.verts),
        ))
        maximum = Vector((
            max(vertex.co.x for vertex in bm.verts),
            max(vertex.co.y for vertex in bm.verts),
            max(vertex.co.z for vertex in bm.verts),
        ))
        self.assertEqual(tuple(minimum), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(maximum), (3.0, 3.0, 3.0))

        front_vertices = {
            (
                round(vertex.co.x, 4),
                round(vertex.co.y, 4),
                round(vertex.co.z, 4),
            )
            for vertex in bm.verts
            if abs(vertex.co.y) < 1e-5
        }
        if radius_mode == 'EDGES':
            self.assertIn((3.0, 0.0, 0.0), front_vertices)
        else:
            self.assertIn((3.0, 0.0, 0.2071), front_vertices)
            self.assertNotIn((3.0, 0.0, 0.0), front_vertices)

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_centered_on_bottom_edge_and_flush_with_side_preserves_valid_topology(self):
        self._assert_cut_centered_on_bottom_edge_and_flush_with_side_preserves_valid_topology(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_centered_on_bottom_edge_and_flush_with_side_preserves_valid_topology(self):
        self._assert_cut_centered_on_bottom_edge_and_flush_with_side_preserves_valid_topology(
            'FACES'
        )

    def _assert_cut_spanning_single_face_creates_two_disconnected_faces(
            self, radius_mode):
        """A tall cylinder profile should separate one quad into two faces."""
        object_name = (
            f"cylinder_cut_spanning_single_face_{radius_mode.lower()}"
        )
        mesh = bpy.data.meshes.new(object_name)
        bm_new = bmesh.new()
        vertices = [
            bm_new.verts.new(position)
            for position in (
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
            )
        ]
        bm_new.faces.new(vertices)
        bm_new.to_mesh(mesh)
        bm_new.free()

        obj = bpy.data.objects.new(object_name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        _apply_material_box_project(obj, 1.0)

        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        bm = bmesh.from_edit_mesh(mesh)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(mesh)

        # Centering the very tall octagon above the face keeps all profile
        # corners outside z=[0, 1]. Each side therefore crosses the host as
        # one edge while the profile still removes a complete central strip.
        with bpy.context.temp_override(**context_override):
            success, message = execute_cylinder_cut(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                Vector((0.5, -0.5, 1.5)),
                0.2,
                4.0,
                1.0,
                Vector((1.0, 0.0, 0.0)),
                Vector((0.0, 0.0, 1.0)),
                Vector((0.0, 1.0, 0.0)),
                8,
                radius_mode,
            )

        self.assertTrue(success, message)
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        self.assertEqual(len(bm.faces), 2)
        self.assertEqual(len(bm.verts), 8)
        self.assertEqual(len(bm.edges), 8)
        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, True, False],
            "Cylinder Cut should finish in edge select mode",
        )
        self.assertEqual(len([face for face in bm.faces if face.select]), 0)

        remaining = {
            vertex for vertex in bm.verts if vertex.is_valid and vertex.link_faces
        }
        components = []
        while remaining:
            start = remaining.pop()
            component = {start}
            stack = [start]
            while stack:
                current = stack.pop()
                for edge in current.link_edges:
                    other = edge.other_vert(current)
                    if other not in remaining:
                        continue
                    remaining.remove(other)
                    component.add(other)
                    stack.append(other)
            components.append(component)
        self.assertEqual(len(components), 2)
        self.assertEqual(sorted(len(component) for component in components), [4, 4])

        faces_by_x = sorted(
            bm.faces,
            key=lambda face: face.calc_center_median().x,
        )
        left_face, right_face = faces_by_x
        self.assertLess(max(vertex.co.x for vertex in left_face.verts), 0.5)
        self.assertGreater(min(vertex.co.x for vertex in right_face.verts), 0.5)
        self.assertAlmostEqual(
            min(vertex.co.x for vertex in left_face.verts), 0.0, places=4,
        )
        self.assertAlmostEqual(
            max(vertex.co.x for vertex in right_face.verts), 1.0, places=4,
        )
        for face in bm.faces:
            self.assertEqual(len(face.verts), 4)
            self.assertAlmostEqual(
                min(vertex.co.z for vertex in face.verts), 0.0, places=4,
            )
            self.assertAlmostEqual(
                max(vertex.co.z for vertex in face.verts), 1.0, places=4,
            )
            for vertex in face.verts:
                self.assertAlmostEqual(vertex.co.y, 0.0, places=4)

        expected_boundary_vertices = {
            'EDGES': {
                (0.3311, 0.0, 0.0),
                (0.3104, 0.0, 1.0),
                (0.6689, 0.0, 0.0),
                (0.6896, 0.0, 1.0),
            },
            'FACES': {
                (0.3, 0.0, 0.0),
                (0.3, 0.0, 1.0),
                (0.7, 0.0, 0.0),
                (0.7, 0.0, 1.0),
            },
        }
        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(len(selected_edges), 2)
        actual_boundary_vertices = {
            (
                round(vertex.co.x, 4),
                round(vertex.co.y, 4),
                round(vertex.co.z, 4),
            )
            for edge in selected_edges
            for vertex in edge.verts
        }
        self.assertEqual(
            actual_boundary_vertices,
            expected_boundary_vertices[radius_mode],
        )
        for edge in selected_edges:
            self.assertEqual(len(edge.link_faces), 1)

        expected_remaining_areas = {
            'EDGES': 0.6414213562,
            'FACES': 0.6,
        }
        self.assertAlmostEqual(
            sum(face.calc_area() for face in bm.faces),
            expected_remaining_areas[radius_mode],
            places=4,
        )

        bmesh.update_edit_mesh(mesh)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_cylinder_cut_edges_radius_mode_spanning_single_face_creates_two_disconnected_faces(self):
        self._assert_cut_spanning_single_face_creates_two_disconnected_faces(
            'EDGES'
        )

    def test_cylinder_cut_faces_radius_mode_spanning_single_face_creates_two_disconnected_faces(self):
        self._assert_cut_spanning_single_face_creates_two_disconnected_faces(
            'FACES'
        )
