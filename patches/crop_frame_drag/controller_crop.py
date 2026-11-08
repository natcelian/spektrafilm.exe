"""The crop part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes CropMixin as a base.
"""
from __future__ import annotations

import numpy as np

from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.controller_layers import INPUT_LAYER_NAME
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.shell_layout import dialog_parent, set_canvas_background_color
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm_gui.crop_frame import (
    MIN_CROP_SIZE,
    angle_from_line,
    crop_rect_uv,
    fit_rect_about_centre,
    furthest_drag,
    parse_ratio,
    rect_fits_rotated_image,
    rect_inside_frame,
    rect_to_center_size,
    refit_to_ratio,
    resize_rect,
    swap_ratio_label,
    swap_rect_orientation,
)
from spektrafilm_gui.canvas_backgrounds import CANVAS_BACKGROUND_OPTIONS
from spektrafilm_gui.loupe import frozen_autoexposure_ev


class CropMixin:
    """GuiController's crop methods."""

    def set_crop_tool(self, mode: str) -> None:
        """Arm or disarm the on-canvas crop tool. While armed the camera pan
        is suspended so the drag drives the crop instead. Phase 9 modes:
        'frame' (persistent LR crop frame) and 'line' (straighten); the
        legacy 'draw'/'move' rubber band remains for the napari-era path."""
        if mode in ('frame', 'line') and self._gpu_canvas is not None:
            self._crop_tool_mode = mode
            if mode == 'frame':
                self._arm_crop_frame()
            else:
                self._arm_straighten_line()
            return
        if mode == 'off' and self._frame_rect_uv is not None:
            # LR semantics: toggling the crop tool off APPLIES the frame
            self._frame_commit()
            return
        self._crop_tool_mode = mode if mode in ('draw', 'move') else 'off'
        # Phase 6B: when the wgpu canvas is active it owns the interaction
        # (overlay rubber band); napari's drag callbacks are not used.
        if self._gpu_canvas is not None:
            self._set_canvas_crop_tool(self._crop_tool_mode)
            return
        callbacks = self._viewer.mouse_drag_callbacks
        if self._crop_tool_mode == 'off':
            if self._crop_tool_drag in callbacks:
                callbacks.remove(self._crop_tool_drag)
            self._remove_crop_tool_overlay()
            self._set_camera_interactive(True)
            return
        if self._crop_tool_drag not in callbacks:
            callbacks.append(self._crop_tool_drag)
        self._set_camera_interactive(False)
        hint = ('Drag on the image to draw the crop rectangle'
                if self._crop_tool_mode == 'draw'
                else 'Drag to move the crop rectangle (Shift = fine)')
        set_status(self._viewer, hint, timeout_ms=0)

    # -- Phase 6B: crop tool on the wgpu canvas -----------------------------
    def _set_canvas_crop_tool(self, mode: str) -> None:
        canvas = self._gpu_canvas
        if mode == 'off':
            canvas.arm_crop_tool('off')
            return
        finishing_widgets = self._widgets.scan_finishing
        if mode == 'draw' and bool(finishing_widgets.crop.value):
            finishing_widgets.crop.value = False
            self._finish_crop_tool('Scan crop cleared - draw again on the full scan once it refreshes')
            return
        self._canvas_move_center0 = tuple(float(v) for v in finishing_widgets.crop_center.value)
        self._canvas_move_size0 = tuple(float(v) for v in finishing_widgets.crop_size.value)
        self._canvas_move_crop_active = bool(finishing_widgets.crop.value)
        canvas.arm_crop_tool(
            mode,
            on_draw=self._canvas_crop_drawn,
            on_move=self._canvas_crop_moved,
            on_finish=lambda: self._finish_crop_tool('Scan crop moved'),
            aspect_ratio=self._widgets.scan_frame.aspect_ratio,
        )
        self._follow_skew_on_ruler()
        hint = ('Drag on the image to draw the crop rectangle'
                if mode == 'draw'
                else 'Drag to move the crop rectangle (Shift = fine)')
        set_status(self._viewer, hint, timeout_ms=0)

    def _follow_skew_on_ruler(self) -> None:
        """THE RULER FOLLOWS THE FIELD, so straightening from the panel with
        the crop open moves the mark. Connected once, on the first arm:
        `on_editor_changed` is the tuple-safe helper, and reconnecting on
        every arm would stack duplicate slots. Called by every crop mode that
        paints the ruler -- the LR frame only started to on 2026-09-19; until
        then it read 0.0 whatever the skew was."""
        if not self._crop_angle_connected:
            from spektrafilm_gui.dyeworks_editors import on_editor_changed

            self._crop_angle_connected = on_editor_changed(
                self._widgets.scan_finishing.skew_deg,
                lambda *_: self._sync_crop_angle())
        self._sync_crop_angle()

    def _sync_crop_angle(self) -> None:
        """Feed the crop overlay's ruler from `scan_finishing.skew_deg`.

        2026-08-05; see design/DECISIONS.md#d-controller-305
        """
        canvas = self._gpu_canvas
        setter = getattr(canvas, 'set_crop_angle', None)
        if not callable(setter):
            return
        try:
            setter(float(self._widgets.scan_finishing.skew_deg.value))
        except (AttributeError, TypeError, ValueError):
            pass

    def _canvas_crop_drawn(self, u0: float, v0: float, u1: float, v1: float) -> None:
        """Rubber-band finished on the wgpu canvas: (u, v) are normalized
        image coordinates — the same frame the scan crop is defined in."""
        width, height = self._gpu_canvas.renderer.image_size
        long_edge = max(width, height, 1)
        rect_u, rect_v = abs(u1 - u0), abs(v1 - v0)
        size_x = rect_u * width / long_edge      # fraction of the long side
        size_y = rect_v * height / long_edge
        if size_x < 0.005 or size_y < 0.005:
            self._finish_crop_tool('Crop rectangle too small; kept previous crop')
            return
        finishing_widgets = self._widgets.scan_finishing
        finishing_widgets.crop_center.value = (
            min(max((u0 + u1) / 2.0, 0.0), 1.0),
            min(max((v0 + v1) / 2.0, 0.0), 1.0),
        )
        finishing_widgets.crop_size.value = (
            min(max(size_x, 0.005), 1.0),
            min(max(size_y, 0.005), 1.0),
        )
        finishing_widgets.crop.value = True
        self._finish_crop_tool('Scan crop set from the drawn rectangle')

    def _canvas_crop_moved(self, du: float, dv: float, fine: bool) -> None:
        """Streaming move deltas (fractions of the displayed image rect) —
        same sensitivity/span composition as the napari path."""
        sensitivity = 0.2 if fine else 0.5
        span_x = self._canvas_move_size0[0] if self._canvas_move_crop_active else 1.0
        span_y = self._canvas_move_size0[1] if self._canvas_move_crop_active else 1.0
        self._widgets.scan_finishing.crop_center.value = (
            min(max(self._canvas_move_center0[0] + du * sensitivity * span_x, 0.0), 1.0),
            min(max(self._canvas_move_center0[1] + dv * sensitivity * span_y, 0.0), 1.0),
        )

    def _crop_image_wh(self) -> tuple[float, float]:
        width, height = self._gpu_canvas.renderer.image_size
        return (max(float(width), 1.0), max(float(height), 1.0))

    def _frame_ratio_uv(self):
        """The locked aspect (width/height, converted to uv units) or None.
        Any preset other than Free acts as the lock; Free is unlocked."""

        image_wh = self._crop_image_wh()
        text = ''
        section = getattr(self._widgets, 'scan_frame', None)
        combo = getattr(section, 'ratio_combo', None)
        if combo is not None:
            text = str(combo.currentText())
        ratio = parse_ratio(text, image_wh)
        if ratio is None:
            return None
        width, height = image_wh
        return ratio * height / width

    def _arm_crop_frame(self) -> None:
        finishing_widgets = self._widgets.scan_finishing
        center = tuple(float(v) for v in finishing_widgets.crop_center.value)
        size = tuple(float(v) for v in finishing_widgets.crop_size.value)
        crop_active = bool(finishing_widgets.crop.value)
        skew = float(finishing_widgets.skew_deg.value)
        self._frame_initial = (crop_active, center, size, skew)
        if crop_active:
            # LR shows the WHOLE image while cropping: lift the crop and
            # REFRESH THE VIEW EXPLICITLY (with auto preview off the editor
            # signal renders nothing and the frame would sit over the stale
            # cropped view — the 'crop tool not working' report).
            finishing_widgets.crop.value = False
            self._frame_rect_uv = crop_rect_uv(center, size, self._crop_image_wh())
            self.request_view_refresh()
        else:
            self._frame_rect_uv = (0.0, 0.0, 1.0, 1.0)
        self._gpu_canvas.arm_crop_tool(
            'frame',
            on_frame_drag=self._frame_dragged,
            on_frame_release=self._frame_drag_released,
            on_frame_commit=self._frame_commit,
            on_frame_cancel=self._frame_cancel,
            on_skew_start=self._skew_drag_started,
            on_skew=self._skew_dragged,
            on_skew_release=self._skew_drag_released,
        )
        self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
        self._follow_skew_on_ruler()
        set_status(self._viewer,
                   'Crop: drag handles to resize, drag inside to move the frame, '
                   'drag outside the picture to straighten (Shift = fine) - '
                   'Enter/double-click applies, Esc cancels',
                   timeout_ms=0)

    def _skew_drag_started(self) -> None:
        if self._frame_rect_uv is None:
            return
        self._skew_drag_state = [float(self._widgets.scan_finishing.skew_deg.value),
                                 tuple(self._frame_rect_uv)]

    def _skew_dragged(self, delta_deg: float, fine: bool) -> None:
        """Turn the image by the pointer's turn about the picture's centre,
        and fit the crop inside the turned image. The fit starts from the
        frame as it was at the PRESS, so turning back within one drag gives
        the frame back rather than leaving it shrunk, and shrinks it about
        its own centre, so it stays where the user put it."""

        state = self._skew_drag_state
        if state is None or self._frame_rect_uv is None:
            return
        state[0] += float(delta_deg) * (self.SKEW_FINE_FACTOR if fine else 1.0)
        state[0] = max(min(state[0], 45.0), -45.0)
        skew = round(state[0], 2)
        self._frame_rect_uv = fit_rect_about_centre(state[1], skew, self._crop_image_wh())
        self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
        self._widgets.scan_finishing.skew_deg.value = skew

    def _skew_drag_released(self) -> None:
        if self._skew_drag_state is None:
            return
        self._skew_drag_state = None
        # the turned picture must show even with auto preview off (the field
        # change alone renders nothing then), as arming the frame does
        self.request_view_refresh()
        set_status(self._viewer,
                   f'Straightened: skew {float(self._widgets.scan_finishing.skew_deg.value):+.2f} deg')

    def _frame_dragged(self, hit: str, du: float, dv: float, fine: bool) -> None:
        """A handle (or the frame) held and moved. Tracked from where the
        drag started, as Lightroom's is: only what is held moves, it stops
        where it meets the picture's edge, and it follows the pointer again
        once the pointer comes back. (It used to fit the dragged frame into
        the turned picture -- d-controller-257 -- which slid and shrank it,
        moving the edges the user was not holding.)"""
        if self._frame_rect_uv is None:
            return
        factor = 0.2 if fine else 1.0
        if self._frame_drag_from is None or self._frame_drag_from[0] != hit:
            self._frame_drag_from = (hit, self._frame_rect_uv, [0.0, 0.0])
        _hit, start, total = self._frame_drag_from
        total[0] += du * factor
        total[1] += dv * factor
        rect = self._held_frame(hit, start, total[0], total[1])
        self._frame_rect_uv = rect
        self._gpu_canvas.set_crop_frame(rect)

    def _frame_drag_released(self) -> None:
        self._frame_drag_from = None

    def _held_frame(self, hit: str, start, du: float, dv: float):
        """The frame `start` with `hit` dragged by (du, dv), as far as it can
        go inside the turned picture."""
        ratio = self._frame_ratio_uv() if hit != 'inside' else None
        skew = float(self._widgets.scan_finishing.skew_deg.value)
        image_wh = self._crop_image_wh()

        def at(du_, dv_):
            if hit == 'inside':                 # the frame follows the pointer
                u0, v0, u1, v1 = start
                return (u0 + du_, v0 + dv_, u1 + du_, v1 + dv_)
            return resize_rect(start, hit, du_, dv_, ratio_uv=ratio, clamp=False)

        def fits(rect):
            return rect_inside_frame(rect) and rect_fits_rotated_image(rect, skew, image_wh)

        if not fits(start):                     # (a frame from before the angle)
            rect = resize_rect(start, hit, du, dv, ratio_uv=ratio) if hit != 'inside' else start
            return fit_rect_about_centre(rect, skew, image_wh)
        if ratio is not None and len(hit) == 2:
            # a corner on a locked ratio: both axes move together
            t = furthest_drag(lambda t: at(t * du, t * dv), fits)
            return at(t * du, t * dv)
        # each axis on its own, so a corner (or the frame) meeting one edge
        # still slides along it
        tu = furthest_drag(lambda t: at(t * du, 0.0), fits)
        tv = furthest_drag(lambda t: at(tu * du, t * dv), fits)
        return at(tu * du, tv * dv)

    def _frame_commit(self) -> None:
        if self._frame_rect_uv is None:
            return
        u0, v0, u1, v1 = self._frame_rect_uv
        # full frame is judged on the uv spans (the persisted size is
        # long-edge-normalized, so a full-frame short axis is < 1.0)
        full_frame = (u1 - u0) >= 0.999 and (v1 - v0) >= 0.999
        center, size = rect_to_center_size(self._frame_rect_uv, self._crop_image_wh())
        finishing_widgets = self._widgets.scan_finishing
        finishing_widgets.crop_center.value = (
            min(max(center[0], 0.0), 1.0), min(max(center[1], 0.0), 1.0))
        finishing_widgets.crop_size.value = (
            min(max(size[0], MIN_CROP_SIZE), 1.0), min(max(size[1], MIN_CROP_SIZE), 1.0))
        finishing_widgets.crop.value = not full_frame
        self._frame_rect_uv = None
        self._frame_initial = None
        self._finish_crop_tool(
            'Scan crop applied' if not full_frame else 'Full frame - crop off')

    def _frame_cancel(self) -> None:
        finishing_widgets = self._widgets.scan_finishing
        if self._frame_initial is not None:
            crop_active, center, size, skew = self._frame_initial
            finishing_widgets.crop_center.value = center
            finishing_widgets.crop_size.value = size
            finishing_widgets.skew_deg.value = skew     # a turn made in the tool too
            finishing_widgets.crop.value = crop_active
        self._frame_rect_uv = None
        self._frame_initial = None
        self._finish_crop_tool('Crop cancelled - previous crop restored')

    def refit_crop_to_ratio(self) -> None:
        """Ratio preset selected while the frame is armed: re-fit the frame
        to the new aspect (centered, max-fit)."""

        if self._frame_rect_uv is None or self._gpu_canvas is None:
            return
        image_wh = self._crop_image_wh()
        section = getattr(self._widgets, 'scan_frame', None)
        combo = getattr(section, 'ratio_combo', None)
        text = str(combo.currentText()) if combo is not None else ''
        ratio = parse_ratio(text, image_wh)
        self._frame_rect_uv = refit_to_ratio(self._frame_rect_uv, ratio, image_wh)
        self._gpu_canvas.set_crop_frame(self._frame_rect_uv)

    def swap_crop_orientation(self) -> None:
        """The Frame section's Swap: landscape <-> portrait, LR-style — the
        crop's pixel dimensions swap (shrinking uniformly only when the
        swapped shape no longer fits), and a locked ratio preset flips to its
        counterpart (16:9 -> 9:16) so further handle drags stay consistent."""

        section = getattr(self._widgets, 'scan_frame', None)
        combo = getattr(section, 'ratio_combo', None)
        if combo is not None:
            swapped_label = swap_ratio_label(combo.currentText())
            index = combo.findText(swapped_label)
            if index >= 0 and index != combo.currentIndex():
                was_blocked = combo.blockSignals(True)   # keep our size-preserving swap
                combo.setCurrentIndex(index)
                combo.blockSignals(was_blocked)
        image_wh = self._crop_image_wh() if self._gpu_canvas is not None else (1.0, 1.0)
        if self._frame_rect_uv is not None and self._gpu_canvas is not None:
            self._frame_rect_uv = swap_rect_orientation(self._frame_rect_uv, image_wh)
            self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
            set_status(self._viewer, 'Crop frame orientation swapped')
            return
        finishing_widgets = self._widgets.scan_finishing
        if not bool(finishing_widgets.crop.value):
            set_status(self._viewer, 'No crop to swap - arm the Crop tool or set a crop first')
            return
        center = tuple(float(v) for v in finishing_widgets.crop_center.value)
        size = tuple(float(v) for v in finishing_widgets.crop_size.value)
        rect = swap_rect_orientation(crop_rect_uv(center, size, image_wh), image_wh)
        new_center, new_size = rect_to_center_size(rect, image_wh)
        finishing_widgets.crop_center.value = new_center
        finishing_widgets.crop_size.value = new_size
        set_status(self._viewer, 'Crop orientation swapped')
        self.request_view_refresh()

    def reset_crop(self) -> None:
        """The Frame section's Reset: back to the ORIGINAL photo format —
        full frame, ratio Free. Works on the armed frame or the persisted
        crop directly (view refreshed either way)."""
        section = getattr(self._widgets, 'scan_frame', None)
        combo = getattr(section, 'ratio_combo', None)
        if combo is not None:
            was_blocked = combo.blockSignals(True)
            combo.setCurrentIndex(0)            # 'Free'
            combo.blockSignals(was_blocked)
        if self._frame_rect_uv is not None and self._gpu_canvas is not None:
            self._frame_rect_uv = (0.0, 0.0, 1.0, 1.0)
            self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
            set_status(self._viewer, 'Crop frame reset to the full photo')
            return
        finishing_widgets = self._widgets.scan_finishing
        finishing_widgets.crop.value = False
        finishing_widgets.crop_center.value = (0.5, 0.5)
        finishing_widgets.crop_size.value = (1.0, 1.0)
        set_status(self._viewer, 'Crop reset to the original photo format')
        self.request_view_refresh()

    # -- Phase 9: straighten line --------------------------------------------
    def _arm_straighten_line(self) -> None:
        self._gpu_canvas.arm_crop_tool('line', on_line=self._straighten_line_drawn)
        set_status(self._viewer,
                   'Straighten: drag a line along a horizon or vertical', timeout_ms=0)

    def _straighten_line_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        angle = angle_from_line(x0, y0, x1, y1)
        finishing_widgets = self._widgets.scan_finishing
        current = float(finishing_widgets.skew_deg.value)
        new_skew = max(min(current + angle, 45.0), -45.0)
        finishing_widgets.skew_deg.value = round(new_skew, 2)
        # 2026-09-19; see design/DECISIONS.md#d-controller-258
        image_wh = self._crop_image_wh()
        if self._frame_rect_uv is not None:
            self._frame_rect_uv = fit_rect_about_centre(
                self._frame_rect_uv, new_skew, image_wh)
            self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
        elif bool(finishing_widgets.crop.value):
            center = tuple(float(v) for v in finishing_widgets.crop_center.value)
            size = tuple(float(v) for v in finishing_widgets.crop_size.value)
            rect = fit_rect_about_centre(
                crop_rect_uv(center, size, image_wh), new_skew, image_wh)
            new_center, new_size = rect_to_center_size(rect, image_wh)
            finishing_widgets.crop_center.value = new_center
            finishing_widgets.crop_size.value = new_size
        self._finish_crop_tool(f'Straightened {angle:+.2f} deg (skew {new_skew:+.2f})')

    def _set_camera_interactive(self, interactive: bool) -> None:
        # napari >= 0.5 splits camera interaction into mouse_pan / mouse_zoom
        # ('interactive' is gone) — set whichever exists, or the drag pans the
        # canvas instead of driving the crop tool.
        camera = getattr(self._viewer, 'camera', None)
        if camera is None:
            return
        for attribute in ('interactive', 'mouse_pan', 'mouse_zoom'):
            if hasattr(camera, attribute):
                try:
                    setattr(camera, attribute, bool(interactive))
                except (AttributeError, ValueError):
                    pass

    def _crop_tool_drag(self, viewer, event):
        mode = self._crop_tool_mode
        if mode == 'off' or getattr(event, 'button', 1) != 1:
            return
        extent = self._crop_target_world_extent()
        if extent is None:
            return
        finishing_widgets = self._widgets.scan_finishing
        start = tuple(float(v) for v in event.position[-2:])   # (y, x) world
        if mode == 'move':
            editor = finishing_widgets.crop_center
            center0 = tuple(float(v) for v in editor.value)
            size0 = tuple(float(v) for v in finishing_widgets.crop_size.value)
            crop_active = bool(finishing_widgets.crop.value)
            ymin, xmin, ymax, xmax = extent
            yield
            while event.type == 'mouse_move':
                pos = tuple(float(v) for v in event.position[-2:])
                sensitivity = 0.2 if 'Shift' in tuple(getattr(event, 'modifiers', ())) else 0.5
                fx = (pos[1] - start[1]) / max(xmax - xmin, 1e-9) * sensitivity
                fy = (pos[0] - start[0]) / max(ymax - ymin, 1e-9) * sensitivity
                # With a crop active the display shows the cropped region, so
                # a full-width drag spans one crop width in pre-crop units.
                span_x = size0[0] if crop_active else 1.0
                span_y = size0[1] if crop_active else 1.0
                editor.value = (
                    min(max(center0[0] + fx * span_x, 0.0), 1.0),
                    min(max(center0[1] + fy * span_y, 0.0), 1.0),
                )
                yield
            self._finish_crop_tool('Scan crop moved')
            return
        # draw mode: the rect is defined against the FULL rendered scan; if a
        # scan crop is already active, clear it first (the preview refreshes
        # to full frame, then draw).
        if bool(finishing_widgets.crop.value):
            finishing_widgets.crop.value = False
            self._finish_crop_tool('Scan crop cleared - draw again on the full scan once it refreshes')
            return
        end = start
        yield
        while event.type == 'mouse_move':
            end = tuple(float(v) for v in event.position[-2:])
            end = self._constrain_crop_corner(start, end)
            self._update_crop_tool_overlay(start, end)
            yield
        self._remove_crop_tool_overlay()
        ymin, xmin, ymax, xmax = extent
        width_ws, height_ws = xmax - xmin, ymax - ymin
        long_ws = max(width_ws, height_ws, 1e-9)
        rect_w = abs(end[1] - start[1])
        rect_h = abs(end[0] - start[0])
        if rect_w / long_ws < 0.005 or rect_h / long_ws < 0.005:
            self._finish_crop_tool('Crop rectangle too small; kept previous crop')
            return
        cx = (start[1] + end[1]) / 2.0
        cy = (start[0] + end[0]) / 2.0
        finishing_widgets.crop_center.value = (
            min(max((cx - xmin) / max(width_ws, 1e-9), 0.0), 1.0),
            min(max((cy - ymin) / max(height_ws, 1e-9), 0.0), 1.0),
        )
        finishing_widgets.crop_size.value = (
            min(max(rect_w / long_ws, 0.005), 1.0),
            min(max(rect_h / long_ws, 0.005), 1.0),
        )
        finishing_widgets.crop.value = True
        self._finish_crop_tool('Scan crop set from the drawn rectangle')

    def _constrain_crop_corner(self, start, end):
        """Constrain the dragged corner to the selected aspect ratio, exactly
        as NegPy does during the drag (the smaller axis follows)."""
        ratio = self._widgets.scan_frame.aspect_ratio()
        if ratio is None:
            return end
        dx = end[1] - start[1]
        dy = end[0] - start[0]
        if abs(dx) > abs(dy) * ratio:
            dx = abs(dy) * ratio * (1 if dx >= 0 else -1)
        else:
            dy = abs(dx) / ratio * (1 if dy >= 0 else -1)
        return (start[0] + dy, start[1] + dx)

    def _crop_target_world_extent(self):
        """World extent of the layer the SCAN crop tool draws on: the rendered
        output when visible (the crop is a cutout of the rendered film),
        otherwise the input preview. No geometry re-projection is needed —
        the displayed render already carries the scan desqueeze/skew, and the
        scan crop is defined in exactly that frame."""
        output_layer = self._output_layer()
        layer = (output_layer if output_layer is not None
                 and getattr(output_layer, 'visible', False) else None)
        if layer is None:
            layer = self._layers.preview_input_layer() or self._layers.image_layer(INPUT_LAYER_NAME)
        if layer is None:
            return None
        world = np.asarray(layer.extent.world)
        return (float(world[0][-2]), float(world[0][-1]),
                float(world[1][-2]), float(world[1][-1]))

    def _update_crop_tool_overlay(self, p0, p1) -> None:
        rect = np.array([
            [p0[0], p0[1]], [p0[0], p1[1]], [p1[0], p1[1]], [p1[0], p0[1]],
        ])
        layer = next((l for l in self._viewer.layers
                      if getattr(l, 'name', None) == self._CROP_TOOL_LAYER_NAME), None)
        previous_active = getattr(self._viewer.layers.selection, 'active', None)
        if layer is None:
            layer = self._viewer.add_shapes(
                [rect], shape_type='polygon',
                edge_color='white', face_color=[0.0, 0.0, 0.0, 0.25],
                edge_width=0.003, name=self._CROP_TOOL_LAYER_NAME,
            )
            layer.editable = False
            if previous_active is not None and previous_active is not layer:
                self._viewer.layers.selection.active = previous_active
        else:
            layer.data = [rect]

    def _remove_crop_tool_overlay(self) -> None:
        layer = next((l for l in self._viewer.layers
                      if getattr(l, 'name', None) == self._CROP_TOOL_LAYER_NAME), None)
        if layer is not None:
            self._viewer.layers.remove(layer)

    def _finish_crop_tool(self, message: str) -> None:
        self._widgets.scan_frame.release_tools()
        self.set_crop_tool('off')
        set_status(self._viewer, message)
        # Crop/straighten results are VIEW changes: render them even when
        # auto preview is off (same rule as the scan-film toggle).
        self.request_view_refresh()

    def mirror_input_image_horizontal(self) -> None:
        """Mirror the source image left-right (like the NegPy flip-H toggle;
        applied to the working image, so the whole pipeline inherits it)."""
        self._mirror_input_image(axis=1)

    def mirror_input_image_vertical(self) -> None:
        """Mirror the source image top-bottom."""
        self._mirror_input_image(axis=0)

    def _mirror_input_image(self, *, axis: int) -> None:
        input_image = self._current_input_image
        if input_image is None:
            return
        data = np.asarray(input_image)
        mirrored = np.ascontiguousarray(np.flip(data, axis=axis))
        self._update_preview_cache(
            mirrored,
            home_input_stack=True,
            hide_output=True,
        )
        self._request_auto_preview_if_enabled()

    def roll_step(self, delta: int) -> None:
        """Prev/next frame from the bottom navigation bar (clamped). With the
        star filter active, frames below the minimum rating are skipped."""
        if not self._roll.files:
            set_status(self._viewer, 'The roll is empty')
            return
        step = 1 if int(delta) > 0 else -1
        index = self._roll.current_index
        for _ in range(len(self._roll.files)):
            index = index + step
            if not (0 <= index < len(self._roll.files)):
                if self._rating_filter:
                    set_status(self._viewer, 'No more frames matching the star filter')
                return
            if self._passes_rating_filter(self._roll.files[index]):
                self.roll_activate(index)
                return

    def set_canvas_background_color(self, color: str) -> None:
        """Apply one of the navigation-bar background swatches and persist the
        choice across sessions (NegPy persists its canvas_bg_index the same way)."""
        set_canvas_background_color(self._viewer, color)
        if self._gpu_canvas is not None:
            try:
                self._gpu_canvas.set_background_hex(color)
            except Exception:
                pass
        app_settings().setValue('canvas_background', color)

    def cycle_canvas_background(self) -> None:
        """Ctrl+L: step through the three nav-bar background swatches (the
        persisted choice is the cycle position; the swatch buttons follow)."""
        current = str(app_settings().value('canvas_background', '') or '')
        colors = [color for _, color in CANVAS_BACKGROUND_OPTIONS]
        index = colors.index(current) if current in colors else -1
        next_index = (index + 1) % len(colors)
        label, color = CANVAS_BACKGROUND_OPTIONS[next_index]
        self.set_canvas_background_color(color)
        self._sync_background_swatches(next_index)
        set_status(self._viewer, f'Canvas background: {label}')

    def _sync_background_swatches(self, checked_index: int) -> None:
        """Reflect a programmatic background change on the nav-bar swatch
        buttons (cosmetic; never let a lookup failure break the shortcut)."""
        try:
            from qtpy import QtWidgets
            from spektrafilm_gui.canvas_backgrounds import CANVAS_BACKGROUND_OPTIONS
            host = dialog_parent(self._viewer)
            if host is None:
                return
            for index in range(len(CANVAS_BACKGROUND_OPTIONS)):
                button = host.findChild(QtWidgets.QToolButton, f'canvasSwatch{index}')
                if button is not None:
                    button.blockSignals(True)
                    button.setChecked(index == checked_index)
                    button.blockSignals(False)
            from spektrafilm_gui.left_panel_config import UiSectionView

            for section in host.findChildren(UiSectionView):
                section._refresh_canvas_text()
        except Exception:
            pass

    def cycle_canvas_guides(self) -> None:
        """Ctrl+O: cycle the composition guide overlay on the canvas
        (off -> grid -> rule of thirds -> golden ratio -> diagonals ->
        triangle -> golden spiral -> center cross), persisted across sessions."""
        if self._gpu_canvas is None:
            set_status(self._viewer, 'Composition guides need the canvas')
            return
        try:
            mode = self._gpu_canvas.cycle_guide()
        except Exception:
            set_status(self._viewer, 'Composition guides are unavailable on this canvas')
            return
        app_settings().setValue('canvas_guides', mode)
        labels = {
            'off': 'off',
            'grid': 'grid',
            'thirds': 'rule of thirds',
            'golden': 'golden ratio',
            'diagonals': 'diagonals',
            'triangle': 'triangle',
            'golden_spiral': 'golden spiral',
            'center': 'center cross',
        }
        set_status(self._viewer, f'Composition guides: {labels.get(mode, mode)}')

    def flip_canvas_guides(self) -> None:
        """Shift+O: flip the orientation of the triangle / golden-spiral guide."""
        if self._gpu_canvas is None:
            set_status(self._viewer, 'Composition guides need the canvas')
            return
        try:
            orient = self._gpu_canvas.flip_guide()
        except Exception:
            set_status(self._viewer, 'Composition guides are unavailable on this canvas')
            return
        app_settings().setValue('canvas_guide_orient', orient)
        mode = getattr(self._gpu_canvas, 'guide_mode', 'off')
        if mode in ('triangle', 'golden_spiral'):
            set_status(self._viewer, f'Guide orientation: {int(orient) + 1}/4')
        else:
            set_status(self._viewer, 'Orientation flip applies to the triangle / golden spiral')

    def toggle_scan_film(self) -> None:
        """Ctrl+F: flip the bottom-bar 'scan film' toggle (show the scanned
        negative instead of the print), then refresh the preview."""
        sim = self._widgets.simulation
        try:
            new_value = not bool(sim.scan_film_value())
            sim.set_scan_film_value(new_value)
        except Exception:
            return
        self.request_view_refresh()
        set_status(self._viewer, f'Scan film: {"on" if new_value else "off"}')

    def request_view_refresh(self, *_args) -> None:
        """Scan-film / scan-for-print are VIEW switches, not parameter tweaks:
        re-render the preview even when auto preview is off or still dormant
        (routing them through request_auto_preview made the toggle and Ctrl+F
        look dead — NAT 2026-07-12)."""
        self._schedule_history_push()
        if self._current_preview_image is None:
            return
        if self._auto_preview_enabled():
            self.request_auto_preview()
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; the view switches after this render')
            return
        self._run_preview(report_status=False)

    def scan_auto_levels(self) -> None:
        """SCAN tab Auto levels: measure the rendered output's per-channel
        0.1 / 99.9 percentiles (scanner-software style floors/ceilings) and
        set the master black/white points from their envelope, backed off by
        AUTO_LEVELS_MARGIN of the span on each side, plus a master gamma that
        centers the luma median (NAT 2026-07-18). Luminance only: the
        per-channel offsets are reset to zero, never set — stretching each
        channel to its own floor/ceiling neutralized real casts and skewed
        grading, usually toward red (NAT 2026-07-18)."""
        output_layer = self._output_layer()
        if output_layer is None:
            set_status(self._viewer, 'Run a preview or scan before Auto levels')
            return
        data = np.asarray(output_layer.data)[..., :3]
        if not np.issubdtype(data.dtype, np.floating):
            data = runtime.normalized_image_data(data)
        floors = np.percentile(data.reshape(-1, 3), 0.1, axis=0)
        ceilings = np.percentile(data.reshape(-1, 3), 99.9, axis=0)
        # Envelope of the channel floors/ceilings: the same master points on
        # all three channels, so no channel clips and no cast is introduced.
        master_black = float(floors.min())
        master_white = float(ceilings.max())
        margin = self.AUTO_LEVELS_MARGIN * max(master_white - master_black, 1e-6)
        master_black -= margin
        master_white += margin
        gamma = self._auto_levels_gamma(data, master_black, master_white)
        section = self._widgets.scan_finishing
        section.black_point.value = round(master_black, 4)
        section.white_point.value = round(master_white, 4)
        section.black_point_rgb.value = (0.0, 0.0, 0.0)
        section.white_point_rgb.value = (0.0, 0.0, 0.0)
        section.gamma.value = gamma
        set_status(self._viewer, 'Scan levels measured from the rendered output (luminance only)')
        self._request_auto_preview_if_enabled()

    def _auto_levels_gamma(self, data: np.ndarray, master_black: float, master_white: float) -> float:
        """Master gamma putting the mapped luma median at the target: the
        finishing kernel applies out**(1/g) after the levels mapping, so
        pos**(1/g) = target -> g = ln(pos)/ln(target). Same curve on all
        three channels — no cast."""
        luma = data @ np.array([0.2126, 0.7152, 0.0722])   # Rec.709, as the kernel
        pos = (float(np.median(luma)) - master_black) / max(master_white - master_black, 1e-6)
        if not 0.02 < pos < 0.98:
            return 1.0
        gamma = float(np.log(pos) / np.log(self.AUTO_LEVELS_GAMMA_TARGET))
        if abs(gamma - 1.0) < self.AUTO_LEVELS_GAMMA_DEADBAND:
            return 1.0
        low, high = self.AUTO_LEVELS_GAMMA_RANGE
        return round(min(max(gamma, low), high), 2)

    def meter_camera_exposure(self) -> None:
        """Exposure control 'Meter': HOLD the auto-exposure reading.

        Measured exactly like the pipeline's auto_exposure stage — the same
        film-raw meter through the same film, on the UNCROPPED input (the
        stage runs before crop_and_rescale) decimated the way roll analysis
        does, which the measurement is robust to. The reading is then held:
        auto exposure is switched ON with `auto_exposure_hold` and the EV in
        `auto_exposure_held_ev`, which the mapper hands the engine as its
        frozen EV. So the picture is exactly auto ON's at compensation 0
        (dE76 0), print included, and the compensation field adjusts from
        there. Writing the reading into the manual exposure field instead
        (the earlier behaviour) exposed the same negative, but Andrea's
        midgray print balance follows the meter only while auto exposure is
        on (with it off the scale is 1), so the print differed: dE76 5.9 with
        hoya_y2 and 15.8 with hoya_r1 in the app (the GUI pins the print's
        compensation reference at EV 0, so that scale is the whole cause
        here; at the engine's defaults the compensation is also cancelled in
        the print, 7.4 with no filter). While a reading is held the same
        button releases it (auto exposure meters every render again)."""

        simulation = self._widgets.simulation
        if simulation.auto_exposure_hold.value:
            simulation.auto_exposure_hold.value = False
            set_status(self._viewer, 'Released the held exposure: auto exposure meters each render')
            self._request_auto_preview_if_enabled()
            return
        image = self._current_input_image
        needs_white_balance = image is not None      # base needs the live WB
        if image is None:
            image = self._current_preview_image      # already balanced
        if image is None:
            set_status(self._viewer, 'Load an image before metering')
            return
        state = collect_gui_state(widgets=self._widgets)
        image = np.asarray(image)[..., :3]
        step = max(1, int(np.ceil(max(image.shape[0], image.shape[1]) / 640)))
        small = np.ascontiguousarray(image[::step, ::step])
        if needs_white_balance:
            # Phase 17A: balance AFTER decimation (per-pixel op, commutes
            # with subsampling exactly) so metering never pays full res.
            small = self._white_balanced_input(small, state=state)
        try:
            # 2026-09-29; see design/DECISIONS.md#d-controller-260
            with runtime.PIPELINE_RENDER_LOCK:
                measured = frozen_autoexposure_ev(small, build_params_from_state(state))
        except Exception as error:
            import logging
            logging.getLogger('spektrafilm.app').exception('metering failed')
            set_status(self._viewer, f'Metering failed: {error}')
            return
        # Held as measured: the picture must be auto ON's, so nothing is
        # rounded or clamped (the field's own range does not apply to it).
        simulation.auto_exposure_held_ev.value = float(measured)
        simulation.auto_exposure.value = True
        simulation.auto_exposure_hold.value = True
        set_status(self._viewer, (
            f'Metered {measured:+.2f} EV and held: auto exposure stays on at this '
            'reading; compensation adjusts from it (Release to meter live again)'))
        self._request_auto_preview_if_enabled()
