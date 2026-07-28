"""UV Transform Modal - GPU drawing for ghost texture preview and handles.

Draws:
1. A semi-transparent textured quad showing the full texture tile in 3D space
2. Interactive handles at corners (scale), center (move), and top (rotation)
3. A face outline highlight
"""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


# Visual constants
GHOST_ALPHA = 0.4
FACE_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.6)
HANDLE_COLOR_CORNER = (1.0, 0.8, 0.0, 0.9)
HANDLE_COLOR_MOVE = (0.3, 0.7, 1.0, 0.9)
HANDLE_COLOR_ROTATION = (0.3, 1.0, 0.5, 0.9)
# U axis (horizontal/left-right) — red. V axis (vertical/up-down) — purple.
HANDLE_COLOR_AXIS_U = (1.0, 0.35, 0.3, 0.9)
HANDLE_COLOR_AXIS_V = (0.75, 0.4, 1.0, 0.9)
HANDLE_COLOR_HOVER = (1.0, 1.0, 1.0, 1.0)
HANDLE_COLOR_REPETITION = (0.3, 0.7, 1.0, 0.45)
QUAD_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.35)
PIXEL_REFERENCE_COLOR = (0.2, 0.85, 1.0, 0.85)
HANDLE_CORNER_RADIUS = 7.0
HANDLE_MOVE_RADIUS = 8.0
HANDLE_ROTATION_RADIUS = 7.0
HANDLE_REPETITION_RADIUS = 6.0
HANDLE_BAR_HALF_LENGTH = 8.5
HANDLE_BAR_HALF_WIDTH = 3.5

# Shader source for textured quad (image sampling in 3D)
_VERT_SRC = (
    "void main()"
    "{"
    "  uv_interp = uv;"
    "  gl_Position = viewProjectionMatrix * vec4(pos, 1.0);"
    "}"
)

_FRAG_SRC = (
    "void main()"
    "{"
    "  vec4 tex_color = texture(image, uv_interp);"
    "  FragColor = vec4(tex_color.rgb, tex_color.a * alpha);"
    "}"
)

_image_shader = None


def _ensure_image_shader():
    """Create the textured quad shader on first use."""
    global _image_shader
    if _image_shader is not None:
        return _image_shader

    vert_info = gpu.types.GPUStageInterfaceInfo("uv_transform_iface")
    vert_info.smooth('VEC2', "uv_interp")

    shader_info = gpu.types.GPUShaderCreateInfo()
    shader_info.push_constant('MAT4', "viewProjectionMatrix")
    shader_info.push_constant('FLOAT', "alpha")
    shader_info.sampler(0, 'FLOAT_2D', "image")
    shader_info.vertex_in(0, 'VEC3', "pos")
    shader_info.vertex_in(1, 'VEC2', "uv")
    shader_info.vertex_out(vert_info)
    shader_info.fragment_out(0, 'VEC4', "FragColor")
    shader_info.vertex_source(_VERT_SRC)
    shader_info.fragment_source(_FRAG_SRC)

    _image_shader = gpu.shader.create_from_info(shader_info)
    return _image_shader


def _get_view_projection_matrix():
    """Get the current view-projection matrix from the GPU stack."""
    return gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()


def _get_viewport_size():
    """Get the current viewport size from bpy.context.region."""
    region = bpy.context.region
    return (region.width, region.height)


def draw_ghost_texture(quad_corners, blender_image, use_linear_filter):
    """Draw a semi-transparent textured quad in 3D space.

    Args:
        quad_corners: list of 4 Vector3 [BL, BR, TR, TL]
        blender_image: bpy.types.Image to sample from
        use_linear_filter: True for linear interpolation, False for nearest
            (point/closest) — matches the material's Image Texture
            interpolation setting.
    """
    if blender_image is None:
        return

    shader = _ensure_image_shader()

    # Get or create GPU texture from the Blender image. The texture is
    # cached/shared with other Blender consumers, so its sampler state
    # must be re-applied every draw call rather than assumed.
    gpu_texture = gpu.texture.from_image(blender_image)
    gpu_texture.filter_mode(use_linear_filter)

    # Build triangulated quad (two triangles: BL-BR-TR, BL-TR-TL)
    positions = [
        quad_corners[0][:], quad_corners[1][:], quad_corners[2][:],
        quad_corners[0][:], quad_corners[2][:], quad_corners[3][:],
    ]
    uvs = [
        (0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
        (0.0, 0.0), (1.0, 1.0), (0.0, 1.0),
    ]

    batch = batch_for_shader(shader, 'TRIS', {"pos": positions, "uv": uvs})

    shader.bind()
    shader.uniform_float("viewProjectionMatrix", _get_view_projection_matrix())
    shader.uniform_sampler("image", gpu_texture)
    shader.uniform_float("alpha", GHOST_ALPHA)
    batch.draw(shader)


def draw_quad_outline(quad_corners):
    """Draw the outline of the texture quad."""
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')

    # Quad edges: BL->BR->TR->TL->BL
    positions = [c[:] for c in quad_corners] + [quad_corners[0][:]]

    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": positions})

    shader.bind()
    shader.uniform_float("color", QUAD_OUTLINE_COLOR)
    shader.uniform_float("lineWidth", 1.5)
    shader.uniform_float("viewportSize", _get_viewport_size())
    batch.draw(shader)


def draw_face_outline(face_corners_3d):
    """Draw a highlight outline around the selected face."""
    if not face_corners_3d:
        return

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')

    positions = [c[:] for c in face_corners_3d] + [face_corners_3d[0][:]]
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": positions})

    shader.bind()
    shader.uniform_float("color", FACE_OUTLINE_COLOR)
    shader.uniform_float("lineWidth", 2.0)
    shader.uniform_float("viewportSize", _get_viewport_size())
    batch.draw(shader)


def draw_pixel_snap_reference(reference_vertex, quad_corners):
    """Draw a subtle diamond around the active pixel-snap reference vertex."""
    if reference_vertex is None:
        return

    bl, br, _tr, tl = quad_corners
    right_dir = br - bl
    up_dir = tl - bl
    right_len = right_dir.length
    up_len = up_dir.length
    if right_len < 0.0001 or up_len < 0.0001:
        return

    right_dir /= right_len
    up_dir /= up_len
    marker_size = (right_len + up_len) * 0.0125
    positions = [
        (reference_vertex + up_dir * marker_size)[:],
        (reference_vertex + right_dir * marker_size)[:],
        (reference_vertex - up_dir * marker_size)[:],
        (reference_vertex - right_dir * marker_size)[:],
        (reference_vertex + up_dir * marker_size)[:],
    ]

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": positions})
    shader.bind()
    shader.uniform_float("color", PIXEL_REFERENCE_COLOR)
    shader.uniform_float("lineWidth", 2.0)
    shader.uniform_float("viewportSize", _get_viewport_size())
    batch.draw(shader)


def draw_handles_2d(
        handle_layout, hover_type, hover_index,
        drag_type, drag_index, ui_scale):
    """Draw constant-size handle indicators in screen space (POST_PIXEL)."""
    if handle_layout is None:
        return

    visible_corner_indices = list(
        handle_layout['visible_corner_indices']
    )
    visible_edge_indices = list(handle_layout['visible_edge_indices'])
    show_move_axis_v = handle_layout['show_move_axis_v']
    show_move_axis_h = handle_layout['show_move_axis_h']
    show_move_free = handle_layout['show_move_free']
    show_rotation = handle_layout['show_rotation']

    # A drag may cross a decluttering threshold. Keep its active handle drawn
    # until release even when that tier would normally hide it.
    if drag_type == 'corner' and drag_index not in visible_corner_indices:
        visible_corner_indices.append(drag_index)
    elif drag_type == 'edge' and drag_index not in visible_edge_indices:
        visible_edge_indices.append(drag_index)
    elif drag_type == 'move_v':
        show_move_axis_v = True
    elif drag_type == 'move_h':
        show_move_axis_h = True
    elif drag_type == 'move_free':
        show_move_free = True
    elif drag_type == 'rotation':
        show_rotation = True

    corner_radius = HANDLE_CORNER_RADIUS * ui_scale
    move_radius = HANDLE_MOVE_RADIUS * ui_scale
    rotation_radius = HANDLE_ROTATION_RADIUS * ui_scale
    bar_half_length = HANDLE_BAR_HALF_LENGTH * ui_scale
    bar_half_width = HANDLE_BAR_HALF_WIDTH * ui_scale

    if show_rotation:
        top_mid = handle_layout['edge_midpoints'][2]
        rot_pos = handle_layout['rotation']
        line_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        line_batch = batch_for_shader(line_shader, 'LINE_STRIP', {
            "pos": [top_mid[:], rot_pos[:]]
        })
        line_shader.bind()
        line_shader.uniform_float(
            "color", HANDLE_COLOR_ROTATION[:3] + (0.5,)
        )
        line_shader.uniform_float("lineWidth", 1.0)
        line_shader.uniform_float("viewportSize", _get_viewport_size())
        line_batch.draw(line_shader)

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    def _draw_diamond_2d(center, radius, color):
        top = center + Vector((0.0, radius))
        right = center + Vector((radius, 0.0))
        bottom = center - Vector((0.0, radius))
        left = center - Vector((radius, 0.0))
        positions = [
            center[:], top[:], right[:],
            center[:], right[:], bottom[:],
            center[:], bottom[:], left[:],
            center[:], left[:], top[:],
        ]
        batch = batch_for_shader(shader, 'TRIS', {"pos": positions})
        shader.uniform_float("color", color)
        batch.draw(shader)

    def _draw_bar_2d(center, along_dir, color):
        across_dir = Vector((-along_dir.y, along_dir.x))
        along = along_dir * bar_half_length
        across = across_dir * bar_half_width
        p0 = center - along - across
        p1 = center + along - across
        p2 = center + along + across
        p3 = center - along + across
        positions = [p0[:], p1[:], p2[:], p0[:], p2[:], p3[:]]
        batch = batch_for_shader(shader, 'TRIS', {"pos": positions})
        shader.uniform_float("color", color)
        batch.draw(shader)

    shader.bind()

    # Corner handles (scale both axes)
    for i in visible_corner_indices:
        pos = handle_layout['corners'][i]
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'corner' and hover_index == i
            else HANDLE_COLOR_CORNER
        )
        _draw_diamond_2d(pos, corner_radius, color)

    # Edge handles (axis-locked resize). Even indices are horizontal edges
    # (bottom/top) which scale V; odd indices are vertical edges which
    # scale U. Draw as bars aligned with the edge they sit on.
    for i in visible_edge_indices:
        pos = handle_layout['edge_midpoints'][i]
        if i % 2 == 0:  # horizontal edge → V axis resize
            base_color = HANDLE_COLOR_AXIS_V
            along_dir = handle_layout['axis_u']
        else:           # vertical edge → U axis resize
            base_color = HANDLE_COLOR_AXIS_U
            along_dir = handle_layout['axis_v']
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'edge' and hover_index == i
            else base_color
        )
        _draw_bar_2d(pos, along_dir, color)

    # Axis-constrained move handles (bars aligned with their active axis)
    if show_move_axis_v:
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'move_v'
            else HANDLE_COLOR_AXIS_V
        )
        _draw_bar_2d(
            handle_layout['move_axis_v'], handle_layout['axis_v'], color
        )

    if show_move_axis_h:
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'move_h'
            else HANDLE_COLOR_AXIS_U
        )
        _draw_bar_2d(
            handle_layout['move_axis_h'], handle_layout['axis_u'], color
        )

    # Free-move center handle (unconstrained)
    if show_move_free:
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'move_free'
            else HANDLE_COLOR_MOVE
        )
        _draw_diamond_2d(handle_layout['center'], move_radius, color)

    # Rotation handle
    if show_rotation:
        color = (
            HANDLE_COLOR_HOVER
            if hover_type == 'rotation'
            else HANDLE_COLOR_ROTATION
        )
        _draw_diamond_2d(
            handle_layout['rotation'], rotation_radius, color
        )


def draw_repetition_handles_2d(
        repetition_layouts, hover_repetition, ui_scale):
    """Draw lightweight activation handles for inactive visible UV tiles."""
    if not repetition_layouts:
        return

    radius = HANDLE_REPETITION_RADIUS * ui_scale
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    normal_positions = []
    hover_positions = []

    for layout in repetition_layouts:
        center = layout['center']
        top = center + Vector((0.0, radius))
        right = center + Vector((radius, 0.0))
        bottom = center - Vector((0.0, radius))
        left = center - Vector((radius, 0.0))
        positions = [
            center[:], top[:], right[:],
            center[:], right[:], bottom[:],
            center[:], bottom[:], left[:],
            center[:], left[:], top[:],
        ]
        repeat_key = (layout['repeat_u'], layout['repeat_v'])
        if hover_repetition == repeat_key:
            hover_positions.extend(positions)
        else:
            normal_positions.extend(positions)

    def draw_batch(positions, color):
        if not positions:
            return
        batch = batch_for_shader(shader, 'TRIS', {"pos": positions})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)

    draw_batch(normal_positions, HANDLE_COLOR_REPETITION)
    draw_batch(hover_positions, HANDLE_COLOR_HOVER)
