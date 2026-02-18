import bpy
import bmesh
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ...utils import is_level_design_workspace
from .raycast import raycast_bvh_skip_backfaces


def _nearest_vert_on_face(hit_point, face):
    """Find the vertex on a face nearest to the hit point."""
    best_vert = None
    best_dist = float('inf')
    for vert in face.verts:
        dist = (vert.co - hit_point).length_squared
        if dist < best_dist:
            best_dist = dist
            best_vert = vert
    return best_vert


def _point_to_segment_dist_sq(point, seg_a, seg_b):
    """Squared distance from a point to a line segment."""
    ab = seg_b - seg_a
    ab_sq = ab.length_squared
    if ab_sq < 1e-12:
        return (point - seg_a).length_squared
    t = max(0.0, min(1.0, (point - seg_a).dot(ab) / ab_sq))
    proj = seg_a + ab * t
    return (point - proj).length_squared


def _nearest_edge_on_face(hit_point, face):
    """Find the edge on a face nearest to the hit point."""
    best_edge = None
    best_dist = float('inf')
    for edge in face.edges:
        dist = _point_to_segment_dist_sq(hit_point, edge.verts[0].co, edge.verts[1].co)
        if dist < best_dist:
            best_dist = dist
            best_edge = edge
    return best_edge


def _do_loop_select(bm, me, face, hit_point, extend):
    """Perform edge loop selection from the nearest edge on the hit face."""
    target_edge = _nearest_edge_on_face(hit_point, face)
    if target_edge is None:
        return

    # Save current selection if extending
    saved_vert_sel = None
    saved_edge_sel = None
    saved_face_sel = None
    if extend:
        saved_vert_sel = {v.index for v in bm.verts if v.select}
        saved_edge_sel = {e.index for e in bm.edges if e.select}
        saved_face_sel = {f.index for f in bm.faces if f.select}

    # Deselect all, select target edge, update mesh for the operator
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False

    target_edge.select = True
    for v in target_edge.verts:
        v.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(me)

    # Use Blender's loop select
    bpy.ops.mesh.loop_multi_select(ring=False)

    if extend:
        # Re-fetch bmesh after operator call
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        # Restore saved selection on top of loop
        for v in bm.verts:
            if v.index in saved_vert_sel:
                v.select = True
        for e in bm.edges:
            if e.index in saved_edge_sel:
                e.select = True
        for f in bm.faces:
            if f.index in saved_face_sel:
                f.select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)


class LEVELDESIGN_OT_backface_select(Operator):
    """Select through backface-culled faces"""
    bl_idname = "leveldesign.backface_select"
    bl_label = "Backface-Aware Select"
    bl_options = {'REGISTER', 'UNDO'}

    extend: bpy.props.BoolProperty()
    loop: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        if not is_level_design_workspace():
            return False
        obj = context.object
        return (obj is not None
                and obj.type == 'MESH'
                and context.mode == 'EDIT_MESH')

    def invoke(self, context, event):
        obj = context.object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()

        # Build ray from mouse position
        region = context.region
        rv3d = context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)

        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

        # Transform to object local space
        matrix_inv = obj.matrix_world.inverted()
        ray_origin_local = matrix_inv @ ray_origin
        ray_direction_local = (matrix_inv.to_3x3() @ view_vector).normalized()

        # Raycast skipping backface-culled faces
        bvh = BVHTree.FromBMesh(bm)
        location, normal, face_index, distance = raycast_bvh_skip_backfaces(
            bvh, ray_origin_local, ray_direction_local,
            bm, me.materials, max_iterations=64
        )

        if face_index is None:
            if not self.extend:
                bpy.ops.mesh.select_all(action='DESELECT')
            return {'FINISHED'}

        face = bm.faces[face_index]
        hit_point = location

        select_mode = context.tool_settings.mesh_select_mode
        is_vert_mode = select_mode[0]
        is_edge_mode = select_mode[1]
        is_face_mode = select_mode[2]

        # Alt+click: loop select (works in all modes)
        if self.loop:
            _do_loop_select(bm, me, face, hit_point, self.extend)
            return {'FINISHED'}

        # Plain or Shift click
        if not self.extend:
            bpy.ops.mesh.select_all(action='DESELECT')
            # Re-fetch bmesh after operator call
            bm = bmesh.from_edit_mesh(me)
            bm.faces.ensure_lookup_table()
            face = bm.faces[face_index]

        if is_face_mode:
            face.select = not face.select if self.extend else True
            bm.faces.active = face
        elif is_edge_mode:
            edge = _nearest_edge_on_face(hit_point, face)
            if edge is not None:
                new_state = not edge.select if self.extend else True
                edge.select = new_state
                for v in edge.verts:
                    v.select = new_state
        elif is_vert_mode:
            vert = _nearest_vert_on_face(hit_point, face)
            if vert is not None:
                vert.select = not vert.select if self.extend else True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)

        return {'FINISHED'}


def register():
    bpy.utils.register_class(LEVELDESIGN_OT_backface_select)


def unregister():
    bpy.utils.unregister_class(LEVELDESIGN_OT_backface_select)
