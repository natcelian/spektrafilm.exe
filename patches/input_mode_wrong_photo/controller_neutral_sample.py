"""The neutral sample part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes NeutralSampleMixin as a base.
"""
from __future__ import annotations

import numpy as np

from spektrafilm_gui.recover_section import recover_settings
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm.utils.raw_file_processor import solve_neutral_white_balance


class NeutralSampleMixin:
    """GuiController's neutral sample methods."""

    def arm_neutral_sample(self, armed: bool) -> None:
        """Arm the canvas for one neutral pick, or disarm it."""
        armed = bool(armed)
        if armed and not self._load_selected_frame():    # sample its own pixels
            armed = False
        self._neutral_sample_armed = armed
        canvas = self._gpu_canvas
        if canvas is not None and callable(getattr(canvas, 'arm_strip_click', None)):
            # THE CANVAS HAS ONE CLICK SLOT, and the test strip uses it too
            # (`_on_strip_canvas_click`, click a band to adopt it). Disarming
            # to None took the strip's handler with it, so arming the
            # eyedropper while a strip was showing killed click-to-adopt
            # until the strip was rebuilt -- silently, because nothing
            # errors, the clicks just stop meaning anything.
            #
            # So disarming gives the slot BACK to whoever had it.
            canvas.arm_strip_click(
                self._on_neutral_sample_click if armed
                else (self._on_strip_canvas_click
                      if self._strip_bands else None))
        set_status(self._viewer,
                   'Click something neutral in the picture'
                   if armed else 'White balance sampling cancelled')

    def _neutral_base_image(self):
        """The UNBALANCED base to sample, or None.

        `solve_neutral_white_balance` inverts the custom white balance,
        which is defined against the daylight base. When the frame is on
        `as_shot` the stored input has the camera`s balance baked in
        (`_current_input_kind is None`), so sampling it would ask what
        makes an already-balanced pixel neutral and get 6504 K back. In
        that case the daylight base is decoded once, here, rather than
        answering confidently with a wrong number.
        """
        if self._current_input_kind == 'raw':
            return self._current_input_image
        path = self._current_input_path
        if not path:
            return None
        try:
            from spektrafilm.utils.raw_file_processor import load_raw_daylight_base
            from spektrafilm_gui.roll_session import classify_path

            if classify_path(path) != 'raw':
                return None
            state = collect_gui_state(widgets=self._widgets)
            return load_raw_daylight_base(
                path, lens_correction=state.load_raw.lens_correction,
                recover=recover_settings(state, path))
        except Exception:
            # `logging.getLogger(...)`, not a module-level `logger`: this
            # file has none, and the name I first wrote here would have
            # raised NameError INSIDE an except block -- replacing the real
            # failure with a worse one, on the path nobody is looking at.
            # The GPU package carries a test for exactly this shape.
            import logging

            logging.getLogger('spektrafilm.app').exception(
                'could not decode a daylight base to sample')
            return None

    def _on_neutral_sample_click(self, u: float, v: float) -> None:
        """A click landed while the eyedropper was armed."""

        self.arm_neutral_sample(False)          # one pick, then disarm
        base = self._neutral_base_image()
        section = self._file_section()
        if base is None or section is None:
            set_status(self._viewer,
                       'Nothing to sample - the eyedropper needs a RAW frame')
            return
        array = np.asarray(base)
        height, width = array.shape[0], array.shape[1]
        x = int(min(max(float(u), 0.0), 1.0) * (width - 1))
        y = int(min(max(float(v), 0.0), 1.0) * (height - 1))
        radius = int(self.NEUTRAL_SAMPLE_RADIUS)
        patch = array[max(0, y - radius):y + radius + 1,
                      max(0, x - radius):x + radius + 1, :3]
        if patch.size == 0:
            return
        temperature, tint = solve_neutral_white_balance(
            np.asarray(patch, dtype=np.float64).reshape(-1, 3).mean(axis=0))
        section.sample_landed(temperature, tint)
        set_status(self._viewer,
                   f'White balance sampled: {temperature:.0f} K - {tint:.2f}')

    def _file_section(self):
        panel = getattr(self, '_left_panel', None)
        return getattr(panel, 'file_section', None) if panel is not None else None
