"""Shared cylinder profile drawing workflow for cut and builder tools."""

from . import utils as modal_draw_utils
from .base_operator import MIN_RECTANGLE_SIZE
from ..cylinder_profile import build_cylinder_profile


class CylinderProfileDrawMixin:
    """Draw an elliptical polygon profile, then extrude it to a depth."""

    def invoke(self, context, event):
        self._side_count = self.side_count
        self._radius_mode = self.radius_mode
        self._on_cylinder_profile_invoked()
        return super().invoke(context, event)

    def modal(self, context, event):
        if event.value == 'PRESS' and event.type in {
                'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            target = getattr(self, "_active_view_target", None)
            if (
                    not getattr(self, "_cancelled", False)
                    and target is not None
                    and target.is_live()):
                with context.temp_override(**target.override_kwargs()):
                    if event.type == 'WHEELUPMOUSE':
                        self._side_count += 1
                    else:
                        self._side_count = max(3, self._side_count - 1)
                    self.side_count = self._side_count
                    self._update_shape_preview()
                    self._on_cylinder_profile_changed(context)
                    self._update_header(context)
                    modal_draw_utils.tag_redraw_all_3d_views()
                    return {'RUNNING_MODAL'}

        return super().modal(context, event)

    def _on_cylinder_profile_invoked(self):
        """Allow a consumer to initialise data tied to its profile."""

    def _on_cylinder_profile_changed(self, context):
        """Allow a consumer to refresh data tied to its profile."""

    def _confirm_first_vertex(self, context, event):
        result = super()._confirm_first_vertex(context, event)
        self._update_shape_preview()
        return result

    def _confirm_line_end(self, context, event):
        result = super()._confirm_line_end(context, event)
        self._update_shape_preview()
        self._on_cylinder_profile_changed(context)
        return result

    def _update_second_vertex_preview(self, context, event):
        super()._update_second_vertex_preview(context, event)
        self._update_shape_preview()
        self._on_cylinder_profile_changed(context)

    def _update_depth_preview(self, context, event):
        super()._update_depth_preview(context, event)
        self._update_shape_preview()
        self._on_cylinder_profile_changed(context)

    def _update_shape_preview(self):
        if self._first_vertex is None or self._second_vertex is None:
            return
        if self._local_x is None or self._local_y is None:
            return

        difference = self._second_vertex - self._first_vertex
        signed_radius_x = difference.dot(self._local_x)
        signed_radius_y = difference.dot(self._local_y)
        radius_x = abs(signed_radius_x)
        radius_y = abs(signed_radius_y)

        profile = build_cylinder_profile(
            self._first_vertex,
            radius_x,
            radius_y,
            self._local_x,
            self._local_y,
            self._side_count,
            self._radius_mode,
        )
        x_point = self._first_vertex + self._local_x * signed_radius_x
        y_point = self._first_vertex + self._local_y * signed_radius_y
        quarter_corner = x_point + self._local_y * signed_radius_y
        guides = (
            (self._first_vertex, x_point),
            (x_point, quarter_corner),
            (quarter_corner, y_point),
            (y_point, self._first_vertex),
        )
        self._preview.update_custom_profile(profile, guides)

    def _get_rectangle_invalid_message(self, local_dx, local_dy):
        if local_dx < MIN_RECTANGLE_SIZE and local_dy < MIN_RECTANGLE_SIZE:
            return "Move away from the center"
        if local_dx < MIN_RECTANGLE_SIZE:
            return "First radius must be greater than zero"
        if local_dy < MIN_RECTANGLE_SIZE:
            return "Second radius must be greater than zero"
        return None

    def _update_header(self, context):
        tool_name = self._get_tool_name()
        sides_hint = f"Sides: {self._side_count} (Wheel)"
        if self._state == self.STATE_FIRST_VERTEX:
            lock_hint = (
                " (Axis Locked)"
                if self._axis_lock_normal is not None
                else " | Ctrl to lock axis"
            )
            text = (
                f"{tool_name}: Click to set center{lock_hint} | "
                f"{sides_hint} | ESC to cancel"
            )
        elif self._state == self.STATE_LINE_END:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            else:
                prompt = "Click to set first radius"
            text = f"{tool_name}: {prompt} | {sides_hint} | ESC to cancel"
        elif self._state == self.STATE_SECOND_VERTEX:
            if self._invalid_message is not None:
                prompt = self._invalid_message
            elif self._line_mode:
                prompt = "Click to set perpendicular radius"
            else:
                prompt = "Click to set quarter profile"
            text = f"{tool_name}: {prompt} | {sides_hint} | ESC to cancel"
        elif self._state == self.STATE_DEPTH:
            if self._invalid_message is not None:
                prompt = f"{self._invalid_message} ({self._depth:.3f})"
            else:
                prompt = (
                    f"Move mouse to set depth ({self._depth:.3f}) | "
                    "Click to confirm"
                )
            text = f"{tool_name}: {prompt} | {sides_hint} | ESC to cancel"
        else:
            text = f"{tool_name} | {sides_hint} | ESC to cancel"
        if context.area is not None:
            context.area.header_text_set(text)
