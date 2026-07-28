"""UV Transform Modal - Handle interaction logic.

Hit-testing, drag state management, and transform computation for
the scale/offset/rotation handles.
"""

import math
from mathutils import Vector

from bpy_extras.view3d_utils import location_3d_to_region_2d


# Handle sizing and spacing in screen pixels. These values are scaled by
# Blender's UI scale at the call site.
HANDLE_HIT_RADIUS = 11.0
FULL_HANDLE_HALF_EXTENT = 36.0
COMPACT_HANDLE_HALF_EXTENT = 14.0
MINIMAL_SCALE_CORNER_INDEX = 2
_SCREEN_AXIS_EPSILON = 0.001
# Minimum drag distance (pixels) before a drag starts
DRAG_THRESHOLD = 4
# Rotation handle distance factor (proportion of average quad half-size)
ROTATION_HANDLE_DISTANCE = 0.25
# Axis-constrained move handle distance (proportion of center→edge-midpoint)
MOVE_AXIS_HANDLE_DISTANCE = 0.4


def _project_to_screen(region, rv3d, point_3d):
    """Project a 3D point to 2D screen coordinates. Returns None if behind camera."""
    return location_3d_to_region_2d(region, rv3d, point_3d)


def compute_texture_quad_3d(face_center, proj_x, proj_y, scale_u, scale_v,
                            tex_meters_u, tex_meters_v, offset_x, offset_y):
    """Compute the 4 corners of the full texture tile in 3D world space.

    The texture tile is the region UV (0,0)-(1,1) projected back into world space.

    Returns list of 4 Vector3 corners: [bottom-left, bottom-right, top-right, top-left]
    in the face's plane.
    """
    # The first loop vertex is the UV origin. The texture quad in UV space is
    # the unit square (0,0)-(1,1). We need to find where UV (0,0) maps to in
    # 3D, then build the quad from there.
    #
    # From apply_uv_to_face:
    #   u = x / (scale_u * tex_meters_u) + offset_x
    #   v = y / (scale_v * tex_meters_v) + offset_y
    #
    # Inverting: x = (u - offset_x) * scale_u * tex_meters_u
    #            y = (v - offset_y) * scale_v * tex_meters_v
    #
    # UV (0,0) -> x = -offset_x * scale_u * tex_meters_u
    #             y = -offset_y * scale_v * tex_meters_v
    # UV (1,0) -> x = (1 - offset_x) * scale_u * tex_meters_u
    # UV (0,1) -> y = (1 - offset_y) * scale_v * tex_meters_v

    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v

    # 3D displacement from face_center (first vertex) for each UV corner
    def uv_to_3d(u, v):
        x = (u - offset_x) * su
        y = (v - offset_y) * sv
        return face_center + proj_x * x + proj_y * y

    bl = uv_to_3d(0.0, 0.0)
    br = uv_to_3d(1.0, 0.0)
    tr = uv_to_3d(1.0, 1.0)
    tl = uv_to_3d(0.0, 1.0)

    return [bl, br, tr, tl]


def compute_handle_positions(quad_corners):
    """Compute handle positions from the texture quad corners.

    Returns dict with:
        'corners': list of 4 corner positions (for scale handles)
        'edge_midpoints': list of 4 edge midpoint positions (for axis-locked resize)
        'center': center of the quad (for unconstrained move)
        'move_axis_v': vertical-only move handle (offset from center toward top edge)
        'move_axis_h': horizontal-only move handle (offset from center toward right edge)
        'rotation': rotation handle position (offset from center along top edge normal)
    """
    bl, br, tr, tl = quad_corners

    center = (bl + br + tr + tl) * 0.25

    edge_midpoints = [
        (bl + br) * 0.5,  # bottom
        (br + tr) * 0.5,  # right
        (tr + tl) * 0.5,  # top
        (tl + bl) * 0.5,  # left
    ]

    # Axis-constrained move handles, offset partway from center toward an edge
    # midpoint so the user can grab them distinctly from the free-move center.
    move_axis_v = center + (edge_midpoints[2] - center) * MOVE_AXIS_HANDLE_DISTANCE
    move_axis_h = center + (edge_midpoints[1] - center) * MOVE_AXIS_HANDLE_DISTANCE

    # Rotation handle: extend from center past the top midpoint
    top_mid = edge_midpoints[2]
    top_dir = (top_mid - center)
    top_len = top_dir.length
    if top_len > 0.0001:
        rotation_pos = top_mid + top_dir.normalized() * (top_len * ROTATION_HANDLE_DISTANCE)
    else:
        rotation_pos = top_mid

    return {
        'corners': list(quad_corners),
        'edge_midpoints': edge_midpoints,
        'center': center,
        'move_axis_v': move_axis_v,
        'move_axis_h': move_axis_h,
        'rotation': rotation_pos,
    }


def compute_handle_screen_layout(region, rv3d, quad_corners, ui_scale):
    """Project exact handle anchors and select an uncluttered visibility tier.

    Full-size quads show every control. Smaller quads hide the axis-specific
    controls, and extremely small quads show free move plus one both-axis
    corner scale handle. No displayed handle is moved away from its true
    projected anchor.

    Returns None when the handle center cannot be projected into the viewport.
    """
    handle_positions = compute_handle_positions(quad_corners)
    corners = [
        _project_to_screen(region, rv3d, position)
        for position in handle_positions['corners']
    ]
    edge_midpoints = [
        _project_to_screen(region, rv3d, position)
        for position in handle_positions['edge_midpoints']
    ]
    center = _project_to_screen(region, rv3d, handle_positions['center'])
    move_axis_v = _project_to_screen(
        region, rv3d, handle_positions['move_axis_v']
    )
    move_axis_h = _project_to_screen(
        region, rv3d, handle_positions['move_axis_h']
    )
    rotation = _project_to_screen(
        region, rv3d, handle_positions['rotation']
    )
    projected_positions = (
        corners + edge_midpoints
        + [center, move_axis_v, move_axis_h, rotation]
    )
    if any(position is None for position in projected_positions):
        return None

    axis_u = edge_midpoints[1] - center
    axis_v = edge_midpoints[2] - center

    if axis_u.length > _SCREEN_AXIS_EPSILON:
        axis_u_direction = axis_u.normalized()
    elif axis_v.length > _SCREEN_AXIS_EPSILON:
        axis_u_direction = Vector((axis_v.y, -axis_v.x)).normalized()
    else:
        axis_u_direction = Vector((1.0, 0.0))

    if axis_v.length > _SCREEN_AXIS_EPSILON:
        axis_v_direction = axis_v.normalized()
    elif axis_u.length > _SCREEN_AXIS_EPSILON:
        axis_v_direction = Vector((-axis_u.y, axis_u.x)).normalized()
    else:
        axis_v_direction = Vector((0.0, 1.0))

    shortest_half_extent = min(axis_u.length, axis_v.length)
    full_threshold = FULL_HANDLE_HALF_EXTENT * ui_scale
    compact_threshold = COMPACT_HANDLE_HALF_EXTENT * ui_scale

    if shortest_half_extent >= full_threshold:
        visible_corner_indices = (0, 1, 2, 3)
        visible_edge_indices = (0, 1, 2, 3)
        show_move_axis_v = True
        show_move_axis_h = True
        show_move_free = True
        show_rotation = True
    elif shortest_half_extent >= compact_threshold:
        visible_corner_indices = (0, 1, 2, 3)
        visible_edge_indices = ()
        show_move_axis_v = False
        show_move_axis_h = False
        show_move_free = True
        show_rotation = True
    else:
        visible_corner_indices = (MINIMAL_SCALE_CORNER_INDEX,)
        visible_edge_indices = ()
        show_move_axis_v = False
        show_move_axis_h = False
        show_move_free = True
        show_rotation = False

    return {
        'corners': corners,
        'edge_midpoints': edge_midpoints,
        'center': center,
        'move_axis_v': move_axis_v,
        'move_axis_h': move_axis_h,
        'rotation': rotation,
        'axis_u': axis_u_direction,
        'axis_v': axis_v_direction,
        'visible_corner_indices': visible_corner_indices,
        'visible_edge_indices': visible_edge_indices,
        'show_move_axis_v': show_move_axis_v,
        'show_move_axis_h': show_move_axis_h,
        'show_move_free': show_move_free,
        'show_rotation': show_rotation,
    }


def hit_test_handles(mouse_pos, handle_layout, hit_radius):
    """Test which handle (if any) the mouse is over.

    Args:
        mouse_pos: (x, y) tuple of mouse position in region coords
        handle_layout: screen-space dict from compute_handle_screen_layout
        hit_radius: maximum screen-space distance from a handle center

    Returns:
        Tuple of (handle_type, handle_index) or (None, None).
        handle_type is one of: 'corner', 'edge', 'move_free',
        'move_v', 'move_h', 'rotation'.
    """
    if handle_layout is None:
        return None, None

    mx, my = mouse_pos
    best_dist = hit_radius
    best_type = None
    best_index = None

    # Test rotation handle first (highest priority since it's smallest target)
    if handle_layout['show_rotation']:
        screen = handle_layout['rotation']
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_dist = dist
            best_type = 'rotation'
            best_index = 0

    # Test corner handles (scale)
    for i in handle_layout['visible_corner_indices']:
        screen = handle_layout['corners'][i]
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_dist = dist
            best_type = 'corner'
            best_index = i

    # Test edge handles (axis-locked resize)
    for i in handle_layout['visible_edge_indices']:
        screen = handle_layout['edge_midpoints'][i]
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_dist = dist
            best_type = 'edge'
            best_index = i

    # Test axis-constrained move handles (checked before free-move center
    # so they win when overlapping the center handle's hit radius)
    if handle_layout['show_move_axis_v']:
        screen = handle_layout['move_axis_v']
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_dist = dist
            best_type = 'move_v'
            best_index = 0

    if handle_layout['show_move_axis_h']:
        screen = handle_layout['move_axis_h']
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_dist = dist
            best_type = 'move_h'
            best_index = 0

    # Test center handle (unconstrained move)
    if handle_layout['show_move_free']:
        screen = handle_layout['center']
        dist = math.hypot(screen.x - mx, screen.y - my)
        if dist < best_dist:
            best_type = 'move_free'
            best_index = 0

    return best_type, best_index


def compute_scale_offset_from_corner_drag(dragged_3d, corner_index, fixed_quad_corners,
                                          first_vert_world, proj_x, proj_y,
                                          tex_meters_u, tex_meters_v):
    """Compute new scale and offset from a bounding-box-style corner drag.

    The opposite corner stays fixed while the dragged corner moves freely.
    This adjusts both scale and offset so that only the dragged edges move.

    Args:
        dragged_3d: New 3D position of the dragged corner (on face plane)
        corner_index: Which corner is being dragged (0=BL, 1=BR, 2=TR, 3=TL)
        fixed_quad_corners: The quad corners from when the drag started
        first_vert_world: The first vertex of the face in world space
        proj_x, proj_y: Rotated projection axes in world space
        tex_meters_u, tex_meters_v: Texture dimensions in meters

    Returns:
        (scale_u, scale_v, offset_x, offset_y)
    """
    # UV coordinates for each quad corner
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]

    opposite_index = (corner_index + 2) % 4
    fixed_pos = fixed_quad_corners[opposite_index]

    du, dv = CORNER_UVS[corner_index]
    fu, fv = CORNER_UVS[opposite_index]

    # Project positions onto texture axes relative to first vertex
    fixed_x = (fixed_pos - first_vert_world).dot(proj_x)
    fixed_y = (fixed_pos - first_vert_world).dot(proj_y)
    dragged_x = (dragged_3d - first_vert_world).dot(proj_x)
    dragged_y = (dragged_3d - first_vert_world).dot(proj_y)

    # su = scale_u * tex_meters_u (total tile size in world units along U).
    # denom is always +1 or -1 since opposite corners differ in both u and v.
    # Sign of su/sv is meaningful: a negative value means the user dragged the
    # corner past its opposite and the texture is now mirrored along that axis.
    su = (dragged_x - fixed_x) / (du - fu)
    sv = (dragged_y - fixed_y) / (dv - fv)

    # Derive offset so the fixed corner stays in place. apply_uv_to_face
    # skips writes when |scale * tex_meters| is near zero, so division here
    # is safe as long as the caller doesn't divide by the returned scale.
    offset_x = fu - fixed_x / su if abs(su) > 1e-8 else 0.0
    offset_y = fv - fixed_y / sv if abs(sv) > 1e-8 else 0.0

    scale_u = su / tex_meters_u
    scale_v = sv / tex_meters_v

    return scale_u, scale_v, offset_x, offset_y


def recompute_offset_for_fixed_corner(corner_index, fixed_quad_corners,
                                      first_vert_world, proj_x, proj_y,
                                      scale_u, scale_v,
                                      tex_meters_u, tex_meters_v):
    """Recompute offset to keep the opposite corner fixed after scale snapping.

    After snapping scale values, the offset must be recalculated so the
    opposite corner of the quad stays in its original position.

    Returns:
        (offset_x, offset_y)
    """
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]

    opposite_index = (corner_index + 2) % 4
    fixed_pos = fixed_quad_corners[opposite_index]
    fu, fv = CORNER_UVS[opposite_index]

    fixed_x = (fixed_pos - first_vert_world).dot(proj_x)
    fixed_y = (fixed_pos - first_vert_world).dot(proj_y)

    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v

    offset_x = fu - fixed_x / su if abs(su) > 1e-8 else 0.0
    offset_y = fv - fixed_y / sv if abs(sv) > 1e-8 else 0.0

    return offset_x, offset_y


# Which corner on the opposite edge to use as the fixed reference for edge-drag.
# edge 0 (bottom): opposite edge is top; fixed corner is TR (index 2)
# edge 1 (right):  opposite edge is left; fixed corner is BL (index 0)
# edge 2 (top):    opposite edge is bottom; fixed corner is BL (index 0)
# edge 3 (left):   opposite edge is right; fixed corner is BR (index 1)
_EDGE_FIXED_CORNER_IDX = [2, 0, 0, 1]

# (axis, this_edge_uv_coord, fixed_edge_uv_coord) per edge
_EDGE_UV_INFO = [
    ('v', 0.0, 1.0),
    ('u', 1.0, 0.0),
    ('v', 1.0, 0.0),
    ('u', 0.0, 1.0),
]


def compute_scale_offset_from_edge_drag(dragged_3d, edge_index, fixed_quad_corners,
                                        first_vert_world, proj_x, proj_y,
                                        tex_meters_u, tex_meters_v,
                                        drag_start_scale_u, drag_start_scale_v,
                                        drag_start_offset_x, drag_start_offset_y):
    """Compute new scale/offset from an axis-locked edge drag.

    Edges 0/2 (bottom/top) control scale_v; edges 1/3 (right/left) control
    scale_u. The perpendicular scale/offset are held at their drag-start
    values; the parallel offset is recomputed so the opposite edge stays
    pinned to its original world-space position.

    Returns (scale_u, scale_v, offset_x, offset_y).
    """
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]
    axis, drag_edge_coord, _fixed_edge_coord = _EDGE_UV_INFO[edge_index]

    fixed_corner_idx = _EDGE_FIXED_CORNER_IDX[edge_index]
    fixed_pos = fixed_quad_corners[fixed_corner_idx]
    fu, fv = CORNER_UVS[fixed_corner_idx]

    fixed_x = (fixed_pos - first_vert_world).dot(proj_x)
    fixed_y = (fixed_pos - first_vert_world).dot(proj_y)
    dragged_x = (dragged_3d - first_vert_world).dot(proj_x)
    dragged_y = (dragged_3d - first_vert_world).dot(proj_y)

    scale_u = drag_start_scale_u
    scale_v = drag_start_scale_v
    offset_x = drag_start_offset_x
    offset_y = drag_start_offset_y

    # Sign of su/sv is meaningful: a negative value means the dragged edge
    # crossed the pinned edge and the texture is now mirrored along that axis.
    if axis == 'u':
        delta_u = drag_edge_coord - fu  # ±1
        su = (dragged_x - fixed_x) / delta_u
        scale_u = su / tex_meters_u
        offset_x = fu - fixed_x / su if abs(su) > 1e-8 else offset_x
    else:
        delta_v = drag_edge_coord - fv  # ±1
        sv = (dragged_y - fixed_y) / delta_v
        scale_v = sv / tex_meters_v
        offset_y = fv - fixed_y / sv if abs(sv) > 1e-8 else offset_y

    return scale_u, scale_v, offset_x, offset_y


def recompute_offset_for_fixed_edge(edge_index, fixed_quad_corners,
                                    first_vert_world, proj_x, proj_y,
                                    scale_u, scale_v,
                                    tex_meters_u, tex_meters_v,
                                    drag_start_offset_x, drag_start_offset_y):
    """Recompute the axis-parallel offset after scale snapping.

    Only the offset for the edge's active axis is recomputed; the
    perpendicular offset is returned from drag_start (unchanged).
    """
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]
    axis, _drag_edge_coord, _fixed_edge_coord = _EDGE_UV_INFO[edge_index]

    fixed_corner_idx = _EDGE_FIXED_CORNER_IDX[edge_index]
    fixed_pos = fixed_quad_corners[fixed_corner_idx]
    fu, fv = CORNER_UVS[fixed_corner_idx]

    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v

    offset_x = drag_start_offset_x
    offset_y = drag_start_offset_y

    if axis == 'u':
        if abs(su) > 1e-8:
            fixed_x = (fixed_pos - first_vert_world).dot(proj_x)
            offset_x = fu - fixed_x / su
    else:
        if abs(sv) > 1e-8:
            fixed_y = (fixed_pos - first_vert_world).dot(proj_y)
            offset_y = fv - fixed_y / sv

    return offset_x, offset_y


def snap_edge_drag_corners_to_face(edge_index, first_vert_world,
                                   proj_x, proj_y,
                                   scale_u, scale_v,
                                   tex_meters_u, tex_meters_v,
                                   offset_x, offset_y,
                                   face_edges, threshold):
    """Snap either corner of a stretched edge along its active axis.

    Both corners on the dragged edge move by the same amount, so this keeps
    the perpendicular scale unchanged. If both corners reach separate face
    edges at the same axis position, the shared movement snaps both.

    Returns (scale_u, scale_v), with only the stretched axis possibly changed.
    """
    edge_corner_indices = ((0, 1), (1, 2), (2, 3), (3, 0))
    axis, drag_edge_coord, fixed_edge_coord = _EDGE_UV_INFO[edge_index]
    delta_uv = drag_edge_coord - fixed_edge_coord

    quad_corners = compute_texture_quad_3d(
        first_vert_world, proj_x, proj_y,
        scale_u, scale_v, tex_meters_u, tex_meters_v,
        offset_x, offset_y
    )
    moving_corners = [
        quad_corners[index] for index in edge_corner_indices[edge_index]
    ]

    movement_axis = proj_x if axis == 'u' else proj_y
    snap_delta = _snap_quad_vertices_to_edges_along_axis(
        moving_corners, face_edges, proj_x, proj_y,
        movement_axis, threshold
    )
    if snap_delta is None:
        return scale_u, scale_v

    if axis == 'u' and abs(tex_meters_u) > 1e-8:
        scale_u += snap_delta.dot(proj_x) / (delta_uv * tex_meters_u)
    elif axis == 'v' and abs(tex_meters_v) > 1e-8:
        scale_v += snap_delta.dot(proj_y) / (delta_uv * tex_meters_v)

    return scale_u, scale_v


def _snap_scale_along_axis(adj_pos, fixed_pos, axis, perp_axis, delta_uv,
                           face_edges, threshold, min_scale):
    """Find the best scale snap for an adjacent corner along one axis.

    The adjacent corner moves along a line: fixed_pos + d * axis (with a
    constant perpendicular offset of zero, since adjacent corners share one
    UV coordinate with the fixed corner).

    For edges: find where the edge intersects the corner's movement line
    (fixed_pos + d * axis) so the snap target is stable regardless of
    the current mouse position.

    Returns the snapped scale (world-space, i.e. scale * tex_meters), or None.
    A negative return value indicates the edge lies on the mirrored side of
    the fixed corner — valid when the user has dragged the quad through itself.
    """
    current_dist = (adj_pos - fixed_pos).dot(axis)
    best_snap = None
    best_delta = threshold

    for a, b in face_edges:
        # Find where the edge crosses the movement line
        # Movement line: fixed_pos + d * axis (perp component = 0)
        # Edge: a + t * (b - a)
        # At intersection: (a + t * edge - fixed_pos).dot(perp_axis) = 0
        edge = b - a
        edge_perp = edge.dot(perp_axis)
        if abs(edge_perp) < 1e-10:
            # Edge is parallel to the movement line — no crossing
            continue
        t = -(a - fixed_pos).dot(perp_axis) / edge_perp
        if t < 0.0 or t > 1.0:
            continue
        crossing = a + edge * t
        crossing_dist = (crossing - fixed_pos).dot(axis)
        candidate = crossing_dist / delta_uv
        # Reject near-zero magnitudes (degenerate), but allow either sign so a
        # mirrored quad can snap to face edges on the far side of the pivot.
        if abs(candidate) < min_scale:
            continue
        delta = abs(current_dist - crossing_dist)
        if delta < best_delta:
            best_delta = delta
            best_snap = candidate

    return best_snap


def snap_adjacent_corners_to_face(corner_index, fixed_quad_corners,
                                  first_vert_world, proj_x, proj_y,
                                  scale_u, scale_v,
                                  tex_meters_u, tex_meters_v,
                                  face_edges, threshold):
    """Snap the two adjacent (non-fixed, non-dragged) corners to face features.

    When dragging a corner, the opposite corner is fixed and the two adjacent
    corners move.  Each adjacent corner controls one scale axis:
    - The corner sharing the dragged corner's U controls scale_u
    - The corner sharing the dragged corner's V controls scale_v

    Snapping is done along one axis only per adjacent corner, so distance is
    measured purely along the controlled axis rather than in full 3D.

    Returns:
        (scale_u, scale_v) — possibly adjusted.
    """
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]

    opposite_index = (corner_index + 2) % 4
    drag_u, drag_v = CORNER_UVS[corner_index]
    fixed_u, fixed_v = CORNER_UVS[opposite_index]
    fixed_pos = fixed_quad_corners[opposite_index]

    # Compute current positions of adjacent corners from the current scale/offset
    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v
    offset_x, offset_y = recompute_offset_for_fixed_corner(
        corner_index, fixed_quad_corners,
        first_vert_world, proj_x, proj_y,
        scale_u, scale_v, tex_meters_u, tex_meters_v
    )

    def _corner_pos(u, v):
        x = (u - offset_x) * su
        y = (v - offset_y) * sv
        return first_vert_world + proj_x * x + proj_y * y

    # Check adj_su (controls scale_u): shares U with dragged corner
    adj_su_uv = (drag_u, fixed_v)
    adj_su_pos = _corner_pos(adj_su_uv[0], adj_su_uv[1])
    delta_u = drag_u - fixed_u  # always +1 or -1
    snapped_su = _snap_scale_along_axis(
        adj_su_pos, fixed_pos, proj_x, proj_y, delta_u,
        face_edges, threshold, 0.001 * tex_meters_u
    )
    if snapped_su is not None:
        scale_u = snapped_su / tex_meters_u

    # Check adj_sv (controls scale_v): shares V with dragged corner
    # Recompute with potentially updated scale_u
    su = scale_u * tex_meters_u
    offset_x, offset_y = recompute_offset_for_fixed_corner(
        corner_index, fixed_quad_corners,
        first_vert_world, proj_x, proj_y,
        scale_u, scale_v, tex_meters_u, tex_meters_v
    )
    adj_sv_uv = (fixed_u, drag_v)
    adj_sv_pos = _corner_pos(adj_sv_uv[0], adj_sv_uv[1])
    delta_v = drag_v - fixed_v  # always +1 or -1
    snapped_sv = _snap_scale_along_axis(
        adj_sv_pos, fixed_pos, proj_y, proj_x, delta_v,
        face_edges, threshold, 0.001 * tex_meters_v
    )
    if snapped_sv is not None:
        scale_v = snapped_sv / tex_meters_v

    return scale_u, scale_v


def compute_offset_from_drag(drag_start_3d, drag_current_3d,
                             proj_x, proj_y, start_offset_x, start_offset_y,
                             scale_u, scale_v, tex_meters_u, tex_meters_v):
    """Compute new offset values from a move drag.

    Offset changes are in UV tile units: a drag of one full texture tile
    in 3D space = 1.0 offset change.
    """
    delta_3d = drag_current_3d - drag_start_3d

    # Project delta onto texture axes
    delta_along_u = delta_3d.dot(proj_x)
    delta_along_v = delta_3d.dot(proj_y)

    # Convert 3D distance to UV offset (inverse of the projection)
    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v

    # Negate: increasing offset shifts the texture opposite to the drag
    # direction (u = x/su + offset, so higher offset = texture moves left).
    # We want the texture to follow the drag, so subtract.
    delta_offset_x = 0.0
    delta_offset_y = 0.0
    if abs(su) > 0.0001:
        delta_offset_x = -delta_along_u / su
    if abs(sv) > 0.0001:
        delta_offset_y = -delta_along_v / sv

    return start_offset_x + delta_offset_x, start_offset_y + delta_offset_y


def snap_offsets_to_reference_vertex_pixel_corner(
        reference_vertex, first_vert_world, proj_x, proj_y,
        scale_u, scale_v, tex_meters_u, tex_meters_v,
        pixel_width, pixel_height, offset_x, offset_y, snap_u, snap_v):
    """Snap a reference vertex's UV coordinate to the nearest pixel corner."""
    delta = reference_vertex - first_vert_world
    su = scale_u * tex_meters_u
    sv = scale_v * tex_meters_v

    if snap_u and pixel_width > 0 and abs(su) > 1e-8:
        u = delta.dot(proj_x) / su + offset_x
        pixel_u = u * pixel_width
        offset_x += (round(pixel_u) - pixel_u) / pixel_width

    if snap_v and pixel_height > 0 and abs(sv) > 1e-8:
        v = delta.dot(proj_y) / sv + offset_y
        pixel_v = v * pixel_height
        offset_y += (round(pixel_v) - pixel_v) / pixel_height

    return offset_x, offset_y


def snap_scale_to_furthest_vertex_pixel_seam(
        vertices, fixed_pos, axis, uv_direction, current_scale,
        tex_meters, pixel_count):
    """Snap scale so the furthest vertex in the UV direction lies on a seam.

    ``fixed_pos`` is a texture-tile boundary held in place by the resize
    operation. The returned scale quantizes the number of pixels between that
    boundary and the furthest selected vertex while preserving scale sign.
    """
    if (not vertices or pixel_count <= 0 or abs(current_scale) < 1e-8
            or abs(tex_meters) < 1e-8):
        return current_scale

    scale_sign = 1.0 if current_scale > 0.0 else -1.0
    world_direction = axis * (uv_direction * scale_sign)
    furthest = max(
        vertices,
        key=lambda vertex: (vertex - fixed_pos).dot(world_direction),
    )
    signed_distance = (furthest - fixed_pos).dot(axis)
    if abs(signed_distance) < 1e-8:
        return current_scale

    current_pixel_span = (
        signed_distance / (current_scale * tex_meters) * pixel_count
    )
    snapped_pixel_span = round(current_pixel_span)
    if snapped_pixel_span == 0:
        return current_scale

    snapped_scale = (
        signed_distance * pixel_count / (snapped_pixel_span * tex_meters)
    )
    if abs(snapped_scale) < 1e-8:
        return current_scale
    return snapped_scale


def compute_rotation_from_drag(drag_current_3d, quad_center, proj_x, proj_y):
    """Compute new rotation from a rotation handle drag.

    Rotation is computed as the angle of the drag point relative to
    the quad center, projected onto the face plane. proj_x/proj_y are
    the unrotated face-local axes so the returned angle is absolute.
    Returns None if the drag point coincides with the quad center.
    """
    delta = drag_current_3d - quad_center

    dx = delta.dot(proj_x)
    dy = delta.dot(proj_y)

    if abs(dx) < 0.0001 and abs(dy) < 0.0001:
        return None

    # The rotation handle starts at the top of the quad (along +V),
    # which is 90 degrees from the +U axis
    return math.degrees(math.atan2(dx, dy))


def snap_value(value, snap_increment):
    """Snap a value to the nearest increment."""
    if snap_increment <= 0:
        return value
    return round(value / snap_increment) * snap_increment


def ray_plane_intersection(ray_origin, ray_direction, plane_point, plane_normal):
    """Intersect a ray with a plane. Returns the 3D intersection point or None."""
    denom = ray_direction.dot(plane_normal)
    if abs(denom) < 1e-8:
        return None
    t = (plane_point - ray_origin).dot(plane_normal) / denom
    if t < 0:
        return None
    return ray_origin + ray_direction * t


# ---------------------------------------------------------------------------
#  Snap helpers
# ---------------------------------------------------------------------------

# Thresholds for proximity snaps
ASPECT_SNAP_THRESHOLD = 0.08    # scale ratio tolerance for 1:1 snap
VERTEX_SNAP_DISTANCE = 0.05     # world-space distance for vertex snaps
EDGE_SNAP_DISTANCE = 0.05       # world-space distance for edge snaps
ROTATION_SNAP_DEGREES = 3.0     # degree tolerance for edge-angle snap
PARALLEL_EDGE_TOLERANCE = 0.01  # sin(angle) tolerance for edge parallelism


def snap_point_to_grid(point_3d, grid_size):
    """Snap a 3D point to the world grid on all axes."""
    return Vector((
        round(point_3d.x / grid_size) * grid_size,
        round(point_3d.y / grid_size) * grid_size,
        round(point_3d.z / grid_size) * grid_size,
    ))


def snap_aspect_ratio(scale_u, scale_v):
    """If scale_u and scale_v are close to each other, snap to 1:1 ratio.

    Only snaps when both scales have the same sign — opposite-sign scales
    represent a single-axis mirror, which is not an aspect-ratio issue.

    Returns (scale_u, scale_v) — possibly modified to match.
    """
    if abs(scale_u) < 0.001 or abs(scale_v) < 0.001:
        return scale_u, scale_v
    if (scale_u > 0) != (scale_v > 0):
        return scale_u, scale_v
    ratio = scale_u / scale_v
    if abs(ratio - 1.0) < ASPECT_SNAP_THRESHOLD:
        avg = (scale_u + scale_v) * 0.5
        return avg, avg
    return scale_u, scale_v


def snap_aspect_ratio_on_axis(scale_u, scale_v, active_axis):
    """Snap only the active scale axis to 1:1 when it is close.

    Edge drags resize one axis while the perpendicular axis remains locked.
    This keeps that locked axis untouched and moves only the dragged edge.
    """
    if abs(scale_u) < 0.001 or abs(scale_v) < 0.001:
        return scale_u, scale_v
    if (scale_u > 0) != (scale_v > 0):
        return scale_u, scale_v
    ratio = scale_u / scale_v
    if abs(ratio - 1.0) >= ASPECT_SNAP_THRESHOLD:
        return scale_u, scale_v
    if active_axis == 'u':
        return scale_v, scale_v
    if active_axis == 'v':
        return scale_u, scale_u
    return scale_u, scale_v


def snap_edge_and_aspect(edge_a, edge_b, corner_index, fixed_quad_corners,
                         first_vert_world, proj_x, proj_y,
                         tex_meters_u, tex_meters_v,
                         scale_u, scale_v):
    """Slide the dragged corner along a face edge to achieve 1:1 aspect ratio.

    When the dragged corner is snapped to a face edge and the scales are near
    1:1, this finds the point on the edge where scale_u == scale_v, combining
    both constraints.

    Returns (scale_u, scale_v) if the combined snap applies, or None.
    """
    if abs(scale_u) < 0.001 or abs(scale_v) < 0.001:
        return None
    # Opposite-sign scales represent a single-axis mirror — aspect-ratio
    # snapping doesn't apply.
    if (scale_u > 0) != (scale_v > 0):
        return None
    ratio = scale_u / scale_v
    if abs(ratio - 1.0) >= ASPECT_SNAP_THRESHOLD:
        return None

    # We need to find t along the edge [a, b] such that su(t) == sv(t).
    # The dragged point is P(t) = edge_a + t * (edge_b - edge_a).
    # su(t) = (P(t) - fixed).dot(proj_x) / (du - fu)
    # sv(t) = (P(t) - fixed).dot(proj_y) / (dv - fv)
    # Setting su(t)/tex_meters_u == sv(t)/tex_meters_v and solving for t.
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]
    opposite_index = (corner_index + 2) % 4
    fixed_pos = fixed_quad_corners[opposite_index]
    du, dv = CORNER_UVS[corner_index]
    fu, fv = CORNER_UVS[opposite_index]

    edge_dir = edge_b - edge_a
    base = edge_a - fixed_pos

    # su(t) = (base + t * edge_dir).dot(proj_x) / delta_u
    # sv(t) = (base + t * edge_dir).dot(proj_y) / delta_v
    # We want su(t) / tex_meters_u == sv(t) / tex_meters_v
    # => (base.x + t * dir.x) / (delta_u * tex_meters_u) ==
    #    (base.y + t * dir.y) / (delta_v * tex_meters_v)
    delta_u = du - fu  # +1 or -1
    delta_v = dv - fv  # +1 or -1
    base_x = base.dot(proj_x)
    base_y = base.dot(proj_y)
    dir_x = edge_dir.dot(proj_x)
    dir_y = edge_dir.dot(proj_y)

    # Cross-multiply: (base_x + t*dir_x) * delta_v * tex_meters_v
    #              == (base_y + t*dir_y) * delta_u * tex_meters_u
    a_coeff = dir_x * delta_v * tex_meters_v - dir_y * delta_u * tex_meters_u
    b_coeff = base_y * delta_u * tex_meters_u - base_x * delta_v * tex_meters_v

    if abs(a_coeff) < 1e-10:
        return None

    t = b_coeff / a_coeff
    if t < 0.0 or t > 1.0:
        return None

    # Compute the scales at this point
    point = edge_a + edge_dir * t
    point_rel = point - fixed_pos
    su = point_rel.dot(proj_x) / delta_u
    sv = point_rel.dot(proj_y) / delta_v
    if abs(su) < 0.001 * tex_meters_u or abs(sv) < 0.001 * tex_meters_v:
        return None

    return su / tex_meters_u, sv / tex_meters_v


def snap_point_to_face_features(point_3d, face_vertices, face_edges, threshold):
    """Snap a 3D point to face vertices or edges if close enough.

    Vertices take priority over edges. Within each category the closest
    candidate within the threshold wins.
    Returns (snapped_point, edge_pair_or_none).
    edge_pair_or_none is (a, b) for edge snaps, None for vertex/no snap.
    """
    # Vertex snap — find the closest vertex within threshold
    best_vert = None
    best_vert_dist = threshold
    for vert in face_vertices:
        dist = (point_3d - vert).length
        if dist < best_vert_dist:
            best_vert_dist = dist
            best_vert = vert
    if best_vert is not None:
        return best_vert.copy(), None

    # Edge snap — find the closest edge within threshold
    best_edge_point = None
    best_edge_pair = None
    best_edge_dist = threshold
    for a, b in face_edges:
        edge = b - a
        edge_len_sq = edge.length_squared
        if edge_len_sq < 1e-10:
            continue
        t = (point_3d - a).dot(edge) / edge_len_sq
        t = max(0.0, min(1.0, t))
        closest = a + edge * t
        dist = (point_3d - closest).length
        if dist < best_edge_dist:
            best_edge_dist = dist
            best_edge_point = closest
            best_edge_pair = (a, b)
    if best_edge_point is not None:
        return best_edge_point, best_edge_pair

    return point_3d, None


def snap_quad_vertices_to_face_vertices(quad_corners, face_vertices, threshold):
    """Snap the closest quad corner onto the closest face vertex.

    Returns the offset delta (Vector3) to apply, or None if no pair is
    within threshold. Vertex-only; use as a higher-priority pass before
    any edge-based snap.
    """
    best_delta = None
    best_dist = threshold
    for qc in quad_corners:
        for fv in face_vertices:
            delta = fv - qc
            dist = delta.length
            if 1e-6 < dist < best_dist:
                best_dist = dist
                best_delta = delta
    return best_delta


def _closest_point_on_edge(point, edge_a, edge_b):
    """Return the closest point and interpolation factor on an edge."""
    edge = edge_b - edge_a
    edge_len_sq = edge.length_squared
    if edge_len_sq < 1e-10:
        return None, None
    t = (point - edge_a).dot(edge) / edge_len_sq
    t = max(0.0, min(1.0, t))
    return edge_a + edge * t, t


def _cross_2d(ax, ay, bx, by):
    """Return the scalar cross product of two 2D vectors."""
    return ax * by - ay * bx


def _snap_quad_vertices_to_edges_along_axis(
        quad_corners, face_edges, proj_x, proj_y,
        movement_axis, threshold):
    """Snap the nearest UV corner/face-edge crossing along one move axis."""
    if movement_axis.length_squared < 1e-10:
        return None

    axis = movement_axis.normalized()
    axis_x = axis.dot(proj_x)
    axis_y = axis.dot(proj_y)
    best_delta = None
    best_distance = threshold

    for corner in quad_corners:
        for edge_a, edge_b in face_edges:
            edge = edge_b - edge_a
            edge_x = edge.dot(proj_x)
            edge_y = edge.dot(proj_y)
            determinant = _cross_2d(axis_x, axis_y, edge_x, edge_y)
            if abs(determinant) < 1e-10:
                continue

            relative = edge_a - corner
            relative_x = relative.dot(proj_x)
            relative_y = relative.dot(proj_y)
            axis_distance = _cross_2d(
                relative_x, relative_y, edge_x, edge_y
            ) / determinant
            edge_factor = _cross_2d(
                relative_x, relative_y, axis_x, axis_y
            ) / determinant

            if edge_factor < 0.0 or edge_factor > 1.0:
                continue

            distance = abs(axis_distance)
            if distance <= 1e-6 or distance >= best_distance:
                continue

            delta = axis * axis_distance
            moved_corner = corner + delta
            edge_point = edge_a + edge * edge_factor
            if (moved_corner - edge_point).length > 1e-5:
                continue

            best_distance = distance
            best_delta = delta

    return best_delta


def _solve_edge_snap_pair(first, second, threshold):
    """Solve the translation that satisfies two corner-to-edge constraints."""
    normal_a = first['normal']
    normal_b = second['normal']
    distance_a = first['distance']
    distance_b = second['distance']
    normal_dot = normal_a.dot(normal_b)
    determinant = 1.0 - normal_dot * normal_dot

    if determinant < 1e-4:
        # Parallel constraints can combine only when both request essentially
        # the same translation. Opposing or offset targets conflict.
        delta_a = normal_a * distance_a
        delta_b = normal_b * distance_b
        if (delta_a - delta_b).length > 1e-5:
            return None
        delta = (delta_a + delta_b) * 0.5
    else:
        coeff_a = (
            distance_a - normal_dot * distance_b
        ) / determinant
        coeff_b = (
            distance_b - normal_dot * distance_a
        ) / determinant
        delta = normal_a * coeff_a + normal_b * coeff_b

    # Two perpendicular snaps can legitimately combine into a diagonal move,
    # but shallow-angle solutions must not pull the preview from far away.
    if delta.length > threshold * math.sqrt(2.0):
        return None

    for constraint in (first, second):
        moved_corner = constraint['corner'] + delta
        closest, _t = _closest_point_on_edge(
            moved_corner, constraint['edge_a'], constraint['edge_b']
        )
        if closest is None or (moved_corner - closest).length > 1e-5:
            return None

    return delta


def snap_quad_vertices_to_face_edges(quad_corners, face_edges,
                                     proj_x, proj_y, threshold,
                                     movement_axis):
    """Snap UV corners onto one or two compatible face edges.

    Translation has two degrees of freedom in the face plane, so two UV
    corners can snap to separate, non-parallel face edges at the same time.
    When no compatible pair exists, the closest single corner-to-edge snap is
    returned to preserve the normal one-target behavior. For an axis-locked
    move, movement_axis selects the nearest corner/edge intersection reachable
    by travelling strictly along that axis.
    """
    if movement_axis is not None:
        return _snap_quad_vertices_to_edges_along_axis(
            quad_corners, face_edges, proj_x, proj_y,
            movement_axis, threshold
        )

    constraints = []
    best_single_delta = None
    best_single_dist = threshold

    for corner_index, corner in enumerate(quad_corners):
        for edge_a, edge_b in face_edges:
            closest, t = _closest_point_on_edge(corner, edge_a, edge_b)
            if closest is None:
                continue

            single_delta = closest - corner
            single_dist = single_delta.length
            if 1e-6 < single_dist < best_single_dist:
                best_single_dist = single_dist
                best_single_delta = single_delta

            # Endpoint proximity is handled by the higher-priority face-vertex
            # snap. Multi-edge constraints use the interior of each edge.
            if t <= 1e-6 or t >= 1.0 - 1e-6 or single_dist >= threshold:
                continue

            edge = edge_b - edge_a
            edge_x = edge.dot(proj_x)
            edge_y = edge.dot(proj_y)
            normal = proj_x * -edge_y + proj_y * edge_x
            if normal.length_squared < 1e-10:
                continue
            normal.normalize()

            constraints.append({
                'corner_index': corner_index,
                'corner': corner,
                'edge_a': edge_a,
                'edge_b': edge_b,
                'normal': normal,
                'distance': single_delta.dot(normal),
                'original_distance': single_dist,
            })

    best_pair_delta = None
    best_pair_score = None
    for first_index, first in enumerate(constraints):
        for second in constraints[first_index + 1:]:
            if first['corner_index'] == second['corner_index']:
                continue
            delta = _solve_edge_snap_pair(first, second, threshold)
            if delta is None or delta.length <= 1e-6:
                continue
            score = (
                delta.length,
                first['original_distance'] + second['original_distance'],
            )
            if best_pair_score is None or score < best_pair_score:
                best_pair_score = score
                best_pair_delta = delta

    if best_pair_delta is not None:
        return best_pair_delta
    return best_single_delta


def compute_face_edge_angles(face_edges, face_local_x, face_local_y):
    """Compute the angles (in degrees) of each face edge in face-local space.

    Returns a list of angles, one per edge.
    """
    angles = []
    for a, b in face_edges:
        edge = b - a
        dx = edge.dot(face_local_x)
        dy = edge.dot(face_local_y)
        angles.append(math.degrees(math.atan2(dx, dy)))
    return angles


def snap_rotation_to_face_edges(rotation, face_edge_angles):
    """Snap rotation to a face edge angle if close enough.

    The quad edges are at rotation, rotation+90, rotation+180, rotation+270.
    If any of these is close to a face edge angle, snap.

    Returns the snapped rotation (or original if no snap).
    """
    best_rot = rotation
    best_diff = ROTATION_SNAP_DEGREES

    for quad_offset in (0.0, 90.0, 180.0, 270.0):
        quad_angle = rotation + quad_offset
        for face_angle in face_edge_angles:
            # Compute minimal angular difference
            diff = (quad_angle - face_angle + 180.0) % 360.0 - 180.0
            if abs(diff) < best_diff:
                best_diff = abs(diff)
                best_rot = rotation - diff
    return best_rot


def _snap_scale_to_parallel_face_edge(fixed_pos, axis, perp_axis, delta_uv,
                                      face_edges, current_scale_world,
                                      threshold, min_scale_world):
    """Snap a preview edge (at axis-coord = current_scale_world * delta_uv)
    to a face edge that runs perpendicular to axis (i.e. parallel to perp_axis).

    Such face edges have constant axis-coord; snapping aligns the preview edge
    with that coord regardless of whether the edge spans the movement line.
    All scales are in world units (scale * tex_meters).
    Returns the snapped world-scale, or None.
    """
    current_dist = current_scale_world * delta_uv
    best_snap = None
    best_delta = threshold
    for a, b in face_edges:
        edge = b - a
        edge_len = edge.length
        if edge_len < 1e-10:
            continue
        # Accept only edges whose direction is (nearly) along perp_axis
        if abs(edge.dot(axis) / edge_len) > PARALLEL_EDGE_TOLERANCE:
            continue
        face_axis_coord = (a - fixed_pos).dot(axis)
        candidate = face_axis_coord / delta_uv
        # Reject near-zero magnitudes (degenerate), but allow either sign so a
        # mirrored quad can snap to face edges on the far side of the pivot.
        if abs(candidate) < min_scale_world:
            continue
        delta = abs(current_dist - face_axis_coord)
        if delta < best_delta:
            best_delta = delta
            best_snap = candidate
    return best_snap


def snap_scale_to_parallel_face_edges(corner_index, fixed_quad_corners,
                                      proj_x, proj_y,
                                      scale_u, scale_v,
                                      tex_meters_u, tex_meters_v,
                                      face_edges, threshold):
    """Snap scale by bringing a dragged-side preview edge flush with a
    parallel face edge.

    Complements snap_adjacent_corners_to_face for the case where a face edge
    is parallel to a preview edge but lies outside the adjacent corner's
    movement line — typical when the preview is much larger than the face.

    Returns (scale_u, scale_v) — each axis possibly adjusted.
    """
    CORNER_UVS = [(0, 0), (1, 0), (1, 1), (0, 1)]
    opposite_index = (corner_index + 2) % 4
    drag_u, drag_v = CORNER_UVS[corner_index]
    fixed_u, fixed_v = CORNER_UVS[opposite_index]
    fixed_pos = fixed_quad_corners[opposite_index]
    delta_u = drag_u - fixed_u
    delta_v = drag_v - fixed_v

    # scale_u controls the vertical preview edge (parallel to proj_y)
    snapped_su = _snap_scale_to_parallel_face_edge(
        fixed_pos, proj_x, proj_y, delta_u,
        face_edges,
        scale_u * tex_meters_u,
        threshold, 0.001 * tex_meters_u,
    )
    if snapped_su is not None:
        scale_u = snapped_su / tex_meters_u

    # scale_v controls the horizontal preview edge (parallel to proj_x)
    snapped_sv = _snap_scale_to_parallel_face_edge(
        fixed_pos, proj_y, proj_x, delta_v,
        face_edges,
        scale_v * tex_meters_v,
        threshold, 0.001 * tex_meters_v,
    )
    if snapped_sv is not None:
        scale_v = snapped_sv / tex_meters_v

    return scale_u, scale_v


def _best_perp_snap_to_face_edge(preview_points, perp_axis,
                                 face_edges, threshold):
    """Smallest perp-axis shift that brings one of preview_points onto a
    face edge running perpendicular to perp_axis (i.e. the preview edges
    at those points are parallel to the face edge).

    Returns the signed perp shift, or None.
    """
    best_delta = None
    best_dist = threshold
    for point in preview_points:
        for a, b in face_edges:
            edge = b - a
            edge_len = edge.length
            if edge_len < 1e-10:
                continue
            # Face edge must run perpendicular to perp_axis
            if abs(edge.dot(perp_axis) / edge_len) > PARALLEL_EDGE_TOLERANCE:
                continue
            face_perp = (a - point).dot(perp_axis)
            dist = abs(face_perp)
            if 1e-6 < dist < best_dist:
                best_dist = dist
                best_delta = face_perp
    return best_delta


def snap_quad_edges_to_parallel_face_edges(quad_corners, face_edges,
                                           proj_x, proj_y, threshold):
    """Translation that brings preview edges flush with parallel face edges.

    Each preview axis (proj_x, proj_y) is snapped independently so orthogonal
    alignments combine. Complements snap_quad_vertices_to_face_edges when the
    preview is larger than the face and no preview corner is near a face
    vertex/edge intersection.

    Returns a 3D Vector delta, or None if no snap.
    """
    bl, br, tr, tl = quad_corners
    bottom_mid = (bl + br) * 0.5
    top_mid = (tl + tr) * 0.5
    left_mid = (bl + tl) * 0.5
    right_mid = (br + tr) * 0.5

    delta_y = _best_perp_snap_to_face_edge(
        [bottom_mid, top_mid], proj_y, face_edges, threshold,
    )
    delta_x = _best_perp_snap_to_face_edge(
        [left_mid, right_mid], proj_x, face_edges, threshold,
    )

    if delta_x is None and delta_y is None:
        return None

    delta = Vector((0.0, 0.0, 0.0))
    if delta_x is not None:
        delta = delta + proj_x * delta_x
    if delta_y is not None:
        delta = delta + proj_y * delta_y
    return delta
