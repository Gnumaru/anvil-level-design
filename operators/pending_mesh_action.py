"""Durable pending mesh actions and their invalidation rules.

The action payload lives on the mesh so Blender history restores it. A small
runtime guard captures the producer's final geometry and selection; later
changes dismiss the action. History handlers re-arm restored payloads.
"""

from dataclasses import dataclass
import math

import bmesh
import bpy
from mathutils import Vector

from ..core.geometry import are_verts_coplanar
from ..core.logging import debug_log


_MODE_LAYER = "_aw_mode"
_DEPTH_LAYER = "_aw_depth"
_DX_LAYER = "_aw_dx"
_DY_LAYER = "_aw_dy"
_DZ_LAYER = "_aw_dz"
_BPO_LAYER = "_aw_bpo"
_FACE_LAYER = "_aw_face"
_CUBOID_LAYERS = (
    "_aw_cox", "_aw_coy", "_aw_coz",
    "_aw_lxx", "_aw_lxy", "_aw_lxz",
    "_aw_lyx", "_aw_lyy", "_aw_lyz",
    "_aw_cdx", "_aw_cdy",
)
_CYLINDER_LAYERS = (
    "_aw_ccx", "_aw_ccy", "_aw_ccz",
    "_aw_crxx", "_aw_crxy", "_aw_crxz",
    "_aw_cryx", "_aw_cryy", "_aw_cryz",
    "_aw_czx", "_aw_czy", "_aw_czz",
)
_CYLINDER_SIDE_COUNT_LAYER = "_aw_csc"
_CYLINDER_RADIUS_MODE_LAYER = "_aw_crm"
_COPLANAR_LAYER = "_aw_copl"
_OBJECT_MODE_PROP = "_aw_mode"
_MODE_TO_INT = {
    'NONE': 0,
    'BRIDGE': 1,
    'CORRIDOR': 2,
    'INVERT': 3,
    'FOLDED_PLANE': 4,
}
_INT_TO_MODE = {value: key for key, value in _MODE_TO_INT.items()}
_RADIUS_MODE_TO_INT = {'EDGES': 1, 'FACES': 2}
_INT_TO_RADIUS_MODE = {value: key for key, value in _RADIUS_MODE_TO_INT.items()}
_FOLDED_EPSILON = 1e-4


@dataclass(frozen=True)
class PendingMeshAction:
    kind: str
    depth: float
    direction: tuple
    back_plane_offset: float
    face_indices: tuple
    cuboid_params: object
    cylinder_params: object
    coplanar_blocked: int
    object_mode: bool


@dataclass(frozen=True)
class _ActionGuard:
    object_pointer: int
    kind: str
    object_mode: bool
    geometry: tuple
    selection: tuple


_armed_action = None


def _selection_signature(bm):
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    active_face = bm.faces.active.index if bm.faces.active is not None else -1
    return (
        tuple(v.index for v in bm.verts if v.select),
        tuple(e.index for e in bm.edges if e.select),
        tuple(f.index for f in bm.faces if f.select),
        active_face,
    )


def _bmesh_geometry_signature(bm):
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return (
        tuple((v.index, tuple(v.co)) for v in bm.verts),
        tuple((e.index, tuple(sorted(v.index for v in e.verts))) for e in bm.edges),
        tuple((f.index, tuple(v.index for v in f.verts)) for f in bm.faces),
    )


def _mesh_geometry_signature(mesh):
    return (
        tuple((v.index, tuple(v.co)) for v in mesh.vertices),
        tuple((e.index, tuple(e.vertices)) for e in mesh.edges),
        tuple((p.index, tuple(p.vertices)) for p in mesh.polygons),
    )


def _arm(obj, kind, object_mode, bm):
    global _armed_action
    object_pointer = obj.as_pointer()
    if (
            _armed_action is not None
            and (
                _armed_action.object_pointer != object_pointer
                or _armed_action.object_mode != object_mode
            )):
        # A new producer replaces the one available context action. Clear the
        # prior durable payload too, otherwise history or file loading can
        # resurrect an action that is no longer reachable at runtime.
        _dismiss_armed_action(None)
    geometry = _mesh_geometry_signature(obj.data) if object_mode else _bmesh_geometry_signature(bm)
    selection = () if object_mode else _selection_signature(bm)
    _armed_action = _ActionGuard(
        object_pointer, kind, object_mode, geometry, selection,
    )
    debug_log(f"[ContextAction] Armed pending action on '{obj.name}'")


def reset_runtime_state():
    global _armed_action
    _armed_action = None


def get_pending_action_kind(obj, context_mode):
    """Return the armed action kind without reading or fingerprinting geometry."""
    if _armed_action is None or obj is None:
        return 'NONE'
    object_mode = context_mode != 'EDIT_MESH'
    if (
            _armed_action.object_pointer != obj.as_pointer()
            or _armed_action.object_mode != object_mode):
        return 'NONE'
    return _armed_action.kind


def _set_on_bmesh(
        bm,
        kind,
        depth,
        direction,
        back_plane_offset,
        box_faces,
        cuboid_params,
        cylinder_params,
        coplanar_blocked):
    box_indices = {face.index for face in box_faces} if box_faces is not None else None
    mode_layer = bm.verts.layers.int.get(_MODE_LAYER) or bm.verts.layers.int.new(_MODE_LAYER)
    depth_layer = bm.verts.layers.float.get(_DEPTH_LAYER) or bm.verts.layers.float.new(_DEPTH_LAYER)
    dx_layer = bm.verts.layers.float.get(_DX_LAYER) or bm.verts.layers.float.new(_DX_LAYER)
    dy_layer = bm.verts.layers.float.get(_DY_LAYER) or bm.verts.layers.float.new(_DY_LAYER)
    dz_layer = bm.verts.layers.float.get(_DZ_LAYER) or bm.verts.layers.float.new(_DZ_LAYER)
    bpo_layer = bm.verts.layers.float.get(_BPO_LAYER) or bm.verts.layers.float.new(_BPO_LAYER)
    face_layer = None
    if box_indices is not None:
        face_layer = bm.faces.layers.int.get(_FACE_LAYER) or bm.faces.layers.int.new(_FACE_LAYER)
    cuboid_layers = ()
    if cuboid_params is not None:
        cuboid_layers = tuple(
            bm.verts.layers.float.get(name) or bm.verts.layers.float.new(name)
            for name in _CUBOID_LAYERS
        )
    cylinder_layers = ()
    cylinder_side_count_layer = None
    cylinder_radius_mode_layer = None
    if cylinder_params is not None:
        cylinder_layers = tuple(
            bm.verts.layers.float.get(name) or bm.verts.layers.float.new(name)
            for name in _CYLINDER_LAYERS
        )
        cylinder_side_count_layer = (
            bm.verts.layers.int.get(_CYLINDER_SIDE_COUNT_LAYER)
            or bm.verts.layers.int.new(_CYLINDER_SIDE_COUNT_LAYER)
        )
        cylinder_radius_mode_layer = (
            bm.verts.layers.int.get(_CYLINDER_RADIUS_MODE_LAYER)
            or bm.verts.layers.int.new(_CYLINDER_RADIUS_MODE_LAYER)
        )
    coplanar_layer = None
    if coplanar_blocked is not None:
        coplanar_layer = (
            bm.verts.layers.int.get(_COPLANAR_LAYER)
            or bm.verts.layers.int.new(_COPLANAR_LAYER)
        )

    # Creating any custom-data layer can invalidate existing element refs.
    bm.verts.ensure_lookup_table()
    first_vert = bm.verts[0]
    first_vert[mode_layer] = _MODE_TO_INT.get(kind, 0)
    first_vert[depth_layer] = depth
    first_vert[dx_layer] = direction[0]
    first_vert[dy_layer] = direction[1]
    first_vert[dz_layer] = direction[2]
    first_vert[bpo_layer] = back_plane_offset

    if box_indices is not None:
        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face[face_layer] = 1 if face.index in box_indices else 0

    if cuboid_params is not None:
        origin, local_x, local_y, cdx, cdy = cuboid_params
        values = (
            origin[0], origin[1], origin[2],
            local_x[0], local_x[1], local_x[2],
            local_y[0], local_y[1], local_y[2],
            cdx, cdy,
        )
        for layer, value in zip(cuboid_layers, values):
            first_vert[layer] = value

    if cylinder_params is not None:
        center, radius_x, radius_y, local_z, side_count, radius_mode = cylinder_params
        values = tuple(center) + tuple(radius_x) + tuple(radius_y) + tuple(local_z)
        for layer, value in zip(cylinder_layers, values):
            first_vert[layer] = value
        first_vert[cylinder_side_count_layer] = side_count
        first_vert[cylinder_radius_mode_layer] = _RADIUS_MODE_TO_INT[radius_mode]

    if coplanar_blocked is not None:
        first_vert[coplanar_layer] = coplanar_blocked


def _read_from_bmesh(bm):
    mode_layer = bm.verts.layers.int.get(_MODE_LAYER)
    if mode_layer is None or not bm.verts:
        return None
    bm.verts.ensure_lookup_table()
    first_vert = bm.verts[0]
    kind = _INT_TO_MODE.get(first_vert[mode_layer], 'NONE')
    if kind == 'NONE':
        return None

    def float_value(name):
        layer = bm.verts.layers.float.get(name)
        return first_vert[layer] if layer is not None else 0.0

    cuboid_params = None
    if kind == 'FOLDED_PLANE' and bm.verts.layers.float.get(_CUBOID_LAYERS[0]) is not None:
        values = tuple(float_value(name) for name in _CUBOID_LAYERS)
        cuboid_params = (
            Vector(values[0:3]),
            Vector(values[3:6]),
            Vector(values[6:9]),
            values[9],
            values[10],
        )
    cylinder_params = None
    cylinder_side_count_layer = bm.verts.layers.int.get(
        _CYLINDER_SIDE_COUNT_LAYER
    )
    cylinder_radius_mode_layer = bm.verts.layers.int.get(
        _CYLINDER_RADIUS_MODE_LAYER
    )
    if (
            kind == 'FOLDED_PLANE'
            and cylinder_side_count_layer is not None
            and first_vert[cylinder_side_count_layer] > 0
            and cylinder_radius_mode_layer is not None):
        values = tuple(float_value(name) for name in _CYLINDER_LAYERS)
        radius_mode = _INT_TO_RADIUS_MODE.get(
            first_vert[cylinder_radius_mode_layer]
        )
        if radius_mode is not None:
            cylinder_params = (
                Vector(values[0:3]),
                Vector(values[3:6]),
                Vector(values[6:9]),
                Vector(values[9:12]),
                first_vert[cylinder_side_count_layer],
                radius_mode,
            )
    coplanar_layer = bm.verts.layers.int.get(_COPLANAR_LAYER)
    coplanar_blocked = first_vert[coplanar_layer] if coplanar_layer is not None else 0
    face_layer = bm.faces.layers.int.get(_FACE_LAYER)
    face_indices = ()
    if face_layer is not None:
        bm.faces.index_update()
        face_indices = tuple(face.index for face in bm.faces if face[face_layer] != 0)
    return PendingMeshAction(
        kind,
        float_value(_DEPTH_LAYER),
        (float_value(_DX_LAYER), float_value(_DY_LAYER), float_value(_DZ_LAYER)),
        float_value(_BPO_LAYER),
        face_indices,
        cuboid_params,
        cylinder_params,
        coplanar_blocked,
        False,
    )


def clear_on_bmesh(bm):
    mode_layer = bm.verts.layers.int.get(_MODE_LAYER)
    if mode_layer is not None and bm.verts:
        bm.verts.ensure_lookup_table()
        first_vert = bm.verts[0]
        first_vert[mode_layer] = 0
        for name in (_DEPTH_LAYER, _DX_LAYER, _DY_LAYER, _DZ_LAYER, _BPO_LAYER):
            layer = bm.verts.layers.float.get(name)
            if layer is not None:
                first_vert[layer] = 0.0
        for name in _CUBOID_LAYERS:
            layer = bm.verts.layers.float.get(name)
            if layer is not None:
                first_vert[layer] = 0.0
        for name in _CYLINDER_LAYERS:
            layer = bm.verts.layers.float.get(name)
            if layer is not None:
                first_vert[layer] = 0.0
        for name in (
                _CYLINDER_SIDE_COUNT_LAYER,
                _CYLINDER_RADIUS_MODE_LAYER):
            layer = bm.verts.layers.int.get(name)
            if layer is not None:
                first_vert[layer] = 0
    face_layer = bm.faces.layers.int.get(_FACE_LAYER)
    if face_layer is not None:
        for face in bm.faces:
            face[face_layer] = 0


def _read_object_mode(obj):
    if (
            obj is None
            or obj.type != 'MESH'
            or obj.data is None
            or obj.data.library is not None):
        return None
    kind = obj.data.get(_OBJECT_MODE_PROP, 'NONE')
    if kind not in _MODE_TO_INT or kind == 'NONE':
        return None
    return PendingMeshAction(
        kind, 0.0, (0.0, 0.0, 0.0), 0.0, (), None, None, 0, True,
    )


def _clear_object_mode(obj):
    if obj is not None and obj.type == 'MESH' and obj.data is not None:
        if _OBJECT_MODE_PROP in obj.data:
            del obj.data[_OBJECT_MODE_PROP]


def _find_object_by_pointer(object_pointer):
    for obj in bpy.data.objects:
        if obj.as_pointer() == object_pointer:
            return obj
    return None


def _clear_edit_mode_payload(obj, bm):
    edit_bmesh = bm
    if edit_bmesh is None and obj.data.is_editmode:
        try:
            edit_bmesh = bmesh.from_edit_mesh(obj.data)
        except (ReferenceError, RuntimeError):
            edit_bmesh = None
    if edit_bmesh is not None:
        clear_on_bmesh(edit_bmesh)
        bmesh.update_edit_mesh(obj.data)
        return

    mode_attribute = obj.data.attributes.get(_MODE_LAYER)
    if mode_attribute is not None and mode_attribute.data:
        mode_attribute.data[0].value = 0
        obj.data.update()


def _dismiss_armed_action(bm):
    global _armed_action
    if _armed_action is None:
        return False
    guard = _armed_action
    obj = _find_object_by_pointer(guard.object_pointer)
    if obj is not None and obj.type == 'MESH' and obj.data is not None:
        if guard.object_mode:
            _clear_object_mode(obj)
        else:
            _clear_edit_mode_payload(obj, bm)
        debug_log(f"[ContextAction] Dismissed pending action on '{obj.name}'")
    _armed_action = None
    return True


def _guard_matches(obj, object_mode, bm):
    if _armed_action is None or obj is None:
        return False
    if not object_mode and bm is None:
        return False
    if _armed_action.object_pointer != obj.as_pointer() or _armed_action.object_mode != object_mode:
        return False
    geometry = _mesh_geometry_signature(obj.data) if object_mode else _bmesh_geometry_signature(bm)
    selection = () if object_mode else _selection_signature(bm)
    return geometry == _armed_action.geometry and selection == _armed_action.selection


def resolve_pending_action(obj, context_mode, bm):
    object_mode = context_mode != 'EDIT_MESH'
    if not object_mode and bm is None:
        return None
    action = _read_object_mode(obj) if object_mode else _read_from_bmesh(bm)
    if action is None or not _guard_matches(obj, object_mode, bm):
        return None
    return action


def restore_after_history(obj, context_mode, bm):
    reset_runtime_state()
    if obj is None or obj.type != 'MESH':
        return
    object_mode = context_mode != 'EDIT_MESH'
    if not object_mode and bm is None:
        return
    action = _read_object_mode(obj) if object_mode else _read_from_bmesh(bm)
    if action is not None:
        _arm(obj, action.kind, object_mode, bm)


def validate_pending_action(active_obj, context_mode, bm):
    if _armed_action is None:
        return False
    if active_obj is None or active_obj.as_pointer() != _armed_action.object_pointer:
        return _dismiss_armed_action(None)
    object_mode = context_mode != 'EDIT_MESH'
    if object_mode != _armed_action.object_mode:
        return _dismiss_armed_action(None)
    if not object_mode and bm is None:
        return _dismiss_armed_action(None)
    if _guard_matches(active_obj, object_mode, bm):
        return False
    return _dismiss_armed_action(bm)


def complete_pending_action(obj, action, bm):
    """Clear a validated action after its geometry operation succeeds."""
    global _armed_action
    if (
            _armed_action is None
            or obj is None
            or _armed_action.object_pointer != obj.as_pointer()
            or _armed_action.kind != action.kind
            or _armed_action.object_mode != action.object_mode):
        return False
    if action.object_mode:
        _clear_object_mode(obj)
    else:
        if bm is None:
            return False
        clear_on_bmesh(bm)
    _armed_action = None
    return True


def _count_edge_groups(bm):
    selected_edges = [edge for edge in bm.edges if edge.select]
    if not selected_edges:
        return 0
    remaining = set(selected_edges)
    groups = 0
    while remaining:
        groups += 1
        queue = [remaining.pop()]
        while queue:
            edge = queue.pop()
            neighbours = {
                linked
                for vert in edge.verts
                for linked in vert.link_edges
                if linked in remaining
            }
            remaining.difference_update(neighbours)
            queue.extend(neighbours)
    return groups


def _check_folded_plane(selected_verts, cuboid_params):
    origin, local_x, local_y, cdx, cdy = cuboid_params
    corners = (
        origin,
        origin + local_x * cdx,
        origin + local_x * cdx + local_y * cdy,
        origin + local_y * cdy,
    )
    for corner in corners:
        count = 0
        for vert in selected_verts:
            offset = vert.co - corner
            in_plane = local_x * offset.dot(local_x) + local_y * offset.dot(local_y)
            if in_plane.length < _FOLDED_EPSILON:
                count += 1
        if count >= 2:
            return True
    return False


def build_cylinder_weld_params(
        obj,
        center,
        radius_x,
        radius_y,
        local_x,
        local_y,
        local_z,
        side_count,
        radius_mode):
    """Capture a cylinder profile in mesh-local coordinates for a pending weld."""
    world_to_local = obj.matrix_world.inverted()
    rotation = world_to_local.to_3x3()
    axis_x = Vector(local_x).normalized()
    axis_y = Vector(local_y).normalized()
    axis_z = Vector(local_z).normalized()
    local_depth_axis = rotation @ axis_z
    return (
        world_to_local @ Vector(center),
        rotation @ (axis_x * radius_x),
        rotation @ (axis_y * radius_y),
        local_depth_axis.normalized(),
        side_count,
        radius_mode,
    )


def build_cylinder_weld_profile(cylinder_params):
    """Rebuild the captured cylinder cap profile in mesh-local coordinates."""
    center, radius_x, radius_y, _local_z, side_count, radius_mode = cylinder_params
    angle_step = math.tau / side_count
    angle_offset = 0.0
    radius_scale = 1.0
    if radius_mode == 'FACES':
        angle_offset = angle_step * 0.5
        radius_scale = 1.0 / math.cos(angle_step * 0.5)
    return tuple(
        center
        + radius_x * (math.cos(angle_offset + index * angle_step) * radius_scale)
        + radius_y * (math.sin(angle_offset + index * angle_step) * radius_scale)
        for index in range(side_count)
    )


def _check_folded_cylinder(selected_verts, cylinder_params):
    _center, _radius_x, _radius_y, local_z, _side_count, _radius_mode = cylinder_params
    for first_index, first in enumerate(selected_verts):
        for second in selected_verts[first_index + 1:]:
            difference = second.co - first.co
            depth_distance = difference.dot(local_z)
            if abs(depth_distance) < _FOLDED_EPSILON:
                continue
            off_axis = difference - local_z * depth_distance
            if off_axis.length < _FOLDED_EPSILON:
                return True
    return False


def _derive_kind(bm, depth, cuboid_params, cylinder_params):
    groups = _count_edge_groups(bm)
    if groups == 2:
        return 'BRIDGE', 0.0
    if groups != 1:
        return 'NONE', 0.0
    selected_verts = list({vert for edge in bm.edges if edge.select for vert in edge.verts})
    if abs(depth) > 0 and are_verts_coplanar(selected_verts):
        return 'CORRIDOR', depth
    if cuboid_params is not None and _check_folded_plane(selected_verts, cuboid_params):
        return 'FOLDED_PLANE', depth
    if (
            cylinder_params is not None
            and _check_folded_cylinder(selected_verts, cylinder_params)):
        return 'FOLDED_PLANE', depth
    return 'NONE', 0.0


def store_from_edge_selection(
        obj,
        depth,
        direction,
        back_plane_offset,
        first_vertex,
        second_vertex,
        local_x,
        local_y,
        coplanar_blocked):
    if obj is None or obj.type != 'MESH' or not obj.data.is_editmode:
        return
    bm = bmesh.from_edit_mesh(obj.data)
    world_to_local = obj.matrix_world.inverted()
    rotation = world_to_local.to_3x3()
    origin = world_to_local @ first_vertex
    axis_x = (rotation @ Vector(local_x)).normalized()
    axis_y = (rotation @ Vector(local_y)).normalized()
    world_difference = Vector(second_vertex) - Vector(first_vertex)
    cdx = abs(world_difference.dot(Vector(local_x))) * (rotation @ Vector(local_x)).length
    cdy = abs(world_difference.dot(Vector(local_y))) * (rotation @ Vector(local_y)).length
    local_difference = (world_to_local @ Vector(second_vertex)) - origin
    if local_difference.dot(axis_x) < 0:
        axis_x = -axis_x
    if local_difference.dot(axis_y) < 0:
        axis_y = -axis_y
    cuboid_params = (origin, axis_x, axis_y, cdx, cdy)
    kind, effective_depth = _derive_kind(bm, depth, cuboid_params, None)
    stored_cuboid = cuboid_params if kind == 'FOLDED_PLANE' else None
    stored_coplanar = coplanar_blocked if kind == 'FOLDED_PLANE' else None
    _set_on_bmesh(
        bm, kind, effective_depth, tuple(direction), back_plane_offset,
        None, stored_cuboid, None, stored_coplanar,
    )
    bmesh.update_edit_mesh(obj.data)
    if kind == 'NONE':
        reset_runtime_state()
    else:
        _arm(obj, kind, False, bm)
    debug_log(f"[ContextAction] Stored {kind} on '{obj.name}'")


def store_cylinder_from_edge_selection(
        obj,
        depth,
        direction,
        back_plane_offset,
        cylinder_params):
    """Capture a cylinder cut boundary as a durable context weld action."""
    if obj is None or obj.type != 'MESH' or not obj.data.is_editmode:
        return
    bm = bmesh.from_edit_mesh(obj.data)
    kind, effective_depth = _derive_kind(
        bm, depth, None, cylinder_params,
    )
    stored_cylinder = cylinder_params if kind == 'FOLDED_PLANE' else None
    _set_on_bmesh(
        bm,
        kind,
        effective_depth,
        tuple(direction),
        back_plane_offset,
        None,
        None,
        stored_cylinder,
        None,
    )
    bmesh.update_edit_mesh(obj.data)
    if kind == 'NONE':
        reset_runtime_state()
    else:
        _arm(obj, kind, False, bm)
    debug_log(f"[ContextAction] Stored cylinder {kind} on '{obj.name}'")


def store_from_box_builder(obj, new_face_vert_positions):
    if obj is None or obj.type != 'MESH' or not obj.data.is_editmode:
        return
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    signatures = set(new_face_vert_positions)
    box_faces = []
    for face in bm.faces:
        face_verts = frozenset(tuple(vert.co) for vert in face.verts)
        if (face.index, face_verts) in signatures:
            box_faces.append(face)
    if not box_faces:
        return
    _set_on_bmesh(
        bm, 'INVERT', 0.0, (0.0, 0.0, 0.0), 0.0,
        box_faces, None, None, None,
    )
    bmesh.update_edit_mesh(obj.data)
    _arm(obj, 'INVERT', False, bm)


def store_from_box_builder_object_mode(obj):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return
    obj.data[_OBJECT_MODE_PROP] = 'INVERT'
    _arm(obj, 'INVERT', True, None)


def _ranges_overlap(a_min, a_max, b_min, b_max):
    return a_min < b_max + _FOLDED_EPSILON and b_min < a_max + _FOLDED_EPSILON


def snapshot_coplanar_sides(bm, cuboid_params):
    origin, local_x, local_y, cdx, cdy = cuboid_params
    local_z = local_x.cross(local_y).normalized()
    definitions = (
        (local_x, 0.0, local_x, local_y, local_z),
        (local_x, cdx, -local_x, local_y, local_z),
        (local_y, 0.0, local_y, local_x, local_z),
        (local_y, cdy, -local_y, local_x, local_z),
    )
    bm.normal_update()
    blocked = 0
    for side_index, (filter_axis, offset, inward_normal, u_axis, w_axis) in enumerate(definitions):
        cuboid_u_max = cdx if abs(u_axis.dot(local_x)) > 0.5 else cdy
        for face in bm.faces:
            if not face.is_valid or face.hide:
                continue
            if abs(abs(face.normal.dot(inward_normal)) - 1.0) >= _FOLDED_EPSILON:
                continue
            if not all(abs((vert.co - origin).dot(filter_axis) - offset) < _FOLDED_EPSILON for vert in face.verts):
                continue
            face_us = [(vert.co - origin).dot(u_axis) for vert in face.verts]
            if _ranges_overlap(0.0, cuboid_u_max, min(face_us), max(face_us)):
                blocked |= 1 << side_index
                break
    return blocked
