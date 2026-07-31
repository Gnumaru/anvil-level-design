import bmesh

from .base_test import AnvilTestCase
from ..hotspot_mapping.json_storage import (
    TILING_NONE,
    TILING_VERTICAL,
    TILING_HORIZONTAL,
    add_texture_as_hotspottable,
    add_line,
    get_texture_hotspots,
    toggle_cell_tiling,
)
from ..operators.hotspot_apply import (
    apply_hotspot_uvs,
    calculate_tiling_repeat_count,
    find_best_hotspot,
)


IMAGE_WIDTH = 100
IMAGE_HEIGHT = 100
PIXELS_PER_METER = 100


def _create_upward_wall_island(width, height):
    """Create a wall quad whose positive UV V direction is world-up."""
    bm = bmesh.new()
    verts = [
        bm.verts.new((0.0, 0.0, 0.0)),
        bm.verts.new((width, 0.0, 0.0)),
        bm.verts.new((width, 0.0, height)),
        bm.verts.new((0.0, 0.0, height)),
    ]
    face = bm.faces.new(verts)
    uv_layer = bm.loops.layers.uv.verify()

    for loop in face.loops:
        loop[uv_layer].uv = (loop.vert.co.x, loop.vert.co.z)

    bm.normal_update()
    return bm, [face], uv_layer


def _hotspot(identifier, width, height, orientation, tiling_type):
    return {
        'id': identifier,
        'x': 0,
        'y': 0,
        'width': width,
        'height': height,
        'orientation_type': orientation,
        'tiling': tiling_type,
    }


def _find_for_wall(width, height, hotspots, face_type, size_weight):
    bm, island, uv_layer = _create_upward_wall_island(width, height)
    try:
        return find_best_hotspot(
            width / height, hotspots, IMAGE_WIDTH, IMAGE_HEIGHT,
            face_type, island, uv_layer, width * height,
            PIXELS_PER_METER, size_weight
        )
    finally:
        bm.free()


def _uv_spans(island, uv_layer):
    u_values = []
    v_values = []
    for face in island:
        for loop in face.loops:
            u_values.append(loop[uv_layer].uv.x)
            v_values.append(loop[uv_layer].uv.y)
    return max(u_values) - min(u_values), max(v_values) - min(v_values)


class HotspotTilingStorageTest(AnvilTestCase):

    def test_hotspot_tiling_defaults_off_and_directions_are_mutually_exclusive(self):
        texture_name = "tiling_mutual_exclusion"
        add_texture_as_hotspottable(
            texture_name, IMAGE_WIDTH, IMAGE_HEIGHT
        )
        cell_key = "0_0_100_100"

        hotspots = get_texture_hotspots(texture_name)
        self.assertEqual(hotspots[0]['tiling'], TILING_NONE)

        result = toggle_cell_tiling(
            texture_name, cell_key, TILING_VERTICAL
        )
        self.assertEqual(result, TILING_VERTICAL)
        self.assertEqual(
            get_texture_hotspots(texture_name)[0]['tiling'],
            TILING_VERTICAL
        )

        result = toggle_cell_tiling(
            texture_name, cell_key, TILING_HORIZONTAL
        )
        self.assertEqual(result, TILING_HORIZONTAL)
        self.assertEqual(
            get_texture_hotspots(texture_name)[0]['tiling'],
            TILING_HORIZONTAL
        )

        result = toggle_cell_tiling(
            texture_name, cell_key, TILING_HORIZONTAL
        )
        self.assertEqual(result, TILING_NONE)
        self.assertEqual(
            get_texture_hotspots(texture_name)[0]['tiling'], TILING_NONE
        )

    def test_hotspot_tiling_line_splits_preserve_only_eligible_cells(self):
        texture_name = "tiling_split_inheritance"
        add_texture_as_hotspottable(
            texture_name, IMAGE_WIDTH, IMAGE_HEIGHT
        )
        toggle_cell_tiling(
            texture_name, "0_0_100_100", TILING_VERTICAL
        )

        add_line(texture_name, "v", 50, 0, IMAGE_HEIGHT)
        vertically_split = get_texture_hotspots(texture_name)
        self.assertEqual(len(vertically_split), 2)
        self.assertTrue(all(
            hotspot['tiling'] == TILING_VERTICAL
            for hotspot in vertically_split
        ))

        add_line(texture_name, "h", 50, 0, 50)
        partially_split = get_texture_hotspots(texture_name)
        left_cells = [
            hotspot for hotspot in partially_split
            if hotspot['x'] == 0
        ]
        right_cells = [
            hotspot for hotspot in partially_split
            if hotspot['x'] == 50
        ]
        self.assertTrue(all(
            hotspot['tiling'] == TILING_NONE
            for hotspot in left_cells
        ))
        self.assertEqual(len(right_cells), 1)
        self.assertEqual(right_cells[0]['tiling'], TILING_VERTICAL)

    def test_horizontal_hotspot_tiling_line_splits_preserve_only_eligible_cells(self):
        texture_name = "horizontal_tiling_split_inheritance"
        add_texture_as_hotspottable(
            texture_name, IMAGE_WIDTH, IMAGE_HEIGHT
        )
        toggle_cell_tiling(
            texture_name, "0_0_100_100", TILING_HORIZONTAL
        )

        add_line(texture_name, "h", 50, 0, IMAGE_WIDTH)
        horizontally_split = get_texture_hotspots(texture_name)
        self.assertEqual(len(horizontally_split), 2)
        self.assertTrue(all(
            hotspot['tiling'] == TILING_HORIZONTAL
            for hotspot in horizontally_split
        ))

        add_line(texture_name, "v", 50, 0, 50)
        partially_split = get_texture_hotspots(texture_name)
        top_cells = [
            hotspot for hotspot in partially_split
            if hotspot['y'] == 0
        ]
        bottom_cells = [
            hotspot for hotspot in partially_split
            if hotspot['y'] == 50
        ]
        self.assertTrue(all(
            hotspot['tiling'] == TILING_NONE
            for hotspot in top_cells
        ))
        self.assertEqual(len(bottom_cells), 1)
        self.assertEqual(bottom_cells[0]['tiling'], TILING_HORIZONTAL)


class HotspotTilingUvTest(AnvilTestCase):

    def test_vertical_hotspot_tiling_applies_partial_single_and_multiple_tiles(self):
        hotspot = _hotspot(
            "vertical", 25, 100, "Any", TILING_VERTICAL
        )
        for repeat_count in (0.6, 1.0, 3.0):
            with self.subTest(repeat_count=repeat_count):
                bm, island, uv_layer = _create_upward_wall_island(
                    0.25, repeat_count
                )
                try:
                    calculated = calculate_tiling_repeat_count(
                        0.25 / repeat_count, hotspot,
                        IMAGE_WIDTH, IMAGE_HEIGHT, 0
                    )
                    self.assertAlmostEqual(calculated, repeat_count)

                    apply_hotspot_uvs(
                        island, uv_layer, hotspot,
                        IMAGE_WIDTH, IMAGE_HEIGHT, 0
                    )
                    u_span, v_span = _uv_spans(island, uv_layer)
                    self.assertAlmostEqual(u_span, 0.25)
                    self.assertAlmostEqual(v_span, repeat_count)
                finally:
                    bm.free()

    def test_horizontal_hotspot_tiling_applies_partial_single_and_multiple_tiles(self):
        hotspot = _hotspot(
            "horizontal", 100, 25, "Any", TILING_HORIZONTAL
        )
        for repeat_count in (0.6, 1.0, 3.0):
            with self.subTest(repeat_count=repeat_count):
                bm, island, uv_layer = _create_upward_wall_island(
                    repeat_count, 0.25
                )
                try:
                    calculated = calculate_tiling_repeat_count(
                        repeat_count / 0.25, hotspot,
                        IMAGE_WIDTH, IMAGE_HEIGHT, 0
                    )
                    self.assertAlmostEqual(calculated, repeat_count)

                    apply_hotspot_uvs(
                        island, uv_layer, hotspot,
                        IMAGE_WIDTH, IMAGE_HEIGHT, 0
                    )
                    u_span, v_span = _uv_spans(island, uv_layer)
                    self.assertAlmostEqual(u_span, repeat_count)
                    self.assertAlmostEqual(v_span, 0.25)
                finally:
                    bm.free()


class HotspotTilingSelectionTest(AnvilTestCase):

    def test_hotspot_tiling_non_tiling_axis_chooses_rotation_by_size_not_aspect(self):
        cases = (
            (
                TILING_VERTICAL,
                _hotspot("vertical", 25, 100, "Any", TILING_VERTICAL),
                0.25, 0.20,
            ),
            (
                TILING_HORIZONTAL,
                _hotspot("horizontal", 100, 25, "Any", TILING_HORIZONTAL),
                0.20, 0.25,
            ),
        )
        for tiling_type, hotspot, width, height in cases:
            with self.subTest(tiling_type=tiling_type):
                selected, rotation = _find_for_wall(
                    width, height, [hotspot], "wall", 0.0
                )
                self.assertIs(selected, hotspot)
                self.assertIn(rotation, (0, 180))

    def test_hotspot_tiling_upwards_orientation_takes_priority_over_closest_size_axis(self):
        cases = (
            (
                TILING_VERTICAL,
                _hotspot(
                    "vertical_up", 25, 100, "Upwards",
                    TILING_VERTICAL
                ),
                1.0, 0.25,
            ),
            (
                TILING_HORIZONTAL,
                _hotspot(
                    "horizontal_up", 100, 25, "Upwards",
                    TILING_HORIZONTAL
                ),
                0.25, 1.0,
            ),
        )
        for tiling_type, hotspot, width, height in cases:
            with self.subTest(tiling_type=tiling_type):
                selected, rotation = _find_for_wall(
                    width, height, [hotspot], "wall", 0.5
                )
                self.assertIs(selected, hotspot)
                self.assertEqual(rotation, 0)

    def test_hotspot_tiling_supports_every_orientation_with_each_tiling_axis(self):
        orientation_face_types = (
            ("Any", "wall"),
            ("Upwards", "wall"),
            ("Floor", "floor"),
            ("Ceiling", "ceiling"),
        )
        tiling_cases = (
            (TILING_VERTICAL, 25, 100, 0.25, 2.0),
            (TILING_HORIZONTAL, 100, 25, 2.0, 0.25),
        )

        for tiling_type, hs_width, hs_height, width, height in tiling_cases:
            for orientation, face_type in orientation_face_types:
                with self.subTest(
                        tiling_type=tiling_type, orientation=orientation):
                    hotspot = _hotspot(
                        f"{tiling_type}_{orientation}",
                        hs_width, hs_height, orientation, tiling_type
                    )
                    selected, rotation = _find_for_wall(
                        width, height, [hotspot], face_type, 0.5
                    )
                    self.assertIs(selected, hotspot)
                    self.assertIn(rotation, (0, 180))
                    repeat_count = calculate_tiling_repeat_count(
                        width / height, hotspot,
                        IMAGE_WIDTH, IMAGE_HEIGHT, rotation
                    )
                    self.assertAlmostEqual(repeat_count, 2.0)

    def test_hotspot_tiling_orientation_filters_reject_incompatible_face_types(self):
        invalid_orientation_faces = (
            ("Upwards", "floor"),
            ("Floor", "wall"),
            ("Ceiling", "wall"),
        )
        tiling_cases = (
            (TILING_VERTICAL, 25, 100, 0.25, 2.0),
            (TILING_HORIZONTAL, 100, 25, 2.0, 0.25),
        )

        for tiling_type, hs_width, hs_height, width, height in tiling_cases:
            for orientation, face_type in invalid_orientation_faces:
                with self.subTest(
                        tiling_type=tiling_type, orientation=orientation,
                        face_type=face_type):
                    hotspot = _hotspot(
                        f"{tiling_type}_{orientation}",
                        hs_width, hs_height, orientation, tiling_type
                    )
                    selected, rotation = _find_for_wall(
                        width, height, [hotspot], face_type, 0.5
                    )
                    self.assertIsNone(selected)
                    self.assertEqual(rotation, 0)

    def test_partial_tiling_hotspot_loses_to_exact_non_tiling_aspect(self):
        cases = (
            (
                TILING_VERTICAL,
                _hotspot(
                    "partial_vertical", 25, 100, "Any",
                    TILING_VERTICAL
                ),
                _hotspot(
                    "exact_vertical", 25, 60, "Any", TILING_NONE
                ),
                0.25, 0.60,
            ),
            (
                TILING_HORIZONTAL,
                _hotspot(
                    "partial_horizontal", 100, 25, "Any",
                    TILING_HORIZONTAL
                ),
                _hotspot(
                    "exact_horizontal", 60, 25, "Any", TILING_NONE
                ),
                0.60, 0.25,
            ),
        )
        for tiling_type, tiling_hotspot, exact_hotspot, width, height in cases:
            with self.subTest(tiling_type=tiling_type):
                selected, _rotation = _find_for_wall(
                    width, height,
                    [tiling_hotspot, exact_hotspot], "wall", 0.5
                )
                self.assertIs(selected, exact_hotspot)

    def test_multiple_tile_hotspot_beats_non_tiling_region_with_worse_aspect(self):
        cases = (
            (
                TILING_VERTICAL,
                _hotspot(
                    "multiple_vertical", 25, 100, "Any",
                    TILING_VERTICAL
                ),
                _hotspot(
                    "ordinary_vertical", 75, 100, "Any", TILING_NONE
                ),
                0.25, 3.0,
            ),
            (
                TILING_HORIZONTAL,
                _hotspot(
                    "multiple_horizontal", 100, 25, "Any",
                    TILING_HORIZONTAL
                ),
                _hotspot(
                    "ordinary_horizontal", 100, 75, "Any", TILING_NONE
                ),
                3.0, 0.25,
            ),
        )
        for tiling_type, tiling_hotspot, ordinary_hotspot, width, height in cases:
            with self.subTest(tiling_type=tiling_type):
                selected, rotation = _find_for_wall(
                    width, height,
                    [tiling_hotspot, ordinary_hotspot], "wall", 0.8
                )
                self.assertIs(selected, tiling_hotspot)
                repeat_count = calculate_tiling_repeat_count(
                    width / height, selected,
                    IMAGE_WIDTH, IMAGE_HEIGHT, rotation
                )
                self.assertAlmostEqual(repeat_count, 3.0)
