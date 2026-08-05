import os

import bpy
import bmesh

from ..core.materials import create_material_with_image, find_material_with_image
from ..core.uv_projection import box_project
from ..core.uv_projection import apply_uv_to_face

TEXTURE_PATH = os.path.join(os.path.dirname(__file__), "dev_orange_wall.png")


def _get_context_override():
    """Build a temp_override context dict for operators in timer callbacks."""
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    return {"window": window}


def get_undo_context():
    """Build a complete 3D View context for Blender undo and redo operators."""
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    screen = window.screen
    area = next(area for area in screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return {
        "window": window,
        "screen": screen,
        "area": area,
        "region": region,
    }


def wait_for_condition(predicate, failure_message):
    """Yield event-loop ticks until a Blender-driven condition becomes true."""
    for _attempt in range(200):
        bpy.context.view_layer.update()
        if predicate():
            return
        yield 0.01
    raise AssertionError(failure_message)


def modal_operator_running(bl_idname):
    """Return whether Blender currently has the named modal operator running."""
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    return any(
        operator.bl_idname == bl_idname
        for operator in window.modal_operators
    )


def edit_mesh_cache_is_current():
    """Return whether Anvil has processed the current Edit Mode topology."""
    from ..handlers import face_cache

    face_count = 0
    vertex_count = 0
    edit_mesh_count = 0
    seen_meshes = set()
    for obj in bpy.context.view_layer.objects:
        if (
                obj.type != 'MESH'
                or obj.data is None
                or not obj.data.is_editmode
                or obj.data.name in seen_meshes):
            continue
        seen_meshes.add(obj.data.name)
        bm = bmesh.from_edit_mesh(obj.data)
        face_count += len(bm.faces)
        vertex_count += len(bm.verts)
        edit_mesh_count += 1

    return (
        edit_mesh_count > 0
        and face_cache.last_face_count == face_count
        and face_cache.last_vertex_count == vertex_count
        and len(face_cache.face_data_cache) == face_count
    )


def get_context_action():
    """Resolve the authoritative action and captured payload used by W."""
    from ..operators.context_action import resolve_context_action
    active_object = bpy.context.active_object
    action_bmesh = None
    if (
            bpy.context.mode == 'EDIT_MESH'
            and active_object is not None
            and active_object.type == 'MESH'):
        action_bmesh = bmesh.from_edit_mesh(active_object.data)
    return resolve_context_action(
        bpy.context.scene,
        active_object,
        bpy.context.mode,
        action_bmesh,
    )


def get_context_action_kind():
    """Read the same cheap action summary used by the panel and W poll."""
    from ..operators.context_action import get_context_action_summary
    return get_context_action_summary(
        bpy.context.active_object,
        bpy.context.mode,
    ).kind


def create_vertical_plane(name):
    """Create a 1x1 vertical plane in the XZ plane (facing +Y).

    Vertices: (0,0,0), (1,0,0), (1,0,1), (0,0,1)

    The face is textured with dev_orange_wall.png using the addon's
    material/UV pipeline.

    Returns the new object, linked to the active scene.
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    v0 = bm.verts.new((0, 0, 0))
    v1 = bm.verts.new((1, 0, 0))
    v2 = bm.verts.new((1, 0, 1))
    v3 = bm.verts.new((0, 0, 1))
    bm.faces.new((v0, v1, v2, v3))

    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    _apply_material(obj, 1.0, 1.0)

    return obj


def create_textured_cube(name, scale_u, scale_v, use_box_project):
    """Create a 1x1x1 cube with all faces textured at the given UV scale.

    The cube spans (0,0,0) to (1,1,1). Returns the object in object mode.

    Args:
        name: Object name
        scale_u: Horizontal UV scale (ignored when use_box_project is True)
        scale_v: Vertical UV scale (ignored when use_box_project is True)
        use_box_project: If True, use box projection instead of per-face local
                         projection
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)

    # bmesh.ops.create_cube centers at origin; shift to (0,0,0)-(1,1,1)
    for v in bm.verts:
        v.co.x += 0.5
        v.co.y += 0.5
        v.co.z += 0.5

    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    if use_box_project:
        _apply_material_box_project(obj, 1.0)
    else:
        _apply_material(obj, scale_u, scale_v)

    return obj


def _apply_material_box_project(obj, scale):
    """Load dev_orange_wall.png and apply it with Box Project."""
    image = bpy.data.images.load(TEXTURE_PATH, check_existing=True)

    mat = find_material_with_image(image)
    if not mat:
        mat = create_material_with_image(image)

    obj.data.materials.append(mat)
    mat_index = obj.data.materials.find(mat.name)

    ppm = bpy.context.scene.level_design_props.pixels_per_meter

    ctx = _get_context_override()
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.verify()

    for face in bm.faces:
        face.material_index = mat_index
        box_project(face, uv_layer, mat, ppm, scale)

    bmesh.update_edit_mesh(obj.data)
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='OBJECT')


def add_uv_layer(obj, layer_name, scale_u, scale_v):
    """Add a new UV layer and project all faces with apply_uv_to_face."""
    mat = obj.data.materials[0]
    ppm = bpy.context.scene.level_design_props.pixels_per_meter
    was_edit = (obj.mode == 'EDIT')

    ctx = _get_context_override()
    if not was_edit:
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.new(layer_name)

    for face in bm.faces:
        apply_uv_to_face(face, uv_layer, scale_u, scale_v, 0.0, 0.0, 0.0,
                         mat, ppm, obj.data)

    bmesh.update_edit_mesh(obj.data)
    if not was_edit:
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='OBJECT')


def add_uv_layer_box_project(obj, layer_name, scale):
    """Add a new UV layer and project all faces with Box Project."""
    mat = obj.data.materials[0]
    ppm = bpy.context.scene.level_design_props.pixels_per_meter
    was_edit = (obj.mode == 'EDIT')

    ctx = _get_context_override()
    if not was_edit:
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.new(layer_name)

    for face in bm.faces:
        box_project(face, uv_layer, mat, ppm, scale)

    bmesh.update_edit_mesh(obj.data)
    if not was_edit:
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.mode_set(mode='OBJECT')


def _apply_material(obj, scale_u=1.0, scale_v=1.0):
    """Load dev_orange_wall.png and apply it as a material with UVs to all faces."""
    image = bpy.data.images.load(TEXTURE_PATH, check_existing=True)

    mat = find_material_with_image(image)
    if not mat:
        mat = create_material_with_image(image)

    obj.data.materials.append(mat)
    mat_index = obj.data.materials.find(mat.name)

    ppm = bpy.context.scene.level_design_props.pixels_per_meter

    ctx = _get_context_override()
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.verify()

    for face in bm.faces:
        face.material_index = mat_index
        apply_uv_to_face(face, uv_layer, scale_u, scale_v, 0.0, 0.0, 0.0,
                         mat, ppm, obj.data)

    bmesh.update_edit_mesh(obj.data)
    with bpy.context.temp_override(**ctx):
        bpy.ops.object.mode_set(mode='OBJECT')
