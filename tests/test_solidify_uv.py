"""Regression coverage for Solidify Faces redo-panel UV updates."""

import math

import bmesh
import bpy

from ..core.uv_projection import derive_transform_from_uvs
from ..handlers.lifecycle import on_undo_post, on_undo_pre
from .base_test import AnvilTestCase
from .helpers import create_vertical_plane, get_undo_context


def _read_face_uv_transforms(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.active
    ppm = bpy.context.scene.level_design_props.pixels_per_meter
    return [
        derive_transform_from_uvs(face, uv_layer, ppm, obj.data)
        for face in bm.faces
    ]


def _assert_all_faces_keep_anvil_uv_scale(test_case, obj, expected_scale):
    transforms = _read_face_uv_transforms(obj)
    test_case.assertEqual(
        len(transforms),
        6,
        "Solidifying one quad should produce two caps and four rim faces",
    )
    invalid_scales = []
    for face_index, transform in enumerate(transforms):
        test_case.assertIsNotNone(
            transform,
            f"Solidified face {face_index} has no derivable UV transform",
        )
        for axis in ('scale_u', 'scale_v'):
            scale = transform[axis]
            test_case.assertTrue(
                math.isfinite(scale),
                f"Solidified face {face_index} has non-finite {axis}: {scale}",
            )
            if abs(scale - expected_scale) > 1e-3:
                invalid_scales.append((face_index, axis, scale))
    test_case.assertEqual(
        invalid_scales,
        [],
        msg=(
            "Solidify left Blender-generated UV scales after its thickness "
            f"changed; expected {expected_scale} on every axis, got "
            f"{invalid_scales}. All transforms: {transforms}"
        ),
    )


def _repeat_solidify_after_internal_rollback(undo_context, thickness):
    """Model Blender's Adjust Last Operation undo-and-repeat sequence.

    Blender keeps the adjusted mesh operator active while its internal undo
    callbacks run. A direct ``bpy.ops.ed.undo()`` instead makes the undo
    operator active, so call Anvil's lifecycle hooks around an undo with only
    those same hooks temporarily detached. This preserves the production
    callback state without driving the popup UI or dragging the mouse.
    """
    on_undo_pre(bpy.context.scene)

    undo_pre_handlers = bpy.app.handlers.undo_pre
    undo_post_handlers = bpy.app.handlers.undo_post
    undo_pre_index = undo_pre_handlers[:].index(on_undo_pre)
    undo_post_index = undo_post_handlers[:].index(on_undo_post)
    undo_pre_handlers.remove(on_undo_pre)
    undo_post_handlers.remove(on_undo_post)
    try:
        with bpy.context.temp_override(**undo_context):
            undo_result = bpy.ops.ed.undo()
    finally:
        undo_pre_handlers.insert(undo_pre_index, on_undo_pre)
        undo_post_handlers.insert(undo_post_index, on_undo_post)

    on_undo_post(bpy.context.scene)
    with bpy.context.temp_override(**undo_context):
        redo_result = bpy.ops.mesh.solidify(
            'EXEC_DEFAULT',
            True,
            thickness=thickness,
        )
    return undo_result, redo_result


class SolidifyFacesRedoUVTest(AnvilTestCase):

    def test_solidify_faces_thickness_redo_preserves_anvil_uv_scale(self):
        """Changing Thickness in Adjust Last Operation must re-project UVs.

        Blender implements a redo-panel property change by undoing the prior
        operator result and immediately repeating the operator with the new
        properties. Reproduce that sequence directly so the test does not
        depend on mouse dragging or the panel's UI controls.
        """
        obj = create_vertical_plane("solidify_faces_redo")
        undo_context = get_undo_context()

        with bpy.context.temp_override(**undo_context):
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.ed.undo_push(message="Before Solidify Faces")
        self.refresh_face_cache()

        with bpy.context.temp_override(**undo_context):
            result = bpy.ops.mesh.solidify(
                'EXEC_DEFAULT',
                True,
                thickness=0.125,
            )
        self.assertIn('FINISHED', result)
        yield 0.5

        _assert_all_faces_keep_anvil_uv_scale(self, obj, 1.0)
        self.assertEqual(bpy.context.active_operator.bl_idname, "MESH_OT_solidify")

        # This is the non-UI equivalent of changing Thickness by one arrow
        # notch: rollback the current result, then repeat Solidify Faces with
        # the adjusted operator property before Blender returns to idle.
        undo_result, redo_result = _repeat_solidify_after_internal_rollback(
            undo_context,
            0.126,
        )
        self.assertIn('FINISHED', undo_result)
        self.assertIn('FINISHED', redo_result)
        yield 0.5

        _assert_all_faces_keep_anvil_uv_scale(self, obj, 1.0)
