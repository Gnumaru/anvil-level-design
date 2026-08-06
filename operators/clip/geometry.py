"""Plane clipping and loop-cap geometry for the Clip tool."""

import bmesh
from mathutils import Vector


CLIP_MODE_BISECT = 'BISECT'
CLIP_MODE_REMOVE_ABOVE = 'REMOVE_ABOVE'
CLIP_MODE_REMOVE_BELOW = 'REMOVE_BELOW'
CLIP_MODES = {
    CLIP_MODE_BISECT,
    CLIP_MODE_REMOVE_ABOVE,
    CLIP_MODE_REMOVE_BELOW,
}

PLANE_EPSILON = 1e-5
_TARGET_FACE_LAYER = "_anvil_clip_target"


def _plane_distance(point, plane_co, plane_no):
    return (point - plane_co).dot(plane_no)


def _edge_is_in_plane(edge, plane_co, plane_no):
    return all(
        abs(_plane_distance(vert.co, plane_co, plane_no)) <= PLANE_EPSILON
        for vert in edge.verts
    )


def _clear_selection(bm):
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vert in bm.verts:
        vert.select = False
    bm.select_flush(False)


def quadrangulate_faces(bm, faces):
    """Replace n-gons with triangles and merge suitable pairs into quads."""
    ngons = [
        face
        for face in faces
        if face.is_valid and len(face.verts) > 4
    ]
    if not ngons:
        return

    result = bmesh.ops.triangulate(bm, faces=ngons)
    triangles = [
        face
        for face in result.get('faces', [])
        if face.is_valid
    ]
    if not triangles:
        return

    bmesh.ops.join_triangles(
        bm,
        faces=triangles,
        cmp_seam=False,
        cmp_sharp=False,
        cmp_uvs=False,
        cmp_vcols=False,
        cmp_materials=False,
        angle_face_threshold=3.14159,
        angle_shape_threshold=3.14159,
    )


def _face_crosses_plane(face, plane_co, plane_no):
    distances = [
        _plane_distance(vert.co, plane_co, plane_no)
        for vert in face.verts
    ]
    return (
        any(distance > PLANE_EPSILON for distance in distances)
        and any(distance < -PLANE_EPSILON for distance in distances)
    )


def _tag_target_faces(bm, plane_co, plane_no):
    layer = bm.faces.layers.int.new(_TARGET_FACE_LAYER)

    bm.faces.ensure_lookup_table()
    visible_faces = [face for face in bm.faces if not face.hide]
    selected_faces = [face for face in visible_faces if face.select]
    target_faces = selected_faces if selected_faces else visible_faces
    target_set = set(target_faces)
    for face in bm.faces:
        if face not in target_set:
            face[layer] = 0
        elif _face_crosses_plane(face, plane_co, plane_no):
            face[layer] = 2
        else:
            face[layer] = 1
    return layer, target_faces


def _target_geometry(target_faces):
    edges = {
        edge
        for face in target_faces
        for edge in face.edges
    }
    verts = {
        vert
        for edge in edges
        for vert in edge.verts
    }
    return list(target_faces) + list(edges) + list(verts)


def _local_clip_plane(matrix_world, first_point, second_point, grid_normal):
    line_direction = Vector(second_point) - Vector(first_point)
    if line_direction.length <= PLANE_EPSILON:
        return None
    line_direction.normalize()

    grid_normal = Vector(grid_normal)
    if grid_normal.length <= PLANE_EPSILON:
        return None
    grid_normal.normalize()

    # "Above" is the left-hand side of the drawn line when looking along the
    # grid normal. This same direction drives the modal removal-side preview.
    world_plane_normal = grid_normal.cross(line_direction)
    if world_plane_normal.length <= PLANE_EPSILON:
        return None
    world_plane_normal.normalize()

    world_to_local = matrix_world.inverted()
    local_plane_co = world_to_local @ Vector(first_point)
    local_plane_no = matrix_world.to_3x3().transposed() @ world_plane_normal
    if local_plane_no.length <= PLANE_EPSILON:
        return None
    local_plane_no.normalize()
    return local_plane_co, local_plane_no


def execute_clip(
        obj, tool_settings, first_point, second_point, grid_normal,
        clip_mode, prefer_quads, matrix_world):
    """Bisect visible target faces and optionally discard one plane side."""
    if obj is None or obj.type != 'MESH':
        return (False, "No active mesh object")
    if not obj.data.is_editmode:
        return (False, "Active mesh must be in edit mode")
    if clip_mode not in CLIP_MODES:
        return (False, f"Unknown clip mode: {clip_mode}")

    local_plane = _local_clip_plane(
        matrix_world,
        first_point,
        second_point,
        grid_normal,
    )
    if local_plane is None:
        return (False, "Clip line must have a non-zero length")
    plane_co, plane_no = local_plane

    bm = bmesh.from_edit_mesh(obj.data)
    target_layer, target_faces = _tag_target_faces(
        bm, plane_co, plane_no,
    )
    if not target_faces:
        bm.faces.layers.int.remove(target_layer)
        return (False, "No visible faces to clip")

    clear_outer = clip_mode == CLIP_MODE_REMOVE_ABOVE
    clear_inner = clip_mode == CLIP_MODE_REMOVE_BELOW
    result = bmesh.ops.bisect_plane(
        bm,
        geom=_target_geometry(target_faces),
        dist=PLANE_EPSILON,
        plane_co=plane_co,
        plane_no=plane_no,
        use_snap_center=False,
        clear_outer=clear_outer,
        clear_inner=clear_inner,
    )

    affected_faces = [
        face
        for face in bm.faces
        if face.is_valid and face[target_layer] == 2
    ]
    if prefer_quads:
        quadrangulate_faces(bm, affected_faces)

    cut_result_edges = {
        element
        for element in result.get('geom_cut', [])
        if isinstance(element, bmesh.types.BMEdge) and element.is_valid
    }
    selected_edges = []
    for edge in bm.edges:
        if not edge.is_valid or not _edge_is_in_plane(edge, plane_co, plane_no):
            continue
        belongs_to_target = any(
            face.is_valid and face[target_layer] != 0
            for face in edge.link_faces
        )
        if edge in cut_result_edges or belongs_to_target:
            selected_edges.append(edge)

    _clear_selection(bm)
    bm.select_mode = {'EDGE'}
    for edge in selected_edges:
        edge.select = True
    bm.select_flush_mode()
    tool_settings.mesh_select_mode = (False, True, False)

    bm.faces.layers.int.remove(target_layer)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data)

    mode_label = {
        CLIP_MODE_BISECT: "Bisected",
        CLIP_MODE_REMOVE_ABOVE: "Clipped above",
        CLIP_MODE_REMOVE_BELOW: "Clipped below",
    }[clip_mode]
    return (
        True,
        f"{mode_label} mesh; selected {len(selected_edges)} clip edges",
    )


def find_selected_edge_loops(bm):
    """Return every selected connected component that is a simple loop."""
    remaining = {
        edge
        for edge in bm.edges
        if edge.select and edge.is_valid
    }
    loops = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = [seed]
        while queue:
            edge = queue.pop()
            neighbours = {
                linked_edge
                for vert in edge.verts
                for linked_edge in vert.link_edges
                if linked_edge in remaining
            }
            remaining.difference_update(neighbours)
            component.update(neighbours)
            queue.extend(neighbours)

        vertex_degrees = {}
        for edge in component:
            for vert in edge.verts:
                vertex_degrees[vert] = vertex_degrees.get(vert, 0) + 1
        if len(component) >= 3 and all(
                degree == 2 for degree in vertex_degrees.values()):
            loops.append(tuple(component))
    return loops


def fill_selected_edge_loops(bm, prefer_quads):
    """Fill each selected simple edge loop and return its resulting faces."""
    edge_loops = find_selected_edge_loops(bm)
    created_faces = []
    for edge_loop in edge_loops:
        result = bmesh.ops.contextual_create(bm, geom=list(edge_loop))
        created_faces.extend(
            face
            for face in result.get('faces', [])
            if face.is_valid
        )

    if prefer_quads:
        quadrangulate_faces(bm, created_faces)
    bm.normal_update()
    return created_faces
