import bmesh
import bpy
from mathutils import Vector

from ..operators.stair_builder.geometry import (
    BORDER_ALIGN_RISER_BOTTOMS,
    BORDER_ALIGN_STEP_TIPS,
    HEIGHT_EVEN,
    ORIENTATION_AXIS_1_POSITIVE,
    SIZING_STEP_COUNT,
    SIZING_STEP_HEIGHT,
    TERMINATION_DESTINATION,
    TERMINATION_TOP_TREAD,
    UNDERSIDE_NONE,
    UNDERSIDE_SLOPED,
    UNDERSIDE_SOLID,
    _create_bmesh_geometry,
    _mesh_data_from_parameters,
)
from .base_test import AnvilTestCase


class StairBuilderBorderFacesTest(AnvilTestCase):
    """Test that stair borders retain every exposed side polygon."""

    BORDER_INNER_Y = -0.25
    TOLERANCE = 1e-6

    def _build_left_border_data(
            self, step_count, border_alignment, termination,
            include_final_riser, underside):
        return _mesh_data_from_parameters(
            Vector((0.0, 0.0, 0.0)),
            Vector((4.0, -2.0, 0.0)),
            2.0,
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, -1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            ORIENTATION_AXIS_1_POSITIVE,
            SIZING_STEP_COUNT,
            step_count,
            0.25,
            HEIGHT_EVEN,
            termination,
            include_final_riser,
            True,
            True,
            True,
            True,
            False,
            0.25,
            border_alignment,
            underside,
        )

    def _build_left_border_bmesh(self, step_count, border_alignment):
        data = self._build_left_border_data(
            step_count,
            border_alignment,
            TERMINATION_TOP_TREAD,
            True,
            UNDERSIDE_SOLID,
        )
        bm = bmesh.new()
        _vertices, faces = _create_bmesh_geometry(
            bm,
            data.vertices,
            data.faces,
        )
        bm.normal_update()
        return (bm, faces)

    def _left_border_inner_faces(self, faces, normal_y_sign):
        return [
            face
            for face in faces
            if all(
                abs(vertex.co.y - self.BORDER_INNER_Y) < self.TOLERANCE
                for vertex in face.verts
            )
            and face.normal.y * normal_y_sign > 1.0 - self.TOLERANCE
        ]

    def test_stair_builder_riser_bottom_borders_preserve_every_outside_face(self):
        for step_count in range(2, 13):
            with self.subTest(step_count=step_count):
                bm, faces = self._build_left_border_bmesh(
                    step_count,
                    BORDER_ALIGN_RISER_BOTTOMS,
                )
                try:
                    outside_faces = self._left_border_inner_faces(faces, 1.0)
                    self.assertEqual(len(outside_faces), step_count)
                finally:
                    bm.free()

    def test_stair_builder_step_tip_borders_preserve_every_inside_face(self):
        for step_count in range(2, 13):
            with self.subTest(step_count=step_count):
                bm, faces = self._build_left_border_bmesh(
                    step_count,
                    BORDER_ALIGN_STEP_TIPS,
                )
                try:
                    inside_faces = self._left_border_inner_faces(faces, -1.0)
                    self.assertEqual(len(inside_faces), step_count - 1)
                finally:
                    bm.free()

    def test_stair_builder_destination_floor_borders_reach_box_top_for_every_alignment(self):
        for border_alignment in (
                BORDER_ALIGN_RISER_BOTTOMS,
                BORDER_ALIGN_STEP_TIPS):
            for underside in (
                    UNDERSIDE_NONE,
                    UNDERSIDE_SOLID,
                    UNDERSIDE_SLOPED):
                with self.subTest(
                        border_alignment=border_alignment,
                        underside=underside):
                    data = self._build_left_border_data(
                        4,
                        border_alignment,
                        TERMINATION_DESTINATION,
                        False,
                        underside,
                    )
                    top_end_vertices = [
                        vertex
                        for vertex in data.vertices
                        if abs(vertex.x - 4.0) < self.TOLERANCE
                        and abs(vertex.z - 2.0) < self.TOLERANCE
                    ]
                    self.assertTrue(any(
                        abs(vertex.y) < self.TOLERANCE
                        for vertex in top_end_vertices
                    ))
                    self.assertTrue(any(
                        abs(vertex.y - self.BORDER_INNER_Y) < self.TOLERANCE
                        for vertex in top_end_vertices
                    ))


class StairBuilderOperatorPropertiesTest(AnvilTestCase):
    """Test Stair Builder action-panel property configuration."""

    def test_stair_builder_action_panel_properties_use_requested_defaults(self):
        properties = bpy.ops.leveldesign.stair_builder.get_rna_type().properties
        expected = {
            "sizing_mode": ("Fill By", SIZING_STEP_HEIGHT),
            "target_step_height": ("Target Riser Height", 0.1),
            "height_distribution": ("Height Fit", HEIGHT_EVEN),
            "termination": ("Top Of Box", TERMINATION_TOP_TREAD),
            "include_final_riser": ("Create Final Riser", False),
            "left_side": ("Left Side", True),
            "right_side": ("Right Side", True),
            "back": ("Back", False),
            "left_border": ("Left Border", False),
            "right_border": ("Right Border", False),
            "border_width": ("Border Width", 0.2),
            "border_alignment": (
                "Border Alignment",
                BORDER_ALIGN_STEP_TIPS,
            ),
            "underside": ("Underside", UNDERSIDE_NONE),
        }
        for identifier, (name, default) in expected.items():
            prop = properties[identifier]
            self.assertEqual(prop.name, name)
            if isinstance(default, float):
                self.assertAlmostEqual(prop.default, default)
            else:
                self.assertEqual(prop.default, default)
