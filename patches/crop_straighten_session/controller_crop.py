"""The crop part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes CropMixin as a base.
"""
from __future__ import annotations

import math
from contextlib import contextmanager

import numpy as np
from qtpy import QtCore, QtWidgets

from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui import notices
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.controller_layers import INPUT_LAYER_NAME, INPUT_PREVIEW_LAYER_NAME
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.shell_layout import dialog_parent, set_canvas_background_color
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm_gui.crop_frame import (
    MIN_CROP_SIZE,
    angle_from_line,
    clamp_rect,
    crop_rect_uv,
    fit_rect_about_centre,
    fit_rect_in_rotated_image,
    furthest_drag,
    oriented_ratio,
    parse_ratio,
    rect_fits_rotated_image,
    rect_inside_frame,
    rect_to_center_size,
    refit_to_ratio,
    resize_rect,
    swap_rect_orientation,
)
from spektrafilm_gui.canvas_backgrounds import CANVAS_BACKGROUND_OPTIONS
from spektrafilm_gui.loupe import frozen_autoexposure_ev


#: what crop_center / crop_size keep (their four decimals, widget_specs)
CROP_FIELD_STEP = 1e-4


class _EscapeCancelsCrop(QtCore.QObject):
    """An Esc that reaches the main window -- one nothing under the focus
    used (the slider, the panel) -- cancels the open crop. A field, a
    popover or a menu that takes Esc keeps it."""

    def __init__(self, cancel) -> None:
        super().__init__()
        self._cancel = cancel

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
            self._cancel()
            return True
        return False


class CropMixin:
    """GuiController's crop methods."""

    # THE CROP SESSION is the armed crop frame. Its controls live in Scan >
    # Frame (Aspect ratio and orientation, Angle, Constrain to image,
    # Straighten, Reset, and while it is open Cancel · Done); the canvas only
    # has the frame. Nothing applies until Done. Typing or dragging an angle
    # opens it, as do the rail's crop button, a ratio or orientation pick and
    # a straighten line. The frame on screen is always `_frame_base_uv` (the
    # frame the user set, or the stored crop) fitted to the current angle --
    # never the previous fit refitted, which shrank it every time.
    _crop_session_render = None
    _crop_escape = None
    _frame_initial_portrait = False
    #: (key, unturned float frame, output colour space, display transform):
    #: the crop's film rendered once without the turn (`_crop_turn_fast_path`)
    _crop_flat = None
    _crop_flat_pending = None
    _crop_scopes_timer = None
    #: the desqueeze when the crop opened, for Cancel (Reset includes it)
    _frame_initial_desqueeze = None
    _skew_last_seen = 0.0
    _frame_base_uv = None
    _frame_from_straighten = False
    _crop_constrain = True
    #: (handle, frame at the press, total drag so far) while a handle is held
    _frame_drag_from = None
    #: True while the tool itself puts an angle back (Cancel, Reset)
    _skew_quiet = False

    def _connect_crop_session(self) -> None:
        """Wired here so the protected app.py stays untouched."""
        editor = getattr(getattr(self._widgets, 'scan_finishing', None), 'skew_deg', None)
        if editor is None:
            return
        from spektrafilm_gui.dyeworks_editors import on_editor_changed

        from spektrafilm_gui.state_bridge import on_restore

        self._skew_last_seen = float(editor.value)
        on_editor_changed(editor, self._on_skew_changed)
        on_restore(self._apply_crop_session_before_restore)
        frame = getattr(self._widgets, 'scan_frame', None)
        if frame is not None:
            frame.crop_done_requested.connect(lambda: self._frame_commit())
            frame.crop_cancel_requested.connect(lambda: self.cancel_crop_session())
            frame.constrain_toggled.connect(self.set_crop_constrain)
            frame.orientation_chosen.connect(self._orientation_chosen)
            frame.set_constrain(self._read_constrain())

    def _apply_crop_session_before_restore(self) -> None:
        """Reset, a preset, a pasted look or a version put back during a
        session: the session is applied first, as leaving the photo does."""
        self.apply_crop_session('Crop applied')

    def crop_session_active(self) -> bool:
        return self._frame_rect_uv is not None

    def _frame_state(self) -> tuple:
        """(crop on, centre, size, angle): what Cancel puts back."""
        finishing_widgets = self._widgets.scan_finishing
        return (bool(finishing_widgets.crop.value),
                tuple(float(v) for v in finishing_widgets.crop_center.value),
                tuple(float(v) for v in finishing_widgets.crop_size.value),
                float(finishing_widgets.skew_deg.value))

    def _on_skew_changed(self, *_args) -> None:
        """The angle moved. In a session: refit the frame. Otherwise any
        change the USER makes opens one -- typed, dragged, scrolled, pasted,
        reset, from whichever row shows the field -- and a restored look
        (undo, presets, roll frames, versions: `state_bridge.restoring`)
        never does. Sorting mode shows a thumbnail, not the photo to fit."""
        from spektrafilm_gui.state_bridge import is_restoring

        editor = self._widgets.scan_finishing.skew_deg
        before, self._skew_last_seen = self._skew_last_seen, float(editor.value)
        if self._skew_quiet:
            return
        if self.crop_session_active():
            self._refit_session_frame()
            self._schedule_session_render()
            return
        if not self._crop_session_allowed():
            return
        crop_active, center, size, _skew = self._frame_state()
        self._open_crop_session((crop_active, center, size, before))

    def _crop_session_allowed(self) -> bool:
        """Not while a stored look is put back, not in Input mode (a
        thumbnail, not the photo), and only with a photo on the canvas."""
        from spektrafilm_gui.state_bridge import is_restoring

        return not (is_restoring() or self._input_mode
                    or self._gpu_canvas is None or self._current_input_image is None)

    def _open_crop_session(self, initial=None) -> None:
        """`initial`: the state before an edit that has already landed (a
        typed angle); None opens on the state now."""
        focus = QtWidgets.QApplication.focusWidget() if initial is not None else None
        if initial is not None:
            self._record_look_before_edit(initial[3])
        self._crop_tool_mode = 'frame'
        self._arm_crop_frame(initial)
        self._sync_crop_buttons(True)
        if focus is not None:
            focus.setFocus()        # keep typing where the user is typing

    @contextmanager
    def _angle_put_back(self):
        """The tool itself setting the angle (Cancel, Reset): the row and the
        render follow as for any change; only the watcher above stays out."""
        self._skew_quiet = True
        try:
            yield self._widgets.scan_finishing.skew_deg
        finally:
            self._skew_quiet = False

    def _record_look_before_edit(self, angle_before: float) -> None:
        """The session lands as ONE undo step on top of the look before the
        edit that opened it -- and that edit has already landed, so put the
        old angle back for the record, silently, and return. (Blocked, not
        `_angle_put_back`: nothing may render the old angle.)"""
        editor = self._widgets.scan_finishing.skew_deg
        inner = getattr(editor, '_inner', None)
        angle_now = float(editor.value)
        blocked = [(widget, widget.blockSignals(True)) for widget in (editor, inner) if widget is not None]
        try:
            editor.value = angle_before
            self.history_flush()
            editor.value = angle_now
        finally:
            for widget, was_blocked in blocked:
                widget.blockSignals(was_blocked)

    def cancel_crop_session(self, reason: str = '', *, refresh: bool = True) -> None:
        """Leave the session without applying it (Cancel, Esc, undo, and a
        turn or mirror of the photo, which the frame no longer fits)."""
        if self.crop_session_active():
            self._frame_cancel(reason, refresh=refresh)

    def apply_crop_session(self, reason: str) -> None:
        """Leaving the photo applies the session, as leaving Lightroom's crop
        tool does: the angle typed is kept, with the frame that keeps its
        corners out. Quietly -- the photo is going away -- and with no render
        (the load that follows renders).

        Three doors out of a photo, each calls this once:
        - `roll_activate`, BEFORE the outgoing frame's look is stored;
        - `load_raw_image` / `load_input_image` for a different file (the
          roll's own loads, a dropped file; a re-decode of the same file
          keeps the session);
        - `state_bridge.on_restore`, for a look put back in place (Reset, a
          preset, a pasted look, a version, roll exposure);
        and `set_input_mode(True)`, which hides the photo behind its thumbnail.
        A turn or mirror of the photo CANCELS instead: the frame was fitted
        to the photo as it was."""
        if self.crop_session_active():
            self._frame_commit(announce=False, refresh=False, message=reason)

    def _ratio_combo(self):
        return getattr(getattr(self._widgets, 'scan_frame', None), 'ratio_combo', None)

    def _locked_ratio(self, image_wh):
        """The picked ratio (width/height) in the chosen orientation, or None
        for Free."""
        frame = getattr(self._widgets, 'scan_frame', None)
        combo = self._ratio_combo()
        ratio = parse_ratio(str(combo.currentText()) if combo is not None else '', image_wh)
        return oriented_ratio(ratio, bool(frame is not None and frame.portrait()))

    def _whole_frame_uv(self, image_wh) -> tuple:
        """The whole photo, at the locked aspect when there is one."""
        ratio = self._locked_ratio(image_wh)
        if ratio is None:
            return (0.0, 0.0, 1.0, 1.0)
        return refit_to_ratio((0.0, 0.0, 1.0, 1.0), ratio, image_wh)

    def _is_straighten_crop(self, state, image_wh) -> bool:
        """True when the stored crop is EXACTLY what Done writes for a
        straighten of the whole frame at that angle -- the crop the
        straighten added, which goes when the angle goes back to 0."""
        crop_active, center, size, skew = state
        if not crop_active or abs(skew) < 1e-9:
            return False
        fitted = fit_rect_about_centre(self._whole_frame_uv(image_wh), skew, image_wh)
        fit_center, fit_size = self._stored_crop(fitted, image_wh, skew)
        return all(abs(a - b) < 1e-6 for a, b in zip((*center, *size), (*fit_center, *fit_size)))

    def _read_constrain(self) -> bool:
        value = app_settings().value('crop_constrain', True)
        return str(value).strip().lower() not in ('false', '0', 'no', 'off')

    def _session_fit(self, base) -> tuple:
        skew = float(self._widgets.scan_finishing.skew_deg.value)
        if not self._crop_constrain or abs(skew) < 1e-9:
            return clamp_rect(tuple(base))
        # shrink about the frame's own centre: what the user framed stays put
        return fit_rect_about_centre(base, skew, self._scan_frame_wh())

    def _refit_session_frame(self) -> None:
        if not self.crop_session_active() or self._frame_base_uv is None:
            return
        self._frame_rect_uv = self._session_fit(self._frame_base_uv)
        if self._gpu_canvas is not None:
            self._gpu_canvas.set_crop_frame(self._frame_rect_uv)

    def _schedule_session_render(self) -> None:
        """The turned picture must show under the frame even with auto
        preview off (as a drag of the angle already does), but once per
        burst of changes: an angle typed, stepped or dragged settles first.
        With auto preview on the field's own change already renders."""
        if self._auto_preview_enabled():
            return
        timer = self._crop_session_render
        if timer is None:
            timer = self._crop_session_render = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.setInterval(150)
            timer.timeout.connect(self._session_render_due)
        timer.start()

    def _crop_flat_key(self, state, image_data):
        """Everything a render depends on but the angle: the same key means
        the kept unturned frame is still the film as it now is."""
        import copy

        flat_state = copy.deepcopy(state)
        flat_state.scan_finishing.skew_deg = 0.0
        return (id(image_data), tuple(np.shape(image_data)), flat_state)

    def _crop_turn_fast_path(self, state, params, image_data, source_layer_name,
                             mode_label) -> bool:
        """While cropping, a preview at a new angle is the kept unturned
        frame turned -- ~12 ms on the GPU against a render (the turn is the
        pipeline's last step, applied to the finished scan, and the same
        shader turns it here). True when the frame was shown here; otherwise
        a crop's render is made unturned, to keep."""
        self._crop_flat_pending = None
        if (not self.crop_session_active() or source_layer_name != INPUT_PREVIEW_LAYER_NAME
                or self._live_stock_recipe is not None):
            return False
        key = self._crop_flat_key(state, image_data)
        if self._crop_flat is not None and self._crop_flat[0] == key:
            self._show_turned(self._turned_result(
                self._crop_flat, float(state.scan_finishing.skew_deg), mode_label))
            return True
        params.scanner.finishing.skew_deg = 0.0     # render the film unturned, once
        self._crop_flat_pending = key
        return False

    def _crop_turn_result(self, result):
        """A finished render that was a crop's unturned frame: keep it, and
        show it turned to the angle now set."""
        key, self._crop_flat_pending = self._crop_flat_pending, None
        if key is None or not self.crop_session_active():
            return result
        # float32: the precision the screen path works in, and half the turn's cost
        self._crop_flat = (key, np.asarray(result.float_image, dtype=np.float32),
                           result.output_color_space, result.use_display_transform)
        return self._turned_result(self._crop_flat,
                                   float(self._widgets.scan_finishing.skew_deg.value),
                                   result.mode_label, result.status_message)

    def _show_turned(self, result) -> None:
        """A turned frame onto the canvas: what showing a render does for the
        picture. The scopes (histogram, thumbnails, curve preview) cost more
        than the turn itself, so they follow once the turning stops."""
        self._showing_input = False
        self._output_display_cached = result.display_image
        self._set_or_add_output_layer(
            result.display_image,
            float_image=result.float_image,
            output_color_space=result.output_color_space,
            output_cctf_encoding=True,
            use_display_transform=result.use_display_transform,
        )
        self._show_last_render()
        timer = self._crop_scopes_timer
        if timer is None:
            timer = self._crop_scopes_timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.setInterval(150)
            timer.timeout.connect(self._crop_scopes_due)
        timer.start()

    def _crop_scopes_due(self) -> None:
        display = self._output_display_cached
        if display is not None:
            self._update_analysis(getattr(display, 'small', display))

    def _turned_result(self, flat, angle: float, mode_label: str, status: str = ''):
        from spektrafilm.gpu.scan_geometry import skew_dispatch
        from spektrafilm.utils.crop_resize import apply_skew

        _key, image, output_color_space, use_display_transform = flat
        turned = image
        if abs(angle) > 1e-9:
            turned = skew_dispatch(image, angle)
            if turned is None:
                turned = apply_skew(image, angle)
        display, display_status = self._prepare_output_display_image(
            turned, output_color_space=output_color_space,
            use_display_transform=use_display_transform)
        return runtime.SimulationResult(
            mode_label=mode_label, display_image=display, float_image=turned,
            output_color_space=output_color_space, use_display_transform=use_display_transform,
            status_message=status or display_status)

    def _session_render_due(self) -> None:
        if not self.crop_session_active():
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            self._crop_session_render.start()       # after the render in flight
            return
        self.request_view_refresh()

    def _sync_crop_buttons(self, armed: bool) -> None:
        """The Frame group (Cancel · Done shown, its Crop tool checked) and
        the viewing rail's crop toggle show the session, however it was
        opened."""
        frame = getattr(self._widgets, 'scan_frame', None)
        if frame is not None:
            frame.set_session_open(armed)
        button = getattr(frame, 'crop_button', None)
        if button is not None and button.isCheckable() and button.isChecked() != armed:
            was_blocked = button.blockSignals(True)
            button.setChecked(armed)
            button.blockSignals(was_blocked)
        # The rail's toggle keeps its signals: they tint its icon. What it
        # calls back is harmless here -- arming re-arms the open session's
        # frame, and by the time it goes off the session has.
        crop_button = getattr(getattr(self, '_view_rail', None), 'crop_button', None)
        if crop_button is not None and crop_button.isChecked() != armed:
            crop_button.setChecked(armed)

    def set_crop_constrain(self, on: bool) -> None:
        """Scan > Frame's Constrain to image: an app setting, remembered; an
        open crop refits to it."""
        self._crop_constrain = bool(on)
        app_settings().setValue('crop_constrain', bool(on))
        self._refit_session_frame()

    def _reset_desqueeze(self) -> None:
        """Desqueeze back to its baseline, as the row's own reset arrow does."""
        editor = self._widgets.scan_finishing.desqueeze
        reset = getattr(editor, 'reset_to_baseline', None)
        if callable(reset):
            reset()

    def _session_reset(self) -> None:
        """Reset inside the crop: no angle, the whole photo, Free, landscape,
        no desqueeze -- still only on screen until Done (Cancel puts it all
        back, desqueeze included)."""
        self._frame_base_uv = (0.0, 0.0, 1.0, 1.0)
        self._frame_from_straighten = False
        frame = getattr(self._widgets, 'scan_frame', None)
        if frame is not None:
            frame.set_portrait(False)
        self._reset_desqueeze()                 # first: it sets the photo's shape
        self._widgets.scan_finishing.skew_deg.value = 0.0
        combo = self._ratio_combo()
        if combo is not None:
            combo.setCurrentIndex(0)            # 'Free'; refits through its own signal
        self._refit_session_frame()

    def _end_crop_session(self) -> None:
        self._crop_flat = None                  # the next render is a whole one again
        self._crop_flat_pending = None
        if self._crop_escape is not None and self._gpu_canvas is not None:
            self._gpu_canvas.window().removeEventFilter(self._crop_escape)
        if self._crop_session_render is not None:
            self._crop_session_render.stop()
        self._frame_base_uv = None
        self._frame_from_straighten = False
        self._sync_crop_buttons(False)

    def _announce_crop_session(self, initial, final) -> None:
        """Done's card: what changed, and one Undo for all of it."""
        crop_was, center_was, size_was, skew_was = initial
        crop_on, center, size, skew = final
        turned = abs(skew - skew_was) > 1e-9
        cropped = (crop_on != crop_was) or (crop_on and (
            any(abs(a - b) > 1e-9 for a, b in zip((*center, *size), (*center_was, *size_was)))))
        if not turned and not cropped:
            set_status(self._viewer, 'Crop and angle unchanged')
            return
        if turned and abs(skew) > 1e-9:
            title = f'Straightened {skew:.1f}°'
            if crop_on and self._crop_constrain:
                title += ', cropped to fit'
        elif turned:
            title = 'Straighten removed' + (', crop kept' if crop_on else '')
        else:
            title = 'Crop applied' if crop_on else 'Crop removed'
        card = notices.system_message_sink(dialog_parent(self._viewer))
        if card is None:
            set_status(self._viewer, title)
            return
        card.show_message('pos', title, 'Undo takes back the angle and the crop together.',
                          actions=[('Undo', self.undo)])

    def _stored_crop(self, rect_uv, image_wh, skew: float):
        """(centre, size) Done writes for the frame: the frame itself to the
        fields' precision, nearest -- so a crop re-opened and applied again is
        the same crop -- and moved inward only when that rounding would reach
        past the turned photo. (Always stepping inward shrank the crop on
        every Crop -> Done.)"""
        center, size = rect_to_center_size(rect_uv, image_wh)
        digits = 4
        nearest = (tuple(round(float(v), digits) for v in center),
                   tuple(round(float(v), digits) for v in size))
        kept = crop_rect_uv(nearest[0], nearest[1], image_wh)
        if rect_inside_frame(kept) and rect_fits_rotated_image(kept, skew, image_wh):
            return nearest
        return self._crop_inside_values(rect_uv, image_wh)

    @staticmethod
    def _crop_inside_values(rect_uv, image_wh, step: float = CROP_FIELD_STEP):
        """(centre, size) for crop_center / crop_size such that the crop the
        fields KEEP still lies inside the fitted rect, when rounding to
        nearest would push the cut past it, onto the empty corners. Each size
        is floored and given one step of room, so a centre at the fields'
        precision inside the fitted rect always exists."""
        width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
        long_edge = max(width, height)
        u0, v0, u1, v1 = rect_uv
        center, size = [], []
        for low, high, span in ((u0, u1, width), (v0, v1, height)):
            fitted = (high - low) * span / long_edge                 # long-edge units
            kept = max(step, (math.floor(round(fitted / step, 9)) - 1) * step)
            half = kept * long_edge / span / 2.0                     # back to uv
            lowest = math.ceil(round((low + half) / step, 9)) * step
            highest = math.floor(round((high - half) / step, 9)) * step
            nearest = round(((low + high) / 2.0) / step) * step
            digits = max(0, round(-math.log10(step)))
            center.append(round(min(max(nearest, lowest), max(lowest, highest)), digits))
            size.append(round(kept, digits))
        return tuple(center), tuple(size)

    def _scan_frame_wh(self) -> tuple[float, float]:
        """(width, height) of the frame the scan crop is defined in: the input
        after its own crop, desqueezed. Only the aspect matters to the crop
        maths. NOT the canvas texture: while a scan crop is on, the view shows
        the CROPPED render, and fitting against its size puts the crop in the
        wrong frame."""
        image = getattr(self, '_current_input_image', None)
        if image is None:
            return self._crop_image_wh()
        height, width = (int(v) for v in np.shape(image)[:2])
        input_widgets = self._widgets.input_image
        if bool(input_widgets.crop.value):
            # spektrafilm.utils.crop_resize.crop_image: size is a fraction of
            # the long side, capped at the image.
            long_edge = max(height, width)
            size_x, size_y = (float(v) for v in input_widgets.crop_size.value)
            width = min(int(round(long_edge * size_x)), width)
            height = min(int(round(long_edge * size_y)), height)
        from spektrafilm.utils.crop_resize import crop_geometry_output_shape

        height, width = crop_geometry_output_shape(
            (height, width), float(self._widgets.scan_finishing.desqueeze.value))
        return (max(float(width), 1.0), max(float(height), 1.0))

    def set_crop_tool(self, mode: str) -> None:
        """Arm or disarm the on-canvas crop tool. While armed the camera pan
        is suspended so the drag drives the crop instead. Phase 9 modes:
        'frame' (persistent LR crop frame) and 'line' (straighten); the
        legacy 'draw'/'move' rubber band remains for the napari-era path."""
        if mode in ('frame', 'line') and self._gpu_canvas is not None:
            self._crop_tool_mode = mode
            if mode == 'line':
                self._arm_straighten_line()
            elif self.crop_session_active():
                self._arm_canvas_frame()        # back to the frame, session kept
            else:
                self._arm_crop_frame()
                self._sync_crop_buttons(True)
                frame = getattr(self._widgets, 'scan_frame', None)
                if frame is not None:
                    frame.reveal_requested.emit()   # the crop's controls are there
            return
        if mode == 'off' and self._frame_rect_uv is not None:
            if self._crop_tool_mode == 'line':
                # Straighten toggled off inside a session without a line:
                # back to the frame, nothing applied
                self._crop_tool_mode = 'frame'
                self._arm_canvas_frame()
                self._sync_crop_buttons(True)
                return
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

        image_wh = self._scan_frame_wh()
        ratio = self._locked_ratio(image_wh)
        if ratio is None:
            return None
        width, height = image_wh
        return ratio * height / width

    def _arm_crop_frame(self, initial=None) -> None:
        """Open the session. `initial` is the state Cancel puts back when the
        session opened on an edit that has already landed (a typed angle);
        otherwise it is the state now."""
        finishing_widgets = self._widgets.scan_finishing
        if initial is None:
            self.history_flush()
        current = self._frame_state()
        self._frame_initial = initial if initial is not None else current
        self._frame_initial_desqueeze = float(finishing_widgets.desqueeze.value)
        combo = self._ratio_combo()
        self._frame_initial_ratio = combo.currentText() if combo is not None else None
        crop_active, center, size, _skew = current
        image_wh = self._scan_frame_wh()
        rect = crop_rect_uv(center, size, image_wh) if crop_active else (0.0, 0.0, 1.0, 1.0)
        # the orientation pair shows the photo's own crop shape (and Cancel
        # puts that back); a locked ratio follows it
        frame = getattr(self._widgets, 'scan_frame', None)
        self._frame_initial_portrait = (rect[3] - rect[1]) * image_wh[1] > (rect[2] - rect[0]) * image_wh[0] + 1e-6
        if frame is not None:
            frame.set_portrait(self._frame_initial_portrait)
        # The frame the angle is fitted FROM. A crop only a straighten made
        # goes back to the whole photo as the angle goes back to 0.
        self._frame_from_straighten = self._is_straighten_crop(self._frame_initial, image_wh)
        self._frame_base_uv = self._whole_frame_uv(image_wh) if self._frame_from_straighten else rect
        self._crop_constrain = self._read_constrain()
        self._frame_rect_uv = self._session_fit(self._frame_base_uv)
        if crop_active:
            # LR shows the WHOLE image while cropping: lift the crop and
            # REFRESH THE VIEW EXPLICITLY (with auto preview off the editor
            # signal renders nothing and the frame would sit over the stale
            # cropped view — the 'crop tool not working' report).
            finishing_widgets.crop.value = False
        self._arm_canvas_frame()
        if self._crop_escape is None:
            self._crop_escape = _EscapeCancelsCrop(lambda: self.cancel_crop_session())
        self._gpu_canvas.window().installEventFilter(self._crop_escape)
        self._schedule_session_render()
        # The how-to is the rail button's tooltip: the status line has ~118 px
        # at the smallest window, and an instruction there is cut mid-word.
        set_status(self._viewer, 'Cropping', timeout_ms=0)

    def _arm_canvas_frame(self) -> None:
        self._gpu_canvas.arm_crop_tool(
            'frame',
            on_frame_drag=self._frame_dragged,
            on_frame_release=self._frame_drag_released,
            on_frame_commit=self._frame_commit,
            on_frame_cancel=self.cancel_crop_session,
            on_skew_start=self._skew_drag_started,
            on_skew=self._skew_dragged,
            on_skew_release=self._skew_drag_released,
        )
        self._gpu_canvas.set_crop_frame(self._frame_rect_uv)
        self._follow_skew_on_ruler()

    def _skew_drag_started(self) -> None:
        if self._frame_rect_uv is None:
            return
        self._skew_drag_state = [float(self._widgets.scan_finishing.skew_deg.value)]

    def _skew_dragged(self, delta_deg: float, fine: bool) -> None:
        """Turn the image by the pointer's turn about the picture's centre.
        The field's change refits the frame from the session's base frame, so
        turning back gives the frame back rather than leaving it shrunk."""

        state = self._skew_drag_state
        if state is None or self._frame_rect_uv is None:
            return
        state[0] += float(delta_deg) * (self.SKEW_FINE_FACTOR if fine else 1.0)
        state[0] = max(min(state[0], 45.0), -45.0)
        self._widgets.scan_finishing.skew_deg.value = round(state[0], 1)

    def _skew_drag_released(self) -> None:
        if self._skew_drag_state is None:
            return
        self._skew_drag_state = None
        # the turned picture must show even with auto preview off (the field
        # change alone renders nothing then), as arming the frame does
        self.request_view_refresh()
        set_status(self._viewer, 'Cropping', timeout_ms=0)

    def _frame_dragged(self, hit: str, du: float, dv: float, fine: bool) -> None:
        """A handle (or the frame) held and moved. Tracked from where the
        drag started, as Lightroom's is: only what is held moves, it stops
        where it meets the picture's edge, and it follows the pointer again
        once the pointer comes back."""
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
        # what the user set is what later angles are fitted from
        self._frame_base_uv = rect
        self._frame_from_straighten = False
        self._gpu_canvas.set_crop_frame(rect)

    def _frame_drag_released(self) -> None:
        self._frame_drag_from = None

    def _held_frame(self, hit: str, start, du: float, dv: float):
        """The frame `start` with `hit` dragged by (du, dv), as far as it can
        go inside the picture (the turned picture with Constrain on)."""
        ratio = self._frame_ratio_uv() if hit != 'inside' else None
        skew = float(self._widgets.scan_finishing.skew_deg.value)
        image_wh = self._scan_frame_wh()

        def at(du_, dv_):
            if hit == 'inside':                 # the frame follows the pointer
                u0, v0, u1, v1 = start
                return (u0 + du_, v0 + dv_, u1 + du_, v1 + dv_)
            return resize_rect(start, hit, du_, dv_, ratio_uv=ratio, clamp=False)

        def fits(rect):
            return rect_inside_frame(rect) and (
                not self._crop_constrain or rect_fits_rotated_image(rect, skew, image_wh))

        if not fits(start):                     # (a stored crop from before the angle)
            rect = resize_rect(start, hit, du, dv, ratio_uv=ratio) if hit != 'inside' else start
            return fit_rect_about_centre(rect, skew, image_wh) if self._crop_constrain else rect
        if ratio is not None and len(hit) == 2:
            # a corner on a locked ratio: both axes move together
            t = furthest_drag(lambda t: at(t * du, t * dv), fits)
            return at(t * du, t * dv)
        # each axis on its own, so a corner (or the frame) meeting one edge
        # still slides along it
        tu = furthest_drag(lambda t: at(t * du, 0.0), fits)
        tv = furthest_drag(lambda t: at(tu * du, t * dv), fits)
        return at(tu * du, tv * dv)

    def _frame_commit(self, *, announce: bool = True, refresh: bool = True,
                      message: str | None = None) -> None:
        """Done (the bar, Enter, double-click, the Crop tool toggled off, or
        leaving the photo): apply the angle and the crop as ONE undo step."""
        if self._frame_rect_uv is None:
            return
        u0, v0, u1, v1 = self._frame_rect_uv
        # full frame is judged on the uv spans (the persisted size is
        # long-edge-normalized, so a full-frame short axis is < 1.0)
        full_frame = (u1 - u0) >= 0.999 and (v1 - v0) >= 0.999
        image_wh = self._scan_frame_wh()
        finishing_widgets = self._widgets.scan_finishing
        skew = float(finishing_widgets.skew_deg.value)
        if full_frame:
            pass                                # crop off: its fields are inert, leave them
        elif self._crop_constrain and abs(skew) > 1e-9:
            # what the fields keep must stay inside the turned photo
            center, size = self._stored_crop(self._frame_rect_uv, image_wh, skew)
        else:
            center, size = rect_to_center_size(self._frame_rect_uv, image_wh)
            center = (min(max(center[0], 0.0), 1.0), min(max(center[1], 0.0), 1.0))
            size = (min(max(size[0], MIN_CROP_SIZE), 1.0), min(max(size[1], MIN_CROP_SIZE), 1.0))
        if not full_frame:
            finishing_widgets.crop_center.value = center
            finishing_widgets.crop_size.value = size
        finishing_widgets.crop.value = not full_frame
        initial = self._frame_initial
        self._frame_rect_uv = None
        self._frame_initial = None
        self._frame_initial_ratio = None
        self._frame_initial_desqueeze = None
        self._end_crop_session()
        self._finish_crop_tool(
            message or ('Scan crop applied' if not full_frame else 'Full frame - crop off'),
            refresh=refresh)
        self.history_flush()                    # the session's one undo step
        if initial is not None and announce:
            self._announce_crop_session(initial, self._frame_state())

    def _frame_cancel(self, reason: str = '', *, refresh: bool = True) -> None:
        """Cancel (Esc, undo, a turn of the photo): the photo exactly as
        before the session -- angle, crop, ratio, orientation, desqueeze."""
        initial = self._frame_initial
        ratio = self._frame_initial_ratio
        desqueeze = self._frame_initial_desqueeze
        # out of the session FIRST: what goes back below is not a session
        # change (it would refit and render the half-restored state)
        self._frame_rect_uv = None
        self._frame_initial = None
        self._frame_initial_ratio = None
        self._frame_initial_desqueeze = None
        frame = getattr(self._widgets, 'scan_frame', None)
        if frame is not None:
            frame.set_portrait(self._frame_initial_portrait)
        finishing = self._widgets.scan_finishing
        if desqueeze is not None and abs(float(finishing.desqueeze.value) - desqueeze) > 1e-9:
            finishing.desqueeze.value = desqueeze
        if initial is not None:
            crop_active, center, size, skew = initial
            finishing_widgets = self._widgets.scan_finishing
            finishing_widgets.crop_center.value = center
            finishing_widgets.crop_size.value = size
            with self._angle_put_back() as editor:
                editor.value = skew                 # a turn made in the tool too
            finishing_widgets.crop.value = crop_active
        if ratio is not None and frame is not None:
            frame.show_ratio(ratio)
        self._end_crop_session()
        self._finish_crop_tool(reason or 'Crop cancelled', refresh=refresh)

    def refit_crop_to_ratio(self) -> None:
        """A ratio picked in Scan > Frame: the frame refits to it (centred,
        as large as fits). A pick with no crop open opens one, so the pick
        shows and Cancel puts the old ratio back."""

        if self._frame_rect_uv is None:
            if not self._crop_session_allowed():
                return
            self._open_crop_session()
            frame = getattr(self._widgets, 'scan_frame', None)
            if frame is not None:
                self._frame_initial_ratio = frame.previous_ratio
        image_wh = self._scan_frame_wh()
        self._frame_base_uv = refit_to_ratio(self._frame_base_uv or self._frame_rect_uv,
                                             self._locked_ratio(image_wh), image_wh)
        self._frame_from_straighten = False
        self._refit_session_frame()

    def _orientation_chosen(self, portrait: bool) -> None:
        """Scan > Frame's landscape / portrait pair asked for a turn; opens
        the crop when it is not open."""
        if self._frame_rect_uv is None:
            if not self._crop_session_allowed():
                return
            self._open_crop_session()           # the pair now shows the frame's shape
        frame = getattr(self._widgets, 'scan_frame', None)
        if frame is not None and frame.portrait() == bool(portrait):
            return                              # the frame is that way already
        self.swap_crop_orientation()

    def swap_crop_orientation(self) -> None:
        """Landscape <-> portrait, LR-style: the frame's pixel dimensions
        swap (shrinking only when the swapped shape no longer fits) and a
        locked ratio turns with it (3:2 becomes 2:3). Opens the crop when it
        is not open."""

        if self._frame_rect_uv is None:
            if not self._crop_session_allowed():
                return
            self._open_crop_session()
        frame = getattr(self._widgets, 'scan_frame', None)
        image_wh = self._scan_frame_wh()
        self._frame_base_uv = swap_rect_orientation(
            self._frame_base_uv or self._frame_rect_uv, image_wh)
        self._frame_from_straighten = False
        if frame is not None:
            u0, v0, u1, v1 = self._frame_base_uv
            frame.set_portrait((v1 - v0) * image_wh[1] > (u1 - u0) * image_wh[0] + 1e-6)
        self._refit_session_frame()

    def reset_crop(self) -> None:
        """Scan > Frame's Reset (and the group's header reset): the photo as
        it was shot -- no crop, no angle, ratio Free, landscape, no
        desqueeze: everything in the group -- as one undo step. Inside the
        crop it resets the crop, still only on screen until Done."""
        if self.crop_session_active():
            self._session_reset()               # one Reset, whichever button
            return
        self.history_flush()                    # what came before stays its own step
        section = getattr(self._widgets, 'scan_frame', None)
        if section is not None:
            section.show_ratio('Free')
            section.set_portrait(False)
        self._reset_desqueeze()
        finishing_widgets = self._widgets.scan_finishing
        finishing_widgets.crop.value = False
        finishing_widgets.crop_center.value = (0.5, 0.5)
        finishing_widgets.crop_size.value = (1.0, 1.0)
        with self._angle_put_back() as editor:
            editor.value = 0.0
        self.history_flush()
        set_status(self._viewer, 'Frame reset')
        self.request_view_refresh()

    # -- Phase 9: straighten line --------------------------------------------
    def _arm_straighten_line(self) -> None:
        self._gpu_canvas.arm_crop_tool('line', on_line=self._straighten_line_drawn)
        set_status(self._viewer, 'Straighten', timeout_ms=0)

    def _straighten_line_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        angle = angle_from_line(x0, y0, x1, y1)
        finishing_widgets = self._widgets.scan_finishing
        current = float(finishing_widgets.skew_deg.value)
        new_skew = max(min(current + angle, 45.0), -45.0)
        # The angle goes IN the crop session, so the frame refits to keep the
        # corners out and nothing applies until Done. (It used to set the
        # angle alone -- d-controller-258 -- and with no crop on, the photo
        # kept four smeared corners.)
        self._widgets.scan_frame.release_tools()     # the line has done its job
        if self.crop_session_active():
            self._crop_tool_mode = 'frame'
            self._arm_canvas_frame()                 # back to the frame's handles
        else:
            self._open_crop_session()       # from the photo as it is: Cancel restores it
        finishing_widgets.skew_deg.value = round(new_skew, 1)    # the session refits
        self._sync_crop_buttons(True)
        set_status(self._viewer, 'Cropping', timeout_ms=0)

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

    def _finish_crop_tool(self, message: str, *, refresh: bool = True) -> None:
        self._widgets.scan_frame.release_tools()
        self.set_crop_tool('off')
        set_status(self._viewer, message)
        # Crop/straighten results are VIEW changes: render them even when
        # auto preview is off (same rule as the scan-film toggle) -- unless a
        # load that renders anyway follows (a frame switch).
        if refresh:
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
        self.cancel_crop_session('Crop cancelled (photo mirrored)',
                                 refresh=False)
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
