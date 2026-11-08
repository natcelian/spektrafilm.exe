"""The compare part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes CompareMixin as a base.
"""
from __future__ import annotations

import numpy as np

from spektrafilm_gui import controller_profile_sync as profile_sync
from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui.controller_layers import INPUT_PREVIEW_LAYER_NAME
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.shell_layout import dialog_parent, show_fullscreen_image
from spektrafilm_gui.shell_layout import reset_viewer_camera
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state import build_default_gui_state
from spektrafilm_gui.state import digest_after_selection
from spektrafilm_gui.state import gui_state_from_params
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm_gui.topbar import refresh_icon_tint

PROFILE_SYNC_FIELDS = profile_sync.PROFILE_SYNC_FIELDS
SimulationRequest = runtime.SimulationRequest
SimulationResult = runtime.SimulationResult


class CompareMixin:
    """GuiController's compare methods."""

    def toggle_reference_frame(self) -> None:
        """Flag/unflag the current frame as the roll's reference.

        The darkroom model NAT asked for: you make a print you like and pin
        it up, then judge later prints against THAT print. Flagging captures
        what is on screen right now -- it does not re-develop anything.
        """
        roll_file = self._roll.current
        if roll_file is None:
            set_status(self._viewer, 'Load an image before setting a reference')
            return
        if self._roll.toggle_reference(roll_file):
            self._capture_reference()
            if self._reference_image is None:
                set_status(self._viewer, (
                    f'{roll_file.name} is the reference. Render it once to '
                    'capture the print to compare against.'))
            else:
                set_status(self._viewer, f'Reference set: {roll_file.name}')
        else:
            self._reference_image = None
            set_status(self._viewer, f'Reference cleared ({roll_file.name})')
        self._roll_refresh_view()
        self.sync_reference_state()

    def _capture_reference(self) -> None:
        """Pin the current render, if the current frame is the reference.

        Called on every finished render too, so a roll loaded from disk
        re-acquires its reference image the first time that frame is shown --
        the flag survives the save, the pixels do not.
        """
        roll_file = self._roll.current
        if roll_file is None or not self._roll.is_reference(roll_file):
            return
        if not self._loaded_frame_is_current():
            # The render on hand is another frame's: drop the old pin rather
            # than show it as this one; the frame's next render pins it.
            self._reference_image = None
            return
        image = self._output_display_cached
        if image is None:
            return
        self._reference_image = np.clip(
            np.asarray(image)[..., :3], 0.0, 1.0).astype(np.float32)

    def _kept_key(self):
        target = self._versions_target()
        if target is None:
            return None
        return self._roll._attempt_key(target)

    def _capture_kept(self) -> None:
        """Pin the current print as this image's last kept version."""
        key = self._kept_key()
        image = self._output_display_cached
        if key is None or image is None or not self._loaded_frame_is_current():
            return
        if self._kept_images is None:
            self._kept_images = {}
        self._kept_images.pop(key, None)            # newest last
        self._kept_images[key] = np.clip(
            np.asarray(image)[..., :3], 0.0, 1.0).astype(np.float32)
        while len(self._kept_images) > self._KEPT_IMAGES_MAX:
            self._kept_images.pop(next(iter(self._kept_images)))

    def _kept_image(self):
        key = self._kept_key()
        return None if key is None else (self._kept_images or {}).get(key)

    def _sync_kept_state(self) -> None:
        """Tell Split whether this image has a kept print, and keep a Split
        that is showing one honest: after a Keep it shows the new print, and
        on a frame with none it falls back to the input."""
        kept = self._kept_image()
        segment = getattr(self, '_view_segment', None)
        if segment is not None:
            segment.set_kept_available(kept is not None)
        last = self._last_compare
        if last is None or last[1] != 'kept' or last[0] is kept:
            return
        if kept is not None:
            self._push_compare_image(kept, 'kept', 'last kept version')
            return
        # back to the input, on the same view (the negative stays on the
        # left if that is where it was)
        fallback = 'negative:input' if self._negative_left_mode is not None else 'input'
        if segment is not None:
            segment.set_mode(fallback)
        self.set_compare_mode(fallback)

    def has_reference(self) -> bool:
        return self._roll.reference_file() is not None

    def sync_reference_state(self) -> None:
        """Push the reference state out to the top bar: the flag's checked
        state, and whether Split may offer 'reference' as a source."""
        self._sync_reference_button()
        segment = getattr(self, '_view_segment', None)
        if segment is not None:
            segment.set_reference_available(self.has_reference())

    def _sync_reference_button(self) -> None:
        """Reflect the flag on the top bar without emitting from it."""
        button = getattr(self, '_reference_button', None)
        if button is None:
            return
        roll_file = self._roll.current
        active = roll_file is not None and self._roll.is_reference(roll_file)
        was_blocked = button.blockSignals(True)
        try:
            button.setChecked(active)
        finally:
            button.blockSignals(was_blocked)

        refresh_icon_tint(button, active=active)

    def _split_is_requested(self) -> bool:
        """Did this compare come from the SPLIT button or the NEGATIVE one?

        The segment emits the same 'negative' for both, because the source
        is what the controller needs to know and the framing is not. It is
        the framing here: the page's Negative view is the negative ALONE
        (`applyView` hides the print and floods the whole mount), while
        Split is one frame with a seam. Defaults to split, which is what
        every caller that is not the segment means.
        """
        segment = getattr(self, '_view_segment', None)
        is_split = getattr(segment, 'is_split', None)
        return True if not callable(is_split) else bool(is_split())

    def _set_stage_paper(self, base=None, right=None) -> None:
        """Dress the paper mount for the view. A print sits on paper; a
        NEGATIVE sits on the unexposed base of its own film."""
        stage = getattr(self, '_stage_area', None)
        set_paper = getattr(stage, 'set_paper', None)
        if callable(set_paper):
            try:
                set_paper(base, right)
            except RuntimeError:
                pass

    def _blank_film_hex(self) -> str:
        r, g, b = self._blank_film_rgb()
        return '#%02X%02X%02X' % tuple(
            int(round(max(0.0, min(1.0, v)) * 255)) for v in (r, g, b))

    def set_compare_mode(self, mode: str) -> None:
        mode = (mode or 'off').lower()
        if mode.startswith('negative:'):
            self._start_negative_compare(mode[len('negative:'):])
            return
        self._leave_negative_left()
        if mode not in ('input', 'negative', 'reference', 'kept'):
            self._remove_compare_layer()
            self._last_compare = None
            self._set_stage_paper()
            if self._gpu_canvas is not None:
                # remember where the seam was, so re-entering Split resumes
                position = self._gpu_canvas.split_position()
                if position is not None and 0.0 < position < 1.0:
                    self._split_position = position
                self._gpu_canvas.set_split(None)
                self._gpu_canvas.clear_compare()
            if mode != 'off':
                set_status(self._viewer, 'Compare off')
            return
        if mode == 'reference':
            reference = self._roll.reference_file()
            if reference is None:
                set_status(self._viewer, (
                    'No reference frame: flag one first to compare against it'))
                return
            if self._reference_image is None:
                set_status(self._viewer, (
                    f'Open {reference.name} and render it once to capture '
                    'the reference print'))
                return
            self._push_compare_image(
                self._reference_image, 'reference', f'reference ({reference.name})')
            return
        if mode == 'kept':
            kept = self._kept_image()
            if kept is None:
                set_status(self._viewer, (
                    'No kept version of this image yet -- Keep one to compare '
                    'against it'))
                return
            self._push_compare_image(kept, 'kept', 'last kept version')
            return
        preview_layer = self._layers.preview_input_layer()
        if preview_layer is None:
            set_status(self._viewer, 'Load an image before comparing')
            return
        if mode == 'input':
            image = np.asarray(preview_layer.data)[..., :3]
            compare_display = np.clip(image, 0.0, 1.0).astype(np.float32)
            self._push_compare_image(compare_display, 'input', 'base input')
            return
        self._compare_wants_split = self._split_is_requested()
        self._start_compare_negative_render()

    def _push_compare_image(self, image: np.ndarray, mode: str, label: str) -> None:
        compare_display = np.asarray(image)
        self._set_compare_layer(compare_display)
        self._last_compare = (compare_display, mode)
        split = self._compare_wants_split or mode != 'negative'
        # 2026-08-05; see design/DECISIONS.md#d-controller-262
        surround = {'negative': self._blank_film_hex(),
                    'input': self._INPUT_SURROUND}.get(mode)
        if surround is None:                 # a kept version is a print too
            self._set_stage_paper()
        elif split:
            self._set_stage_paper(right=surround)
        else:
            self._set_stage_paper(base=surround)
        if self._gpu_canvas is not None:
            try:
                self._gpu_canvas.set_compare(self._padded_compare(compare_display, mode))
                # 2026-07-31; see design/DECISIONS.md#d-controller-263
                self._gpu_canvas.set_split(self._split_position if split else 0.0)
            except Exception:
                pass
        set_status(self._viewer,
                   f'Comparing with the {label}. Drag the seam.')

    def _build_compare_negative_request(self) -> SimulationRequest | None:
        """The compare-negative request, built EXACTLY like the scan-film
        view's: the LINEAR preview input (not the display-encoded layer), the
        same params builder + preview configuration, only io.scan_film forced.
        Executed by the same worker/executor as the main render — same
        exposure handling, same display preparation — so the panel colour
        matches the main output by construction. (Two earlier attempts that
        rendered with a bare Simulator on the encoded layer produced a colour
        shift and, with the display transform added, a WHITE panel.)"""
        source = self._simulation_input_image(source_layer_name=INPUT_PREVIEW_LAYER_NAME)
        if source is None:
            return None
        state = collect_gui_state(widgets=self._widgets)
        params = self._configure_simulation_params(
            build_params_from_state(state),
            source_layer_name=INPUT_PREVIEW_LAYER_NAME,
        )
        params.workflow.route = "input > film > scan"
        return SimulationRequest(
            mode_label='Negative compare',
            image=np.double(source),
            params=params,
            output_color_space=state.simulation.output_color_space,
            use_display_transform=state.display.use_display_transform,
        )

    def _start_compare_negative_render(self) -> None:
        """Render the compare-negative through the SAME async worker path as
        the main preview (shared simulator cache mutation is safe here for the
        same reason it is between preview/scan renders: every render re-digests
        fresh params), routing the finished display image to the compare quad
        instead of the output layer."""
        if self._active_simulation_worker is not None:
            set_status(self._viewer, 'Simulation already running; retry the compare after it finishes')
            return
        if self._active_export_worker is not None:
            set_status(self._viewer, 'Export running; wait before comparing.')
            return
        if not self._load_selected_frame():
            return
        request = self._build_compare_negative_request()
        if request is None:
            set_status(self._viewer, 'Load an image before comparing')
            return
        worker = runtime.SimulationWorker(request, execute_request=self._execute_simulation_request)
        worker.signals.finished.connect(self._on_compare_render_finished)
        worker.signals.failed.connect(self._on_compare_render_failed)
        self._active_simulation_worker = worker
        self._active_simulation_label = 'Negative compare'
        self._active_simulation_reports_status = False
        self._set_simulation_controls_enabled(False)
        set_status(self._viewer, 'Rendering negative compare...', timeout_ms=0)
        self._thread_pool.start(worker)

    def _on_compare_render_finished(self, result: SimulationResult) -> None:
        self._active_simulation_worker = None
        self._active_simulation_label = None
        self._active_simulation_reports_status = True
        self._set_simulation_controls_enabled(True)
        target = self._negative_left_pending
        self._negative_left_pending = None
        if target is not None:
            self._show_negative_left(result.display_image, target)
        else:
            self._push_compare_image(result.display_image, 'negative', 'negative')
        self._replay_pending_auto_preview()

    def _on_compare_render_failed(self, message: str) -> None:
        self._negative_left_pending = None
        self._active_simulation_worker = None
        self._active_simulation_label = None
        self._active_simulation_reports_status = True
        self._set_simulation_controls_enabled(True)
        set_status(self._viewer, f'Compare render failed: {message}')
        self._replay_pending_auto_preview()

    def _negative_compare_target(self, target: str):
        """(image, label) for the right of the seam, or None with the status
        line saying why -- the same checks the print's Compare makes."""
        if target == 'reference':
            reference = self._roll.reference_file()
            if reference is None or self._reference_image is None:
                set_status(self._viewer,
                           'No reference print yet: flag a frame and render it once')
                return None
            return self._reference_image, f'reference ({reference.name})'
        if target == 'kept':
            kept = self._kept_image()
            if kept is None:
                set_status(self._viewer, 'No kept version of this image yet: '
                                         'Keep one to compare against it')
                return None
            return kept, 'last kept version'
        preview_layer = self._layers.preview_input_layer()
        if preview_layer is None:
            set_status(self._viewer, 'Load an image before comparing')
            return None
        image = np.clip(np.asarray(preview_layer.data)[..., :3], 0.0, 1.0)
        return image.astype(np.float32), 'base input'

    def _start_negative_compare(self, target: str) -> None:
        """NAT 2026-10-07 ("Negative yes"): Compare on the Negative view --
        the LIVE negative left of the seam, the target right of it, as the
        page draws it. The negative comes from the Negative view's own render
        path; its result is shown in `_show_negative_left`."""
        if target not in ('input', 'reference', 'kept'):
            target = 'input'            # the negative is not compared with itself
        if self._negative_compare_target(target) is None:
            return
        self._negative_left_pending = target
        self._compare_wants_split = True
        self._start_compare_negative_render()
        if self._active_simulation_worker is None:      # it could not start
            self._negative_left_pending = None

    def _show_negative_left(self, negative, target: str) -> None:
        found = self._negative_compare_target(target)
        if found is None or self._gpu_canvas is None:
            return
        image, label = found
        self._negative_left_mode = f'negative:{target}'
        try:
            self._gpu_canvas.set_image(self._padded_compare(np.asarray(negative), 'negative'))
        except Exception:
            pass
        # the right of the seam, exactly as the print's Compare shows it
        self._push_compare_image(image, target, label)
        # ...and the board: film base under the negative, the far side's own
        # surround past the seam (the input's dark, a print's paper)
        from spektrafilm_gui.theme_palette import PAPER_BASE_WHITE

        right = self._INPUT_SURROUND if target == 'input' else PAPER_BASE_WHITE
        self._set_stage_paper(base=self._blank_film_hex(), right=right)
        set_status(self._viewer, f'Comparing the negative with the {label}. Drag the seam.')

    def _leave_negative_left(self) -> None:
        """Put the print back on the left when the negative compare ends."""
        if self._negative_left_mode is None:
            self._negative_left_pending = None
            return
        self._negative_left_mode = None
        self._negative_left_pending = None
        if self._input_mode:
            return
        # What the canvas showed before the negative took the left: the
        # negative went to the canvas directly, so the funnel's record of the
        # last image is still the print. `_output_display_cached` is the
        # fallback, and can be empty.
        target = getattr(self, '_last_canvas_image', None)
        is_output = bool(getattr(self, '_last_canvas_is_output', True))
        if target is None:
            target, is_output = self._output_display_cached, True
        if target is not None:
            self._mirror_to_gpu_canvas(target, is_output=is_output)

    def _refresh_negative_left(self) -> None:
        """A new print arrived while the negative is on the left: render the
        negative again, so the left stays live."""
        mode = self._negative_left_mode
        if mode is None or self._active_simulation_worker is not None:
            return
        self._start_negative_compare(mode[len('negative:'):])

    def _blank_film_rgb(self) -> tuple:
        """The CURRENT film stock's blank-film color: a black (unexposed)
        frame developed and scanned as a negative at engine defaults — the
        base + orange mask exactly as the simulation renders it. Cached per
        stock (one tiny 16x16 render on first use); any failure falls back
        to the measured kodak_gold_200 constant."""
        try:
            stock = str(self._widgets.simulation.film_stock.value)
        except Exception:
            stock = ''
        cached = self._blank_film_cache.get(stock)
        if cached is not None:
            return cached
        color = self._FILM_BASE_FALLBACK
        if stock:
            try:
                from spektrafilm.runtime.params_builder import init_params
                from spektrafilm.runtime.process import simulate

                params = init_params(film_profile=stock)
                params.workflow.route = "input > film > scan"
                params.camera.auto_exposure = False
                params.debug.deactivate_spatial_effects = True
                params.debug.deactivate_stochastic_effects = True
                out = np.asarray(simulate(np.zeros((16, 16, 3)), params))
                color = tuple(float(v) for v in np.clip(
                    out[4:12, 4:12].reshape(-1, 3).mean(axis=0), 0.0, 1.0))
            except Exception:
                color = self._FILM_BASE_FALLBACK
        self._blank_film_cache[stock] = color
        return color

    def _scan_film_active(self) -> bool:
        try:
            return bool(self._widgets.simulation.scan_film_value())
        except Exception:
            return False

    def _display_padding_fraction(self) -> float:
        """How much border to draw INTO the displayed picture. Usually none.

        2026-08-24; see design/DECISIONS.md#d-controller-306

        IT DOES NOT GO WHEN IT IS PART OF THE PICTURE. `paper.padding_burn`
        adds a border ON EXPORT and lets the paper texture bleed into it, and
        its own tooltip promises it is "shown live in the preview too". When
        that is on, the border is not chrome -- it is what will be written to
        the file, and previewing it is the point. So the fraction survives
        exactly there, and `display.white_padding` goes on meaning what it
        always meant: the width of that border.
        """
        paper = self._active_paper_state()
        if paper is None or not getattr(paper, 'padding_burn', False):
            return 0.0
        return float(self._widgets.display.white_padding.value)

    def _padded_compare(self, image: np.ndarray, mode: str) -> np.ndarray:
        """The compare quad gets the SAME relative padding as the main image:
        white around the base input, blank-film color around the negative."""
        fraction = self._display_padding_fraction()
        padding = runtime.padding_pixels_for_image(image, fraction)
        if padding <= 0:
            return image
        fill = self._blank_film_rgb() if mode == 'negative' else None
        return runtime.apply_border_padding(image, padding, fill=fill)

    def _set_compare_layer(self, image: np.ndarray) -> None:
        height, width = image.shape[:2]
        long_edge = max(height, width, 1)
        world_h, world_w = height / long_edge, width / long_edge
        output_layer = self._output_layer()
        ref = output_layer if output_layer is not None else self._layers.preview_input_layer()
        ref_world = np.asarray(ref.extent.world)
        gap = 0.05
        layer = next((l for l in self._viewer.layers
                      if getattr(l, 'name', None) == self._COMPARE_LAYER_NAME), None)
        if layer is None:
            layer = self._viewer.add_image(image, name=self._COMPARE_LAYER_NAME)
        else:
            layer.data = image
        layer.scale = (world_h / height, world_w / width)
        layer.translate = (-0.5 * world_h, float(ref_world[1][-1]) + gap)
        layer.visible = True
        reset_viewer_camera(self._viewer)

    def _remove_compare_layer(self) -> None:
        layer = next((l for l in self._viewer.layers
                      if getattr(l, 'name', None) == self._COMPARE_LAYER_NAME), None)
        if layer is not None:
            self._viewer.layers.remove(layer)
            set_status(self._viewer, 'Compare off')

    def adjust_print_filter_shift(self, channel: str, delta: float) -> None:
        """Keyboard nudge for the enlarger Y/M filter shifts (Ctrl+Shift+8/9/5/6)."""
        editor = (self._widgets.simulation.print_y_filter_shift if channel == 'y'
                  else self._widgets.simulation.print_m_filter_shift)
        editor.value = round(float(editor.value) + float(delta), 4)
        set_status(self._viewer,
                   f'Print {channel.upper()} filter shift: {float(editor.value):+.2f}')

    def show_fullscreen_output(self) -> None:
        """Full-screen the rendered output (white padding included) on a
        completely black background."""

        if not self._loaded_frame_is_current():
            # Sorting moved on and the render on hand is another frame's:
            # open the selected one, render it, and go full screen when that
            # render lands (`_on_simulation_finished`).
            self._fullscreen_when_rendered = True
            self._run_preview(report_status=True)
            if self._active_simulation_worker is None:      # refused, or not loadable
                self._fullscreen_when_rendered = False
            return
        output_layer = self._output_layer()
        if output_layer is None:
            set_status(self._viewer, 'Run a preview or scan before going full screen')
            return
        data = np.asarray(output_layer.data)[..., :3]
        if np.issubdtype(data.dtype, np.floating):
            data = np.clip(data, 0.0, 1.0)
        else:
            data = runtime.normalized_image_data(data)
        state = collect_gui_state(widgets=self._widgets)
        padding_pixels = self._display_padding_fraction() * max(data.shape[:2])
        fill = self._blank_film_rgb() if self._scan_film_active() else None
        padded = runtime.apply_border_padding(data, padding_pixels, fill=fill)
        image_rgb8 = (np.clip(np.asarray(padded, dtype=np.float32), 0.0, 1.0) * 255.0
                      + 0.5).astype(np.uint8)
        show_fullscreen_image(image_rgb8, parent=dialog_parent(self._viewer))

    @staticmethod
    def _synced_field_paths():
        for section_name, field_names in PROFILE_SYNC_FIELDS.items():
            for field_name in field_names:
                yield section_name, field_name

    def _remember_film_polarity(self, stock=None, params=None) -> None:
        """Record the selected film's polarity beside its slug.

        From `params` when the caller already has them, else loaded from the
        slug. Unknown resolves to None, and `_film_polarity_changed` reads
        None as 'no crossing', so a profile that cannot be read never causes
        someone's coupler edits to be discarded.
        """
        positive = getattr(getattr(params, 'film', None), 'is_positive', None)
        if positive is None and stock:
            try:
                from spektrafilm.profiles.io import load_profile
                positive = load_profile(str(stock)).info.type == 'positive'
            except Exception:
                positive = None
        self._last_synced_film_is_positive = (
            None if positive is None else bool(positive))

    def _film_polarity_changed(self, params) -> bool:
        """Did this stock change cross negative <-> positive?

        False when there is nothing to compare against (the first sync of a
        session, or a stock whose polarity cannot be read), so the answer is
        never a guess that discards someone's edits.
        """
        previous = self._last_synced_film_is_positive
        if previous is None:
            return False
        current = getattr(getattr(params, 'film', None), 'is_positive', None)
        if current is None:
            return False
        return bool(current) != bool(previous)

    def _preserve_user_edits(self, state, synced_state, *,
                             polarity_changed: bool = False) -> int:
        """Carry the user's own values across a stock change (H5).

        2026-09-03; see design/DECISIONS.md#d-controller-307

        `polarity_changed` exempts `POLARITY_RESEEDED_SECTIONS`: a negative's
        coupler constants are wrong for a positive in a way that shows as
        banded colour, not as a matter of taste.
        """
        baseline = self._last_synced_values or {}
        if not baseline:
            return 0                    # no sync yet: nothing can be 'moved'
        carried = 0
        for section_name, field_name in self._synced_field_paths():
            if polarity_changed and section_name in self.POLARITY_RESEEDED_SECTIONS:
                continue                # takes the new stock's own values
            current_section = getattr(state, section_name, None)
            target_section = getattr(synced_state, section_name, None)
            if current_section is None or target_section is None:
                continue
            if not hasattr(current_section, field_name):
                continue
            key = f'{section_name}.{field_name}'
            if key not in baseline or key in self._STOCK_SELECTION_KEYS:
                continue
            current = getattr(current_section, field_name)
            if current != baseline[key]:
                setattr(target_section, field_name, current)
                carried += 1
        return carried

    def _report_stock_change(self, carried: int, stock: str, *,
                             polarity_changed: bool = False) -> None:
        """Say what a stock change did to the settings you had tuned.

        2026-09-04; see design/DECISIONS.md#d-controller-308

        A polarity change is the exception that speaks even when nothing was
        kept: the couplers were re-seeded under the user, and that is exactly
        the kind of silent move the notice exists for.
        """
        note = ('The DIR couplers took this film’s own values: its polarity '
                'changed.')
        if carried <= 0:
            if polarity_changed:
                set_status(self._viewer, f'{stock}: {note}')
            return
        kept = (f'{stock}: kept {carried} setting'
                f'{"s" if carried != 1 else ""} you had edited; the rest '
                f'took the stock’s own values.')
        set_status(self._viewer, f'{kept} {note}' if polarity_changed else kept)

    def _seed_sync_baseline(self) -> bool:
        """Record what a sync WOULD write for the stock now selected.

        2026-09-04; see design/DECISIONS.md#d-controller-309

        The baseline is what the sync WOULD WRITE, not what the state
        currently holds. That distinction is the whole point: a preset's
        values differ from the stock's constants, so they read as 'moved' and
        survive -- which is what a preset is for.

        Returns whether a baseline was established; safe to call at any time
        and cheap to call twice (it no-ops once one exists).
        """
        if self._last_synced_values:
            return True
        try:
            state = collect_gui_state(widgets=self._widgets)
            if not state.simulation.film_stock or not state.simulation.print_paper:
                return False
            synced = gui_state_from_params(
                digest_after_selection(build_params_from_state(state)),
                film_stock=state.simulation.film_stock,
                print_paper=state.simulation.print_paper,
            )
        except Exception:
            # A diagnostic convenience must never be why a render fails; the
            # next call gets another chance.
            return False
        self._remember_synced_values(synced)
        self._point_baselines_at_the_stock()
        self._last_synced_film_stock = state.simulation.film_stock
        self._remember_film_polarity(state.simulation.film_stock)
        return True

    def _as_the_widget_holds(self, section_name, field_name, value):
        """`value` as the editor for `section.field` would HOLD it.

        2026-09-11; see design/DECISIONS.md#d-controller-310

        The answer is the editor's own: its text round trip (its decimals, its
        locale) and then its range clamp, which is exactly what `setValue`
        does. Composite editors answer per part. Anything that is not a spin
        box (toggles, enums, selectors) is held exactly as given, and so is a
        value with no editor at all (a bare controller in a test).
        """
        from qtpy import QtWidgets

        widgets = getattr(self, '_widgets', None)
        editor = getattr(getattr(widgets, section_name, None), field_name, None)
        if editor is None:
            return value
        editors = getattr(editor, 'editors', None)
        if callable(editors):
            editors = editors()
        if isinstance(editors, (list, tuple)):
            # `TupleEditor.editors` is a PROPERTY returning a tuple; a
            # callable check alone skipped it and left its pairs unrounded
            parts = list(editors)
        elif isinstance(getattr(editor, '_spins', None), (list, tuple)):
            parts = list(editor._spins)
        elif getattr(editor, '_inner', None) is not None:
            parts = [editor._inner]
        else:
            parts = [editor]

        def held(spin, component):
            if isinstance(component, bool):
                return component
            if isinstance(spin, QtWidgets.QDoubleSpinBox) and isinstance(component, (int, float)):
                # 2026-09-21; see design/DECISIONS.md#d-controller-274
                bounded = min(max(float(component), spin.minimum()), spin.maximum())
                shown = spin.valueFromText(spin.textFromValue(bounded))
                return min(max(shown, spin.minimum()), spin.maximum())
            if isinstance(spin, QtWidgets.QSpinBox) and isinstance(component, int):
                return min(max(component, spin.minimum()), spin.maximum())
            return component

        if isinstance(value, tuple) and len(parts) == len(value) and len(parts) > 1:
            return tuple(held(part, component) for part, component in zip(parts, value))
        if len(parts) == 1 and not isinstance(value, tuple):
            return held(parts[0], value)
        return value

    def _remember_synced_values(self, synced_state) -> None:
        """Snapshot what the sync just wrote, as the baseline the next one
        measures 'the user moved this' against."""
        remembered: dict = {}
        for section_name, field_name in self._synced_field_paths():
            section = getattr(synced_state, section_name, None)
            if section is None or not hasattr(section, field_name):
                continue
            remembered[f'{section_name}.{field_name}'] = self._as_the_widget_holds(
                section_name, field_name, getattr(section, field_name))
        self._last_synced_values = remembered

    def _point_baselines_at_the_stock(self) -> None:
        """Point every synced row's reset at what the selected film and paper give a default look.

        2026-10-10; see design/DECISIONS.md#d-controller_compare-964
        """
        state = collect_gui_state(widgets=self._widgets)
        if not state.simulation.film_stock or not state.simulation.print_paper:
            return
        stock = build_default_gui_state(film_stock=state.simulation.film_stock,
                                        print_paper=state.simulation.print_paper)
        if state.simulation.raw:
            self._overlay_raw_couplers(state, stock)
        profile_sync.update_sync_baselines(
            widgets=self._widgets, stock_state=stock,
            profile_sync_fields=PROFILE_SYNC_FIELDS)
