import json

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon

from ..handlers import set_active_image
from ..operators.prism_builder.geometry import (
    execute_prism_builder_edit_mode,
    execute_prism_builder_object_mode,
)
from ..operators.mesh_cut.concave_prism import build_profile_prism
from .base_test import AnvilTestCase
from .helpers import _get_context_override, create_vertical_plane, TEXTURE_PATH


class PrismBuilderTest(AnvilTestCase):
    """Test Prism Builder geometry, modes, materials, and options."""

    def _profile(self, x_offset):
        return [
            Vector((x_offset - 0.5, 0.0, 0.5)),
            Vector((x_offset, 0.0, 0.0)),
            Vector((x_offset + 1.0, 0.0, 0.0)),
            Vector((x_offset + 1.5, 0.0, 0.5)),
            Vector((x_offset + 1.0, 0.0, 1.0)),
            Vector((x_offset, 0.0, 1.0)),
        ]

    def _create_empty_edit_mesh(self, name):
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.object.mode_set(mode='EDIT')
        return obj

    def _build_edit_prism(self, obj, keep_overlap_faces, prefer_quads):
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        return execute_prism_builder_edit_mode(
            self._profile(0.0),
            2.0,
            Vector((0.0, 1.0, 0.0)),
            obj,
            ppm,
            keep_overlap_faces,
            prefer_quads,
        )

    def _assert_faces_have_material_and_uv_area(self, obj, faces):
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()
        self.assertGreater(len(obj.data.materials), 0)
        for face in faces:
            self.assertEqual(face.material_index, 0)
            uv_area = 0.0
            uvs = [loop[uv_layer].uv for loop in face.loops]
            for index, current in enumerate(uvs):
                following = uvs[(index + 1) % len(uvs)]
                uv_area += current.x * following.y - following.x * current.y
            self.assertGreater(abs(uv_area) * 0.5, 1e-8)

    def test_prism_builder_previous_texture_applies_material_and_uvs(self):
        image = bpy.data.images.load(TEXTURE_PATH, check_existing=True)
        set_active_image(image)
        obj = self._create_empty_edit_mesh("prism_builder_previous_texture")

        result = self._build_edit_prism(obj, True, False)
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 8)
        self._assert_faces_have_material_and_uv_area(obj, list(bm.faces))

    def test_prism_builder_active_face_texture_applies_to_new_faces(self):
        obj = create_vertical_plane("prism_builder_active_face")
        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.select_mode = {'FACE'}
        source_face = bm.faces[0]
        source_face.select = True
        bm.faces.active = source_face
        bmesh.update_edit_mesh(obj.data)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_prism_builder_edit_mode(
            self._profile(3.0),
            1.0,
            Vector((0.0, 1.0, 0.0)),
            obj,
            ppm,
            True,
            False,
        )
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 9)
        self._assert_faces_have_material_and_uv_area(obj, list(bm.faces)[1:])

    def test_prism_builder_prefer_quads_replaces_six_sided_ngons(self):
        obj = self._create_empty_edit_mesh("prism_builder_prefer_quads")
        result = self._build_edit_prism(obj, True, True)
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        cap_faces = [
            face for face in bm.faces
            if abs(face.normal.y) > 0.99
        ]
        self.assertGreater(len(cap_faces), 2)
        self.assertTrue(all(len(face.verts) <= 4 for face in cap_faces))
        self.assertTrue(any(len(face.verts) == 4 for face in cap_faces))

    def test_prism_builder_prefer_quads_preserves_concave_cap_shape(self):
        obj = self._create_empty_edit_mesh("prism_builder_concave_caps")
        profile = [
            Vector((0.30, 0.0, 7.52)),
            Vector((3.92, 0.0, 6.44)),
            Vector((1.38, 0.0, 5.46)),
            Vector((1.32, 0.0, 3.12)),
            Vector((2.44, 0.0, 2.72)),
            Vector((2.60, 0.0, 1.72)),
            Vector((0.0, 0.0, 0.0)),
        ]
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_prism_builder_edit_mode(
            profile,
            1.0,
            Vector((0.0, 1.0, 0.0)),
            obj,
            ppm,
            True,
            True,
        )
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        cap_faces = [
            face for face in bm.faces
            if abs(face.normal.y) > 0.99
        ]
        front_cap_faces = [
            face for face in cap_faces
            if abs(face.calc_center_median().y) < 0.001
        ]
        back_cap_faces = [
            face for face in cap_faces
            if abs(face.calc_center_median().y - 1.0) < 0.001
        ]
        self.assertTrue(all(len(face.verts) in {3, 4} for face in cap_faces))
        self.assertTrue(any(len(face.verts) == 3 for face in cap_faces))
        self.assertTrue(all(face.normal.y < -0.99 for face in front_cap_faces))
        self.assertTrue(all(face.normal.y > 0.99 for face in back_cap_faces))
        for face in cap_faces:
            coordinates = [vertex.co for vertex in face.verts]
            turns = [
                (
                    coordinates[(index + 1) % len(coordinates)]
                    - coordinates[index]
                ).cross(
                    coordinates[(index + 2) % len(coordinates)]
                    - coordinates[(index + 1) % len(coordinates)]
                ).dot(face.normal)
                for index in range(len(coordinates))
            ]
            self.assertTrue(all(turn >= -1e-6 for turn in turns))
        front_cap_area = sum(
            face.calc_area()
            for face in front_cap_faces
        )
        back_cap_area = sum(
            face.calc_area()
            for face in back_cap_faces
        )
        expected_cap_area = abs(sum(
            vertex.x * profile[(index + 1) % len(profile)].z
            - profile[(index + 1) % len(profile)].x * vertex.z
            for index, vertex in enumerate(profile)
        )) * 0.5
        self.assertAlmostEqual(front_cap_area, expected_cap_area, places=5)
        self.assertAlmostEqual(back_cap_area, expected_cap_area, places=5)

        prism = build_profile_prism(
            Matrix.Identity(4),
            profile,
            Vector((0.0, 1.0, 0.0)),
        )
        for face in front_cap_faces:
            for edge in face.edges:
                for factor in (0.25, 0.5, 0.75):
                    point = edge.verts[0].co.lerp(edge.verts[1].co, factor)
                    self.assertTrue(prism.point_inside(point))
            face_coordinates = [
                vertex.co.copy() for vertex in face.verts
            ]
            triangles = tessellate_polygon([face_coordinates])
            for triangle in triangles:
                if triangle and isinstance(triangle[0], int):
                    triangle_coordinates = [
                        face_coordinates[index] for index in triangle
                    ]
                else:
                    triangle_coordinates = triangle
                center = sum(
                    (Vector(vertex) for vertex in triangle_coordinates),
                    Vector((0.0, 0.0, 0.0)),
                ) / 3.0
                self.assertTrue(prism.point_inside(center))

        def edge_coordinates(edge):
            return frozenset(
                (round(vertex.co.x, 5), round(vertex.co.z, 5))
                for vertex in edge.verts
            )

        front_cap_face_set = set(front_cap_faces)
        actual_boundary = {
            edge_coordinates(edge)
            for face in front_cap_faces
            for edge in face.edges
            if sum(
                linked_face in front_cap_face_set
                for linked_face in edge.link_faces
            ) == 1
        }
        expected_boundary = {
            frozenset((
                (round(vertex.x, 5), round(vertex.z, 5)),
                (
                    round(profile[(index + 1) % len(profile)].x, 5),
                    round(profile[(index + 1) % len(profile)].z, 5),
                ),
            ))
            for index, vertex in enumerate(profile)
        }
        self.assertEqual(actual_boundary, expected_boundary)

    def test_prism_builder_remove_overlap_faces_removes_anti_parallel_cap(self):
        mesh = bpy.data.meshes.new("prism_builder_overlap")
        bm_object = bmesh.new()
        vertices = [
            bm_object.verts.new(co)
            for co in (
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                (1.0, 0.0, 1.0),
                (1.0, 0.0, 0.0),
            )
        ]
        bm_object.faces.new(vertices)
        bm_object.to_mesh(mesh)
        bm_object.free()
        obj = bpy.data.objects.new("prism_builder_overlap", mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.select = False
        bm.faces.active = None
        bmesh.update_edit_mesh(obj.data)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        profile = [
            Vector((0.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 1.0)),
            Vector((0.0, 0.0, 1.0)),
        ]

        result = execute_prism_builder_edit_mode(
            profile,
            1.0,
            Vector((0.0, 1.0, 0.0)),
            obj,
            ppm,
            False,
            False,
        )
        self.assertTrue(result[0], result[1])
        self.assertEqual(len(result[2]), 5)

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 6)
        anti_parallel_caps = [
            face for face in bm.faces
            if face.normal.dot(Vector((0.0, -1.0, 0.0))) > 0.99
            and abs(face.calc_center_median().y) < 0.001
        ]
        self.assertEqual(anti_parallel_caps, [])

    def test_prism_builder_remove_overlap_faces_removes_preferred_quad_end_caps(self):
        profile = [
            Vector((0.0, 0.0, 0.0)),
            Vector((2.0, 0.0, 0.0)),
            Vector((2.0, 0.0, 1.0)),
            Vector((1.0, 0.0, 1.0)),
            Vector((1.0, 0.0, 2.0)),
            Vector((0.0, 0.0, 2.0)),
        ]
        mesh = bpy.data.meshes.new("prism_builder_quadified_overlap")
        bm_object = bmesh.new()
        front_vertices = [
            bm_object.verts.new(vertex) for vertex in reversed(profile)
        ]
        back_vertices = [
            bm_object.verts.new(vertex + Vector((0.0, 1.0, 0.0)))
            for vertex in profile
        ]
        bm_object.faces.new(front_vertices)
        bm_object.faces.new(back_vertices)
        bm_object.to_mesh(mesh)
        bm_object.free()
        obj = bpy.data.objects.new("prism_builder_quadified_overlap", mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.select = False
        bm.faces.active = None
        bmesh.update_edit_mesh(obj.data)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter

        result = execute_prism_builder_edit_mode(
            profile,
            1.0,
            Vector((0.0, 1.0, 0.0)),
            obj,
            ppm,
            False,
            True,
        )
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        new_faces = [bm.faces[index] for index, _positions in result[2]]
        overlapping_end_caps = [
            face for face in new_faces
            if (
                all(abs(vertex.co.y) < 0.001 for vertex in face.verts)
                or all(abs(vertex.co.y - 1.0) < 0.001 for vertex in face.verts)
            )
        ]
        self.assertEqual(overlapping_end_caps, [])

    def test_prism_builder_object_mode_suffix_uses_blender_numbering(self):
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        profile = [
            vertex + Vector((2.0, 3.0, 4.0))
            for vertex in self._profile(0.0)
        ]
        values = (
            profile,
            2.0,
            Vector((0.0, 1.0, 0.0)),
            ppm,
            False,
            "-col",
        )
        first_result = execute_prism_builder_object_mode(*values)
        self.assertTrue(first_result[0], first_result[1])
        self.assertEqual(bpy.context.active_object.name, "Anvil.Prism-col")
        self.assertEqual(tuple(bpy.context.active_object.location), tuple(profile[0]))
        self.assertEqual(len(bpy.context.active_object.data.polygons), 8)

        second_result = execute_prism_builder_object_mode(*values)
        self.assertTrue(second_result[0], second_result[1])
        self.assertEqual(bpy.context.active_object.name, "Anvil.Prism.001-col")

    def test_prism_builder_captured_edit_mode_operator_values_replay(self):
        obj = self._create_empty_edit_mesh("prism_builder_action_replay")
        profile = self._profile(0.0)
        with bpy.context.temp_override(**_get_context_override()):
            result = bpy.ops.leveldesign.prism_builder(
                'EXEC_DEFAULT',
                action_profile_json=json.dumps([
                    list(vertex) for vertex in profile
                ]),
                action_depth=2.0,
                action_local_z=(0.0, 1.0, 0.0),
                action_had_selection=False,
                action_was_edit_mode=True,
                action_object_name=obj.name,
                keep_anti_parallel_coplanar_faces=True,
                prefer_quads=False,
                name_suffix="",
            )
        self.assertIn('FINISHED', result)
        self.assertEqual(bpy.context.mode, 'EDIT_MESH')
        self.assertEqual(bpy.context.active_object, obj)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 8)

    def test_prism_builder_action_panel_properties_are_visible(self):
        properties = bpy.ops.leveldesign.prism_builder.get_rna_type().properties
        expected = {
            "name_suffix": ("Suffix", ""),
            "keep_anti_parallel_coplanar_faces": (
                "Keep Overlap Faces",
                True,
            ),
            "prefer_quads": ("Prefer Quads", True),
        }
        for identifier, (name, default) in expected.items():
            prop = properties[identifier]
            self.assertFalse(prop.is_hidden)
            self.assertEqual(prop.name, name)
            self.assertEqual(prop.default, default)
