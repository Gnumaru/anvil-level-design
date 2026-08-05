import json

import bmesh
import bpy
from mathutils import Matrix, Vector

from ..core.uv_projection import derive_transform_from_uvs
from ..operators.mesh_cut.execution import (
    RECONSTRUCTION_MODE_NGONS,
    RECONSTRUCTION_MODE_QUADS,
)
from ..operators.prism_cut.geometry import (
    execute_prism_cut_with_reconstruction,
)
from ..operators.prism_cut.operator import profile_candidate_invalid_message
from ..operators.prism_cut.prism import build_prism_cut_prism
from .base_test import AnvilTestCase
from .helpers import (
    _get_context_override,
    add_uv_layer_box_project,
    create_textured_cube,
    get_context_action_kind,
)


class PrismCutTest(AnvilTestCase):
    """Test concave Prism Cut geometry and reconstruction."""

    def _concave_profile(self):
        return [
            Vector((0.20, -0.50, 0.20)),
            Vector((0.80, -0.50, 0.20)),
            Vector((0.80, -0.50, 0.45)),
            Vector((0.55, -0.50, 0.45)),
            Vector((0.55, -0.50, 0.80)),
            Vector((0.20, -0.50, 0.80)),
        ]

    def _selected_edge_components(self, bm):
        remaining = {
            edge for edge in bm.edges
            if edge.is_valid and edge.select
        }
        components = []
        while remaining:
            first_edge = remaining.pop()
            component = {first_edge}
            pending = [first_edge]
            while pending:
                edge = pending.pop()
                for vertex in edge.verts:
                    connected = {
                        candidate for candidate in vertex.link_edges
                        if candidate in remaining
                    }
                    remaining.difference_update(connected)
                    component.update(connected)
                    pending.extend(connected)
            components.append(component)
        return components

    def _assert_selected_boundary_topology(
            self, bm, expected_component_count, expected_edge_count):
        selected_edges = [
            edge for edge in bm.edges
            if edge.is_valid and edge.select
        ]
        self.assertTrue(
            selected_edges,
            "Prism Cut should leave a selected cut boundary",
        )

        vertex_degrees = {}
        for edge in selected_edges:
            self.assertEqual(
                len(edge.link_faces),
                1,
                "Every selected cut-boundary edge should be open",
            )
            for vertex in edge.verts:
                vertex_degrees[vertex] = vertex_degrees.get(vertex, 0) + 1

        unexpected_degrees = [
            (tuple(vertex.co), degree)
            for vertex, degree in vertex_degrees.items()
            if degree != 2
        ]
        self.assertEqual(
            unexpected_degrees,
            [],
            "Selected cut boundaries should be unbranched closed loops; "
            f"unexpected degrees: {unexpected_degrees}",
        )
        self.assertEqual(
            len(self._selected_edge_components(bm)),
            expected_component_count,
            "Prism Cut should create the expected number of boundary loops",
        )
        if expected_edge_count is not None:
            self.assertEqual(
                len(selected_edges),
                expected_edge_count,
                "Prism Cut should preserve every profile boundary segment",
            )
        return selected_edges

    def _assert_no_loose_geometry(self, bm):
        self.assertEqual(
            [edge for edge in bm.edges if edge.is_valid and not edge.link_faces],
            [],
            "Prism Cut should not leave loose edges",
        )
        self.assertEqual(
            [vertex for vertex in bm.verts
             if vertex.is_valid and not vertex.link_faces],
            [],
            "Prism Cut should not leave loose vertices",
        )

    def _create_trimmed_curved_patch(self, object_name):
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.mesh.primitive_ico_sphere_add(
                subdivisions=3,
                radius=4.0,
            )
        obj = bpy.context.active_object
        obj.name = object_name

        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        affected_face_indices = {
            13,
            119,
            120,
            121,
            122,
            161,
            162,
            163,
            166,
            167,
            168,
            169,
            183,
            184,
            186,
            187,
            190,
            230,
            231,
            233,
            234,
            237,
        }
        kept_faces = {
            face for face in bm.faces
            if face.index in affected_face_indices
        }
        for _ring_index in range(2):
            adjacent_faces = set()
            for face in kept_faces:
                for edge in face.edges:
                    adjacent_faces.update(edge.link_faces)
            kept_faces.update(adjacent_faces)
        bmesh.ops.delete(
            bm,
            geom=[face for face in bm.faces if face not in kept_faces],
            context='FACES',
        )
        return (obj, context_override, bm)

    def _execute_selected_profile_cut(
            self, obj, context_override, profile, depth, local_z,
            reconstruction_mode):
        with bpy.context.temp_override(**context_override):
            success, message = execute_prism_cut_with_reconstruction(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                profile,
                depth,
                local_z,
                reconstruction_mode,
                obj.matrix_world.copy(),
            )
        self.assertTrue(success, message)
        return bmesh.from_edit_mesh(obj.data)

    def _execute_concave_through_cut(self, object_name, reconstruction_mode):
        obj = create_textured_cube(
            object_name,
            1.0,
            1.0,
            use_box_project=True,
        )
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        add_uv_layer_box_project(obj, "UVMap.001", 0.5)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        with bpy.context.temp_override(**context_override):
            success, message = execute_prism_cut_with_reconstruction(
                obj,
                bpy.context.tool_settings,
                bpy.context.scene.level_design_props.pixels_per_meter,
                self._concave_profile(),
                2.0,
                Vector((0.0, 1.0, 0.0)),
                reconstruction_mode,
                obj.matrix_world.copy(),
            )

        self.assertTrue(success, message)
        return (obj, context_override)

    def _assert_concave_through_cut_preserves_geometry(
            self, reconstruction_mode):
        obj, context_override = self._execute_concave_through_cut(
            f"prism_cut_concave_{reconstruction_mode.lower()}",
            reconstruction_mode,
        )
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()

        selected_edges = [edge for edge in bm.edges if edge.select]
        self.assertEqual(
            len(selected_edges),
            12,
            "Two six-edge concave openings should select 12 boundary edges",
        )
        for edge in selected_edges:
            self.assertEqual(
                len(edge.link_faces),
                1,
                "Every selected Prism Cut boundary edge should be open",
            )

        self.assertEqual(
            len([face for face in bm.faces if face.select]),
            0,
            "Prism Cut should deselect reconstructed faces",
        )
        self.assertEqual(
            list(bpy.context.tool_settings.mesh_select_mode),
            [False, True, False],
            "Prism Cut should finish in edge select mode",
        )

        pixels_per_meter = (
            bpy.context.scene.level_design_props.pixels_per_meter
        )
        for face in bm.faces:
            self.assertTrue(face.is_valid)
            for layer, expected_scale in (
                    (bm.loops.layers.uv[0], 1.0),
                    (bm.loops.layers.uv[1], 0.5)):
                transform = derive_transform_from_uvs(
                    face,
                    layer,
                    pixels_per_meter,
                    obj.data,
                )
                self.assertIsNotNone(
                    transform,
                    "Every remaining face should retain projected UVs",
                )
                self.assertAlmostEqual(
                    transform['scale_u'], expected_scale, places=2
                )
                self.assertAlmostEqual(
                    transform['scale_v'], expected_scale, places=2
                )

        if reconstruction_mode == RECONSTRUCTION_MODE_QUADS:
            self.assertFalse(
                any(len(face.verts) > 4 for face in bm.faces),
                "Quad reconstruction should leave only triangles and quads",
            )
        else:
            self.assertTrue(
                any(len(face.verts) > 4 for face in bm.faces),
                "Ngon reconstruction should preserve larger face regions",
            )

        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_prism_cut_concave_profile_through_hole_quads_mode_preserves_geometry(self):
        self._assert_concave_through_cut_preserves_geometry(
            RECONSTRUCTION_MODE_QUADS
        )

    def test_prism_cut_concave_profile_through_hole_ngons_mode_preserves_geometry(self):
        self._assert_concave_through_cut_preserves_geometry(
            RECONSTRUCTION_MODE_NGONS
        )

    def test_prism_cut_concave_profile_through_hole_bridge_creates_tunnel_faces(self):
        obj, context_override = self._execute_concave_through_cut(
            "prism_cut_concave_bridge",
            RECONSTRUCTION_MODE_NGONS,
        )
        bm = bmesh.from_edit_mesh(obj.data)
        existing_faces = set(bm.faces)

        with bpy.context.temp_override(**context_override):
            result = bpy.ops.mesh.bridge_edge_loops()
        self.assertIn('FINISHED', result)

        bm = bmesh.from_edit_mesh(obj.data)
        bridge_faces = set(bm.faces) - existing_faces
        self.assertEqual(
            len(bridge_faces),
            6,
            "Bridge should add one tunnel face for every concave profile edge",
        )
        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_prism_cut_crossing_profile_edges_are_rejected(self):
        crossing_profile = [
            Vector((0.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 1.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((1.0, 0.0, 0.0)),
        ]

        with self.assertRaisesRegex(ValueError, "must not cross or touch"):
            build_prism_cut_prism(
                Matrix.Identity(4),
                crossing_profile,
                1.0,
                Vector((0.0, 1.0, 0.0)),
            )

    def test_prism_cut_candidate_edge_crossing_existing_edge_is_rejected(self):
        profile_vertices = [
            Vector((0.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 1.0)),
            Vector((0.0, 0.0, 1.0)),
        ]
        message = profile_candidate_invalid_message(
            profile_vertices,
            Vector((1.0, 0.0, 0.0)),
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
            Vector((0.0, 1.0, 0.0)),
            False,
        )
        self.assertEqual(message, "Profile edges must not cross")

    def test_prism_cut_concave_profile_classifies_reentrant_area_outside(self):
        prism = build_prism_cut_prism(
            Matrix.Identity(4),
            self._concave_profile(),
            2.0,
            Vector((0.0, 1.0, 0.0)),
        )

        self.assertTrue(prism.point_inside(Vector((0.30, 0.0, 0.60))))
        self.assertTrue(prism.point_inside(Vector((0.70, 0.0, 0.30))))
        self.assertFalse(
            prism.point_inside(Vector((0.70, 0.0, 0.60))),
            "The area beyond the re-entrant corner must remain outside",
        )

    def test_prism_cut_captured_concave_profile_operator_values_replay(self):
        obj = create_textured_cube(
            "prism_cut_captured_operator_values",
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

        profile_json = json.dumps([
            list(vertex) for vertex in self._concave_profile()
        ])
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.prism_cut(
                action_profile_json=profile_json,
                action_depth=2.0,
                action_local_z=(0.0, 1.0, 0.0),
                reconstruction_mode=RECONSTRUCTION_MODE_NGONS,
            )
        self.assertIn('FINISHED', result)
        self.assertEqual(
            get_context_action_kind(),
            'BRIDGE',
            "A replayed through Prism Cut should offer Bridge",
        )

        bm = bmesh.from_edit_mesh(obj.data)
        self.assertEqual(len([edge for edge in bm.edges if edge.select]), 12)
        bmesh.update_edit_mesh(obj.data)
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.data.update()

    def test_prism_cut_concave_profile_finite_depth_stops_before_back_face_preserves_single_front_opening(self):
        obj = create_textured_cube(
            "prism_cut_concave_finite_depth_front_opening",
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

        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            self._concave_profile(),
            0.75,
            Vector((0.0, 1.0, 0.0)),
            RECONSTRUCTION_MODE_NGONS,
        )
        selected_edges = self._assert_selected_boundary_topology(
            bm,
            1,
            6,
        )
        for edge in selected_edges:
            for vertex in edge.verts:
                self.assertAlmostEqual(
                    vertex.co.y,
                    0.0,
                    places=5,
                    msg="A finite cut ending inside the cube should only "
                    "open the front face",
                )
        self._assert_no_loose_geometry(bm)

    def test_prism_cut_concave_profile_tangent_outside_cube_preserves_original_mesh(self):
        obj = create_textured_cube(
            "prism_cut_concave_tangent_outside_cube",
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

        tangent_profile = [
            vertex + Vector((0.80, 0.0, 0.0))
            for vertex in self._concave_profile()
        ]
        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            tangent_profile,
            2.0,
            Vector((0.0, 1.0, 0.0)),
            RECONSTRUCTION_MODE_NGONS,
        )
        self.assertEqual(len(bm.verts), 8)
        self.assertEqual(len(bm.edges), 12)
        self.assertEqual(len(bm.faces), 6)
        self.assertEqual(
            [edge for edge in bm.edges if edge.select],
            [],
            "A cutter whose volume is wholly outside the cube should not "
            "create a cut boundary",
        )
        self._assert_no_loose_geometry(bm)

    def test_prism_cut_concave_profile_opening_through_cube_side_preserves_unbranched_boundary(self):
        obj = create_textured_cube(
            "prism_cut_concave_opening_through_cube_side",
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

        side_opening_profile = [
            vertex + Vector((0.50, 0.0, 0.0))
            for vertex in self._concave_profile()
        ]
        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            side_opening_profile,
            2.0,
            Vector((0.0, 1.0, 0.0)),
            RECONSTRUCTION_MODE_NGONS,
        )
        self._assert_selected_boundary_topology(bm, 1, None)
        self._assert_no_loose_geometry(bm)

    def test_prism_cut_concave_narrow_profile_at_large_local_coordinates_preserves_two_boundary_loops(self):
        obj = create_textured_cube(
            "prism_cut_concave_large_local_coordinates",
            1.0,
            1.0,
            use_box_project=True,
        )
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')

        coordinate_offset = Vector((100000.0, 0.0, 0.0))
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for vertex in bm.verts:
            vertex.co += coordinate_offset
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        narrow_profile = [
            Vector((100000.20, -0.50, 0.20)),
            Vector((100000.80, -0.50, 0.20)),
            Vector((100000.80, -0.50, 0.45)),
            Vector((100000.55, -0.50, 0.45)),
            Vector((100000.55, -0.50, 0.50)),
            Vector((100000.20, -0.50, 0.50)),
        ]
        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            narrow_profile,
            2.0,
            Vector((0.0, 1.0, 0.0)),
            RECONSTRUCTION_MODE_NGONS,
        )
        self._assert_selected_boundary_topology(bm, 2, 12)
        self._assert_no_loose_geometry(bm)

    def test_prism_cut_concave_narrow_profile_at_origin_preserves_two_boundary_loops(self):
        obj = create_textured_cube(
            "prism_cut_concave_narrow_profile_at_origin",
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

        narrow_profile = [
            Vector((0.20, -0.50, 0.20)),
            Vector((0.80, -0.50, 0.20)),
            Vector((0.80, -0.50, 0.45)),
            Vector((0.55, -0.50, 0.45)),
            Vector((0.55, -0.50, 0.50)),
            Vector((0.20, -0.50, 0.50)),
        ]
        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            narrow_profile,
            2.0,
            Vector((0.0, 1.0, 0.0)),
            RECONSTRUCTION_MODE_NGONS,
        )
        self._assert_selected_boundary_topology(bm, 2, 12)
        self._assert_no_loose_geometry(bm)

    def test_prism_cut_concave_profile_through_triangulated_curved_patches_preserves_two_bridgeable_boundary_loops(self):
        obj, context_override, bm = self._create_trimmed_curved_patch(
            "prism_cut_concave_triangulated_curved_patches"
        )
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        profile = [
            Vector((3.25511681101733, -2.0, 1.0)),
            Vector((3.74558999624644, -1.0, 2.0)),
            Vector((4.94426661376765, 0.0, 0.0)),
            Vector((3.98165780701048, -1.0, 1.0)),
        ]
        profile_json = json.dumps([list(vertex) for vertex in profile])
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.prism_cut(
                action_profile_json=profile_json,
                action_depth=-2000.0,
                action_local_z=(
                    0.794655051,
                    -0.577349472,
                    0.187592478,
                ),
                reconstruction_mode=RECONSTRUCTION_MODE_QUADS,
            )

        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        selected_edges = [edge for edge in bm.edges if edge.select]
        selected_vertex_degrees = {}
        for edge in selected_edges:
            self.assertEqual(
                len(edge.link_faces),
                1,
                "Every selected cut edge should be an open boundary edge",
            )
            for vertex in edge.verts:
                selected_vertex_degrees[vertex] = (
                    selected_vertex_degrees.get(vertex, 0) + 1
                )
        unexpected_degrees = [
            (tuple(vertex.co), degree)
            for vertex, degree in selected_vertex_degrees.items()
            if degree != 2
        ]
        self.assertTrue(
            all(
                degree == 2
                for degree in selected_vertex_degrees.values()
            ),
            "Every selected cut boundary should be an unbranched loop; "
            f"unexpected degrees: {unexpected_degrees}",
        )
        self.assertEqual(
            len(self._selected_edge_components(bm)),
            2,
            "The curved through-cut should produce exactly two boundary loops",
        )
        self._assert_no_loose_geometry(bm)
        self.assertEqual(
            get_context_action_kind(),
            'BRIDGE',
            "A through Prism Cut should offer Bridge for its two openings",
        )

    def test_prism_cut_cap_path_suppression_on_curved_patch_preserves_disconnected_face_outside_finite_cap(self):
        obj, context_override, bm = self._create_trimmed_curved_patch(
            "prism_cut_cap_suppression_disconnected_face"
        )
        profile = [
            Vector((3.25511681101733, -2.0, 1.0)),
            Vector((3.74558999624644, -1.0, 2.0)),
            Vector((4.94426661376765, 0.0, 0.0)),
            Vector((3.98165780701048, -1.0, 1.0)),
        ]
        local_z = Vector((
            0.794655051,
            -0.577349472,
            0.187592478,
        )).normalized()

        marker_center = (
            profile[1] + profile[2] + profile[3]
        ) / 3.0
        marker_tangent = (profile[2] - profile[1]).normalized()
        marker_half_width = 0.04
        marker_half_depth = 0.30
        # This disconnected quad lies within the profile's side boundaries
        # and straddles only the drawn cap. Its +local_z half is outside the
        # finite prism and must remain even when the curved component causes
        # the drawn-cap path to be suppressed for that component.
        marker_vertices = [
            bm.verts.new(
                marker_center
                - marker_tangent * marker_half_width
                + local_z * marker_half_depth
            ),
            bm.verts.new(
                marker_center
                - marker_tangent * marker_half_width
                - local_z * marker_half_depth
            ),
            bm.verts.new(
                marker_center
                + marker_tangent * marker_half_width
                - local_z * marker_half_depth
            ),
            bm.verts.new(
                marker_center
                + marker_tangent * marker_half_width
                + local_z * marker_half_depth
            ),
        ]
        bm.faces.new(marker_vertices)
        bm.normal_update()
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        bm = self._execute_selected_profile_cut(
            obj,
            context_override,
            profile,
            -2000.0,
            local_z,
            RECONSTRUCTION_MODE_QUADS,
        )

        surviving_marker_vertices = []
        for vertex in bm.verts:
            offset = vertex.co - marker_center
            depth_coordinate = offset.dot(local_z)
            tangent_coordinate = offset.dot(marker_tangent)
            perpendicular = (
                offset
                - local_z * depth_coordinate
                - marker_tangent * tangent_coordinate
            )
            if (
                    abs(depth_coordinate) <= marker_half_depth + 0.001
                    and abs(tangent_coordinate) <= marker_half_width + 0.001
                    and perpendicular.length <= 0.001):
                surviving_marker_vertices.append((
                    vertex,
                    depth_coordinate,
                ))

        surviving_outside_vertices = [
            vertex for vertex, depth_coordinate in surviving_marker_vertices
            if depth_coordinate > marker_half_depth * 0.5
        ]
        surviving_inside_vertices = [
            vertex for vertex, depth_coordinate in surviving_marker_vertices
            if depth_coordinate < -marker_half_depth * 0.5
        ]
        self.assertEqual(
            len(surviving_outside_vertices),
            2,
            "Suppressing a cap path for the curved patch must not make that "
            "cap unbounded on a disconnected face",
        )
        self.assertEqual(
            surviving_inside_vertices,
            [],
            "The part of the disconnected face inside the finite prism "
            "should still be removed",
        )
        self._assert_no_loose_geometry(bm)

    def _assert_concave_t_profile_into_triangulated_sphere_bisects_edges(
            self, reconstruction_mode, object_name):
        context_override = _get_context_override()
        with bpy.context.temp_override(**context_override):
            bpy.ops.mesh.primitive_ico_sphere_add(
                subdivisions=3,
                radius=4.0,
            )
        obj = bpy.context.active_object
        obj.name = object_name

        with bpy.context.temp_override(**context_override):
            bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {'FACE'}
        for face in bm.faces:
            face.select = True
        bmesh.update_edit_mesh(obj.data)

        profile = [
            Vector((-2.10, -3.35, 1.55)),
            Vector((2.10, -3.35, 1.55)),
            Vector((2.10, -3.35, 0.55)),
            Vector((0.62, -3.35, 0.55)),
            Vector((0.62, -3.35, -1.75)),
            Vector((-0.62, -3.35, -1.75)),
            Vector((-0.62, -3.35, 0.55)),
            Vector((-2.10, -3.35, 0.55)),
        ]
        profile_json = json.dumps([list(vertex) for vertex in profile])
        with bpy.context.temp_override(**context_override):
            result = bpy.ops.leveldesign.prism_cut(
                action_profile_json=profile_json,
                action_depth=1.6,
                action_local_z=(0.0, 1.0, 0.0),
                reconstruction_mode=reconstruction_mode,
            )

        self.assertIn('FINISHED', result)
        bm = bmesh.from_edit_mesh(obj.data)
        selected_vertices = {
            vertex
            for edge in bm.edges
            if edge.select
            for vertex in edge.verts
        }
        self.assertTrue(
            selected_vertices,
            "The concave T profile should produce a selected cut boundary",
        )
        self._assert_selected_boundary_topology(bm, 2, None)
        self._assert_no_loose_geometry(bm)

        tolerance = 0.0001
        unbisected_edges = []
        for vertex in selected_vertices:
            for edge in bm.edges:
                if vertex in edge.verts:
                    continue
                edge_start = edge.verts[0].co
                edge_vector = edge.verts[1].co - edge_start
                edge_length_squared = edge_vector.length_squared
                if edge_length_squared <= tolerance * tolerance:
                    continue
                edge_fraction = (
                    (vertex.co - edge_start).dot(edge_vector)
                    / edge_length_squared
                )
                endpoint_margin = tolerance / edge_vector.length
                if not (
                        endpoint_margin < edge_fraction
                        < 1.0 - endpoint_margin):
                    continue
                closest_point = edge_start + edge_vector * edge_fraction
                if (closest_point - vertex.co).length <= tolerance:
                    unbisected_edges.append((
                        tuple(vertex.co),
                        tuple(edge.verts[0].co),
                        tuple(edge.verts[1].co),
                    ))

        self.assertEqual(
            unbisected_edges,
            [],
            "Every cut-boundary vertex on an existing edge should bisect "
            f"that edge; unbisected intersections: {unbisected_edges}",
        )

    def test_prism_cut_concave_t_profile_into_triangulated_sphere_bisects_crossed_edges(self):
        self._assert_concave_t_profile_into_triangulated_sphere_bisects_edges(
            RECONSTRUCTION_MODE_QUADS,
            "prism_cut_concave_t_triangulated_sphere_quads",
        )

    def test_prism_cut_concave_t_profile_into_triangulated_sphere_ngons_mode_bisects_crossed_edges(self):
        self._assert_concave_t_profile_into_triangulated_sphere_bisects_edges(
            RECONSTRUCTION_MODE_NGONS,
            "prism_cut_concave_t_triangulated_sphere_ngons",
        )
