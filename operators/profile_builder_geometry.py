"""Shared mesh creation for cylinder and freeform prism builders."""

import bpy
import bmesh
from mathutils import Matrix, Vector

from .box_builder.geometry import (
    _active_or_previous_material,
    _apply_material_and_uvs,
    _next_box_builder_datablock_name,
    _remove_antiparallel_coplanar_faces,
)
from .mesh_cut.concave_prism import build_profile_prism
from .mesh_cut.convex_prism import EPSILON
from ..core.face_id import get_face_id_layer
from ..core.materials import MaterialMappingConflictError, ensure_material_slot
from ..core.uv_layers import get_render_active_uv_layer
from ..core.uv_projection import box_project
from ..handlers import cache_single_face


CAP_MODE_NONE = 'NONE'
CAP_MODE_NGON = 'NGON'
CAP_MODE_TRIANGLE_FAN = 'TRIANGLE_FAN'
CAP_MODE_PREFER_QUADS = 'PREFER_QUADS'
CAP_MODES = {
    CAP_MODE_NONE,
    CAP_MODE_NGON,
    CAP_MODE_TRIANGLE_FAN,
    CAP_MODE_PREFER_QUADS,
}


def execute_profile_builder_edit_mode(
        profile_vertices, depth, local_z, obj, ppm, cap_mode,
        keep_anti_parallel_coplanar_faces, shape_name):
    """Add a captured profile prism to an existing edit-mode mesh."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")

    validation_error = _profile_validation_error(
        obj.matrix_world,
        profile_vertices,
        depth,
        local_z,
        cap_mode,
    )
    if validation_error is not None:
        return (False, f"Invalid {shape_name.lower()} geometry: {validation_error}")

    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    active_face = bm.faces.active
    has_source_face = (
        active_face is not None
        and active_face.is_valid
        and not active_face.hide
        and active_face.select
    )

    default_material = None
    if not has_source_face:
        try:
            default_material = _active_or_previous_material()
        except MaterialMappingConflictError as error:
            return (
                False,
                f"{error}. Use Fix Material Mappings (Shift-4).",
            )

    uv_layer = get_render_active_uv_layer(bm, me)
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        uv_layer = bm.loops.layers.uv.new("UVMap")
    get_face_id_layer(bm)
    source_face = bm.faces.active if has_source_face else None
    existing_faces = [
        face for face in bm.faces
        if face.is_valid and not face.hide
    ]

    try:
        prism, is_plane = _build_profile_prism(
            obj.matrix_world,
            profile_vertices,
            depth,
            local_z,
        )
        new_faces, cap_faces_to_process, new_vertices = (
            _create_profile_geometry(bm, prism, is_plane, cap_mode)
        )
    except ValueError as error:
        return (False, f"Invalid {shape_name.lower()} geometry: {error}")

    if not new_faces:
        bmesh.update_edit_mesh(me)
        return (False, f"Failed to create {shape_name.lower()} geometry")

    bm.normal_update()
    if not keep_anti_parallel_coplanar_faces:
        new_faces = _remove_antiparallel_coplanar_faces(
            bm,
            new_faces,
            existing_faces,
        )

    _prefer_quads_on_cap_faces(
        bm,
        [face for face in cap_faces_to_process if face in new_faces],
    )
    new_faces = _faces_from_new_vertices(bm, new_vertices)
    bm.normal_update()

    _apply_material_and_uvs(
        bm,
        new_faces,
        source_face,
        default_material,
        uv_layer,
        ppm,
        me,
        obj,
    )

    new_face_vert_positions = []
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    for face in new_faces:
        if not face.is_valid:
            continue
        face.select = True
        new_face_vert_positions.append(
            (face.index, frozenset(tuple(vert.co) for vert in face.verts))
        )
    bm.select_flush(True)
    bmesh.update_edit_mesh(me)

    return (
        True,
        f"{shape_name} created",
        new_face_vert_positions,
    )


def execute_profile_builder_object_mode(
        profile_vertices, depth, local_z, ppm, cap_mode, base_name,
        name_suffix, origin_world, shape_name):
    """Create a new object from a captured world-space profile prism."""
    matrix_world = Matrix.Identity(4)
    matrix_world.translation = Vector(origin_world)
    validation_error = _profile_validation_error(
        matrix_world,
        profile_vertices,
        depth,
        local_z,
        cap_mode,
    )
    if validation_error is not None:
        return (False, f"Invalid {shape_name.lower()} geometry: {validation_error}")

    try:
        material = _active_or_previous_material()
    except MaterialMappingConflictError as error:
        return (
            False,
            f"{error}. Use Fix Material Mappings (Shift-4).",
        )

    bm = bmesh.new()
    try:
        prism, is_plane = _build_profile_prism(
            matrix_world,
            profile_vertices,
            depth,
            local_z,
        )
        new_faces, cap_faces_to_process, new_vertices = (
            _create_profile_geometry(bm, prism, is_plane, cap_mode)
        )
    except ValueError as error:
        bm.free()
        return (False, f"Invalid {shape_name.lower()} geometry: {error}")

    if not new_faces:
        bm.free()
        return (False, f"Failed to create {shape_name.lower()} geometry")

    _prefer_quads_on_cap_faces(bm, cap_faces_to_process)
    new_faces = _faces_from_new_vertices(bm, new_vertices)
    bm.normal_update()
    data_block_name = _next_box_builder_datablock_name(base_name, name_suffix)
    me = bpy.data.meshes.new(data_block_name)
    obj = bpy.data.objects.new(data_block_name, me)
    obj.location = Vector(origin_world)
    bpy.context.collection.objects.link(obj)
    bm.to_mesh(me)
    bm.free()

    for scene_object in bpy.context.view_layer.objects:
        scene_object.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")

    if material is not None:
        material_index = ensure_material_slot(me, material)
        bpy.ops.object.mode_set(mode='EDIT')
        bm_edit = bmesh.from_edit_mesh(me)
        bm_edit.faces.ensure_lookup_table()
        uv_layer = get_render_active_uv_layer(bm_edit, me)
        if uv_layer is None:
            uv_layer = bm_edit.loops.layers.uv.new("UVMap")

        for face in bm_edit.faces:
            if not face.is_valid:
                continue
            face.material_index = material_index
            box_project(face, uv_layer, material, ppm, 1.0)
            cache_single_face(face, bm_edit, ppm, me)

        bmesh.update_edit_mesh(me)
        bpy.ops.object.mode_set(mode='OBJECT')

    return (True, f"{shape_name} object created")


def _profile_validation_error(
        matrix_world, profile_vertices, depth, local_z, cap_mode):
    if cap_mode not in CAP_MODES:
        return f"Unknown cap mode: {cap_mode}"
    if abs(depth) <= EPSILON and cap_mode == CAP_MODE_NONE:
        return "Skipped caps require a non-zero depth"
    try:
        _build_profile_prism(
            matrix_world,
            profile_vertices,
            depth,
            local_z,
        )
    except ValueError as error:
        return str(error)
    return None


def _build_profile_prism(matrix_world, profile_vertices, depth, local_z):
    is_plane = abs(depth) <= EPSILON
    effective_depth = EPSILON * 2 if is_plane else depth
    extrusion = Vector(local_z).normalized() * effective_depth
    prism = build_profile_prism(matrix_world, profile_vertices, extrusion)
    return (prism, is_plane)


def _create_profile_geometry(bm, prism, is_plane, cap_mode):
    cap_vertices = [bm.verts.new(vertex) for vertex in prism.cap_vertices]
    all_new_vertices = list(cap_vertices)
    cap_faces_to_process = []

    if is_plane:
        created_caps = _create_cap_faces(
            bm,
            cap_vertices,
            prism.planes[0][1],
            cap_mode,
        )
        if cap_mode == CAP_MODE_PREFER_QUADS:
            cap_faces_to_process.extend(created_caps)
        all_new_vertices.extend(
            vertex
            for face in created_caps
            for vertex in face.verts
            if vertex not in all_new_vertices
        )
    else:
        back_vertices = [
            bm.verts.new(vertex) for vertex in prism.back_vertices
        ]
        all_new_vertices.extend(back_vertices)

        for index in range(len(cap_vertices)):
            following = (index + 1) % len(cap_vertices)
            _new_oriented_face(
                bm,
                (
                    cap_vertices[index],
                    cap_vertices[following],
                    back_vertices[following],
                    back_vertices[index],
                ),
                prism.planes[index + 2][1],
            )

        if cap_mode != CAP_MODE_NONE:
            front_faces = _create_cap_faces(
                bm,
                cap_vertices,
                prism.planes[0][1],
                cap_mode,
            )
            back_faces = _create_cap_faces(
                bm,
                back_vertices,
                prism.planes[1][1],
                cap_mode,
            )
            if cap_mode == CAP_MODE_PREFER_QUADS:
                cap_faces_to_process.extend(front_faces + back_faces)
            for face in front_faces + back_faces:
                for vertex in face.verts:
                    if vertex not in all_new_vertices:
                        all_new_vertices.append(vertex)

    return (
        _faces_from_new_vertices(bm, all_new_vertices),
        cap_faces_to_process,
        all_new_vertices,
    )


def _prefer_quads_on_cap_faces(bm, cap_faces):
    bm.normal_update()
    cap_vertex_sets = [
        set(face.verts)
        for face in cap_faces
        if face.is_valid
    ]
    faces_to_triangulate = [
        face for face in cap_faces
        if face.is_valid and (
            len(face.verts) > 4
            or _face_is_concave_quad(face)
        )
    ]
    if not faces_to_triangulate:
        return

    result = bmesh.ops.triangulate(
        bm,
        faces=faces_to_triangulate,
        quad_method='BEAUTY',
        ngon_method='BEAUTY',
    )
    triangles = [
        face for face in result.get('faces', [])
        if face.is_valid and len(face.verts) == 3
    ]
    if triangles:
        bmesh.ops.join_triangles(
            bm,
            faces=triangles,
            cmp_seam=False,
            cmp_sharp=False,
            cmp_uvs=False,
            cmp_vcols=False,
            cmp_materials=False,
            angle_face_threshold=0.01,
            angle_shape_threshold=0.698132,
        )

    bm.normal_update()
    concave_quads = [
        face for face in bm.faces
        if face.is_valid and any(
            set(face.verts) <= cap_vertices
            for cap_vertices in cap_vertex_sets
        )
        if _face_is_concave_quad(face)
    ]
    if concave_quads:
        bmesh.ops.triangulate(
            bm,
            faces=concave_quads,
            quad_method='BEAUTY',
            ngon_method='BEAUTY',
        )


def _face_is_concave_quad(face):
    if len(face.verts) != 4:
        return False

    coordinates = [vertex.co for vertex in face.verts]
    return any(
        (
            coordinates[(index + 1) % 4] - coordinates[index]
        ).cross(
            coordinates[(index + 2) % 4]
            - coordinates[(index + 1) % 4]
        ).dot(face.normal) < -EPSILON
        for index in range(4)
    )


def _faces_from_new_vertices(bm, new_vertices):
    new_vertex_set = set(new_vertices)
    return [
        face for face in bm.faces
        if face.is_valid and set(face.verts) <= new_vertex_set
    ]


def _create_cap_faces(bm, ring_vertices, desired_normal, cap_mode):
    if cap_mode == CAP_MODE_NONE:
        return []
    if cap_mode in {CAP_MODE_NGON, CAP_MODE_PREFER_QUADS}:
        return [
            _new_oriented_face(
                bm,
                tuple(ring_vertices),
                desired_normal,
            )
        ]
    if cap_mode != CAP_MODE_TRIANGLE_FAN:
        raise ValueError(f"Unknown cap mode: {cap_mode}")

    center = sum(
        (vertex.co for vertex in ring_vertices),
        Vector((0.0, 0.0, 0.0)),
    ) / len(ring_vertices)
    center_vertex = bm.verts.new(center)
    faces = []
    for index, vertex in enumerate(ring_vertices):
        following = ring_vertices[(index + 1) % len(ring_vertices)]
        faces.append(_new_oriented_face(
            bm,
            (center_vertex, vertex, following),
            desired_normal,
        ))
    return faces


def _new_oriented_face(bm, vertices, desired_normal):
    winding = list(vertices)
    if _polygon_normal([vertex.co for vertex in winding]).dot(desired_normal) < 0:
        winding.reverse()
    return bm.faces.new(winding)


def _polygon_normal(vertices):
    normal = Vector((0.0, 0.0, 0.0))
    for index, current in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        normal.x += (current.y - following.y) * (current.z + following.z)
        normal.y += (current.z - following.z) * (current.x + following.x)
        normal.z += (current.x - following.x) * (current.y + following.y)
    if normal.length <= EPSILON:
        raise ValueError("Profile face must enclose a non-zero area")
    normal.normalize()
    return normal
