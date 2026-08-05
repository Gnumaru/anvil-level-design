"""
Cube Cut Tool - Main Modal Operator

ModalDrawBase subclass that previews and executes cube cut geometry.
"""

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty
from mathutils import Matrix, Vector

from . import geometry
from .prism import build_cube_cut_prism
from ..mesh_cut.analysis import analyze_convex_prism_cut
from ..modal_draw.base_operator import ModalDrawBase, MIN_RECTANGLE_SIZE
from ..modal_draw import utils as modal_draw_utils
from ...core.workspace_check import is_level_design_workspace
from ...core.logging import (
    add_performance_detail,
    begin_performance_operation_report,
    finish_performance_operation_report,
    performance_stage,
)
from ..pending_mesh_action import store_from_edge_selection, snapshot_coplanar_sides


class MESH_OT_cube_cut(ModalDrawBase, bpy.types.Operator):
    """Cut a cuboid-shaped void from mesh geometry"""
    bl_idname = "leveldesign.cube_cut"
    bl_label = "Cube Cut"
    bl_options = {'REGISTER', 'UNDO'}

    reconstruction_mode: EnumProperty(
        name="Face Reconstruction",
        description="Choose how cut faces are reconstructed",
        items=(
            (
                geometry.RECONSTRUCTION_MODE_QUADS,
                "Reconstruct Quads",
                "Reconstruct cut surfaces as quads where possible",
            ),
            (
                geometry.RECONSTRUCTION_MODE_NGONS,
                "Reconstruct Ngons",
                "Use the fewest face-local connector edges required for valid topology",
            ),
        ),
        default=geometry.RECONSTRUCTION_MODE_QUADS,
    )

    action_first_vertex: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_second_vertex: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_depth: FloatProperty(
        options={'HIDDEN'},
    )
    action_local_x: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_local_y: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_local_z: FloatVectorProperty(
        size=3,
        options={'HIDDEN'},
    )
    action_matrix_world: FloatVectorProperty(
        size=16,
        options={'HIDDEN'},
    )

    action_ortho_infinite_cut: BoolProperty(
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace() and
            context.active_object is not None and
            context.active_object.type == 'MESH' and
            context.mode == 'EDIT_MESH'
        )

    def draw(self, context):
        self.layout.prop(self, "reconstruction_mode")

    def invoke(self, context, event):
        # The preview cache key is made only from the snapped cut dimensions.
        # Mouse movement within one snap cell therefore redraws the cached Xs
        # without re-running the mesh analysis.
        self._cut_preview_dimensions = None
        return super().invoke(context, event)

    def _update_second_vertex_preview(self, context, event):
        super()._update_second_vertex_preview(context, event)
        self._update_cut_vertex_preview(context)

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._update_cut_vertex_preview(context)

    def _on_info_visibility_changed(self, context, visible):
        self._cut_preview_dimensions = None
        if visible:
            self._update_cut_vertex_preview(context)
        else:
            self._preview.update_cut_vertex_markers([])

    def _update_cut_vertex_preview(self, context):
        """Refresh predicted vertex Xs only when snapped dimensions change."""
        if not self._preview.is_info_visible():
            self._cut_preview_dimensions = None
            self._preview.update_cut_vertex_markers([])
            return
        if self._first_vertex is None or self._second_vertex is None:
            return
        if self._local_x is None or self._local_y is None or self._local_z is None:
            return

        preview_first, preview_second, preview_depth, preview_phase = (
            self._get_cut_preview_parameters()
        )
        diff = preview_second - preview_first

        # Signed dimensions distinguish opposite draw directions while keeping
        # the cache independent of raw mouse coordinates and redraw frequency.
        dimensions = (
            diff.dot(self._local_x),
            diff.dot(self._local_y),
            preview_depth,
        )
        if dimensions == self._cut_preview_dimensions:
            return
        self._cut_preview_dimensions = dimensions

        if self._invalid_message is not None:
            self._preview.update_cut_vertex_markers([])
            return

        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self._preview.update_cut_vertex_markers([])
            return

        performance_report = begin_performance_operation_report(
            "Cube Cut Preview Recalculation",
            "Read-only BMesh scan and face-oriented marker preparation; "
            "GPU drawing excluded; BVH disabled",
        )
        add_performance_detail(
            performance_report, "Preview phase", preview_phase
        )
        add_performance_detail(
            performance_report,
            "Snapped dimensions",
            f"{dimensions[0]:.4f} x {dimensions[1]:.4f} x "
            f"{preview_depth:.4f}",
        )

        analysis = None
        markers = []
        try:
            with performance_stage(performance_report, "Get edit BMesh"):
                bm = bmesh.from_edit_mesh(obj.data)

            add_performance_detail(performance_report, "Mesh vertices", len(bm.verts))
            add_performance_detail(performance_report, "Mesh edges", len(bm.edges))
            add_performance_detail(performance_report, "Mesh faces", len(bm.faces))

            with performance_stage(performance_report, "Build cut prism"):
                prism = build_cube_cut_prism(
                    obj.matrix_world,
                    preview_first,
                    preview_second,
                    preview_depth,
                    self._local_x,
                    self._local_y,
                    self._local_z,
                )

            # This deliberately uses the existing direct BMesh scan. A BVH can
            # be introduced later without changing the analysis/preview contract.
            with performance_stage(performance_report, "Analyze intersections"):
                analysis = analyze_convex_prism_cut(bm, prism)

            with performance_stage(
                    performance_report, "Prepare face-oriented X markers"):
                markers = _build_world_cut_vertex_markers(
                    obj.matrix_world,
                    analysis.candidate_vertex_markers,
                )

            add_performance_detail(
                performance_report,
                "Faces to cut",
                len(analysis.faces_to_cut),
            )
            add_performance_detail(
                performance_report,
                "Predicted vertex positions",
                len(analysis.candidate_vertex_points),
            )
            add_performance_detail(
                performance_report,
                "Face-oriented X markers",
                len(markers),
            )
        except Exception as error:
            print(
                "Level Design Tools: Error updating Cube Cut vertex preview: "
                f"{error}"
            )
            markers = []
        finally:
            finish_performance_operation_report(performance_report)

        self._preview.update_cut_vertex_markers(markers)

    def _get_cut_preview_parameters(self):
        """Return the cut volume represented by the current modal stage."""
        if self._state == self.STATE_SECOND_VERTEX and self._is_2d_view:
            # Orthographic Cube Cut executes immediately after the rectangle.
            # Preview the same effectively infinite depth used by execution.
            offset = self._local_z * -5000
            return (
                self._first_vertex + offset,
                self._second_vertex + offset,
                10000,
                "Rectangle (orthographic infinite depth)",
            )

        if self._state == self.STATE_SECOND_VERTEX:
            # A zero-depth cuboid is sufficient to find intersections on the
            # drawn rectangle plane before the user starts choosing depth.
            return (
                self._first_vertex,
                self._second_vertex,
                0.0,
                "Rectangle (zero depth)",
            )

        return (
            self._first_vertex,
            self._second_vertex,
            self._depth,
            "Depth",
        )

    def _confirm_second_vertex(self, context, event):
        """In ortho views, skip the depth step and execute immediately with infinite depth."""
        if not self._is_2d_view:
            return super()._confirm_second_vertex(context, event)

        if self._first_vertex is None or self._second_vertex is None:
            return {'RUNNING_MODAL'}

        invalid_message = self._get_rectangle_invalid_message_for_vertices(
            self._first_vertex, self._second_vertex
        )
        if invalid_message is not None:
            self._set_invalid_message(invalid_message)
            self._update_header(context)
            return {'RUNNING_MODAL'}

        # Shift both vertices back 5000 units along -local_z so the cut
        # is centered on the geometry, then use depth=10000 to extend
        # 5000 units in each direction.
        offset = self._local_z * -5000
        self._first_vertex = self._first_vertex + offset
        self._second_vertex = self._second_vertex + offset
        self._depth = 10000

        result = self._run_action(
            context,
            self._first_vertex,
            self._second_vertex,
            self._depth,
            self._local_x,
            self._local_y,
            self._local_z
        )
        success, message = result[0], result[1]

        if not getattr(self, "_action_reported", False):
            if success:
                self.report({'INFO'}, message)
            else:
                self.report({'ERROR'}, message)

        self._cleanup(context)
        return {'FINISHED'}

    def _get_rectangle_invalid_message(self, local_dx, local_dy):
        if local_dx < MIN_RECTANGLE_SIZE and local_dy < MIN_RECTANGLE_SIZE:
            return "Move away from the start point"
        if self._line_mode and local_dy < MIN_RECTANGLE_SIZE:
            return "Width must be greater than zero"
        if local_dx < MIN_RECTANGLE_SIZE:
            return "Width must be greater than zero"
        if local_dy < MIN_RECTANGLE_SIZE:
            return "Height must be greater than zero"
        return None

    def _execute_action(self, context, first_vertex, second_vertex, depth,
                        local_x, local_y, local_z):
        # The first clicked point becomes the cut pivot.
        # Snapshot coplanar faces BEFORE the cut modifies geometry
        from mathutils import Vector
        matrix_values = self.action_matrix_world
        matrix_world = Matrix((
            matrix_values[0:4],
            matrix_values[4:8],
            matrix_values[8:12],
            matrix_values[12:16],
        ))
        obj = context.active_object
        coplanar_blocked = 0
        if obj and obj.type == 'MESH':
            import bmesh as _bmesh
            bm_snap = _bmesh.from_edit_mesh(obj.data)
            w2l = matrix_world.inverted()
            w2l_rot = w2l.to_3x3()
            snap_origin = w2l @ first_vertex
            snap_lx = (w2l_rot @ Vector(local_x)).normalized()
            snap_ly = (w2l_rot @ Vector(local_y)).normalized()
            diff_world = Vector(second_vertex) - Vector(first_vertex)
            snap_cdx = abs(diff_world.dot(Vector(local_x))) * (w2l_rot @ Vector(local_x)).length
            snap_cdy = abs(diff_world.dot(Vector(local_y))) * (w2l_rot @ Vector(local_y)).length
            snap_diff = (w2l @ Vector(second_vertex)) - snap_origin
            if snap_diff.dot(snap_lx) < 0:
                snap_lx = -snap_lx
            if snap_diff.dot(snap_ly) < 0:
                snap_ly = -snap_ly
            coplanar_blocked = snapshot_coplanar_sides(
                bm_snap, (snap_origin, snap_lx, snap_ly, snap_cdx, snap_cdy))

        result = geometry.execute_cube_cut_with_reconstruction(
            context, first_vertex, second_vertex, depth,
            local_x, local_y, local_z, self.reconstruction_mode, matrix_world,
        )
        success, message = result

        if success:
            extrude_dir = -local_z
            back_point = first_vertex + local_z * depth
            back_plane_offset = back_point.dot(extrude_dir.normalized())
            from ...core.logging import debug_log
            debug_log(f"[CubeCut] Corridor depth setup: depth={depth:.4f}, abs_depth={abs(depth):.4f}")
            debug_log(f"[CubeCut]   first_vertex={first_vertex}, second_vertex={second_vertex}")
            debug_log(f"[CubeCut]   local_z={local_z}, extrude_dir={extrude_dir}")
            debug_log(f"[CubeCut]   back_point={back_point} (first_vertex + local_z * depth)")
            debug_log(f"[CubeCut]   back_plane_offset={back_plane_offset:.4f} (back_point dot extrude_dir)")
            store_from_edge_selection(
                context.active_object, abs(depth), extrude_dir, back_plane_offset,
                first_vertex, second_vertex, local_x, local_y,
                coplanar_blocked,
            )

        return result

    def _capture_action_properties(self, context, first_vertex, second_vertex,
                                   depth, local_x, local_y, local_z):
        self.action_first_vertex = first_vertex
        self.action_second_vertex = second_vertex
        self.action_depth = depth
        self.action_local_x = local_x
        self.action_local_y = local_y
        self.action_local_z = local_z
        self.action_matrix_world = tuple(
            value for row in context.active_object.matrix_world for value in row
        )
        self.action_ortho_infinite_cut = self._is_2d_view

    def execute(self, context):
        first_vertex = Vector(self.action_first_vertex)
        second_vertex = Vector(self.action_second_vertex)
        local_x = Vector(self.action_local_x)
        local_y = Vector(self.action_local_y)
        local_z = Vector(self.action_local_z)

        # Adjust Last Operation can replay before Blender refreshes the
        # object's runtime matrix_world cache after undo (temporarily identity).
        # Use the matrix captured with the world-space draw values instead.

        result = self._execute_action(
            context, first_vertex, second_vertex, self.action_depth,
            local_x, local_y, local_z
        )
        self._last_action_result = result

        success, message = result[0], result[1]
        if success:
            self.report({'INFO'}, message)
            self._action_reported = True
            return {'FINISHED'}

        self.report({'ERROR'}, message)
        self._action_reported = True
        return {'CANCELLED'}

    def _get_tool_name(self):
        return "Cube Cut"


def _build_world_cut_vertex_markers(matrix_world, candidate_markers):
    """Transform mesh-local candidates into face-oriented world-space frames."""
    rotation_scale = matrix_world.to_3x3()
    world_markers = []

    for candidate in candidate_markers:
        # Start with an orthonormal basis on the candidate's supporting face.
        # Transforming both tangents (rather than only its normal) keeps the X
        # on the visible face plane even when the object has non-uniform scale.
        tangent1, tangent2 = modal_draw_utils.get_face_tangents(
            candidate.face_normal
        )
        world_tangent1 = rotation_scale @ tangent1
        world_tangent2 = rotation_scale @ tangent2
        if world_tangent1.length_squared < 1e-10:
            continue
        if world_tangent2.length_squared < 1e-10:
            continue

        world_tangent1.normalize()

        # Restore an orthonormal in-plane basis after object scaling so every
        # marker remains a compact, square X rather than a stretched cross.
        world_tangent2 -= (
            world_tangent1 * world_tangent2.dot(world_tangent1)
        )
        if world_tangent2.length_squared < 1e-10:
            continue
        world_tangent2.normalize()

        world_markers.append((
            matrix_world @ candidate.point,
            world_tangent1,
            world_tangent2,
        ))

    return world_markers


def register():
    bpy.utils.register_class(MESH_OT_cube_cut)


def unregister():
    bpy.utils.unregister_class(MESH_OT_cube_cut)
