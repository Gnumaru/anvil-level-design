import bmesh
import bpy
from mathutils import Vector

from ..handlers import set_active_image
from ..operators.cylinder_builder.geometry import (
    execute_cylinder_builder_edit_mode,
    execute_cylinder_builder_object_mode,
)
from ..operators.profile_builder_geometry import (
    CAP_MODE_NGON,
    CAP_MODE_TRIANGLE_FAN,
)
from .base_test import AnvilTestCase
from .helpers import _get_context_override, create_vertical_plane, TEXTURE_PATH


class CylinderBuilderTest(AnvilTestCase):
    """Test Cylinder Builder geometry, modes, materials, and options."""

    def _create_empty_edit_mesh(self, name):
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with bpy.context.temp_override(**_get_context_override()):
            bpy.ops.object.mode_set(mode='EDIT')
        return obj

    def _build_edit_cylinder(self, obj, skip_caps, cap_fill):
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        return execute_cylinder_builder_edit_mode(
            Vector((0.0, 0.0, 0.0)),
            1.0,
            0.5,
            2.0,
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((0.0, 1.0, 0.0)),
            8,
            'EDGES',
            obj,
            ppm,
            skip_caps,
            cap_fill,
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

    def test_cylinder_builder_previous_texture_applies_material_and_uvs(self):
        image = bpy.data.images.load(TEXTURE_PATH, check_existing=True)
        set_active_image(image)
        obj = self._create_empty_edit_mesh("cylinder_builder_previous_texture")

        result = self._build_edit_cylinder(obj, False, CAP_MODE_NGON)
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 10)
        self._assert_faces_have_material_and_uv_area(obj, list(bm.faces))

    def test_cylinder_builder_active_face_texture_applies_to_new_faces(self):
        obj = create_vertical_plane("cylinder_builder_active_face")
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

        result = execute_cylinder_builder_edit_mode(
            Vector((3.0, 0.0, 0.5)),
            0.75,
            0.5,
            1.0,
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((0.0, 1.0, 0.0)),
            8,
            'EDGES',
            obj,
            ppm,
            False,
            CAP_MODE_NGON,
        )
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        self.assertEqual(len(bm.faces), 11)
        self._assert_faces_have_material_and_uv_area(obj, list(bm.faces)[1:])

    def test_cylinder_builder_skip_caps_creates_only_side_quads(self):
        obj = self._create_empty_edit_mesh("cylinder_builder_skip_caps")
        result = self._build_edit_cylinder(obj, True, CAP_MODE_NGON)
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 8)
        self.assertTrue(all(len(face.verts) == 4 for face in bm.faces))

    def test_cylinder_builder_triangles_to_center_cap_fill_creates_fans(self):
        obj = self._create_empty_edit_mesh("cylinder_builder_triangle_fans")
        result = self._build_edit_cylinder(
            obj,
            False,
            CAP_MODE_TRIANGLE_FAN,
        )
        self.assertTrue(result[0], result[1])

        bm = bmesh.from_edit_mesh(obj.data)
        triangle_count = sum(len(face.verts) == 3 for face in bm.faces)
        quad_count = sum(len(face.verts) == 4 for face in bm.faces)
        self.assertEqual(triangle_count, 16)
        self.assertEqual(quad_count, 8)
        self.assertEqual(len(bm.verts), 18)

    def test_cylinder_builder_object_mode_suffix_uses_blender_numbering(self):
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        values = (
            Vector((2.0, 3.0, 4.0)),
            1.0,
            0.5,
            2.0,
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((0.0, 1.0, 0.0)),
            8,
            'EDGES',
            ppm,
            False,
            CAP_MODE_NGON,
            "-col",
        )
        first_result = execute_cylinder_builder_object_mode(*values)
        self.assertTrue(first_result[0], first_result[1])
        self.assertEqual(bpy.context.active_object.name, "Anvil.Cylinder-col")
        self.assertEqual(tuple(bpy.context.active_object.location), (2.0, 3.0, 4.0))
        self.assertEqual(len(bpy.context.active_object.data.polygons), 10)

        second_result = execute_cylinder_builder_object_mode(*values)
        self.assertTrue(second_result[0], second_result[1])
        self.assertEqual(bpy.context.active_object.name, "Anvil.Cylinder.001-col")

    def test_cylinder_builder_captured_edit_mode_operator_values_replay(self):
        obj = self._create_empty_edit_mesh("cylinder_builder_action_replay")
        with bpy.context.temp_override(**_get_context_override()):
            result = bpy.ops.leveldesign.cylinder_builder(
                'EXEC_DEFAULT',
                action_center=(0.0, 0.0, 0.0),
                radius_x=1.0,
                radius_y=0.5,
                action_depth=2.0,
                action_local_x=(1.0, 0.0, 0.0),
                action_local_y=(0.0, 0.0, 1.0),
                action_local_z=(0.0, 1.0, 0.0),
                action_had_selection=False,
                action_was_edit_mode=True,
                action_object_name=obj.name,
                side_count=8,
                radius_mode='EDGES',
                skip_caps=False,
                cap_fill=CAP_MODE_NGON,
                name_suffix="",
            )
        self.assertIn('FINISHED', result)
        self.assertEqual(bpy.context.mode, 'EDIT_MESH')
        self.assertEqual(bpy.context.active_object, obj)
        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len(bm.faces), 10)

    def test_cylinder_builder_action_panel_properties_are_visible(self):
        properties = bpy.ops.leveldesign.cylinder_builder.get_rna_type().properties
        expected = {
            "name_suffix": ("Suffix", ""),
            "skip_caps": ("Skip Caps", False),
            "cap_fill": ("Cap Fill", CAP_MODE_TRIANGLE_FAN),
        }
        for identifier, (name, default) in expected.items():
            prop = properties[identifier]
            self.assertFalse(prop.is_hidden)
            self.assertEqual(prop.name, name)
            self.assertEqual(prop.default, default)
