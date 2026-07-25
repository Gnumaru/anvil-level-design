"""Undoable operators that execute one captured pending mesh action."""

import bmesh
import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Vector

from ..core.geometry import compute_normal_from_verts
from ..core.logging import debug_log
from ..core.workspace_check import is_level_design_workspace
from .pending_mesh_action import (
    complete_pending_action,
    get_pending_action_kind,
    resolve_pending_action,
)


_FOLDED_EPSILON = 1e-4


def _resolve_edit_action(obj, context_mode, bm, expected_kind):
    action = resolve_pending_action(obj, context_mode, bm)
    if action is None or action.kind != expected_kind:
        return None
    return action


def _edit_action_poll(active_object, context_mode, expected_kind):
    return (
        context_mode == 'EDIT_MESH'
        and active_object is not None
        and active_object.type == 'MESH'
        and get_pending_action_kind(active_object, context_mode) == expected_kind
    )


def _float_matches(first, second):
    return abs(first - second) < 1e-5


def _vector_matches(first, second):
    return all(_float_matches(a, b) for a, b in zip(first, second))


def _bridge_edge_loops():
    return bpy.ops.mesh.bridge_edge_loops()


def _clear_selection(bm):
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False
    bm.select_flush(False)


class LEVELDESIGN_OT_weld_bridge(bpy.types.Operator):
    """Bridge the two edge loops captured by the pending action"""

    bl_idname = "leveldesign.weld_bridge"
    bl_label = "Bridge Edge Loops"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and _edit_action_poll(
                context.active_object, context.mode, 'BRIDGE',
            )
        )

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        action = _resolve_edit_action(obj, context.mode, bm, 'BRIDGE')
        if action is None:
            return {'CANCELLED'}
        try:
            _bridge_edge_loops()
        except RuntimeError as exc:
            self.report({'ERROR'}, f"Bridge failed: {exc}")
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        from ..handlers.new_face_projection import project_new_faces
        from ..handlers.face_cache import cache_face_data
        project_new_faces(context, bm)
        cache_face_data(context)
        _clear_selection(bm)
        context.tool_settings.mesh_select_mode = (False, False, True)
        complete_pending_action(obj, action, bm)
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "Bridged edge loops")
        return {'FINISHED'}


class LEVELDESIGN_OT_weld_invert(bpy.types.Operator):
    """Invert the box faces captured by the pending action"""

    bl_idname = "leveldesign.weld_invert"
    bl_label = "Invert Box"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    face_indices: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    object_mode: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and context.active_object is not None
            and context.active_object.type == 'MESH'
            and get_pending_action_kind(
                context.active_object, context.mode,
            ) == 'INVERT'
        )

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data) if context.mode == 'EDIT_MESH' else None
        action = resolve_pending_action(obj, context.mode, bm)
        if action is None or action.kind != 'INVERT' or action.object_mode != self.object_mode:
            return {'CANCELLED'}

        try:
            target_indices = tuple(
                int(value) for value in self.face_indices.split(',') if value
            )
        except ValueError:
            return {'CANCELLED'}
        if not action.object_mode and target_indices != action.face_indices:
            return {'CANCELLED'}

        entered_edit = context.mode != 'EDIT_MESH'
        selected_face_indices = []
        if entered_edit:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
        else:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            selected_face_indices = [face.index for face in bm.faces if face.select]
            _clear_selection(bm)
            for index in target_indices:
                if index < len(bm.faces):
                    bm.faces[index].select = True
            bm.select_flush(True)
            bmesh.update_edit_mesh(obj.data)

        try:
            bpy.ops.mesh.flip_normals()
        except RuntimeError as exc:
            if entered_edit:
                bpy.ops.object.mode_set(mode='OBJECT')
            self.report({'ERROR'}, f"Invert failed: {exc}")
            return {'CANCELLED'}

        if entered_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            complete_pending_action(obj, action, None)
        else:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            _clear_selection(bm)
            for index in selected_face_indices:
                if index < len(bm.faces):
                    bm.faces[index].select = True
            bm.select_flush(True)
            complete_pending_action(obj, action, bm)
            bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "Normals inverted")
        return {'FINISHED'}


class LEVELDESIGN_OT_weld_corridor(bpy.types.Operator):
    """Fill and extrude the boundary captured by the pending action"""

    bl_idname = "leveldesign.weld_corridor"
    bl_label = "Create Corridor"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    depth: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'})
    direction: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    back_plane_offset: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and _edit_action_poll(
                context.active_object, context.mode, 'CORRIDOR',
            )
        )

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        action = _resolve_edit_action(obj, context.mode, bm, 'CORRIDOR')
        if action is None:
            return {'CANCELLED'}
        if (
                not _float_matches(self.depth, action.depth)
                or not _vector_matches(self.direction, action.direction)
                or not _float_matches(
                    self.back_plane_offset, action.back_plane_offset,
                )):
            return {'CANCELLED'}
        direction = Vector(self.direction)
        debug_log(
            f"[Corridor] Execute: depth={self.depth:.4f}, direction={direction}, "
            f"back_plane_offset={self.back_plane_offset:.4f}"
        )
        selected_edges = [edge for edge in bm.edges if edge.select]
        if not selected_edges:
            self.report({'ERROR'}, "No edges selected")
            return {'CANCELLED'}
        try:
            result = bmesh.ops.contextual_create(bm, geom=selected_edges)
        except Exception as exc:
            self.report({'ERROR'}, f"Fill failed: {exc}")
            return {'CANCELLED'}
        new_faces = result.get('faces', [])
        if not new_faces:
            self.report({'ERROR'}, "No face created")
            return {'CANCELLED'}
        for face in bm.faces:
            face.select = False
        for face in new_faces:
            face.select = True
        bm.select_flush_mode()
        bm.normal_update()
        bm.select_mode = {'FACE'}
        context.tool_settings.mesh_select_mode = (False, False, True)
        world_to_local_rotation = obj.matrix_world.inverted().to_3x3()
        extrusion_direction = (world_to_local_rotation @ direction).normalized()
        origin_projection = obj.matrix_world.translation.dot(direction.normalized())
        local_back_plane_offset = self.back_plane_offset - origin_projection
        extrusion_geometry = list(new_faces)
        for face in new_faces:
            extrusion_geometry.extend(face.edges)
        result = bmesh.ops.extrude_face_region(bm, geom=extrusion_geometry)
        extruded_verts = [item for item in result['geom'] if isinstance(item, bmesh.types.BMVert)]
        extruded_faces = [item for item in result['geom'] if isinstance(item, bmesh.types.BMFace)]
        if not extruded_verts:
            self.report({'ERROR'}, "Extrude failed")
            return {'CANCELLED'}
        _clear_selection(bm)
        for face in extruded_faces:
            face.select = True
        for vert in extruded_verts:
            projection = vert.co.dot(extrusion_direction)
            vert.co += extrusion_direction * (local_back_plane_offset - projection)
        bm.normal_update()
        from ..handlers.new_face_projection import project_new_faces
        from ..handlers.face_cache import cache_face_data
        project_new_faces(context, bm)
        cache_face_data(context)
        _clear_selection(bm)
        context.tool_settings.mesh_select_mode = (False, False, True)
        complete_pending_action(obj, action, bm)
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Corridor created (depth: {self.depth:.3f})")
        return {'FINISHED'}


class LEVELDESIGN_OT_weld_folded_plane(bpy.types.Operator):
    """Complete the folded cuboid sides captured by the pending action"""

    bl_idname = "leveldesign.weld_folded_plane"
    bl_label = "Complete Folded Plane"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    origin: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    local_x: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    local_y: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    cdx: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'})
    cdy: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'})
    coplanar_blocked: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and _edit_action_poll(
                context.active_object, context.mode, 'FOLDED_PLANE',
            )
        )

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        action = _resolve_edit_action(obj, context.mode, bm, 'FOLDED_PLANE')
        if action is None:
            return {'CANCELLED'}
        if action.cuboid_params is None:
            return {'CANCELLED'}
        stored_origin, stored_x, stored_y, stored_cdx, stored_cdy = action.cuboid_params
        if (
                not _vector_matches(self.origin, stored_origin)
                or not _vector_matches(self.local_x, stored_x)
                or not _vector_matches(self.local_y, stored_y)
                or not _float_matches(self.cdx, stored_cdx)
                or not _float_matches(self.cdy, stored_cdy)
                or self.coplanar_blocked != action.coplanar_blocked):
            return {'CANCELLED'}
        origin = Vector(self.origin)
        local_x = Vector(self.local_x)
        local_y = Vector(self.local_y)
        local_z = local_x.cross(local_y).normalized()
        selected_edges = [edge for edge in bm.edges if edge.select]
        selected_verts = list({vert for edge in selected_edges for vert in edge.verts})
        if not selected_edges:
            self.report({'ERROR'}, "No edges selected")
            return {'CANCELLED'}
        corners = (
            origin,
            origin + local_x * self.cdx,
            origin + local_x * self.cdx + local_y * self.cdy,
            origin + local_y * self.cdy,
        )
        depth_edge_verts = {}
        for corner_index, corner in enumerate(corners):
            verts_on_edge = []
            for vert in selected_verts:
                offset = vert.co - corner
                in_plane = local_x * offset.dot(local_x) + local_y * offset.dot(local_y)
                if in_plane.length < _FOLDED_EPSILON:
                    verts_on_edge.append(vert)
            if len(verts_on_edge) >= 2:
                verts_on_edge.sort(key=lambda vert: (vert.co - corner).dot(local_z))
                depth_edge_verts[corner_index] = verts_on_edge
        new_edges = []
        for verts in depth_edge_verts.values():
            for index in range(len(verts) - 1):
                first, second = verts[index], verts[index + 1]
                existing = next(
                    (edge for edge in first.link_edges if edge.other_vert(first) == second),
                    None,
                )
                new_edges.append(existing if existing is not None else bm.edges.new((first, second)))

        definitions = (
            (local_x, 0.0, local_x, local_y, local_z),
            (local_x, self.cdx, -local_x, local_y, local_z),
            (local_y, 0.0, local_y, local_x, local_z),
            (local_y, self.cdy, -local_y, local_x, local_z),
        )
        relevant_verts = set(selected_verts)
        for edge in new_edges:
            relevant_verts.update(edge.verts)
        created_faces = []
        for side_index, (filter_axis, offset, inward_normal, u_axis, w_axis) in enumerate(definitions):
            plane_verts = [
                vert for vert in relevant_verts
                if abs((vert.co - origin).dot(filter_axis) - offset) < _FOLDED_EPSILON
            ]
            if len(plane_verts) < 3 or self.coplanar_blocked & (1 << side_index):
                continue
            coordinates = {
                vert: ((vert.co - origin).dot(u_axis), (vert.co - origin).dot(w_axis))
                for vert in plane_verts
            }
            u_values = [value[0] for value in coordinates.values()]
            w_values = [value[1] for value in coordinates.values()]
            u_min, u_max = min(u_values), max(u_values)
            w_min, w_max = min(w_values), max(w_values)
            bottom = sorted(
                [vert for vert in plane_verts if abs(coordinates[vert][1] - w_min) < _FOLDED_EPSILON],
                key=lambda vert: coordinates[vert][0],
            )
            right = sorted(
                [vert for vert in plane_verts if abs(coordinates[vert][0] - u_max) < _FOLDED_EPSILON],
                key=lambda vert: coordinates[vert][1],
            )
            top = sorted(
                [vert for vert in plane_verts if abs(coordinates[vert][1] - w_max) < _FOLDED_EPSILON],
                key=lambda vert: -coordinates[vert][0],
            )
            left = sorted(
                [vert for vert in plane_verts if abs(coordinates[vert][0] - u_min) < _FOLDED_EPSILON],
                key=lambda vert: -coordinates[vert][1],
            )
            polygon = list(bottom)
            for side_verts in (right, top, left):
                for vert in side_verts:
                    if vert != polygon[-1]:
                        polygon.append(vert)
            if len(polygon) > 1 and polygon[-1] == polygon[0]:
                polygon.pop()
            if len(polygon) < 3:
                continue
            normal = compute_normal_from_verts([vert.co for vert in polygon])
            if normal is not None and normal.dot(inward_normal) < 0:
                polygon.reverse()
            try:
                created_faces.append(bm.faces.new(polygon))
            except ValueError as exc:
                debug_log(f"[FoldedPlane] Failed to create face: {exc}")

        faces_to_triangulate = [
            face for face in created_faces if face.is_valid and len(face.verts) > 4
        ]
        if faces_to_triangulate:
            result = bmesh.ops.triangulate(bm, faces=faces_to_triangulate)
            triangles = {face for face in result.get('faces', []) if face.is_valid}
            triangles.update(
                face for face in created_faces if face.is_valid and len(face.verts) == 3
            )
            if triangles:
                bmesh.ops.join_triangles(
                    bm,
                    faces=list(triangles),
                    cmp_seam=False,
                    cmp_sharp=False,
                    cmp_uvs=False,
                    cmp_vcols=False,
                    cmp_materials=False,
                    angle_face_threshold=3.14,
                    angle_shape_threshold=3.14,
                )
        bm.normal_update()
        from ..handlers.new_face_projection import project_new_faces
        from ..handlers.face_cache import cache_face_data
        project_new_faces(context, bm)
        cache_face_data(context)
        _clear_selection(bm)
        context.tool_settings.mesh_select_mode = (False, False, True)
        complete_pending_action(obj, action, bm)
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "Folded plane weld completed")
        return {'FINISHED'}


_CLASSES = (
    LEVELDESIGN_OT_weld_bridge,
    LEVELDESIGN_OT_weld_invert,
    LEVELDESIGN_OT_weld_corridor,
    LEVELDESIGN_OT_weld_folded_plane,
)


def register():
    for operator_class in _CLASSES:
        bpy.utils.register_class(operator_class)


def unregister():
    for operator_class in reversed(_CLASSES):
        bpy.utils.unregister_class(operator_class)
