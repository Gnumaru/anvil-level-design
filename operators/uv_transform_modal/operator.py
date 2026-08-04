"""UV Transform Modal - Main operator.

A modal tool that shows a ghost texture preview on the selected face
with interactive handles for scale, offset, and rotation.
"""

import math

import bmesh
import bpy
import gpu
from bpy.types import Operator
from mathutils import Vector
from mathutils.geometry import intersect_ray_tri

from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_vector_3d,
    region_2d_to_origin_3d,
)

from ...core.logging import debug_log
from ...core.workspace_check import is_level_design_workspace
from ...core.materials import get_image_from_material, get_texture_dimensions_from_material, get_texture_node_from_material
from ...core.uv_projection import derive_transform_from_uvs, apply_uv_to_face, get_face_local_axes
from ...core.uv_layers import get_render_active_uv_layer
from ...core.hotspot_queries import face_has_hotspot_material
from ...handlers import cache_single_face
from ...properties import set_updating_from_selection, sync_scale_tracking
from ..modal_draw.utils import tag_redraw_all_3d_views, is_snapping_enabled
from ..texture_apply import _dispatch_set_uv_from_other_face

from . import drawing
from .interaction import (
    compute_texture_quad_3d,
    compute_handle_screen_layout,
    compute_visible_repetition_grid,
    hit_test_handles,
    pick_repetition_from_mouse,
    compute_scale_offset_from_corner_drag,
    compute_scale_offset_from_edge_drag,
    recompute_offset_for_fixed_corner,
    recompute_offset_for_fixed_edge,
    snap_adjacent_corners_to_face,
    snap_edge_drag_corners_to_face,
    snap_scale_to_parallel_face_edges,
    compute_offset_from_drag,
    compute_rotation_from_drag,
    snap_aspect_ratio,
    snap_aspect_ratio_on_axis,
    snap_edge_and_aspect,
    snap_point_to_face_features,
    snap_quad_vertices_to_face_edges,
    snap_quad_vertices_to_face_vertices,
    snap_quad_edges_to_parallel_face_edges,
    compute_face_edge_angles,
    snap_rotation_to_face_edges,
    snap_offsets_to_reference_vertex_pixel_corner,
    snap_scale_to_furthest_vertex_pixel_seam,
    ray_plane_intersection,
    HANDLE_HIT_RADIUS,
    SNAP_DISTANCE_PIXELS,
)
from .hotkeys import pixel_snap_shortcut_label, pixel_snap_state_for_event


REPETITION_RETARGET_CELL_MARGIN_FACTOR = 0.4
REPETITION_RETARGET_MIN_MARGIN_PIXELS = 1.0


def _pick_primary_face_from_cursor(selected_faces, event, context, world_matrix):
    """Raycast from the mouse position against the selected faces and return
    the nearest hit. Returns None if the cursor is not over any selected face.
    """
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return None

    coord = (event.mouse_region_x, event.mouse_region_y)
    view_vector = region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = region_2d_to_origin_3d(region, rv3d, coord)

    matrix_inv = world_matrix.inverted()
    ray_origin_local = matrix_inv @ ray_origin
    ray_direction_local = (matrix_inv.to_3x3() @ view_vector).normalized()

    best_dist = float('inf')
    best_face = None
    for face in selected_faces:
        loops = list(face.loops)
        if len(loops) < 3:
            continue
        v0 = loops[0].vert.co
        # Fan triangulation — sufficient to detect a ray hit anywhere on the face
        for i in range(1, len(loops) - 1):
            v1 = loops[i].vert.co
            v2 = loops[i + 1].vert.co
            hit = intersect_ray_tri(
                v0, v1, v2, ray_direction_local, ray_origin_local, True
            )
            if hit is not None:
                dist = (hit - ray_origin_local).length
                if dist < best_dist:
                    best_dist = dist
                    best_face = face
                break
    return best_face


def _build_snap_targets(selected_faces, world_matrix):
    """Collect deduped world-space vertices and edges from all selected faces.

    Returns (vertices, edges):
        vertices: list of Vector (one per unique BMVert used by any face)
        edges:    list of (Vector, Vector) pairs (one per unique BMEdge)

    BMesh elements are hashable by their underlying bmesh id, so BMVert and
    BMEdge work directly as dict keys. Python id()/memory address is NOT
    stable — bmesh wraps elements in transient Python objects.
    """
    vert_map = {}
    edge_map = {}
    for face in selected_faces:
        for loop in face.loops:
            v = loop.vert
            if v not in vert_map:
                vert_map[v] = world_matrix @ v.co
            e = loop.edge
            if e not in edge_map:
                a, b = e.verts
                edge_map[e] = (world_matrix @ a.co, world_matrix @ b.co)
    return list(vert_map.values()), list(edge_map.values())


def _get_undo_redo_keys(context):
    """Get the keys bound to undo and redo operations."""
    keys = set()
    wm = context.window_manager
    kc = wm.keyconfigs.user

    if kc is None:
        return {('Z', True, False, False), ('Z', True, True, False)}

    for km in kc.keymaps:
        for kmi in km.keymap_items:
            if kmi.idname in ('ed.undo', 'ed.redo') and kmi.active:
                keys.add((kmi.type, kmi.ctrl, kmi.shift, kmi.alt))

    if not keys:
        keys = {('Z', True, False, False), ('Z', True, True, False)}

    return keys


class MESH_OT_uv_transform_modal(Operator):
    """Interactively adjust UV scale, offset, and rotation with a ghost texture preview"""
    bl_idname = "leveldesign.uv_transform_modal"
    bl_label = "UV Transform"
    bl_options = {'REGISTER', 'UNDO'}

    _active_instance = None

    action_face_index: bpy.props.IntProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_other_face_indices: bpy.props.StringProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_scale_u: bpy.props.FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_scale_v: bpy.props.FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_rotation: bpy.props.FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_offset_x: bpy.props.FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    action_offset_y: bpy.props.FloatProperty(
        options={'HIDDEN', 'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return (
            is_level_design_workspace()
            and context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.mode == 'EDIT_MESH'
        )

    # ------------------------------------------------------------------
    # Invoke
    # ------------------------------------------------------------------

    def invoke(self, context, event):
        # If already in UV transform modal, ignore the second invocation
        if MESH_OT_uv_transform_modal._active_instance is not None:
            return {'CANCELLED'}

        self._cancelled = False
        self._draw_handler_3d = None
        self._draw_handler_2d = None
        self._face_overlay_space = None
        self._saved_face_overlay_show_faces = None
        self._modal_window = context.window
        self._modal_workspace = context.workspace

        obj = context.active_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()

        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'WARNING'}, "Select at least one face")
            return {'CANCELLED'}

        for sf in selected_faces:
            if face_has_hotspot_material(sf, me):
                self.report({'WARNING'}, "Cannot use UV Transform on hotspot faces")
                return {'CANCELLED'}

        self._world_matrix = obj.matrix_world.copy()

        # Pick the primary face: prefer the selected face under the mouse
        # cursor; fall back to the active face if it is selected; otherwise
        # use the first selected face.
        face = _pick_primary_face_from_cursor(
            selected_faces, event, context, self._world_matrix
        )
        if face is None:
            active = bm.faces.active
            if active is not None and active.select:
                face = active
            else:
                face = selected_faces[0]

        props = context.scene.level_design_props
        ppm = props.pixels_per_meter

        uv_layer = get_render_active_uv_layer(bm, me)
        if uv_layer is None:
            self.report({'WARNING'}, "No UV layer found")
            return {'CANCELLED'}

        # Derive current transform
        transform = derive_transform_from_uvs(face, uv_layer, ppm, me)
        if transform is None:
            self.report({'WARNING'}, "Could not read UV transform")
            return {'CANCELLED'}

        # Store face info
        self._face_index = face.index
        self._other_face_indices = [
            sf.index for sf in selected_faces if sf.index != face.index
        ]

        # Save initial transform for cancel revert
        self._saved_scale_u = transform['scale_u']
        self._saved_scale_v = transform['scale_v']
        self._saved_rotation = transform['rotation']
        self._saved_offset_x = transform['offset_x']
        self._saved_offset_y = transform['offset_y']

        # Save initial UVs for every selected face so cancel restores them all
        self._saved_all_uvs = {
            sf.index: [(loop[uv_layer].uv.x, loop[uv_layer].uv.y) for loop in sf.loops]
            for sf in selected_faces
        }
        self._saved_uvs = self._saved_all_uvs[face.index]

        # Current working transform — adjust offset so the preview quad
        # overlaps the face.  UV tiling means shifting offset by an integer
        # produces pixel-identical results, so we pick the integer shift that
        # centres the tile [0,1] on the face's UV centroid.
        uv_centroid_u = sum(uv[0] for uv in self._saved_uvs) / len(self._saved_uvs)
        uv_centroid_v = sum(uv[1] for uv in self._saved_uvs) / len(self._saved_uvs)
        snap_u = math.floor(uv_centroid_u)
        snap_v = math.floor(uv_centroid_v)

        self._scale_u = transform['scale_u']
        self._scale_v = transform['scale_v']
        self._rotation = transform['rotation']
        # Use the RAW (unwrapped) uvs[0] for the working offset; transform
        # dict has already been normalized via normalize_offset, so composing
        # it with snap_u loses the tile information for faces far from [0,1).
        raw_uv0_x, raw_uv0_y = self._saved_uvs[0]
        self._offset_x = raw_uv0_x - snap_u
        self._offset_y = raw_uv0_y - snap_v

        # Apply the adjusted offset to the face UVs so they stay in sync
        # with the preview (texture appearance is identical since we shifted
        # by whole tiles).  The saved UVs above still hold the originals
        # for cancel/revert.
        if snap_u != 0 or snap_v != 0:
            for loop in face.loops:
                loop[uv_layer].uv.x -= snap_u
                loop[uv_layer].uv.y -= snap_v
            bmesh.update_edit_mesh(me)

        # Cache undo/redo key bindings for clean exit
        self._undo_redo_keys = _get_undo_redo_keys(context)

        # Get material and texture info
        mat = me.materials[face.material_index] if face.material_index < len(me.materials) else None
        self._material = mat
        self._image = get_image_from_material(mat)
        self._texture_pixel_width = (
            self._image.size[0]
            if self._image is not None and self._image.size[0] > 0
            else 128
        )
        self._texture_pixel_height = (
            self._image.size[1]
            if self._image is not None and self._image.size[1] > 0
            else 128
        )
        self._tex_meters_u, self._tex_meters_v = get_texture_dimensions_from_material(mat, ppm)
        self._ppm = ppm

        # Compute face geometry in world space
        face_axes = get_face_local_axes(face)
        if not face_axes:
            self.report({'WARNING'}, "Could not compute face axes")
            return {'CANCELLED'}

        self._face_local_x, self._face_local_y = face_axes
        self._face_normal = face.normal.copy()
        self._first_vert_world = self._world_matrix @ face.loops[0].vert.co

        # Outline corners for every selected face (used by the 3D draw handler)
        self._all_face_corners_world = [
            [self._world_matrix @ loop.vert.co for loop in sf.loops]
            for sf in selected_faces
        ]
        # Snap target vertex / edge lists: union over all selected faces, deduped
        self._snap_vertices, self._snap_edges = _build_snap_targets(
            selected_faces, self._world_matrix
        )

        # Transform face-local axes to world space (direction only)
        rot_scale = self._world_matrix.to_3x3()
        face_local_x_world_vec = rot_scale @ self._face_local_x
        face_local_y_world_vec = rot_scale @ self._face_local_y
        self._face_local_x_world = face_local_x_world_vec.normalized()
        self._face_local_y_world = face_local_y_world_vec.normalized()
        self._face_normal_world = (rot_scale @ self._face_normal).normalized()

        # Convert tex_meters from local to world units. The ghost preview and
        # drag math run in world space against the normalized face-axis vectors
        # above, but tex_meters was computed from pixels/ppm in object-local
        # units. Without this scale correction, the ghost would be off by the
        # object's scale factor when it isn't 1.
        self._tex_meters_u *= face_local_x_world_vec.length
        self._tex_meters_v *= face_local_y_world_vec.length

        # Pre-compute edge angles across all selected faces for rotation snapping
        self._face_edge_angles = compute_face_edge_angles(
            self._snap_edges,
            self._face_local_x_world, self._face_local_y_world
        )

        # Drag state
        self._dragging = False
        self._drag_type = None
        self._drag_index = None
        self._drag_start_mouse = None
        self._drag_start_3d = None
        self._view_navigating = False

        # Hover state
        self._hover_type = None
        self._hover_index = None
        self._repetition_grid_cache_key = None
        self._repetition_grid_positions = []
        self._repetition_grid_opacities = []

        # Ctrl-held pixel snapping state. The reference is captured once when
        # the hotkey is pressed and remains stable until the key is released.
        self._pixel_snap_active = False
        self._pixel_snap_reference_vertex = None

        # Start on the repetition under the invocation cursor so the user
        # never has to select a tile before reaching its controls.
        initial_quad = self._compute_quad()
        initial_repetition = pick_repetition_from_mouse(
            context.region, context.region_data,
            (event.mouse_region_x, event.mouse_region_y),
            initial_quad, context.preferences.system.ui_scale
        )
        if initial_repetition is not None:
            self._activate_repetition(
                context,
                initial_repetition['repeat_u'],
                initial_repetition['repeat_v']
            )

        # Register world-space preview and screen-space handle draw handlers.
        self._draw_handler_3d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_3d, (context,), 'WINDOW', 'POST_VIEW'
        )
        self._draw_handler_2d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (context,), 'WINDOW', 'POST_PIXEL'
        )

        # Blender's native selected-face fill obscures the texture preview.
        # This is a per-viewport overlay setting, not a user preference. Keep
        # its exact prior state so cleanup can restore it after the modal.
        self._disable_face_selection_overlay(context)

        # Claim the active-instance slot only after all validation has passed.
        # Early returns above must not leak this class-level reference, or
        # subsequent invocations would bail at the guard at the top.
        MESH_OT_uv_transform_modal._active_instance = self

        context.window_manager.modal_handler_add(self)
        self._update_status_text(context)
        tag_redraw_all_3d_views()

        return {'RUNNING_MODAL'}

    # ------------------------------------------------------------------
    # Modal
    # ------------------------------------------------------------------

    def modal(self, context, event):
        # Another invocation cancelled us - just exit
        if self._cancelled:
            self._cleanup(context)
            return {'CANCELLED'}

        # Exit if user left edit mode (e.g. pressed Tab)
        if context.mode != 'EDIT_MESH':
            self._cleanup(context)
            return {'CANCELLED'}

        region = context.region
        rv3d = context.region_data

        if region is None or rv3d is None:
            return {'PASS_THROUGH'}

        # Undo/redo - exit cleanly
        if event.value == 'PRESS':
            event_key = (event.type, event.ctrl, event.shift, event.alt)
            if event_key in self._undo_redo_keys:
                self._cleanup(context)
                return {'CANCELLED'}

        mouse_pos = (event.mouse_region_x, event.mouse_region_y)

        # Walk navigation takes over after RIGHTMOUSE and can consume its
        # matching release. Tracking that button here can therefore leave
        # repetition retargeting permanently suspended after free camera use.
        # MIDDLEMOUSE navigation remains local to this modal.
        if event.type == 'MIDDLEMOUSE':
            if event.value == 'PRESS':
                self._view_navigating = True
            elif event.value == 'RELEASE':
                self._view_navigating = False

        pixel_snap_state = pixel_snap_state_for_event(
            context.window_manager, event
        )
        if pixel_snap_state is not None:
            if pixel_snap_state and not self._pixel_snap_active:
                self._pixel_snap_reference_vertex = (
                    self._pick_pixel_snap_reference(region, rv3d, mouse_pos)
                )
            elif not pixel_snap_state:
                self._pixel_snap_reference_vertex = None
            self._pixel_snap_active = pixel_snap_state
            self._update_status_text(context)
            tag_redraw_all_3d_views()
            return {'RUNNING_MODAL'}

        # Compute current state for hit testing
        quad = self._compute_quad()
        ui_scale = context.preferences.system.ui_scale
        handle_layout = compute_handle_screen_layout(
            region, rv3d, quad, ui_scale
        )
        hit_radius = HANDLE_HIT_RADIUS * ui_scale

        # The grid is passive. When the mouse leaves the active gizmo's
        # footprint, silently rebase the full gizmo to the repetition beneath
        # it. Whole-tile rebasing leaves the rendered texture unchanged.
        if (
                event.type == 'MOUSEMOVE'
                and not self._dragging
                and not self._view_navigating
                and not self._mouse_in_active_gizmo_zone(
                    mouse_pos, handle_layout
                )):
            repetition = pick_repetition_from_mouse(
                region, rv3d, mouse_pos, quad, ui_scale
            )
            if repetition is not None:
                self._activate_repetition(
                    context,
                    repetition['repeat_u'], repetition['repeat_v']
                )
                quad = self._compute_quad()
                handle_layout = compute_handle_screen_layout(
                    region, rv3d, quad, ui_scale
                )
                self._hover_type = None
                self._hover_index = None

        # ---- Cancel ----
        if event.type == 'ESC' and event.value == 'PRESS':
            self._revert(context)
            self._cleanup(context)
            return {'CANCELLED'}

        # ---- Confirm (keyboard) ----
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            result = self._finish_from_modal(context)
            self._cleanup(context)
            return result

        # ---- Mouse press - start drag or confirm ----
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit_type, hit_index = hit_test_handles(
                mouse_pos, handle_layout, hit_radius
            )
            if hit_type is not None:
                self._dragging = True
                self._drag_type = hit_type
                self._drag_index = hit_index
                self._drag_start_mouse = mouse_pos

                # Save transform at drag start
                self._drag_start_scale_u = self._scale_u
                self._drag_start_scale_v = self._scale_v
                self._drag_start_offset_x = self._offset_x
                self._drag_start_offset_y = self._offset_y
                self._drag_start_rotation = self._rotation
                self._drag_start_quad = list(quad)

                # Compute 3D position of drag start on the face plane
                self._drag_start_3d = self._mouse_to_face_plane(
                    region, rv3d, mouse_pos
                )

                return {'RUNNING_MODAL'}
            else:
                # Click on empty space = confirm
                result = self._finish_from_modal(context)
                self._cleanup(context)
                return result

        # ---- Mouse release - end drag ----
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self._dragging:
                self._dragging = False
                self._drag_type = None
                tag_redraw_all_3d_views()
                return {'RUNNING_MODAL'}

        # ---- Mouse move ----
        if event.type == 'MOUSEMOVE':
            if self._dragging:
                self._apply_drag(context, region, rv3d, mouse_pos, event)
            else:
                # Update hover
                hit_type, hit_index = hit_test_handles(
                    mouse_pos, handle_layout, hit_radius
                )
                if (
                        hit_type != self._hover_type
                        or hit_index != self._hover_index):
                    self._hover_type = hit_type
                    self._hover_index = hit_index
                    # Update cursor
                    if hit_type is not None:
                        context.window.cursor_modal_set('HAND')
                    else:
                        context.window.cursor_modal_restore()

            tag_redraw_all_3d_views()
            return {'RUNNING_MODAL'}

        # Pass through unhandled events (e.g. RMB for camera navigation)
        return {'PASS_THROUGH'}

    # ------------------------------------------------------------------
    # Drag application
    # ------------------------------------------------------------------

    def _apply_drag(self, context, region, rv3d, mouse_pos, event):
        """Apply the current drag to update transform values."""
        current_3d = self._mouse_to_face_plane(region, rv3d, mouse_pos)
        if current_3d is None or self._drag_start_3d is None:
            return

        pixel_snapping = self._pixel_snap_active
        snapping = (
            is_snapping_enabled(context)
            and not event.shift
            and not pixel_snapping
        )
        snap_distance_pixels = SNAP_DISTANCE_PIXELS
        proj_x, proj_y = self._get_rotated_axes_world()

        if self._drag_type == 'corner':
            self._apply_corner_drag(
                current_3d, proj_x, proj_y, snapping, pixel_snapping,
                region, rv3d, snap_distance_pixels
            )

        elif self._drag_type == 'edge':
            self._apply_edge_drag(
                current_3d, proj_x, proj_y, snapping, pixel_snapping,
                region, rv3d, snap_distance_pixels
            )

        elif self._drag_type in {'move_free', 'move_v', 'move_h'}:
            self._apply_move_drag(
                current_3d, proj_x, proj_y, snapping, pixel_snapping,
                region, rv3d, snap_distance_pixels
            )

        elif self._drag_type == 'rotation':
            self._apply_rotation_drag(current_3d, snapping)

        # Apply to UVs
        self._apply_transform(context)

    def _apply_corner_drag(
            self, current_3d, proj_x, proj_y, snapping, pixel_snapping,
            region, rv3d, snap_distance_pixels):
        """Handle corner (resize) drag with snapping."""
        dragged = current_3d
        snap_edge = None

        # Snap dragged corner to face features (vertex/edge proximity)
        if snapping:
            dragged, snap_edge = snap_point_to_face_features(
                dragged, self._snap_vertices, self._snap_edges,
                region, rv3d, snap_distance_pixels
            )

        new_su, new_sv, new_ox, new_oy = compute_scale_offset_from_corner_drag(
            dragged, self._drag_index, self._drag_start_quad,
            self._first_vert_world, proj_x, proj_y,
            self._tex_meters_u, self._tex_meters_v
        )

        # Snap adjacent corners to face features.
        # Each adjacent corner controls one scale axis independently.
        if snapping:
            new_su, new_sv = snap_adjacent_corners_to_face(
                self._drag_index, self._drag_start_quad,
                self._first_vert_world, proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                self._snap_edges, region, rv3d, snap_distance_pixels
            )
            # Edge-to-edge: snap preview edges to parallel face edges.
            # Covers the "preview larger than face" case where adjacent
            # corners fall outside the face.
            new_su, new_sv = snap_scale_to_parallel_face_edges(
                self._drag_index, self._drag_start_quad,
                proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                self._snap_edges, region, rv3d, snap_distance_pixels
            )

        if pixel_snapping:
            corner_uvs = ((0, 0), (1, 0), (1, 1), (0, 1))
            opposite_index = (self._drag_index + 2) % 4
            drag_u, drag_v = corner_uvs[self._drag_index]
            fixed_u, fixed_v = corner_uvs[opposite_index]
            fixed_pos = self._drag_start_quad[opposite_index]
            new_su = snap_scale_to_furthest_vertex_pixel_seam(
                self._snap_vertices, fixed_pos, proj_x, drag_u - fixed_u,
                new_su, self._tex_meters_u, self._texture_pixel_width
            )
            new_sv = snap_scale_to_furthest_vertex_pixel_seam(
                self._snap_vertices, fixed_pos, proj_y, drag_v - fixed_v,
                new_sv, self._tex_meters_v, self._texture_pixel_height
            )

        # Snap to 1:1 aspect ratio if close. If the dragged corner is on a
        # face edge, slide along it so both normal snap constraints apply.
        elif snapping:
            if snap_edge is not None:
                combined = snap_edge_and_aspect(
                    snap_edge[0], snap_edge[1],
                    self._drag_index, self._drag_start_quad,
                    self._first_vert_world, proj_x, proj_y,
                    self._tex_meters_u, self._tex_meters_v,
                    new_su, new_sv
                )
                if combined is not None:
                    new_su, new_sv = combined
            else:
                new_su, new_sv = snap_aspect_ratio(new_su, new_sv)

        # Recompute offset to keep the fixed corner in place
        new_ox, new_oy = recompute_offset_for_fixed_corner(
            self._drag_index, self._drag_start_quad,
            self._first_vert_world, proj_x, proj_y,
            new_su, new_sv, self._tex_meters_u, self._tex_meters_v
        )

        self._scale_u = new_su
        self._scale_v = new_sv
        self._offset_x = new_ox
        self._offset_y = new_oy

    def _apply_move_drag(
            self, current_3d, proj_x, proj_y, snapping, pixel_snapping,
            region, rv3d, snap_distance_pixels):
        """Handle move (offset) drag with optional axis lock and snapping.

        drag_type 'move_v' locks the horizontal (U) offset; 'move_h' locks
        the vertical (V) offset; 'move_free' is unconstrained.
        """
        new_ox, new_oy = compute_offset_from_drag(
            self._drag_start_3d, current_3d,
            proj_x, proj_y,
            self._drag_start_offset_x, self._drag_start_offset_y,
            self._scale_u, self._scale_v,
            self._tex_meters_u, self._tex_meters_v
        )

        lock_u = self._drag_type == 'move_v'
        lock_v = self._drag_type == 'move_h'

        if lock_u:
            new_ox = self._drag_start_offset_x
        if lock_v:
            new_oy = self._drag_start_offset_y

        movement_axis = None
        if lock_u:
            movement_axis = proj_y
        elif lock_v:
            movement_axis = proj_x

        self._offset_x = new_ox
        self._offset_y = new_oy

        if pixel_snapping and self._pixel_snap_reference_vertex is not None:
            self._offset_x, self._offset_y = (
                snap_offsets_to_reference_vertex_pixel_corner(
                    self._pixel_snap_reference_vertex,
                    self._first_vert_world, proj_x, proj_y,
                    self._scale_u, self._scale_v,
                    self._tex_meters_u, self._tex_meters_v,
                    self._texture_pixel_width, self._texture_pixel_height,
                    self._offset_x, self._offset_y,
                    not lock_u, not lock_v,
                )
            )

        # Snap priority (highest first):
        #   1. Quad corner onto face vertex (vertex-to-vertex)
        #   2. One or two quad corners onto compatible face edges
        #   3. Preview edge onto parallel face edge (edge-to-edge)
        if snapping:
            quad = self._compute_quad()
            snap_delta = snap_quad_vertices_to_face_vertices(
                quad, self._snap_vertices,
                region, rv3d, snap_distance_pixels
            )
            if snap_delta is None:
                snap_delta = snap_quad_vertices_to_face_edges(
                    quad, self._snap_edges, proj_x, proj_y,
                    region, rv3d, snap_distance_pixels,
                    movement_axis
                )
            if snap_delta is None:
                snap_delta = snap_quad_edges_to_parallel_face_edges(
                    quad, self._snap_edges,
                    proj_x, proj_y, region, rv3d, snap_distance_pixels
                )
            if snap_delta is not None:
                # Convert the 3D delta to offset delta (negate for same
                # reason as compute_offset_from_drag). Skip the locked axis
                # so the snap can only move us along the allowed direction.
                su = self._scale_u * self._tex_meters_u
                sv = self._scale_v * self._tex_meters_v
                if not lock_u and abs(su) > 0.0001:
                    self._offset_x -= snap_delta.dot(proj_x) / su
                if not lock_v and abs(sv) > 0.0001:
                    self._offset_y -= snap_delta.dot(proj_y) / sv

    def _apply_edge_drag(
            self, current_3d, proj_x, proj_y, snapping, pixel_snapping,
            region, rv3d, snap_distance_pixels):
        """Handle edge (axis-locked resize) drag with snapping.

        Edges 0/2 (bottom/top) resize along V only; edges 1/3 (right/left)
        resize along U only. The opposite edge stays pinned.
        """
        dragged = current_3d

        if snapping:
            dragged, _snap_edge = snap_point_to_face_features(
                dragged, self._snap_vertices, self._snap_edges,
                region, rv3d, snap_distance_pixels
            )

        new_su, new_sv, new_ox, new_oy = compute_scale_offset_from_edge_drag(
            dragged, self._drag_index, self._drag_start_quad,
            self._first_vert_world, proj_x, proj_y,
            self._tex_meters_u, self._tex_meters_v,
            self._drag_start_scale_u, self._drag_start_scale_v,
            self._drag_start_offset_x, self._drag_start_offset_y,
        )

        # Parallel-face-edge snap on the active axis only; the locked axis
        # must keep its drag-start value regardless of what the general
        # snapper would suggest. snap_scale_to_parallel_face_edges works in
        # corner-index terms, so map each edge to a corner on it so the
        # function's opposite-corner lookup lands on the pinned edge.
        #   edge 0 (bottom) → corner 0 (BL, opp=TR on top)
        #   edge 1 (right)  → corner 2 (TR, opp=BL on left)
        #   edge 2 (top)    → corner 2 (TR, opp=BL on bottom)
        #   edge 3 (left)   → corner 3 (TL, opp=BR on right)
        EDGE_TO_SNAP_CORNER = (0, 2, 2, 3)
        if pixel_snapping:
            fixed_corner_indices = (2, 0, 0, 1)
            uv_directions = (-1.0, 1.0, 1.0, -1.0)
            fixed_pos = self._drag_start_quad[
                fixed_corner_indices[self._drag_index]
            ]
            if self._drag_index % 2 == 0:
                new_sv = snap_scale_to_furthest_vertex_pixel_seam(
                    self._snap_vertices, fixed_pos, proj_y,
                    uv_directions[self._drag_index], new_sv,
                    self._tex_meters_v, self._texture_pixel_height
                )
            else:
                new_su = snap_scale_to_furthest_vertex_pixel_seam(
                    self._snap_vertices, fixed_pos, proj_x,
                    uv_directions[self._drag_index], new_su,
                    self._tex_meters_u, self._texture_pixel_width
                )

            new_ox, new_oy = recompute_offset_for_fixed_edge(
                self._drag_index, self._drag_start_quad,
                self._first_vert_world, proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                self._drag_start_offset_x, self._drag_start_offset_y,
            )

        elif snapping:
            # Snap either corner on the moving edge along the stretch axis.
            # The perpendicular scale remains at its drag-start value.
            new_su, new_sv = snap_edge_drag_corners_to_face(
                self._drag_index, self._first_vert_world,
                proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                new_ox, new_oy,
                self._snap_edges, region, rv3d, snap_distance_pixels
            )

            snap_corner = EDGE_TO_SNAP_CORNER[self._drag_index]
            snapped_su, snapped_sv = snap_scale_to_parallel_face_edges(
                snap_corner, self._drag_start_quad,
                proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                self._snap_edges, region, rv3d, snap_distance_pixels
            )
            if self._drag_index % 2 == 0:
                new_sv = snapped_sv
                active_axis = 'v'
            else:
                new_su = snapped_su
                active_axis = 'u'

            new_su, new_sv = snap_aspect_ratio_on_axis(
                new_su, new_sv, active_axis
            )

            new_ox, new_oy = recompute_offset_for_fixed_edge(
                self._drag_index, self._drag_start_quad,
                self._first_vert_world, proj_x, proj_y,
                new_su, new_sv, self._tex_meters_u, self._tex_meters_v,
                self._drag_start_offset_x, self._drag_start_offset_y,
            )

        self._scale_u = new_su
        self._scale_v = new_sv
        self._offset_x = new_ox
        self._offset_y = new_oy

    def _apply_rotation_drag(self, current_3d, snapping):
        """Handle rotation drag with snapping."""
        q = self._drag_start_quad
        drag_center = (q[0] + q[1] + q[2] + q[3]) * 0.25

        new_rot = compute_rotation_from_drag(
            current_3d, drag_center,
            self._face_local_x_world, self._face_local_y_world
        )
        if new_rot is None:
            return

        # Snap to face edge angles if close
        if snapping:
            new_rot = snap_rotation_to_face_edges(new_rot, self._face_edge_angles)

        self._rotation = new_rot

        # Recompute offset so the quad center stays at drag_center
        new_proj_x, new_proj_y = self._get_rotated_axes_world()
        delta = drag_center - self._first_vert_world
        su = self._scale_u * self._tex_meters_u
        sv = self._scale_v * self._tex_meters_v
        if abs(su) > 1e-8:
            self._offset_x = 0.5 - delta.dot(new_proj_x) / su
        if abs(sv) > 1e-8:
            self._offset_y = 0.5 - delta.dot(new_proj_y) / sv

    def _apply_transform(self, context):
        """Apply the current working transform to the primary face UVs,
        propagate to every other selected face, and update the panel."""
        self._apply_transform_values(
            context, self._face_index, self._other_face_indices,
            self._scale_u, self._scale_v, self._rotation,
            self._offset_x, self._offset_y
        )

    def _apply_transform_values(self, context, face_index, other_face_indices,
                                scale_u, scale_v, rotation, offset_x, offset_y):
        """Apply explicit UV transform values to the captured face set."""
        obj = context.active_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()

        if face_index >= len(bm.faces):
            return

        face = bm.faces[face_index]
        if not face.is_valid:
            return

        uv_layer = get_render_active_uv_layer(bm, me)
        if uv_layer is None:
            return

        props = context.scene.level_design_props
        ppm = props.pixels_per_meter
        material = me.materials[face.material_index] if face.material_index < len(me.materials) else None
        world_matrix = obj.matrix_world.copy()

        apply_uv_to_face(
            face, uv_layer,
            scale_u, scale_v, rotation,
            offset_x, offset_y,
            material, ppm, me
        )

        cache_single_face(face, bm, ppm, me)

        # Propagate the primary face's UV settings to the other selected
        # faces using the same transfer path that Alt+LMB uses.
        # set_uv_from_source_params (called by the dispatch) handles caching.
        for idx in other_face_indices:
            if idx >= len(bm.faces):
                continue
            target = bm.faces[idx]
            if not target.is_valid:
                continue
            _dispatch_set_uv_from_other_face(
                face, target, uv_layer, ppm, me,
                world_matrix, bm=bm,
            )

        # Update panel properties
        set_updating_from_selection(True)
        try:
            props.texture_scale_u = scale_u
            props.texture_scale_v = scale_v
            props.texture_rotation = rotation % 360.0
            props.texture_offset_x = offset_x % 1.0
            props.texture_offset_y = offset_y % 1.0
        finally:
            set_updating_from_selection(False)
            sync_scale_tracking(context)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _get_rotated_axes_world(self):
        """Get the rotated projection axes in world space (matching apply_uv_to_face)."""
        rotation_rad = math.radians(self._rotation)
        cos_rot = math.cos(rotation_rad)
        sin_rot = math.sin(rotation_rad)

        proj_x = self._face_local_x_world * cos_rot - self._face_local_y_world * sin_rot
        proj_y = self._face_local_x_world * sin_rot + self._face_local_y_world * cos_rot
        return proj_x, proj_y

    def _compute_quad(self):
        """Compute the current texture quad corners in world space."""
        proj_x, proj_y = self._get_rotated_axes_world()

        return compute_texture_quad_3d(
            self._first_vert_world, proj_x, proj_y,
            self._scale_u, self._scale_v,
            self._tex_meters_u, self._tex_meters_v,
            self._offset_x, self._offset_y
        )

    def _get_visible_repetition_grid(self, region, rv3d, quad):
        """Return cached world-space lines for the current visible UV grid."""
        cache_key = (
            region.width,
            region.height,
            rv3d.view_distance,
            rv3d.view_perspective,
            tuple(
                value
                for row in rv3d.view_matrix
                for value in row
            ),
            tuple(value for point in quad for value in point),
        )
        if cache_key != self._repetition_grid_cache_key:
            (
                self._repetition_grid_positions,
                self._repetition_grid_opacities,
            ) = (
                compute_visible_repetition_grid(region, rv3d, quad)
            )
            self._repetition_grid_cache_key = cache_key
        return (
            self._repetition_grid_positions,
            self._repetition_grid_opacities,
        )

    def _mouse_in_active_gizmo_zone(self, mouse_pos, handle_layout):
        """Keep the active tile stable while its controls are approachable."""
        if handle_layout is None:
            return False

        corners = handle_layout['corners']
        shortest_cell_edge = min(
            (corners[(index + 1) % len(corners)] - corners[index]).length
            for index in range(len(corners))
        )
        margin = max(
            REPETITION_RETARGET_MIN_MARGIN_PIXELS,
            shortest_cell_edge * REPETITION_RETARGET_CELL_MARGIN_FACTOR,
        )

        positions = list(corners)
        if handle_layout['show_rotation']:
            positions.append(handle_layout['rotation'])
        minimum_x = min(point.x for point in positions) - margin
        maximum_x = max(point.x for point in positions) + margin
        minimum_y = min(point.y for point in positions) - margin
        maximum_y = max(point.y for point in positions) + margin
        return (
            minimum_x <= mouse_pos[0] <= maximum_x
            and minimum_y <= mouse_pos[1] <= maximum_y
        )

    def _mouse_to_face_plane(self, region, rv3d, mouse_pos):
        """Project mouse position onto the face plane in world space."""
        ray_origin = region_2d_to_origin_3d(region, rv3d, Vector(mouse_pos))
        ray_dir = region_2d_to_vector_3d(region, rv3d, Vector(mouse_pos))

        return ray_plane_intersection(
            ray_origin, ray_dir,
            self._first_vert_world, self._face_normal_world
        )

    def _activate_repetition(self, context, repeat_u, repeat_v):
        """Make an equivalent visible UV tile the active full gizmo."""
        self._offset_x -= repeat_u
        self._offset_y -= repeat_v
        self._apply_transform(context)

    def _pick_pixel_snap_reference(self, region, rv3d, mouse_pos):
        """Pick the selected-face vertex nearest the cursor in screen space."""
        mouse_x, mouse_y = mouse_pos
        closest_vertex = None
        closest_distance_squared = float('inf')
        for vertex in self._snap_vertices:
            screen_pos = location_3d_to_region_2d(region, rv3d, vertex)
            if screen_pos is None:
                continue
            dx = screen_pos.x - mouse_x
            dy = screen_pos.y - mouse_y
            distance_squared = dx * dx + dy * dy
            if distance_squared < closest_distance_squared:
                closest_distance_squared = distance_squared
                closest_vertex = vertex
        return closest_vertex.copy() if closest_vertex is not None else None

    def _update_status_text(self, context):
        pixel_shortcut = pixel_snap_shortcut_label(context.window_manager)
        pixel_indicator = " [Pixel Snap]" if self._pixel_snap_active else ""
        context.workspace.status_text_set(
            f"LMB: Drag handles    {pixel_shortcut}: Pixel Snap    "
            f"Shift: Disable Snap    LMB (empty)/Enter: Confirm    "
            f"Esc: Cancel{pixel_indicator}"
        )

    # ------------------------------------------------------------------
    # Drawing callbacks
    # ------------------------------------------------------------------

    def _draw_3d(self, context):
        """POST_VIEW draw callback: ghost texture and world-space outlines."""
        if not is_level_design_workspace():
            return

        draw_context = bpy.context
        region = draw_context.region
        rv3d = draw_context.region_data
        if region is None or rv3d is None:
            return

        try:
            quad = self._compute_quad()
            repetition_grid = self._get_visible_repetition_grid(
                region, rv3d, quad
            )
        except Exception:
            return

        gpu.state.blend_set('ALPHA')
        gpu.state.depth_mask_set(False)

        try:
            # Ghost texture: depth-tested so it reads as lying on the face.
            gpu.state.depth_test_set('LESS_EQUAL')
            tex_node = get_texture_node_from_material(self._material)
            # Only 'Closest' maps to nearest; Linear/Cubic/Smart all use
            # a filtered sampler.
            use_linear_filter = tex_node is None or tex_node.interpolation != 'Closest'
            drawing.draw_ghost_texture(quad, self._image, use_linear_filter)

            # Outlines are drawn through geometry.
            gpu.state.depth_test_set('NONE')
            drawing.draw_repetition_grid_3d(
                repetition_grid[0], repetition_grid[1], None
            )
            drawing.draw_quad_outline(quad)
            # Outline every selected face so the user can see all the
            # available snap targets.
            for corners in self._all_face_corners_world:
                drawing.draw_face_outline(corners)
        finally:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(True)

    def _draw_2d(self, context):
        """POST_PIXEL draw callback for constant-size interactive handles."""
        if not is_level_design_workspace():
            return

        draw_context = bpy.context
        region = draw_context.region
        rv3d = draw_context.region_data
        if region is None or rv3d is None:
            return

        try:
            quad = self._compute_quad()
            ui_scale = draw_context.preferences.system.ui_scale
            handle_layout = compute_handle_screen_layout(
                region, rv3d, quad, ui_scale
            )
            pixel_reference_position = None
            reference_vertex = self._pixel_snap_reference_vertex
            if self._pixel_snap_active and reference_vertex is not None:
                pixel_reference_position = location_3d_to_region_2d(
                    region, rv3d, reference_vertex
                )
        except Exception:
            return

        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        try:
            drawing.draw_pixel_snap_reference_2d(
                pixel_reference_position, ui_scale
            )
            drawing.draw_handles_2d(
                handle_layout, self._hover_type, self._hover_index,
                self._drag_type, self._drag_index, ui_scale
            )
        finally:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')

    # ------------------------------------------------------------------
    # Cleanup / Revert
    # ------------------------------------------------------------------

    def _disable_face_selection_overlay(self, context):
        """Hide Blender's selected-face fill in the invoking 3D viewport."""
        space = context.space_data
        if space is None or space.type != 'VIEW_3D':
            return

        self._face_overlay_space = space
        self._saved_face_overlay_show_faces = space.overlay.show_faces
        space.overlay.show_faces = False

    def _restore_face_selection_overlay(self):
        """Restore the invoking viewport's selected-face fill setting."""
        space = self._face_overlay_space
        saved_show_faces = self._saved_face_overlay_show_faces
        self._face_overlay_space = None
        self._saved_face_overlay_show_faces = None

        if space is None or saved_show_faces is None:
            return

        try:
            space.overlay.show_faces = saved_show_faces
        except ReferenceError:
            # The invoking area can be destroyed while the modal is active.
            pass

    def _revert(self, context):
        """Restore the original UVs of every selected face on cancel."""
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return

        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()

        uv_layer = get_render_active_uv_layer(bm, me)
        if uv_layer is None:
            return

        for idx, saved_uvs in self._saved_all_uvs.items():
            if idx >= len(bm.faces):
                continue
            face = bm.faces[idx]
            if not face.is_valid:
                continue
            loops = list(face.loops)
            for i, loop in enumerate(loops):
                if i < len(saved_uvs):
                    loop[uv_layer].uv.x = saved_uvs[i][0]
                    loop[uv_layer].uv.y = saved_uvs[i][1]

        bmesh.update_edit_mesh(me)

        for idx in self._saved_all_uvs:
            if idx >= len(bm.faces):
                continue
            face = bm.faces[idx]
            if face.is_valid:
                cache_single_face(face, bm, self._ppm, me)

        # Restore panel properties to the primary face's pre-modal values
        props = context.scene.level_design_props
        set_updating_from_selection(True)
        try:
            props.texture_scale_u = self._saved_scale_u
            props.texture_scale_v = self._saved_scale_v
            props.texture_rotation = self._saved_rotation
            props.texture_offset_x = self._saved_offset_x
            props.texture_offset_y = self._saved_offset_y
        finally:
            set_updating_from_selection(False)
            sync_scale_tracking(context)

    def _normalize_and_apply(self, context):
        """Normalize offsets to [0,1) and re-apply so UVs are stored cleanly."""
        self._offset_x = self._offset_x % 1.0
        self._offset_y = self._offset_y % 1.0
        self._apply_transform(context)

    def _capture_action_properties(self):
        """Store the final modal UV transform on hidden operator properties."""
        self.action_face_index = self._face_index
        self.action_other_face_indices = ",".join(str(idx) for idx in self._other_face_indices)
        self.action_scale_u = self._scale_u
        self.action_scale_v = self._scale_v
        self.action_rotation = self._rotation
        self.action_offset_x = self._offset_x
        self.action_offset_y = self._offset_y

    def _get_action_other_face_indices(self):
        if not self.action_other_face_indices:
            return []
        return [int(part) for part in self.action_other_face_indices.split(",") if part]

    def _finish_from_modal(self, context):
        self._offset_x = self._offset_x % 1.0
        self._offset_y = self._offset_y % 1.0
        self._capture_action_properties()
        return self.execute(context)

    def execute(self, context):
        self._apply_transform_values(
            context,
            self.action_face_index,
            self._get_action_other_face_indices(),
            self.action_scale_u,
            self.action_scale_v,
            self.action_rotation,
            self.action_offset_x,
            self.action_offset_y,
        )
        return {'FINISHED'}

    def _cleanup(self, context):
        """Remove draw handlers and restore state."""
        if MESH_OT_uv_transform_modal._active_instance is self:
            MESH_OT_uv_transform_modal._active_instance = None

        self._restore_face_selection_overlay()

        if self._draw_handler_3d is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler_3d, 'WINDOW')
            self._draw_handler_3d = None

        if self._draw_handler_2d is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handler_2d, 'WINDOW')
            self._draw_handler_2d = None

        if self._modal_window is not None:
            self._modal_window.cursor_modal_restore()
        if self._modal_workspace is not None:
            self._modal_workspace.status_text_set(None)
        tag_redraw_all_3d_views()


def register():
    bpy.utils.register_class(MESH_OT_uv_transform_modal)


def unregister():
    active_instance = MESH_OT_uv_transform_modal._active_instance
    if active_instance is not None:
        active_instance._cancelled = True
        active_instance._cleanup(bpy.context)
    bpy.utils.unregister_class(MESH_OT_uv_transform_modal)
