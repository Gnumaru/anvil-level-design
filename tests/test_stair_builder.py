import random

import bmesh
import bpy
from mathutils import Vector

from ..handlers import set_active_image
from ..operators.texture_apply import _dispatch_set_uv_from_other_face
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
    execute_stair_builder_object_mode,
)
from .base_test import AnvilTestCase
from .helpers import TEXTURE_PATH


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


class StairBuilderUvTest(AnvilTestCase):
    """Test semantic UV origins on Stair Builder faces."""

    TOLERANCE = 1e-5

    def _axis_span(self, face, axis_index):
        values = [vertex.co[axis_index] for vertex in face.verts]
        return max(values) - min(values)

    def _assert_face_edge_is_texture_bottom(self, face, uv_layer, predicate):
        loops = list(face.loops)
        for index, loop in enumerate(loops):
            following = loops[(index + 1) % len(loops)]
            if not predicate(loop.vert.co) or not predicate(following.vert.co):
                continue
            first_uv = loop[uv_layer].uv
            second_uv = following[uv_layer].uv
            self.assertAlmostEqual(first_uv.y, 0.0, delta=self.TOLERANCE)
            self.assertAlmostEqual(second_uv.y, 0.0, delta=self.TOLERANCE)
            self.assertGreater(
                max(item[uv_layer].uv.y for item in loops),
                self.TOLERANCE,
            )
            return
        self.fail("Expected geometry edge was not found on stair face")

    def _assert_face_uv_matches_alt_click(
            self, source_face, target_face, uv_layer, ppm, me, obj_matrix):
        actual_uvs = [loop[uv_layer].uv.copy() for loop in target_face.loops]
        result = _dispatch_set_uv_from_other_face(
            source_face,
            target_face,
            uv_layer,
            ppm,
            me,
            obj_matrix,
        )
        self.assertTrue(result)
        expected_uvs = [loop[uv_layer].uv.copy() for loop in target_face.loops]
        for actual, expected in zip(actual_uvs, expected_uvs):
            for axis in range(2):
                difference = actual[axis] - expected[axis]
                self.assertAlmostEqual(
                    difference,
                    round(difference),
                    delta=self.TOLERANCE,
                )

    def _build_textured_stair_object(
            self, border_alignment, underside, left_border, right_border,
            uv_random_seed):
        image = bpy.data.images.load(TEXTURE_PATH, check_existing=True)
        set_active_image(image)
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        result = execute_stair_builder_object_mode(
            Vector((0.0, 0.0, 0.0)),
            Vector((3.0, -2.0, 0.0)),
            1.5,
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, -1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            ORIENTATION_AXIS_1_POSITIVE,
            SIZING_STEP_COUNT,
            3,
            0.1,
            HEIGHT_EVEN,
            TERMINATION_TOP_TREAD,
            False,
            True,
            True,
            False,
            left_border,
            right_border,
            0.25,
            border_alignment,
            underside,
            uv_random_seed,
            ppm,
            "",
        )
        self.assertTrue(result[0], result[1])
        return bpy.context.active_object

    def test_stair_builder_risers_treads_and_border_tops_align_to_texture_bottom(self):
        obj = self._build_textured_stair_object(
            BORDER_ALIGN_STEP_TIPS,
            UNDERSIDE_NONE,
            True,
            True,
            9384,
        )

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.active
            self.assertIsNotNone(uv_layer)

            risers = [
                face for face in bm.faces
                if face.normal.x < -0.99
                and self._axis_span(face, 0) < self.TOLERANCE
                and self._axis_span(face, 1) > 1.0
            ]
            treads = [
                face for face in bm.faces
                if face.normal.z > 0.99
                and self._axis_span(face, 1) > 1.0
            ]
            border_tops = [
                face for face in bm.faces
                if face.normal.z > 0.1
                and 0.2 < self._axis_span(face, 1) < 0.3
                and self._axis_span(face, 0) > self.TOLERANCE
            ]
            self.assertEqual(len(risers), 3)
            self.assertEqual(len(treads), 3)
            self.assertEqual(len(border_tops), 4)

            for face in risers:
                bottom_height = min(vertex.co.z for vertex in face.verts)
                self._assert_face_edge_is_texture_bottom(
                    face,
                    uv_layer,
                    lambda coordinate: abs(coordinate.z - bottom_height)
                    < self.TOLERANCE,
                )
            for face in treads:
                riser_run = min(vertex.co.x for vertex in face.verts)
                self._assert_face_edge_is_texture_bottom(
                    face,
                    uv_layer,
                    lambda coordinate: abs(coordinate.x - riser_run)
                    < self.TOLERANCE,
                )
            for face in border_tops:
                average_width = sum(vertex.co.y for vertex in face.verts) / len(
                    face.verts
                )
                outside_width = 0.0 if average_width > -1.0 else -2.0
                self._assert_face_edge_is_texture_bottom(
                    face,
                    uv_layer,
                    lambda coordinate: abs(coordinate.y - outside_width)
                    < self.TOLERANCE,
                )
        finally:
            bm.free()

    def test_stair_builder_riser_bottom_border_tops_align_outside_edges_to_texture_bottom(self):
        obj = self._build_textured_stair_object(
            BORDER_ALIGN_RISER_BOTTOMS,
            UNDERSIDE_NONE,
            True,
            True,
            2271,
        )
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.active
            self.assertIsNotNone(uv_layer)
            border_tops = [
                face for face in bm.faces
                if face.normal.z > 0.1
                and 0.2 < self._axis_span(face, 1) < 0.3
                and self._axis_span(face, 0) > self.TOLERANCE
            ]
            self.assertEqual(len(border_tops), 2)
            for face in border_tops:
                average_width = sum(vertex.co.y for vertex in face.verts) / len(
                    face.verts
                )
                outside_width = 0.0 if average_width > -1.0 else -2.0
                self._assert_face_edge_is_texture_bottom(
                    face,
                    uv_layer,
                    lambda coordinate: abs(coordinate.y - outside_width)
                    < self.TOLERANCE,
                )
        finally:
            bm.free()

    def test_stair_builder_aligned_faces_receive_seeded_random_x_offsets(self):
        uv_random_seed = 1984
        obj = self._build_textured_stair_object(
            BORDER_ALIGN_STEP_TIPS,
            UNDERSIDE_NONE,
            True,
            True,
            uv_random_seed,
        )
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.active
            self.assertIsNotNone(uv_layer)
            aligned_faces = [
                face for face in bm.faces
                if (
                    face.normal.x < -0.99
                    and self._axis_span(face, 0) < self.TOLERANCE
                    and self._axis_span(face, 1) > 1.0
                )
                or (
                    face.normal.z > 0.99
                    and self._axis_span(face, 1) > 1.0
                )
                or (
                    face.normal.z > 0.1
                    and 0.2 < self._axis_span(face, 1) < 0.3
                    and self._axis_span(face, 0) > self.TOLERANCE
                )
            ]
            self.assertEqual(len(aligned_faces), 10)

            generator = random.Random(uv_random_seed)
            expected_offsets = [
                generator.random()
                for _face in aligned_faces
            ]
            actual_offsets = [
                list(face.loops)[0][uv_layer].uv.x % 1.0
                for face in aligned_faces
            ]
            for actual, expected in zip(actual_offsets, expected_offsets):
                self.assertAlmostEqual(
                    actual,
                    expected,
                    delta=self.TOLERANCE,
                )
            self.assertGreater(
                len({round(offset, 4) for offset in actual_offsets}),
                1,
            )
        finally:
            bm.free()

    def test_stair_builder_riser_bottom_border_side_caps_tile_from_each_riser(self):
        obj = self._build_textured_stair_object(
            BORDER_ALIGN_RISER_BOTTOMS,
            UNDERSIDE_NONE,
            True,
            True,
            4132,
        )
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.active
            self.assertIsNotNone(uv_layer)
            risers = [
                face for face in bm.faces
                if face.normal.x < -0.99
                and self._axis_span(face, 0) < self.TOLERANCE
                and self._axis_span(face, 1) > 1.0
            ]
            side_caps = [
                face for face in bm.faces
                if abs(face.normal.y) > 0.99
                and self._axis_span(face, 1) < self.TOLERANCE
                and 0.2 < abs(next(iter(face.verts)).co.y) < 1.8
            ]
            self.assertEqual(len(risers), 3)
            self.assertEqual(len(side_caps), 6)
            for side_cap in side_caps:
                riser_run = min(vertex.co.x for vertex in side_cap.verts)
                matching_risers = [
                    face for face in risers
                    if all(
                        abs(vertex.co.x - riser_run) < self.TOLERANCE
                        for vertex in face.verts
                    )
                ]
                self.assertEqual(len(matching_risers), 1)
                self._assert_face_uv_matches_alt_click(
                    matching_risers[0],
                    side_cap,
                    uv_layer,
                    ppm,
                    obj.data,
                    obj.matrix_world,
                )
        finally:
            bm.free()

    def test_stair_builder_step_tip_border_inside_triangles_tile_from_border_tops(self):
        obj = self._build_textured_stair_object(
            BORDER_ALIGN_STEP_TIPS,
            UNDERSIDE_NONE,
            True,
            True,
            7319,
        )
        ppm = bpy.context.scene.level_design_props.pixels_per_meter
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        try:
            bm.normal_update()
            uv_layer = bm.loops.layers.uv.active
            self.assertIsNotNone(uv_layer)
            border_tops = [
                face for face in bm.faces
                if face.normal.z > 0.1
                and 0.2 < self._axis_span(face, 1) < 0.3
                and self._axis_span(face, 0) > self.TOLERANCE
            ]
            inside_triangles = [
                face for face in bm.faces
                if len(face.verts) == 3
                and abs(face.normal.y) > 0.99
                and self._axis_span(face, 1) < self.TOLERANCE
                and 0.2 < abs(next(iter(face.verts)).co.y) < 1.8
            ]
            self.assertEqual(len(border_tops), 4)
            self.assertEqual(len(inside_triangles), 4)
            for inside_triangle in inside_triangles:
                run_midpoint = sum(
                    vertex.co.x for vertex in inside_triangle.verts
                ) / len(inside_triangle.verts)
                side_y = next(iter(inside_triangle.verts)).co.y
                matching_tops = [
                    face for face in border_tops
                    if min(vertex.co.x for vertex in face.verts)
                    - self.TOLERANCE <= run_midpoint
                    <= max(vertex.co.x for vertex in face.verts)
                    + self.TOLERANCE
                    and min(vertex.co.y for vertex in face.verts)
                    - self.TOLERANCE <= side_y
                    <= max(vertex.co.y for vertex in face.verts)
                    + self.TOLERANCE
                ]
                self.assertEqual(len(matching_tops), 1)
                self._assert_face_uv_matches_alt_click(
                    matching_tops[0],
                    inside_triangle,
                    uv_layer,
                    ppm,
                    obj.data,
                    obj.matrix_world,
                )
        finally:
            bm.free()

    def test_stair_builder_straight_slope_sides_align_texture_bottom_to_long_diagonal(self):
        for borders_enabled in (False, True):
            with self.subTest(borders_enabled=borders_enabled):
                obj = self._build_textured_stair_object(
                    BORDER_ALIGN_STEP_TIPS,
                    UNDERSIDE_SLOPED,
                    borders_enabled,
                    borders_enabled,
                    5926,
                )
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                try:
                    bm.normal_update()
                    uv_layer = bm.loops.layers.uv.active
                    self.assertIsNotNone(uv_layer)
                    side_faces = [
                        face for face in bm.faces
                        if abs(face.normal.y) > 0.99
                        and self._axis_span(face, 1) < self.TOLERANCE
                        and self._axis_span(face, 0) > 2.9
                    ]
                    self.assertEqual(len(side_faces), 2)
                    for side_face in side_faces:
                        loops = list(side_face.loops)
                        diagonal_edges = []
                        for index, loop in enumerate(loops):
                            following = loops[(index + 1) % len(loops)]
                            run_span = abs(
                                loop.vert.co.x - following.vert.co.x
                            )
                            height_span = abs(
                                loop.vert.co.z - following.vert.co.z
                            )
                            if run_span > self.TOLERANCE \
                                    and height_span > self.TOLERANCE:
                                diagonal_edges.append((
                                    run_span,
                                    (loop.vert.co.z + following.vert.co.z)
                                    * 0.5,
                                    loop,
                                    following,
                                ))
                        self.assertTrue(diagonal_edges)
                        _run_span, _average_height, first, second = max(
                            diagonal_edges,
                            key=lambda edge: (edge[0], -edge[1]),
                        )
                        self.assertAlmostEqual(
                            first[uv_layer].uv.y,
                            0.0,
                            delta=self.TOLERANCE,
                        )
                        self.assertAlmostEqual(
                            second[uv_layer].uv.y,
                            0.0,
                            delta=self.TOLERANCE,
                        )
                finally:
                    bm.free()
