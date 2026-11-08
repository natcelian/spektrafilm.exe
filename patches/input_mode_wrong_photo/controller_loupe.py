"""The loupe part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes LoupeMixin as a base.
"""
from __future__ import annotations

import numpy as np
from qtpy import QtCore

from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui.controller_layers import LOUPE_LAYER_NAME
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import collect_gui_state
import json as _json
from spektrafilm_gui.loupe import (
    LOUPE_MAX_PX,
    LOUPE_MIN_PX,
    LOUPE_TILE_PX,
    RING_COARSE_RGB,
    RING_FINE_RGB,
    circular_loupe_rgba,
    coarse_loupe_view,
    drop_placed_lamp,
    frozen_autoexposure_ev,
    input_pixel_size_um,
    loupe_halo_px,
    loupe_tile,
)

QTimer = getattr(QtCore, 'QTimer')


class LoupeMixin:
    """GuiController's loupe methods."""

    def _loupe_unavailable_reason(self, state) -> str | None:
        if self._current_input_image is None:
            return 'Load an image before using the loupe'
        if self._current_preview_image is None:
            return 'Render a preview before using the loupe'
        if state.input_image.crop:
            return 'Camera crop active: loupe unavailable (v1)'
        # 2026-09-18; see design/DECISIONS.md#d-controller-280
        return None

    def _loupe_frozen_ev(self, state, params) -> float | None:
        """The full-image autoexposure EV (pipeline's own small-preview +
        meter), cached per input/WB/metering signature — the loupe must
        never meter the patch."""

        if not state.simulation.auto_exposure:
            return None
        held = getattr(params.camera, 'auto_exposure_ev_override', None)
        if held is not None:
            return float(held)     # a held reading IS the frozen EV

        # 2026-09-29; see design/DECISIONS.md#d-controller-281
        key = (
            id(self._current_input_image),
            self._preview_wb_signature,
            state.simulation.auto_exposure_method,
            state.input_image.input_color_space,
            state.input_image.apply_cctf_decoding,
            _json.dumps(self._current_look_dict(), sort_keys=True),
        )
        cached = self._loupe_frozen_ev_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        # Downscale first (AA small preview of the full base), then balance:
        # white balance is per-pixel, so it commutes with the downscale, and
        # this order never materializes a full-res balanced copy.
        from spektrafilm.runtime.services.resize import ResizingService

        # The AA downscale can dispatch to the GPU: take the shared render
        # lock so it never races a worker render on the wgpu device.
        with runtime.PIPELINE_RENDER_LOCK:
            small = ResizingService(params.io, state.simulation.film_format_mm).small_preview(
                np.asarray(self._current_input_image))
        small = self._white_balanced_input(np.ascontiguousarray(small), state=state)
        with runtime.PIPELINE_RENDER_LOCK:
            ev = frozen_autoexposure_ev(small, params)
        self._loupe_frozen_ev_cache = (key, ev)
        return ev

    def _prepare_loupe_job(self, center_uv, tile_px: int | None = None):
        """GUI-thread half of a loupe render: guards, tile geometry, the
        balanced padded slice and the frozen references. Cheap (slicing +
        cached EV); the heavy pipeline half runs wherever the caller
        wants. None (with a status reason) when the loupe is unavailable."""

        state = collect_gui_state(widgets=self._widgets)
        reason = self._loupe_unavailable_reason(state)
        if reason is not None:
            set_status(self._viewer, reason)
            return None
        base = np.asarray(self._current_input_image)
        params = build_params_from_state(state)
        # v1.1.1 fix: the loupe's 'grain as scanned' claim requires the same
        # per-photo seed the scan renders with (it bypasses
        # _configure_simulation_params, so stamp it here).
        self._stamp_auto_grain_seed(params)
        # 2026-09-24; see design/DECISIONS.md#d-controller-282
        lamp_placed = drop_placed_lamp(params)
        # H-C1: the tile boosts against the WHOLE frame's max. Before the
        # crop is switched off below, so the probe honours the real framing.
        params.settings.boost_frame_probe = self._boost_frame_probe(params, state=state)
        # The tile is already the framing: never re-crop or re-scale it.
        params.io.crop = False
        params.io.upscale_factor = 1.0
        pitch = input_pixel_size_um(base.shape, state.simulation.film_format_mm)
        halo = loupe_halo_px(params, pitch)
        tile = loupe_tile(base.shape, center_uv, tile_px or LOUPE_TILE_PX, halo)
        if tile is None:
            set_status(self._viewer, 'Image is smaller than the loupe tile')
            return None
        ev = self._loupe_frozen_ev(state, params)
        # 17B/G5: tell the pipeline where this tile sits in the full frame.
        # The GPU grain sampler keys its per-pixel streams on absolute
        # position, so the tile's grain then matches EXACTLY what a
        # full-resolution render puts there — the 'grain as scanned' claim.
        # The padded slice starts at pad_x0/pad_y0, which is what the
        # kernel sees as pixel (0, 0).
        params.settings.render_origin = (int(tile.pad_x0), int(tile.pad_y0))
        padded = self._white_balanced_input(
            np.ascontiguousarray(base[tile.padded]), state=state)

        return {
            'padded': padded,
            'tile': tile,
            'params': params,
            # Simulator cache key: any state change invalidates; hovers at
            # the same look reuse one digested simulator (a fresh digest per
            # tile made every fine render seconds late — NAT saw only the
            # pixelated coarse view).
            # The origin is NOT part of the key: it is re-aimed on the
            # cached simulator (set_render_origin), so hovering never pays
            # a re-digest — that cost is what made the loupe feel slow.
            # The boost probe (H-C1) is per PHOTO and per white balance, not
            # per look, so it joins the key: a cached simulator must never
            # carry another photo's frame max.
            'sim_key': (_json.dumps(self._current_look_dict(), sort_keys=True)
                        + f'|probe:{hash(params.settings.boost_frame_probe)}'),
            'origin': (int(tile.pad_x0), int(tile.pad_y0)),
            # The print veil is the SECOND random field in the chain, and it
            # has to follow the frame too or the tile is only representative.
            # When it is off there is nothing to key, and the claim rests on
            # grain alone.
            'veil_active': bool(getattr(params.print_render.glare, 'active', False)
                                and float(getattr(params.print_render.glare,
                                                  'percent', 0.0)) > 0),
            # The frame's veil follows its lamp and this tile's does not, so
            # a placed lamp alone rules out 'as scanned'.
            'lamp_placed': lamp_placed,
            'pixel_size_um': pitch,
            'autoexposure_ev': ev,
            'output_color_space': state.simulation.output_color_space,
            'use_display_transform': state.display.use_display_transform,
        }

    def _render_loupe_tile(self, center_uv, tile_px: int | None = None):
        """Synchronous loupe render (the gates pin this path; the
        interactive mode runs the same halves on a TaskWorker). Returns
        {'image', 'tile', 'pixel_size_um'} or None. Uses its own
        simulator: the 16C stage cache and preview state are untouched."""
        job = self._prepare_loupe_job(center_uv, tile_px)
        if job is None:
            return None
        image = self._run_loupe_job(job)
        return {'image': image, 'tile': job['tile'],
                'pixel_size_um': job['pixel_size_um']}

    def toggle_loupe(self) -> None:
        """Loupe button: toggle the 1:1 window (Esc also dismisses)."""
        if self._loupe_active:
            self._deactivate_loupe('Loupe off')
            return
        if not self._loaded_frame_is_current():
            # The loupe magnifies a render of THIS frame, and there is none
            # yet: open the frame, which renders it.
            if self._load_selected_frame():
                set_status(self._viewer, 'Opened the frame; use the loupe once it has rendered')
            return
        state = collect_gui_state(widgets=self._widgets)
        reason = self._loupe_unavailable_reason(state)
        if reason is not None:
            set_status(self._viewer, reason)
            return
        if self._output_layer() is None:
            set_status(self._viewer, 'Render a preview before using the loupe')
            return
        self._loupe_active = True
        # Canvas chrome (Phase 6C: the canvas IS the display): hover + circle
        # live on the canvas overlay. Falls back to the napari callbacks when
        # no interactive canvas exists (headless/tests).
        canvas = self._gpu_canvas
        self._loupe_canvas_mode = False
        if canvas is not None and callable(getattr(canvas, 'arm_loupe', None)):
            canvas.arm_loupe(on_move=self._on_loupe_canvas_move,
                             on_lock=self._on_loupe_lock_changed,
                             on_resize=self._on_loupe_resize)
            self._loupe_canvas_mode = bool(getattr(canvas, 'loupe_armed', lambda: True)())
        if not self._loupe_canvas_mode:
            callbacks = getattr(self._viewer, 'mouse_move_callbacks', None)
            if callbacks is not None and self._on_loupe_mouse_move not in callbacks:
                callbacks.append(self._on_loupe_mouse_move)
        try:
            self._viewer.bind_key('Escape', self._on_loupe_escape, overwrite=True)
        except (AttributeError, TypeError):
            pass
        if self._loupe_rest_timer is None:
            self._loupe_rest_timer = QTimer()
            self._loupe_rest_timer.setSingleShot(True)
            self._loupe_rest_timer.setInterval(self._LOUPE_REST_MS)
            self._loupe_rest_timer.timeout.connect(self._on_loupe_rest)
        self._show_loupe_coarse()
        self._loupe_rest_timer.start()
        set_status(self._viewer,
                   'Loupe: move to inspect (coarse while moving, full quality at '
                   'rest). Esc or the Loupe button dismisses it.', timeout_ms=0)

    def _on_loupe_escape(self, _viewer=None) -> None:
        if self._loupe_active:
            self._deactivate_loupe('Loupe off')

    def _deactivate_loupe(self, message: str | None) -> None:
        self._loupe_active = False
        self._loupe_serial += 1          # invalidates any in-flight render
        if self._loupe_rest_timer is not None:
            self._loupe_rest_timer.stop()
        canvas = self._gpu_canvas
        if canvas is not None and callable(getattr(canvas, 'arm_loupe', None)):
            canvas.arm_loupe(None)
        callbacks = getattr(self._viewer, 'mouse_move_callbacks', None)
        if callbacks is not None and self._on_loupe_mouse_move in callbacks:
            callbacks.remove(self._on_loupe_mouse_move)
        try:
            self._viewer.bind_key('Escape', None, overwrite=True)
        except (AttributeError, TypeError):
            pass
        self._layers.hide_layer(LOUPE_LAYER_NAME)
        if message is not None:
            set_status(self._viewer, message)

    def _loupe_world_geometry(self):
        """(world_h, world_w) of the output layer's centered frame, plus
        the preview long edge in pixels — the loupe's placement basis."""
        output_layer = self._output_layer()
        if output_layer is None:
            return None
        shape = np.asarray(output_layer.data).shape
        long_edge = max(int(shape[0]), int(shape[1]), 1)
        return (shape[0] / long_edge, shape[1] / long_edge, long_edge)

    def _on_loupe_mouse_move(self, viewer, event) -> None:
        if not self._loupe_active:
            return
        geometry = self._loupe_world_geometry()
        if geometry is None:
            return
        world_h, world_w, _long = geometry
        position = getattr(event, 'position', None)
        if position is None or len(position) < 2:
            return
        y, x = float(position[-2]), float(position[-1])
        u = min(max(x / world_w + 0.5, 0.0), 1.0)
        v = min(max(y / world_h + 0.5, 0.0), 1.0)
        self._loupe_moved(u, v)

    def _on_loupe_canvas_move(self, u: float, v: float) -> None:
        if self._loupe_active:
            self._loupe_moved(min(max(float(u), 0.0), 1.0),
                              min(max(float(v), 0.0), 1.0))

    def _on_loupe_lock_changed(self, u, v) -> None:
        """Click pinned the loupe (u, v) or released it (None, None) —
        NAT 2026-07-29. The pinned loupe keeps its live loop: parameter
        changes refresh the tile at the pinned zone."""
        if not self._loupe_active:
            return
        if u is None:
            set_status(self._viewer, 'Loupe released: following the cursor')
            return
        self._loupe_moved(min(max(float(u), 0.0), 1.0),
                          min(max(float(v), 0.0), 1.0))
        set_status(self._viewer,
                   'Loupe locked. Click the circle to release it.', timeout_ms=0)

    def _on_loupe_resize(self, steps: float) -> None:
        """Ctrl+scroll on the loupe (proposal 17B DECIDED): diameter
        180-420 logical px in 20 px steps; re-renders at the new size."""

        if not self._loupe_active:
            return
        current = self._loupe_size_px or LOUPE_TILE_PX
        self._loupe_size_px = int(
            min(max(current + 20 * steps, LOUPE_MIN_PX), LOUPE_MAX_PX))
        self._loupe_serial += 1
        self._show_loupe_coarse()
        self._loupe_rest_timer.start()

    def _loupe_tile_px(self) -> int:
        """Physical-pixel tile size: the circle paints 1:1 DEVICE pixels
        (a logical-px tile gets upscaled by Qt on scaled displays and
        reads as mush — NAT 2026-07-29)."""

        base = self._loupe_size_px or LOUPE_TILE_PX
        canvas = self._gpu_canvas
        ratio = 1.0
        if canvas is not None:
            getter = getattr(canvas, 'devicePixelRatioF', None)
            if callable(getter):
                try:
                    ratio = float(getter())
                except (TypeError, ValueError):
                    ratio = 1.0
        return max(int(round(base * max(ratio, 1.0))), base)

    def _loupe_moved(self, u: float, v: float) -> None:
        # Sub-pixel hover jitter must not drop a fine render mid-flight:
        # ignore moves below ~a tenth of a permille of the image.
        last = self._loupe_center_uv
        if abs(u - last[0]) < 1e-4 and abs(v - last[1]) < 1e-4:
            return
        self._loupe_center_uv = (u, v)
        self._loupe_serial += 1
        self._show_loupe_coarse()
        self._loupe_rest_timer.start()   # restart the rest countdown

    def _set_loupe_circle(self, tile_rgb, ring_rgb) -> None:
        if self._loupe_canvas_mode and self._gpu_canvas is not None:
            # Canvas chrome: the circle is QPainter-drawn on the overlay
            # (no wgpu device involvement — see the render-lock story). The
            # glass is naked (spec D1); the overlay draws the accent outline
            # outside it while the coarse, dragging view is up.
            self._gpu_canvas.set_loupe_view(circular_loupe_rgba(tile_rgb, None),
                                            dragging=ring_rgb is not None)
            return
        rgba = circular_loupe_rgba(tile_rgb, ring_rgb)
        geometry = self._loupe_world_geometry()
        if geometry is None:
            return
        world_h, world_w, preview_long = geometry
        u, v = self._loupe_center_uv
        self._layers.set_or_add_loupe_layer(
            rgba,
            world_long_edge=LOUPE_TILE_PX / float(preview_long),
            center_world=((v - 0.5) * world_h, (u - 0.5) * world_w),
        )

    def _loupe_display_buffer(self):
        """The buffer the user is LOOKING at: the mirrored display cache on
        the canvas chrome, the output layer data otherwise."""
        if self._output_display_cached is not None:
            return np.asarray(self._output_display_cached)
        output_layer = self._output_layer()
        return np.asarray(output_layer.data) if output_layer is not None else None

    def _show_loupe_coarse(self) -> None:
        """WHILE MOVING: magnified crop of the current output buffer —
        zero pipeline work, accent ring marks the coarse state."""

        display = self._loupe_display_buffer()
        if display is None or self._current_input_image is None:
            return
        full_long = max(np.asarray(self._current_input_image).shape[:2])
        coarse = coarse_loupe_view(
            display, self._loupe_center_uv,
            tile_px=self._loupe_tile_px(), full_long_edge=int(full_long))
        self._set_loupe_circle(coarse, RING_COARSE_RGB)

    def _retire_loupe_worker(self) -> None:
        """Park the finished worker instead of freeing it: its thread may
        still be unwinding inside emit() when this queued handler runs."""
        self._loupe_worker_retired = self._loupe_worker
        self._loupe_worker = None

    def _on_loupe_fine_failed(self, message: str) -> None:
        self._loupe_render_busy = False
        self._retire_loupe_worker()
        set_status(self._viewer, f'Loupe render failed: {message}')

    def _on_loupe_fine_ready(self, serial: int, display) -> None:
        self._loupe_render_busy = False
        self._retire_loupe_worker()
        if not self._loupe_active:
            return
        if serial != self._loupe_serial:
            # Stale: the cursor moved while rendering — drop and re-arm.
            self._loupe_rest_timer.start()
            return
        self._set_loupe_circle(display, RING_FINE_RGB)
        # 17B: the caption difference IS the spec — claim 'as scanned' only
        # when the position-stable GPU grain path ran at this tile's origin.
        set_status(self._viewer,
                   'Loupe: 1:1 - grain as scanned'
                   if self._loupe_grain_as_scanned
                   else 'Loupe: 1:1 - representative grain')
